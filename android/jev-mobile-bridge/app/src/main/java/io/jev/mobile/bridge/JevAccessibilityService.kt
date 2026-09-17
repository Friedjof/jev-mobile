package io.jev.mobile.bridge

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.graphics.Rect
import android.os.Bundle
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.util.ArrayDeque
import java.security.SecureRandom
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Minimal, USB-forwarded semantic Android bridge.
 *
 * The HTTP server binds to loopback only. The desktop must explicitly use
 * `adb forward tcp:8765 tcp:8765`; no LAN listener or cloud transport exists.
 */
class JevAccessibilityService : AccessibilityService() {
    private val running = AtomicBoolean(false)
    // Accepting is inherently blocking. Keep one dedicated acceptor and a
    // bounded request pool; a cached pool here can create unbounded blocked
    // accept threads and exhaust a small phone's memory.
    private val acceptExecutor = Executors.newSingleThreadExecutor()
    private val requestExecutor = Executors.newFixedThreadPool(2)
    private val events = ArrayDeque<JSONObject>()
    private val eventLock = Any()
    @Volatile private var sequence = 0L
    @Volatile private var server: ServerSocket? = null
    private val bridgeToken: String by lazy {
        getSharedPreferences(PREFERENCES, MODE_PRIVATE).getString(TOKEN_KEY, null)
            ?: ByteArray(24).also(SecureRandom()::nextBytes).joinToString("") { "%02x".format(it) }
                .also { getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit().putString(TOKEN_KEY, it).apply() }
    }

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "Accessibility service created")
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        Log.i(TAG, "Accessibility service connected")
        // Persist the local token before the desktop's unauthenticated health
        // probe. The desktop debug adapter can then retrieve it via `adb run-as`.
        val tokenInitialized = bridgeToken.isNotEmpty()
        Log.i(TAG, "Bridge authentication initialized=$tokenInitialized")
        try {
            serviceInfo = serviceInfo.apply {
                flags = flags or AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                    AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS or
                    AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS
                notificationTimeout = 100
            }
        } catch (error: Exception) {
            Log.e(TAG, "Could not configure accessibility service", error)
        }
        startServer()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val record = JSONObject().apply {
            put("sequence", ++sequence)
            put("time_ms", System.currentTimeMillis())
            put("type", eventTypeName(event.eventType))
            put("package", event.packageName?.toString() ?: "")
            put("window_id", event.windowId)
        }
        synchronized(eventLock) {
            events.addLast(record)
            while (events.size > 100) events.removeFirst()
        }
    }

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        Log.i(TAG, "Accessibility service destroyed")
        running.set(false)
        server?.close()
        acceptExecutor.shutdownNow()
        requestExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun startServer() {
        if (!running.compareAndSet(false, true)) return
        acceptExecutor.execute {
            try {
                ServerSocket(PORT, 8, InetAddress.getByName("127.0.0.1")).also {
                    server = it
                    Log.i(TAG, "Bridge listening on phone loopback:$PORT")
                }.use { socket ->
                    while (running.get()) {
                        val client = socket.accept()
                        requestExecutor.execute { handle(client) }
                    }
                }
            } catch (error: Exception) {
                Log.e(TAG, "Bridge server stopped", error)
                running.set(false)
            }
        }
    }

    private fun handle(socket: Socket) {
        socket.use {
            val input = BufferedReader(InputStreamReader(it.getInputStream(), StandardCharsets.UTF_8))
            val requestLine = input.readLine() ?: return
            val parts = requestLine.split(" ")
            if (parts.size < 2) return respond(it.getOutputStream(), 400, error("Malformed request"))
            val headers = mutableMapOf<String, String>()
            while (true) {
                val line = input.readLine() ?: break
                if (line.isEmpty()) break
                val separator = line.indexOf(':')
                if (separator > 0) headers[line.substring(0, separator).lowercase()] = line.substring(separator + 1).trim()
            }
            val contentLength = headers["content-length"]?.toIntOrNull() ?: 0
            val body = CharArray(contentLength)
            var offset = 0
            while (offset < contentLength) {
                val read = input.read(body, offset, contentLength - offset)
                if (read < 0) break
                offset += read
            }
            val response = when (parts[0] to parts[1]) {
                "GET" to "/health" -> JSONObject().put("ok", true).put("service", "jev-mobile-bridge").put("authentication", "required")
                "GET" to "/state" -> authenticated(headers) { buildState() }
                "GET" to "/events" -> authenticated(headers) { buildEvents() }
                "POST" to "/action" -> authenticated(headers) { performAction(JSONObject(String(body))) }
                else -> error("Unknown endpoint")
            }
            respond(it.getOutputStream(), if (response.optBoolean("ok", true)) 200 else 400, response)
        }
    }

    private fun authenticated(headers: Map<String, String>, action: () -> JSONObject): JSONObject =
        if (headers[AUTH_HEADER] == bridgeToken) action() else error("Unauthorized bridge request")

    private fun respond(output: OutputStream, status: Int, json: JSONObject) {
        val data = json.toString().toByteArray(StandardCharsets.UTF_8)
        output.write("HTTP/1.1 $status ${if (status == 200) "OK" else "Bad Request"}\r\nContent-Type: application/json\r\nContent-Length: ${data.size}\r\nConnection: close\r\n\r\n".toByteArray())
        output.write(data)
    }

    private fun buildState(): JSONObject {
        // During an app transition Android can briefly return null here even
        // though an active/focused interactive window already has a root.
        val root = rootInActiveWindow
            ?: windows.firstOrNull { it.isActive || it.isFocused }?.root
            ?: return error("No active accessibility window")
        return try {
            JSONObject().apply {
                put("ok", true)
                put("sequence", sequence)
                put("captured_at_ms", System.currentTimeMillis())
                put("active_window_id", root.windowId)
                put("package", root.packageName?.toString() ?: "")
                put("keyboard_visible", windows.any { window -> window.type == android.view.accessibility.AccessibilityWindowInfo.TYPE_INPUT_METHOD })
                put("tree", nodeJson(root, "0"))
            }
        } finally {
            root.recycle()
        }
    }

    private fun buildEvents(): JSONObject = JSONObject().apply {
        put("ok", true)
        put("sequence", sequence)
        put("events", JSONArray().also { result -> synchronized(eventLock) { events.forEach(result::put) } })
    }

    private fun nodeJson(node: AccessibilityNodeInfo, path: String): JSONObject {
        val bounds = Rect().also(node::getBoundsInScreen)
        return JSONObject().apply {
            put("id", "${node.windowId}:$path")
            put("resource_id", node.viewIdResourceName ?: "")
            put("class_name", node.className?.toString() ?: "")
            put("package", node.packageName?.toString() ?: "")
            put("text", node.text?.toString() ?: "")
            put("content_description", node.contentDescription?.toString() ?: "")
            put("hint", node.hintText?.toString() ?: "")
            put("state_description", if (android.os.Build.VERSION.SDK_INT >= 30) node.stateDescription?.toString() ?: "" else "")
            put("bounds", JSONObject().put("left", bounds.left).put("top", bounds.top).put("right", bounds.right).put("bottom", bounds.bottom))
            put("clickable", node.isClickable)
            put("long_clickable", node.isLongClickable)
            put("focusable", node.isFocusable)
            put("focused", node.isFocused)
            put("checkable", node.isCheckable)
            put("checked", node.isChecked)
            put("editable", node.isEditable)
            put("password", node.isPassword)
            put("scrollable", node.isScrollable)
            put("enabled", node.isEnabled)
            put("visible", node.isVisibleToUser)
            put("window_id", node.windowId)
            put("actions", JSONArray().also { list -> node.actionList.forEach { list.put(actionName(it.id)) } })
            put("children", JSONArray().also { children ->
                for (index in 0 until node.childCount) node.getChild(index)?.let { child ->
                    try { children.put(nodeJson(child, "$path.$index")) } finally { child.recycle() }
                }
            })
        }
    }

    private fun performAction(request: JSONObject): JSONObject {
        val action = request.optString("type")
        val target = request.optString("target")
        val node = resolveNode(target) ?: return error("Unknown or stale target")
        return try {
            val success = when (action) {
                "click" -> node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                "focus" -> node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                "set_text" -> node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, request.optString("text"))
                })
                "scroll_forward" -> node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
                "scroll_backward" -> node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
                else -> false
            }
            if (success) JSONObject().put("ok", true) else error("Action was rejected by Android")
        } finally {
            node.recycle()
        }
    }

    private fun resolveNode(target: String): AccessibilityNodeInfo? {
        val path = target.substringAfter(':', missingDelimiterValue = "")
        if (path.isEmpty()) return null
        var current = rootInActiveWindow ?: return null
        for (part in path.split('.').drop(1)) {
            val child = current.getChild(part.toIntOrNull() ?: return null)
            current.recycle()
            current = child ?: return null
        }
        return current
    }

    private fun error(message: String) = JSONObject().put("ok", false).put("error", message)

    private fun actionName(id: Int) = when (id) {
        AccessibilityNodeInfo.ACTION_CLICK -> "CLICK"
        AccessibilityNodeInfo.ACTION_LONG_CLICK -> "LONG_CLICK"
        AccessibilityNodeInfo.ACTION_FOCUS -> "FOCUS"
        AccessibilityNodeInfo.ACTION_SET_TEXT -> "SET_TEXT"
        AccessibilityNodeInfo.ACTION_SCROLL_FORWARD -> "SCROLL_FORWARD"
        AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD -> "SCROLL_BACKWARD"
        else -> "ACTION_$id"
    }

    private fun eventTypeName(type: Int) = when (type) {
        AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> "WINDOW_STATE_CHANGED"
        AccessibilityEvent.TYPE_WINDOWS_CHANGED -> "WINDOWS_CHANGED"
        AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> "WINDOW_CONTENT_CHANGED"
        AccessibilityEvent.TYPE_VIEW_FOCUSED -> "VIEW_FOCUSED"
        AccessibilityEvent.TYPE_VIEW_CLICKED -> "VIEW_CLICKED"
        AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED -> "VIEW_TEXT_CHANGED"
        AccessibilityEvent.TYPE_VIEW_SCROLLED -> "VIEW_SCROLLED"
        else -> "EVENT_$type"
    }

    companion object {
        const val PORT = 8765
        const val AUTH_HEADER = "x-jev-mobile-token"
        const val PREFERENCES = "jev_mobile_bridge"
        const val TOKEN_KEY = "token"
        const val TAG = "JevMobileBridge"
    }
}

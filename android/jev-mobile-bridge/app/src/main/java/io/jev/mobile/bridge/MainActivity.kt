package io.jev.mobile.bridge

import android.accessibilityservice.AccessibilityServiceInfo
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val status = TextView(this).apply { textSize = 16f; setPadding(48, 64, 48, 32) }
        val button = Button(this).apply {
            text = "Open Accessibility settings"
            setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(status)
            addView(button)
        })
        status.text = if (isServiceEnabled()) {
            "Bridge active\n\nUSB-only endpoint: http://127.0.0.1:8765\nUse: adb forward tcp:8765 tcp:8765"
        } else {
            "Enable “Jev Mobile Bridge” in Android Accessibility settings.\n\nThe bridge only listens on phone localhost; it is not exposed to Wi-Fi or the internet."
        }
    }

    private fun isServiceEnabled(): Boolean {
        val manager = getSystemService(Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
        return manager.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
            .any { it.resolveInfo.serviceInfo.packageName == packageName }
    }
}

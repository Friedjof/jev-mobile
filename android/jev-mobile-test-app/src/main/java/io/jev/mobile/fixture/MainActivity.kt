package io.jev.mobile.fixture

import android.app.Activity
import android.os.Bundle
import android.view.View
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject

/** Deterministic accessibility fixture; the agent uses only normal UI semantics. */
class MainActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("entities", MODE_PRIVATE) }
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        // Test setup only: this Intent extra is consumed by the fixture UI,
        // never by Jev Mobile.  The agent still sees an ordinary editor and
        // must use accessibility/navigation to leave it safely.
        if (intent?.getStringExtra("fixture_state") == "foreign_editor") {
            val foreign = JSONObject().put("id", "fixture-foreign").put("title", "ForeignExisting").put("body", "DoNotModify")
            save(listOf(foreign))
            showEditor(foreign)
        } else showCollection()
    }

    private fun entities(): MutableList<JSONObject> = JSONArray(prefs.getString("items", "[]")).let { array -> MutableList(array.length()) { array.getJSONObject(it) } }
    private fun save(items: List<JSONObject>) = prefs.edit().putString("items", JSONArray(items).toString()).apply()
    private fun showCollection() {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 48, 32, 32) }
        root.addView(TextView(this).apply { text = "Items collection"; textSize = 24f; contentDescription = "Items collection" })
        root.addView(Button(this).apply { text = "Create item"; contentDescription = "Create item"; setOnClickListener {
            val item = JSONObject().put("id", java.util.UUID.randomUUID().toString()).put("title", "").put("body", "")
            val all = entities(); all.add(item); save(all); showEditor(item)
        } })
        entities().forEach { item -> root.addView(Button(this).apply { text = item.getString("title"); contentDescription = "Existing item ${item.getString("title")}"; setOnClickListener { showEditor(item) } }) }
        setContentView(root)
    }
    private fun showEditor(existing: JSONObject?) {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 48, 32, 32) }
        root.addView(Button(this).apply { text = "Back to collection"; contentDescription = "Back to collection"; setOnClickListener { showCollection() } })
        val title = EditText(this).apply { hint = "Title"; contentDescription = "Title"; setText(existing?.optString("title") ?: "") }
        val body = EditText(this).apply { hint = "Body"; contentDescription = "Body"; minLines = 3; setText(existing?.optString("body") ?: "") }
        root.addView(title); root.addView(body)
        root.addView(Button(this).apply { text = "Save item"; contentDescription = "Save item"; setOnClickListener {
            // `entities()` reconstructs JSON objects from persistence.  Update
            // that freshly loaded collection by stable id instead of mutating
            // the editor's stale JSON object, so a saved value remains real
            // persistence across an Activity/process restart.
            val all = entities()
            val id = existing?.optString("id") ?: java.util.UUID.randomUUID().toString()
            val item = JSONObject().put("id", id).put("title", title.text.toString()).put("body", body.text.toString())
            val index = all.indexOfFirst { it.optString("id") == id }
            if (index >= 0) all[index] = item else all.add(item)
            save(all); showCollection()
        } })
        setContentView(root)
    }
}

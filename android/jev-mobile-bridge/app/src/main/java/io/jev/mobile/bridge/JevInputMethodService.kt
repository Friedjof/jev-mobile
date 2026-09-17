package io.jev.mobile.bridge

import android.inputmethodservice.InputMethodService
import android.view.inputmethod.InputConnection

/**
 * Unicode-safe companion IME used only when explicitly enabled by its owner.
 *
 * The accessibility bridge calls [commit] in-process; no broadcast receiver,
 * LAN listener, or shell-escaped text is involved. Android still requires the
 * user to enable/select this IME before it can receive input.
 */
class JevInputMethodService : InputMethodService() {
    override fun onStartInput(attribute: android.view.inputmethod.EditorInfo?, restarting: Boolean) {
        super.onStartInput(attribute, restarting)
        active = this
    }

    override fun onFinishInput() {
        if (active === this) active = null
        super.onFinishInput()
    }

    private fun commitInternal(text: String): Boolean {
        val connection: InputConnection = currentInputConnection ?: return false
        return connection.commitText(text, 1)
    }

    companion object {
        @Volatile private var active: JevInputMethodService? = null

        fun commit(text: String): Boolean = active?.commitInternal(text) ?: false
    }
}

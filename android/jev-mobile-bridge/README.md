# Jev Mobile Bridge

`jev-mobile-bridge` is a deliberately small Android companion app for the
Python controller. It is not a replacement for Android security controls and
is currently a debug-only proof of concept.

It exposes a richer Accessibility snapshot than the current Mobile MCP backend:

- real `clickable`, `editable`, `focusable`, `checkable`, `checked`, and
  `scrollable` flags;
- view resource IDs, hints, state descriptions, bounds, window IDs, hierarchy,
  and Android-provided actions;
- semantic `CLICK`, `FOCUS`, `SET_TEXT`, and scroll execution.

The service binds its HTTP endpoint to `127.0.0.1:8765` **on the phone only**.
The desktop reaches it solely through USB debugging:

```text
desktop Python -> adb forward tcp:8765 tcp:8765 -> phone loopback bridge
```

There is no LAN listener, Wi-Fi transport, or cloud service. A random local
token protects state and action endpoints. The debug Python adapter retrieves
that token through `adb run-as`; a future release build requires
`JEV_MOBILE_BRIDGE_TOKEN` on the desktop.

## Local development

The project expects Android SDK API 34 and JDK 17. Keep the local SDK path in
an untracked `local.properties` file:

```properties
sdk.dir=/absolute/path/to/Android/Sdk
```

Build and install the debug APK:

```bash
gradle :app:assembleDebug
adb -s YOUR_SERIAL install -r app/build/outputs/apk/debug/app-debug.apk
adb -s YOUR_SERIAL shell am start -n io.jev.mobile.bridge/.MainActivity
```

On the phone, press **Open Accessibility settings**, select **Jev Mobile
Bridge**, and enable it manually. Android deliberately requires this explicit
user action. The service can then be inspected without changing the device:

```bash
uv run jev-mobile inspect --backend bridge --serial YOUR_SERIAL
```

The Python controller remains backend-neutral; `--backend mobile-mcp` is still
the default fallback.

## Important Android 11 limitation

Mobile Next's on-device `com.mobilenext.mobilecli.DeviceServer` uses Android
`UiAutomation`. On Android 11, its default UiAutomation flags suppress
third-party Accessibility Services. Therefore do **not** run Mobile MCP UI-tree
inspection while this bridge is enabled. Stop the DeviceServer first; Android
will then bind `JevAccessibilityService` normally. Mobile MCP can remain a
fallback for non-UiAutomation operations only, unless its UiAutomation setup is
changed to request `FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES`.

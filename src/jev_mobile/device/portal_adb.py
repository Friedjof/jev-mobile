"""USB-only Mobilerun Portal adapter.

Portal is used exclusively through ``adb shell content`` and local ADB input;
this module neither configures Portal sockets nor reverse/cloud connections.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from typing import Any

from ..state.models import Bounds, RawDeviceElement, RawDeviceState
from .base import MutationOutcomeUnknown, MutationReceipt, MutationRejected


class PortalAdbError(RuntimeError):
    pass


class PortalAdbDeviceAdapter:
    authority = "content://com.mobilerun.portal"

    def __init__(self, serial: str, *, adb_command: str = "adb") -> None:
        self.serial, self.adb_command = serial, adb_command
        self._refs: dict[str, str] = {}
        self._scroll_bounds: Bounds | None = None
        self._snapshot_sequence = 0

    async def __aenter__(self) -> "PortalAdbDeviceAdapter":
        await self._content_query("ping")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def observe(self) -> RawDeviceState:
        payload = await self._content_query("state_full?filter=false")
        state = self._unwrap(payload)
        tree = state.get("tree") or state.get("a11y_tree") or state.get("accessibilityTree") or []
        nodes = tree if isinstance(tree, list) else [tree]
        elements: list[RawDeviceElement] = []
        self._refs, self._scroll_bounds = {}, None
        for node, parent, depth in self._walk(nodes, None, 0):
            element = self._element(node, parent, depth)
            if element is None: continue
            element.node_id = element.node_id or f"e{len(elements) + 1}"
            self._refs[element.node_id] = element.node_id
            if self._scroll_bounds is None and element.scrollable: self._scroll_bounds = element.bounds
            elements.append(element)
        self._nodes = {element.node_id: element for element in elements if element.node_id}
        self._snapshot_sequence += 1
        phone = state.get("phone_state") or state.get("phoneState") or state
        return RawDeviceState(package=phone.get("package") or phone.get("packageName") or phone.get("currentPackage") or state.get("package"),
            activity=phone.get("activity") or phone.get("activityName") or phone.get("currentActivity"), elements=elements,
            snapshot_id=f"s{self._snapshot_sequence}",
            observed_at_monotonic=time.monotonic(), keyboard_visible=bool(phone.get("keyboard_visible") or phone.get("keyboardVisible")),
            focused_element_id=phone.get("focused_element_id") or phone.get("focusedElementId"),
            active_window_id=phone.get("window_id") or phone.get("windowId"))

    async def tap(self, target: str) -> MutationReceipt:
        node = self._require_target(target)
        # Portal's content provider does not provide tap; use USB ADB input.
        bounds = node.bounds
        if bounds is None: raise MutationRejected(f"target {target} has no bounds")
        await self._adb("shell", "input", "tap", str(bounds.x + bounds.width // 2), str(bounds.y + bounds.height // 2))
        return MutationReceipt(request_id=uuid.uuid4().hex, status="accepted")

    async def long_press(self, target: str) -> None:
        node = self._require_target(target)
        if not node.bounds: raise MutationRejected(f"target {target} has no bounds")
        x, y = node.bounds.x + node.bounds.width // 2, node.bounds.y + node.bounds.height // 2
        await self._adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), "650")

    async def type_text(self, text: str, target: str | None = None) -> MutationReceipt:
        if target:
            await self.tap(target)
        return await self.type_text_ime(text)

    async def type_text_ime(self, text: str) -> MutationReceipt:
        return await self._ime_input(text, clear=True)

    async def append_text(self, text: str, target: str | None = None) -> MutationReceipt:
        if target:
            await self.tap(target)
        return await self._ime_input(text, clear=False)

    async def clear_text(self, target: str | None = None) -> MutationReceipt:
        if target:
            await self.tap(target)
        try:
            await self._content_insert("keyboard/clear")
        except PortalAdbError as error:
            raise MutationRejected(str(error)) from error
        return MutationReceipt(request_id=uuid.uuid4().hex, status="accepted")

    async def _ime_input(self, text: str, *, clear: bool) -> MutationReceipt:
        try:
            bindings = ["base64_text:s:" + base64.b64encode(text.encode()).decode(), f"clear:b:{str(clear).lower()}"]
            await self._content_insert("keyboard/input", *bindings)
        except PortalAdbError as error:
            # The provider could have received the request even if ADB lost its response.
            if "timed out" in str(error).casefold(): raise MutationOutcomeUnknown(str(error)) from error
            raise MutationRejected(str(error)) from error
        return MutationReceipt(request_id=uuid.uuid4().hex, status="accepted")

    async def swipe(self, direction: str) -> None:
        bounds = self._scroll_bounds or Bounds(x=100, y=300, width=600, height=1000)
        x = bounds.x + bounds.width // 2
        top, bottom = bounds.y + bounds.height // 4, bounds.y + bounds.height * 3 // 4
        start, end = (bottom, top) if direction == "down" else (top, bottom)
        await self._adb("shell", "input", "swipe", str(x), str(start), str(x), str(end), "350")

    async def back(self) -> None: await self._adb("shell", "input", "keyevent", "4")
    async def home(self) -> None: await self._adb("shell", "input", "keyevent", "3")
    async def launch_app(self, package: str) -> None: await self._adb("shell", "monkey", "-p", package, "1")
    async def open_app_root(self, package: str) -> None:
        """Enter the package through its dynamically resolved MAIN/LAUNCHER root."""
        resolved = (await self._adb("shell", "cmd", "package", "resolve-activity", "--brief", package)).decode().strip().splitlines()
        component = next((line.strip() for line in reversed(resolved) if "/" in line), None)
        if not component:
            raise MutationRejected(f"no launchable MAIN/LAUNCHER component for {package}")
        # Some Android builds expose the task flags only through ``-f``.
        # NEW_TASK | CLEAR_TOP | SINGLE_TOP is 0x34000000.
        await self._adb("shell", "am", "start", "-f", "0x34000000", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", "-n", component)

    async def reset_app_task(self, package: str) -> None:
        """Rebuild a launcher task without clearing application data or force-stopping."""
        resolved = (await self._adb("shell", "cmd", "package", "resolve-activity", "--brief", package)).decode().strip().splitlines()
        component = next((line.strip() for line in reversed(resolved) if "/" in line), None)
        if not component:
            raise MutationRejected(f"no launchable MAIN/LAUNCHER component for {package}")
        # NEW_TASK | CLEAR_TASK. This resets only the Android activity task;
        # it intentionally never uses pm clear or force-stop.
        await self._adb("shell", "am", "start", "-f", "0x10008000", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", "-n", component)

    async def restart_app_process(self, package: str) -> None:
        """Cold-start a process; caller must have explicit lifecycle approval."""
        await self._adb("shell", "am", "force-stop", package)
        await self.open_app_root(package)
    async def screenshot(self) -> bytes: return await self._adb("exec-out", "screencap", "-p", binary=True)

    def _require_target(self, target: str) -> RawDeviceElement:
        if target not in self._refs: raise MutationRejected(f"unknown or stale Portal target: {target}")
        # refs are rebuilt per observe; lookup the current target by stored node id
        # Bounds are retained in the synthetic map below.
        return self._nodes[target]

    @property
    def _nodes(self) -> dict[str, RawDeviceElement]:
        # populated after observation without making backend identifiers public
        return getattr(self, "__nodes", {})

    @_nodes.setter
    def _nodes(self, value: dict[str, RawDeviceElement]) -> None: setattr(self, "__nodes", value)

    async def _content_query(self, path: str) -> dict[str, Any]:
        output = await self._adb("shell", "content", "query", "--uri", f"{self.authority}/{path}")
        decoded = output.decode(errors="replace")
        if "Could not find provider" in decoded or "Error while accessing provider" in decoded:
            raise PortalAdbError("Mobilerun Portal ContentProvider is unavailable; install Portal and enable Accessibility")
        return self._parse_content(decoded)

    async def _content_insert(self, path: str, *bindings: str) -> None:
        args = ["shell", "content", "insert", "--uri", f"{self.authority}/{path}"]
        for binding in bindings: args.extend(["--bind", binding])
        await self._adb(*args)

    async def _adb(self, *args: str, binary: bool = False) -> bytes:
        process = await asyncio.create_subprocess_exec(self.adb_command, "-s", self.serial, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode: raise PortalAdbError(stderr.decode(errors="replace").strip() or "ADB command failed")
        if not stdout and stderr:
            raise PortalAdbError(stderr.decode(errors="replace").strip())
        return stdout if binary else stdout

    @staticmethod
    def _parse_content(output: str) -> dict[str, Any]:
        match = re.search(r'(?:result|data)=((?:\{.*\}|\[.*\]))', output, re.DOTALL)
        raw = match.group(1) if match else output.strip()
        try: return json.loads(raw)
        except json.JSONDecodeError as error: raise PortalAdbError(f"invalid Portal ContentProvider response: {output[:200]}") from error

    @staticmethod
    def _unwrap(value: Any) -> dict[str, Any]:
        while isinstance(value, dict) and any(key in value for key in ("result", "data")):
            value = value.get("result", value.get("data"))
            if isinstance(value, str): value = json.loads(value)
        return value if isinstance(value, dict) else {"a11y_tree": value}

    def _walk(self, nodes: list[Any], parent: str | None, depth: int, prefix: str = "p"):
        for index, node in enumerate(nodes):
            if not isinstance(node, dict): continue
            node_id = str(node.get("id") or node.get("nodeId") or f"{prefix}{index}")
            # Portal does not expose AccessibilityNodeInfo IDs. A structural
            # path is a backend target only for this observation; registry
            # refs remain the public identity and invalidate on next snapshot.
            node["__jev_id"] = node_id
            yield node, parent, depth
            children = node.get("children") or []
            yield from self._walk(children, node_id, depth + 1, f"{node_id}.")

    def _element(self, node: dict[str, Any], parent: str | None, depth: int) -> RawDeviceElement | None:
        box = node.get("bounds") or node.get("boundsInScreen") or {}
        if isinstance(box, list) and len(box) == 4: box = dict(zip(("left", "top", "right", "bottom"), box))
        left, top = int(box.get("left", box.get("x", 0))), int(box.get("top", box.get("y", 0)))
        right, bottom = int(box.get("right", left + box.get("width", 0))), int(box.get("bottom", top + box.get("height", 0)))
        return RawDeviceElement(node_id=str(node.get("id") or node.get("nodeId") or node.get("__jev_id") or "") or None,
            text=node.get("text") or None, content_description=node.get("contentDescription") or node.get("content_description") or None,
            resource_id=node.get("resourceId") or node.get("resource_id") or None, class_name=node.get("className") or node.get("class_name") or None,
            package=node.get("packageName") or node.get("package") or None, bounds=Bounds(x=left, y=top, width=max(0, right-left), height=max(0, bottom-top)),
            clickable=bool(node.get("clickable") or node.get("isClickable")), editable=bool(node.get("editable") or node.get("isEditable")), enabled=bool(node.get("enabled", node.get("isEnabled", True))),
            selected=bool(node.get("selected") or node.get("isSelected")), visible=bool(node.get("visible", node.get("isVisibleToUser", True))), scrollable=bool(node.get("scrollable") or node.get("isScrollable")),
            focused=bool(node.get("focused") or node.get("isFocused")), checkable=bool(node.get("checkable") or node.get("isCheckable")), checked=bool(node.get("checked") or node.get("isChecked")),
            hint=node.get("hint") or None, window_id=node.get("windowId") or node.get("window_id"), parent_id=parent,
            child_ids=[str(item.get("id") or item.get("nodeId") or f"{node.get('__jev_id')}.{index}")
                       for index, item in enumerate(node.get("children", [])) if isinstance(item, dict)], depth=depth)

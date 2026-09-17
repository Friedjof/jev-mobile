"""Adapter for the first-party Jev Mobile Bridge over USB-only ADB forwarding."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Iterable

import httpx

from ..state.models import Bounds, RawDeviceElement, RawDeviceState
from .base import MutationOutcomeUnknown, MutationReceipt, MutationRejected
from .mobile_mcp import MobileMcpError


class AccessibilityAdbAdapter:
    """Use semantic Accessibility actions first and ADB only for device fallbacks.

    The bridge itself listens exclusively on Android loopback. This adapter
    opens an ADB USB forward to it; no phone LAN port or cloud endpoint is used.
    """

    def __init__(self, serial: str, *, adb_command: str = "adb", port: int = 8765,
                 bridge_token: str | None = None) -> None:
        self.serial, self.adb_command, self.port = serial, adb_command, port
        self.base_url = f"http://127.0.0.1:{port}"
        self.client: httpx.AsyncClient | None = None
        self._scroll_target: str | None = None
        self._bridge_token = bridge_token

    async def __aenter__(self) -> "AccessibilityAdbAdapter":
        await self._adb("forward", f"tcp:{self.port}", f"tcp:{self.port}")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=5)
        try:
            response = await self.client.get("/health")
            response.raise_for_status()
            if not self._bridge_token:
                self._bridge_token = await self._debug_bridge_token()
        except httpx.HTTPError as error:
            await self.client.aclose()
            self.client = None
            raise MobileMcpError("Jev Mobile Bridge is unreachable; enable its Accessibility Service first") from error
        if not self._bridge_token:
            await self.client.aclose()
            self.client = None
            raise MobileMcpError(
                "Bridge authentication token is unavailable. Use the debug APK or set JEV_MOBILE_BRIDGE_TOKEN."
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self.client:
            await self.client.aclose()
        await self._adb("forward", "--remove", f"tcp:{self.port}", check=False)

    async def observe(self) -> RawDeviceState:
        payload = await self._request("GET", "/state")
        elements: list[RawDeviceElement] = []
        self._scroll_target = None
        for node, parent_id, depth in self._flatten(payload["tree"], None, 0):
            bounds = node.get("bounds") or {}
            left, top, right, bottom = (int(bounds.get(key, 0)) for key in ("left", "top", "right", "bottom"))
            actions = [str(item) for item in node.get("actions", [])]
            element = RawDeviceElement(
                node_id=node.get("id"), text=node.get("text") or None,
                content_description=node.get("content_description") or None,
                resource_id=node.get("resource_id") or None, class_name=node.get("class_name") or None,
                package=node.get("package") or None,
                bounds=Bounds(x=left, y=top, width=max(0, right - left), height=max(0, bottom - top)),
                clickable=bool(node.get("clickable")) or "CLICK" in actions,
                editable=bool(node.get("editable")) or "SET_TEXT" in actions,
                enabled=bool(node.get("enabled", True)), selected=bool(node.get("selected", False)),
                visible=bool(node.get("visible", True)), scrollable=bool(node.get("scrollable")),
                focused=bool(node.get("focused")), focusable=bool(node.get("focusable")),
                checkable=bool(node.get("checkable")), checked=bool(node.get("checked")),
                password=bool(node.get("password")), hint=node.get("hint") or None,
                state_description=node.get("state_description") or None, available_actions=actions,
                window_id=node.get("window_id"), parent_id=parent_id,
                child_ids=[child.get("id") for child in node.get("children", []) if child.get("id")], depth=depth,
            )
            if self._scroll_target is None and element.scrollable and "SCROLL_FORWARD" in actions:
                self._scroll_target = element.node_id
            elements.append(element)
        return RawDeviceState(
            package=payload.get("package") or None, elements=elements,
            observed_at_monotonic=time.monotonic(), keyboard_visible=bool(payload.get("keyboard_visible")),
            active_window_id=payload.get("active_window_id"), snapshot_id=str(payload.get("sequence", "")) or None,
        )

    async def tap(self, target: str) -> MutationReceipt:
        return await self._action("click", target)

    async def long_press(self, target: str) -> None:
        raise MobileMcpError("Long press is not exposed by the bridge MVP")

    async def type_text(self, text: str, target: str | None = None) -> MutationReceipt:
        if not target:
            raise MobileMcpError("Accessibility text entry requires a verified editable target")
        # ACTION_SET_TEXT is valid on an already-focused field. Focusing first
        # is actively harmful on several Android widgets because ACTION_FOCUS
        # then returns false and prevents the write from being attempted.
        try:
            return await self._action("set_text", target, text=text)
        except MutationRejected as initial_error:
            try:
                await self._action("focus", target)
                return await self._action("set_text", target, text=text)
            except MutationRejected:
                raise initial_error

    async def type_text_ime(self, text: str) -> MutationReceipt:
        """Commit Unicode through the opt-in Jev companion IME.

        The companion IME must be explicitly enabled and selected by the
        device owner. This method never changes global keyboard settings.
        """
        request_id = uuid.uuid4().hex
        try:
            result = await self._request("POST", "/input", {"request_id": request_id, "text": text})
        except httpx.TimeoutException as error:
            raise MutationOutcomeUnknown("Bridge IME receipt lost; input may have run") from error
        except MobileMcpError as error:
            raise MutationRejected(str(error)) from error
        if not result.get("accepted"):
            raise MutationRejected(result.get("error", "IME input was rejected"))
        return MutationReceipt(request_id=result.get("request_id", request_id), status="accepted",
                               sequence_before=result.get("sequence_before"))

    async def swipe(self, direction: str) -> None:
        if not self._scroll_target:
            raise MobileMcpError("No verified scrollable Accessibility node is available")
        await self._action("scroll_forward" if direction == "down" else "scroll_backward", self._scroll_target)

    async def back(self) -> None:
        await self._adb("shell", "input", "keyevent", "4")

    async def launch_app(self, package: str) -> None:
        await self._adb("shell", "monkey", "-p", package, "1")

    async def screenshot(self) -> bytes:
        return await self._adb("exec-out", "screencap", "-p", binary=True)

    async def _action(self, action_type: str, target: str, *, text: str | None = None) -> MutationReceipt:
        request_id = uuid.uuid4().hex
        body: dict[str, str] = {"request_id": request_id, "type": action_type, "target": target}
        if text is not None:
            body["text"] = text
        try:
            result = await self._request("POST", "/action", body)
        except httpx.TimeoutException as error:
            raise MutationOutcomeUnknown(f"Bridge receipt lost for {action_type}; mutation may have run") from error
        except MobileMcpError as error:
            raise MutationRejected(str(error)) from error
        if not result.get("accepted"):
            raise MutationRejected(result.get("error", "Accessibility action was rejected"))
        return MutationReceipt(request_id=result.get("request_id", request_id), status="accepted",
                               sequence_before=result.get("sequence_before"))

    async def _request(self, method: str, path: str, body: dict[str, str] | None = None) -> dict:
        if not self.client:
            raise MobileMcpError("Bridge adapter is not connected")
        try:
            response = await self.client.request(
                method, path, json=body, headers={"X-Jev-Mobile-Token": self._bridge_token or ""},
            )
        except httpx.TimeoutException:
            raise
        except httpx.HTTPError as error:
            raise MobileMcpError(f"Bridge request failed ({type(error).__name__})") from error
        if response.is_error:
            try:
                detail = response.json().get("error")
            except ValueError:
                detail = None
            raise MobileMcpError(detail or f"Bridge request failed (HTTP {response.status_code})")
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise MobileMcpError(payload.get("error", "Bridge request failed"))
        return payload

    async def _debug_bridge_token(self) -> str | None:
        """Read the random token from the debuggable PoC APK via USB-only ADB.

        Release builds must provide ``JEV_MOBILE_BRIDGE_TOKEN`` explicitly.
        The token never enters a trace or normal CLI output.
        """
        try:
            output = await self._adb(
                "exec-out", "run-as", "io.jev.mobile.bridge", "cat",
                "shared_prefs/jev_mobile_bridge.xml",
            )
        except MobileMcpError:
            return None
        match = re.search(
            r'<string name="token">([0-9a-f]{32,})</string>', output.decode(errors="replace"),
        )
        return match.group(1) if match else None

    async def _adb(self, *args: str, binary: bool = False, check: bool = True) -> bytes:
        process = await asyncio.create_subprocess_exec(
            self.adb_command, "-s", self.serial, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if check and process.returncode:
            raise MobileMcpError(stderr.decode(errors="replace").strip() or "ADB command failed")
        return stdout if binary else stdout

    @staticmethod
    def _flatten(node: dict, parent_id: str | None, depth: int) -> Iterable[tuple[dict, str | None, int]]:
        yield node, parent_id, depth
        for child in node.get("children", []):
            yield from AccessibilityAdbAdapter._flatten(child, node.get("id"), depth + 1)

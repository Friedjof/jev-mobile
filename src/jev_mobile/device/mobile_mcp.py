"""Adapter for the current Mobile Next ``mobile-mcp`` stdio server."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ..state.models import Bounds, RawDeviceElement, RawDeviceState


class MobileMcpError(RuntimeError):
    pass


class MobileMcpAdapter:
    """Map Mobile Next tools to the backend-neutral ``DeviceAdapter`` contract."""

    def __init__(self, command: tuple[str, ...], serial: str | None = None, connect: bool = True) -> None:
        if not command:
            raise ValueError("MOBILE_MCP_COMMAND_JSON is required")
        self.command, self.serial, self.connect_on_enter = command, serial, connect
        self._stdio_cm = self._session_cm = None
        self._errlog = None
        self.session: ClientSession | None = None
        self._refs: dict[str, str] = {}

    async def __aenter__(self) -> "MobileMcpAdapter":
        self._errlog = open(os.devnull, "w", encoding="utf-8")
        self._stdio_cm = stdio_client(StdioServerParameters(command=self.command[0], args=list(self.command[1:])), errlog=self._errlog)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self.session = await self._session_cm.__aenter__()
        await self.session.initialize()
        devices = await self.list_devices()
        if self.connect_on_enter:
            selected = self.serial or (devices[0]["id"] if len(devices) == 1 else None)
            if not selected:
                raise MobileMcpError("Specify MOBILE_DEVICE_SERIAL when multiple devices are online")
            self.serial = selected
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._session_cm:
            await self._session_cm.__aexit__(*exc_info)
        if self._stdio_cm:
            await self._stdio_cm.__aexit__(*exc_info)
        if self._errlog:
            self._errlog.close()
        self.session = None

    async def _tool(self, name: str, arguments: dict[str, Any]) -> tuple[list[str], list[Any]]:
        if self.session is None:
            raise MobileMcpError("MCP session is not connected")
        result = await self.session.call_tool(name, arguments)
        texts = [content.text for content in result.content if hasattr(content, "text")]
        if result.is_error:
            raise MobileMcpError(texts[0] if texts else f"{name} failed")
        return texts, result.content

    @staticmethod
    def _json(text: str) -> Any:
        starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not starts:
            raise MobileMcpError(f"Mobile MCP returned no JSON: {text[:160]}")
        return json.loads(text[min(starts):])

    async def list_devices(self) -> list[dict[str, Any]]:
        texts, _ = await self._tool("mobile_list_available_devices", {})
        return self._json(texts[0]).get("devices", [])

    async def observe(self) -> RawDeviceState:
        if not self.serial:
            raise MobileMcpError("No selected device")
        texts, _ = await self._tool("mobile_list_elements_on_screen", {"device": self.serial, "format": "json"})
        items = self._json(texts[0])
        if not isinstance(items, list):
            raise MobileMcpError("Unexpected element-list response")
        self._refs = {}
        packages: dict[str, int] = {}
        raw_elements: list[RawDeviceElement] = []
        for index, item in enumerate(items):
            coordinates = item.get("coordinates") or {}
            identifier = item.get("identifier")
            package = identifier.rsplit(":", 1)[0] if isinstance(identifier, str) and ":" in identifier else None
            if package:
                packages[package] = packages.get(package, 0) + 1
            raw_elements.append(RawDeviceElement(
                text=item.get("text") or None, content_description=item.get("label") or None,
                resource_id=identifier, class_name=item.get("type"), package=package,
                bounds=Bounds.model_validate(coordinates) if all(key in coordinates for key in ("x", "y", "width", "height")) else None,
                clickable=bool((item.get("text") or item.get("label")) and coordinates),
                editable="EditText" in (item.get("type") or ""), enabled=item.get("enabled", True),
                selected=item.get("selected", False), visible=item.get("visible", True),
                scrollable="ScrollView" in (item.get("type") or "") or "RecyclerView" in (item.get("type") or ""),
                focused=item.get("focused", False), depth=item.get("depth", 0)))
            self._refs[f"e{index + 1}"] = item.get("ref", "")
        app_packages = {name: count for name, count in packages.items() if name not in {"android", "com.android.systemui"}}
        return RawDeviceState(package=max(app_packages or packages, key=(app_packages or packages).get) if packages else None,
                              elements=raw_elements, observed_at_monotonic=time.monotonic())

    async def tap(self, target: str) -> None:
        ref = self._refs.get(target)
        if not ref or not self.serial:
            raise MobileMcpError(f"Unknown or stale semantic element: {target}")
        await self._tool("mobile_click_on_screen_at_coordinates", {"device": self.serial, "ref": ref})

    async def long_press(self, target: str) -> None:
        raise MobileMcpError("Long press requires coordinates and is not a core candidate yet")

    async def type_text(self, text: str, target: str | None = None) -> None:
        if target:
            await self.tap(target)
        await self._tool("mobile_type_keys", {"device": self.serial, "text": text, "submit": False})

    async def swipe(self, direction: str) -> None:
        await self._tool("mobile_swipe_on_screen", {"device": self.serial, "direction": "up" if direction == "down" else "down"})

    async def back(self) -> None:
        await self._tool("mobile_press_button", {"device": self.serial, "button": "BACK"})

    async def launch_app(self, package: str) -> None:
        await self._tool("mobile_launch_app", {"device": self.serial, "packageName": package})

    async def screenshot(self) -> bytes:
        _, content = await self._tool("mobile_take_screenshot", {"device": self.serial, "maxSize": 1024})
        for item in content:
            data = getattr(item, "data", None)
            if data:
                return base64.b64decode(data) if isinstance(data, str) else data
        raise MobileMcpError("Mobile MCP screenshot response did not contain image bytes")

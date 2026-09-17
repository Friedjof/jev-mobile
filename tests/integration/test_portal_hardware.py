"""Opt-in USB smoke test; run with JEV_MOBILE_HARDWARE=1 and a real serial."""

import os

import pytest

from jev_mobile.device.portal_adb import PortalAdbDeviceAdapter


@pytest.mark.skipif(os.getenv("JEV_MOBILE_HARDWARE") != "1", reason="physical Android test is opt-in")
async def test_portal_usb_smoke() -> None:
    serial = os.environ["MOBILE_DEVICE_SERIAL"]
    async with PortalAdbDeviceAdapter(serial) as device:
        before = await device.observe()
        image = await device.screenshot()
        await device.home()
        await device.launch_app("com.mobilerun.portal")
        after = await device.observe()
        await device.swipe("down")
        assert before.elements
        assert image.startswith(b"\x89PNG")
        assert after.package == "com.mobilerun.portal"

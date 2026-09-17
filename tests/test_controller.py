from collections import deque
from jev_mobile.config import Settings
from jev_mobile.controller.loop import MobileController
from jev_mobile.providers.mock import MockProvider
from jev_mobile.state.models import Bounds, RawDeviceElement, RawDeviceState
from jev_mobile.tracing.trace import TraceWriter


class ScriptedDevice:
    def __init__(self) -> None:
        self.states = deque([
            RawDeviceState(package="com.android.settings", activity="Settings", elements=[RawDeviceElement(text="Network & internet", clickable=True, bounds=Bounds(x=1, y=1, width=10, height=10))]),
        ])
    async def observe(self): return self.states[0]
    async def tap(self, target): pass
    async def long_press(self, target): pass
    async def type_text(self, text, target=None): pass
    async def swipe(self, direction): pass
    async def back(self): pass
    async def launch_app(self, package): pass
    async def screenshot(self): return b""


async def test_low_confidence_becomes_checkpoint(tmp_path) -> None:
    settings = Settings((), None, tmp_path, .01, .05, .8)
    controller = MobileController(ScriptedDevice(), MockProvider(["A1"]), settings, TraceWriter(tmp_path))
    result = await controller.run("Open Settings")
    # The scripted UI never changes after an action, so it must not run forever.
    assert result.status.value == "escalated"

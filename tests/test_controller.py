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


class CrowdedDevice(ScriptedDevice):
    def __init__(self) -> None:
        self.taps = []
        self.states = deque([RawDeviceState(
            package="com.example", activity="Example",
            elements=[RawDeviceElement(text=f"Item {index}", clickable=True,
                                       bounds=Bounds(x=1, y=100 + index * 10, width=10, height=10))
                      for index in range(15)],
        )])

    async def tap(self, target):
        self.taps.append(target)


async def test_low_confidence_becomes_checkpoint(tmp_path) -> None:
    settings = Settings((), None, tmp_path, .01, .05, .8)
    controller = MobileController(ScriptedDevice(), MockProvider(["A1"]), settings, TraceWriter(tmp_path))
    result = await controller.run("Open Settings")
    # The scripted UI never changes after an action, so it must not run forever.
    assert result.status.value == "escalated"


async def test_more_actions_advances_without_touching_device(tmp_path) -> None:
    events = []
    settings = Settings((), None, tmp_path, .01, .2, .8, action_page_size=10, max_action_retries=0)
    device = CrowdedDevice()
    controller = MobileController(
        device, MockProvider(["MORE", "A11"]), settings, TraceWriter(tmp_path),
        lambda event, payload: events.append(event),
    )
    result = await controller.run("Browse items")
    assert result.status.value == "escalated"
    assert "page_advanced" in events
    assert device.taps == ["e11"]


async def test_opening_notes_does_not_complete_content_creation_goal(tmp_path) -> None:
    class NotesDevice(ScriptedDevice):
        def __init__(self) -> None:
            self.taps = []
            self.states = deque([
                RawDeviceState(package="com.motorola.launcher3", activity="Home", elements=[
                    RawDeviceElement(text="Keep Notes", clickable=True, bounds=Bounds(x=1, y=100, width=10, height=10)),
                ]),
            ])

        async def tap(self, target):
            self.taps.append(target)

    settings = Settings((), None, tmp_path, .01, .2, .8, max_action_retries=0)
    device = NotesDevice()
    result = await MobileController(device, MockProvider(["A1"]), settings, TraceWriter(tmp_path)).run(
        "Create a new note for shopping: Eggs"
    )
    assert result.status.value == "escalated"
    assert device.taps == ["e1"]

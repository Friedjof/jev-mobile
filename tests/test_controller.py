from collections import deque
from jev_mobile.config import Settings
from jev_mobile.controller.loop import MobileController
from jev_mobile.actions.models import Decision
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


async def test_crowded_screen_uses_semantic_group_search_without_touching_device(tmp_path) -> None:
    events = []
    settings = Settings((), None, tmp_path, .01, .2, .8, action_page_size=10, max_action_retries=0)
    device = CrowdedDevice()
    controller = MobileController(
        device, MockProvider(["GROUP:primary_actions", "MORE", "A11"]), settings, TraceWriter(tmp_path),
        lambda event, payload: events.append(event),
    )
    result = await controller.run("Browse items")
    assert result.status.value == "escalated"
    assert "candidates" in events
    # An unchanged mutation is re-decided rather than replayed. The scripted
    # provider may explore other targets, but must not tap e11 twice.
    assert device.taps[0] == "e11"
    assert device.taps.count("e11") == 1


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


async def test_second_fast_decision_receives_bounded_observed_history(tmp_path) -> None:
    class TransitionDevice(ScriptedDevice):
        def __init__(self) -> None:
            self.index = 0
            self.states = [
                RawDeviceState(package="com.example", elements=[
                    RawDeviceElement(text="Open editor", clickable=True, bounds=Bounds(x=1, y=100, width=10, height=10)),
                ]),
                RawDeviceState(package="com.example", elements=[
                    RawDeviceElement(node_id="body", editable=True, focused=True,
                                     resource_id="example:id/message_text", class_name="android.widget.EditText"),
                ]),
            ]

        async def observe(self):
            return self.states[self.index]

        async def tap(self, target):
            self.index = 1

    class CapturingProvider:
        name = "capturing"

        def __init__(self) -> None:
            self.contexts = []

        async def decide(self, goal, state, actions):
            self.contexts.append(state.recent_context)
            action_id = "A1" if len(self.contexts) == 1 else "ESCALATE"
            return Decision(action_id=action_id, confidence=.99, probabilities={action_id: .99})

    provider = CapturingProvider()
    settings = Settings((), None, tmp_path, .01, .2, .8, max_action_retries=0)
    result = await MobileController(TransitionDevice(), provider, settings, TraceWriter(tmp_path)).run("Open editor")

    assert result.status.value == "escalated"
    assert provider.contexts[0] == []
    assert provider.contexts[1][0]["action"] == 'Tap "Open editor"'
    assert provider.contexts[1][0]["outcome"] == "progress"


async def test_low_confidence_jev_explores_a_group_before_escalating(tmp_path) -> None:
    class WideDevice(CrowdedDevice):
        pass

    class LowThenHighProvider:
        name = "jev-test"

        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, goal, state, actions):
            self.calls += 1
            if self.calls == 1:
                return Decision(
                    action_id="GROUP:primary_actions", confidence=.40,
                    probabilities={"GROUP:primary_actions": .40, "ESCALATE": .35},
                )
            return Decision(action_id="A1", confidence=.99, probabilities={"A1": .99})

    provider = LowThenHighProvider()
    settings = Settings(
        (), None, tmp_path, .01, .2, .8, max_action_retries=0,
        max_safe_exploration_branches=2,
    )
    result = await MobileController(WideDevice(), provider, settings, TraceWriter(tmp_path)).run("Browse items")

    assert result.status.value == "escalated"
    assert provider.calls >= 2

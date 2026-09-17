from jev_mobile.actions.builder import build_candidates
from jev_mobile.actions.models import ActionKind
from jev_mobile.state.models import Bounds, RawDeviceElement, RawDeviceState
from jev_mobile.state.normalize import normalize


def test_safe_dialog_exposes_only_dismiss_and_escalate() -> None:
    state = normalize(RawDeviceState(elements=[
        RawDeviceElement(class_name="android.app.Dialog", text="Update available"),
        RawDeviceElement(text="Not now", clickable=True, bounds=Bounds(x=1, y=1, width=10, height=10)),
    ]))
    actions = build_candidates(state, "Open Display settings")
    assert state.dialog is not None
    assert state.dialog.kind.value == "safe_dismissible"
    assert [action.kind for action in actions] == [ActionKind.TAP, ActionKind.BACK, ActionKind.ESCALATE]


def test_permission_dialog_never_exposes_accept_action() -> None:
    state = normalize(RawDeviceState(elements=[
        RawDeviceElement(class_name="android.app.Dialog", package="com.android.permissioncontroller", text="Allow access?"),
        RawDeviceElement(text="Allow", clickable=True, bounds=Bounds(x=1, y=1, width=10, height=10)),
    ]))
    actions = build_candidates(state, "Open Display settings")
    assert state.dialog is not None
    assert state.dialog.kind.value == "permission"
    assert [action.kind for action in actions] == [ActionKind.BACK, ActionKind.ESCALATE]


def test_unknown_dialog_can_only_be_dismissed_with_back() -> None:
    state = normalize(RawDeviceState(elements=[
        RawDeviceElement(class_name="android.app.Dialog", text="Something happened"),
        RawDeviceElement(text="Continue", clickable=True, bounds=Bounds(x=1, y=1, width=10, height=10)),
    ]))
    actions = build_candidates(state, "Open Display settings")
    assert state.dialog is not None
    assert state.dialog.kind.value == "unknown"
    assert [action.kind for action in actions] == [ActionKind.BACK, ActionKind.ESCALATE]


def test_android_panel_resources_are_detected_as_popup() -> None:
    state = normalize(RawDeviceState(elements=[
        RawDeviceElement(resource_id="android:id/parentPanel"),
        RawDeviceElement(text="Keep browsing", clickable=True, bounds=Bounds(x=1, y=1, width=10, height=10)),
    ]))
    actions = build_candidates(state, "Open Display settings")
    assert state.dialog is not None
    assert [action.kind for action in actions] == [ActionKind.BACK, ActionKind.ESCALATE]

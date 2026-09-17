from jev_mobile.actions.builder import build_candidates
from jev_mobile.actions.models import ActionKind
from jev_mobile.state.models import SemanticElement, SemanticState


def state() -> SemanticState:
    return SemanticState(app="com.android.settings", screen_hint="Settings", fingerprint="x", elements=[
        SemanticElement(id="e1", role="button", label="Network & internet", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0),
    ])


def test_builder_only_exposes_known_actions() -> None:
    actions = build_candidates(state(), "Open Network & internet settings", can_complete=True)
    assert actions[0].target_element_id == "e1"
    assert any(item.kind == ActionKind.ESCALATE for item in actions)
    assert any(item.kind == ActionKind.DONE for item in actions)

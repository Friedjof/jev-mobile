from jev_mobile.actions.builder import build_action_page, build_candidates, known_note_text_present
from jev_mobile.actions.models import ActionKind, ActionRisk
from jev_mobile.state.models import SemanticElement, SemanticState


def state() -> SemanticState:
    return SemanticState(app="com.android.settings", screen_hint="Settings", fingerprint="x", elements=[
        SemanticElement(id="e1", role="button", label="Network & internet", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0),
    ])


def test_builder_only_exposes_known_actions() -> None:
    actions = build_candidates(state(), "Open Network & internet settings", can_complete=True)
    assert actions[0].kind == ActionKind.DONE
    assert any(item.kind == ActionKind.ESCALATE for item in actions)
    assert any(item.kind == ActionKind.DONE for item in actions)


def test_action_page_has_more_actions_for_crowded_screen() -> None:
    crowded = state().model_copy(update={"elements": [
        SemanticElement(id=f"e{i}", role="button", label=f"Item {i}", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=i)
        for i in range(15)
    ]})
    page = build_action_page(crowded, "Browse settings", page_size=10, max_pages=3)
    assert page.total_pages == 2
    assert page.total_device_actions == 15
    assert any(item.kind == ActionKind.MORE_ACTIONS for item in page.actions)


def test_note_goal_prioritizes_visible_notes_app_over_launcher_noise() -> None:
    launcher = state().model_copy(update={"app": "com.motorola.launcher3", "elements": [
        SemanticElement(id="e1", role="button", label="Weather", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0),
        SemanticElement(id="e2", role="button", label="Keep Notes", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=1),
    ]})
    actions = build_candidates(launcher, "Create a new note for shopping: Eggs, bread")
    assert actions[0].label.startswith('Open "Keep Notes"')
    assert actions[0].kind == ActionKind.TAP
    assert actions[0].goal_directed is True


def test_checklist_goal_prioritizes_visible_notes_app_over_launcher_noise() -> None:
    launcher = state().model_copy(update={"app": "com.motorola.launcher3", "elements": [
        SemanticElement(id="e1", role="button", label="Apps list", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0),
        SemanticElement(id="e2", role="button", label="Keep Notes", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=1),
    ]})
    actions = build_candidates(launcher, "Create a shopping checklist: Eggs, bread")
    assert actions[0].target_element_id == "e2"
    assert actions[0].goal_directed is True


def test_note_goal_prefers_create_note_inside_keep_over_sort_notes() -> None:
    keep = state().model_copy(update={"app": "com.google.android.keep", "elements": [
        SemanticElement(id="e1", role="button", label="Create a note", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0),
        SemanticElement(id="e2", role="button", label="Sort notes", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=1),
    ]})
    actions = build_candidates(keep, "Create a new note for shopping: Eggs, bread")
    assert actions[0].label == 'Tap "Create a note"'
    assert actions[0].risk == ActionRisk.REVERSIBLE


def test_note_goal_does_not_type_into_search_before_creating_note() -> None:
    keep = state().model_copy(update={"app": "com.google.android.keep", "elements": [
        SemanticElement(id="e1", role="text_field", label=None, clickable=True, editable=True,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0),
        SemanticElement(id="e2", role="button", label="Create a note", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=1),
    ]})
    actions = build_candidates(keep, "Create a new note for shopping: Eggs, bread")
    assert actions[0].kind == ActionKind.TAP
    assert actions[0].target_element_id == "e2"


def test_explicit_note_text_becomes_reversible_text_input() -> None:
    editor = state().model_copy(update={"elements": [
        SemanticElement(id="e1", role="text_field", label=None, clickable=True, editable=True,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0),
    ]})
    actions = build_candidates(editor, "Create a new note for shopping: Eggs, bread")
    assert actions[0].kind == ActionKind.TYPE_TEXT
    assert actions[0].risk == ActionRisk.REVERSIBLE
    assert actions[0].text == "Eggs, bread"


def test_known_note_text_is_verified_only_when_visible_in_keep() -> None:
    saved = state().model_copy(update={"app": "com.google.android.keep", "elements": [
        SemanticElement(id="e1", role="text_field", label="Eggs, bread", clickable=True, editable=True,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0),
    ]})
    assert known_note_text_present(saved, "Create a new note for shopping: Eggs, bread")
    assert not known_note_text_present(saved, "Create a new note for shopping: Eggs, milk")

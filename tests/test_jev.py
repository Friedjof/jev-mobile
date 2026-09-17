import pytest

from jev_mobile.providers.jev import build_jev_state, probability_margin
from jev_mobile.state.models import SemanticElement, SemanticState


def test_jev_payload_is_compact_and_margin_is_top_two_gap() -> None:
    state = SemanticState(
        app="com.android.settings",
        screen_hint="Settings",
        fingerprint="state",
        elements=[
            SemanticElement(
                id="e1", role="button", label="Network & internet", clickable=True,
                editable=False, enabled=True, selected=False, visible=True,
                scrollable=False, depth=0, raw_index=0,
            )
        ],
    )
    payload = build_jev_state("Open Network & internet", state)
    element = payload["screen"]["elements"][0]
    assert element["id"] == "e1"
    assert element["text"] == "Network & internet"
    assert element["editable"] is False
    assert probability_margin({"A1": 0.51, "A2": 0.47, "A3": 0.02}) == pytest.approx(0.04)


def test_jev_payload_includes_text_field_and_bounded_recent_context() -> None:
    state = SemanticState(
        app="com.example.notes", screen_hint="Editor", fingerprint="editor", recent_context=[
            {"action": "Open notes", "outcome": "Notes list opened"},
            {"action": "Create note", "outcome": "Editor opened"},
        ], elements=[
            SemanticElement(
                id="e57", role="text_field", field_name="Note body", field_role="body", value=None,
                resource_id="com.example:id/edit_note_text", focused=True, multiline=True,
                clickable=True, editable=True, enabled=True, selected=False, visible=True,
                scrollable=False, depth=0, raw_index=0,
            )
        ],
    )
    payload = build_jev_state("Create a note", state)
    field = payload["screen"]["elements"][0]

    assert field["field_name"] == "Note body"
    assert field["field_role"] == "body"
    assert field["focused"] is True
    assert field["resource_id"] == "com.example:id/edit_note_text"
    assert payload["recent_context"] == state.recent_context


def test_jev_payload_preserves_progress_after_many_steps() -> None:
    history = [
        {"action": f"Step {index}", "outcome": "progress", "target": f"Control {index}"}
        for index in range(14)
    ]
    state = SemanticState(
        app="com.example", fingerprint="many", recent_context=history,
        agent_context={"current_subgoal": "Find save", "requirements": {"completed": ["body"]}},
        elements=[],
    )
    payload = build_jev_state("Save note", state)

    assert len(payload["recent_context"]) == 12
    assert payload["recent_context"][0]["action"] == "Step 2"
    assert payload["agent_context"]["current_subgoal"] == "Find save"

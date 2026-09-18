from jev_mobile.actions.builder import build_candidates
from jev_mobile.actions.models import ActionKind
from jev_mobile.controller.context import AgentContext
from jev_mobile.controller.navigator import semantic_group
from jev_mobile.state.models import DialogInfo, DialogKind, SemanticElement, SemanticState
from jev_mobile.tasks import TaskInterpretation, task_spec_from_goal
from jev_mobile.requirements import RequirementEvaluator


GOAL = "create a new note for shopping: Eier, Brot, Fisch, Frischkäse, Salat, Essig"


def _element(**values) -> SemanticElement:
    defaults = dict(id="e1", role="button", label="Add", clickable=True, editable=False,
                    enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0)
    defaults.update(values)
    return SemanticElement(**defaults)


def test_interpretation_creates_one_authoritative_checklist_spec() -> None:
    interpreted = TaskInterpretation(
        purpose="shopping", item_candidates=["Eier", "Brot", "Fisch", "Frischkäse", "Salat", "Essig"],
        content_type="CHECKLIST", title="USE_PURPOSE_AS_TITLE", task_spec=task_spec_from_goal(GOAL),
    ).to_task_spec()

    assert interpreted.content_type == "checklist"
    assert interpreted.title == "Shopping"
    assert interpreted.fields == []
    assert interpreted.items == ["Eier", "Brot", "Fisch", "Frischkäse", "Salat", "Essig"]
    assert any(item.type == "checklist_items" for item in interpreted.completion)


def test_checklist_candidates_track_one_missing_item_and_title() -> None:
    spec = TaskInterpretation(
        purpose="shopping", item_candidates=["Eier", "Brot"], content_type="CHECKLIST",
        title="USE_PURPOSE_AS_TITLE", task_spec=task_spec_from_goal(GOAL),
    ).to_task_spec()
    editor = SemanticState(app="notes", fingerprint="editor", elements=[
        _element(id="title", role="text_field", label=None, value=None, editable=True, field_role="title", field_name="Title"),
        _element(id="item", role="text_field", label=None, value=None, editable=True, field_role="body", field_name="List item"),
    ])
    title = build_candidates(editor, GOAL, task_spec=spec)
    assert title[0].kind == ActionKind.TYPE_TEXT
    assert title[0].target_element_id == "title"
    assert title[0].text == "Shopping"

    titled = editor.model_copy(update={"elements": [editor.elements[0].model_copy(update={"value": "Shopping"}), editor.elements[1]]})
    item = build_candidates(titled, GOAL, task_spec=spec)
    assert item[0].target_element_id == "item"
    assert item[0].text == "Eier"


def test_requirements_update_current_subgoal_and_system_ui_is_not_primary() -> None:
    spec = TaskInterpretation(
        purpose="shopping", item_candidates=["Eier"], content_type="CHECKLIST",
        title="USE_PURPOSE_AS_TITLE", task_spec=task_spec_from_goal(GOAL),
    ).to_task_spec()
    context = AgentContext(GOAL, spec)
    context.observe(SemanticState(app="notes", fingerprint="one", elements=[]))
    assert context.current_subgoal == "Enable checklist mode"
    assert semantic_group(_element(package="com.android.systemui", role="image", label="Wi-Fi")) == "system"


def test_checklist_mode_candidates_keep_add_and_formatting_controls_mechanical() -> None:
    spec = TaskInterpretation(
        purpose="shopping", item_candidates=["Eier"], content_type="CHECKLIST",
        title="USE_PURPOSE_AS_TITLE", task_spec=task_spec_from_goal(GOAL),
    ).to_task_spec()
    editor = SemanticState(app="editor", fingerprint="editor", elements=[
        _element(id="add", label="Add", resource_id="example:id/add_button"),
        _element(id="format", label="Show formatting controls", resource_id="example:id/format"),
    ])
    actions = build_candidates(editor, GOAL, task_spec=spec)

    assert {item.target_element_id for item in actions if item.kind == ActionKind.TAP} == {"add", "format"}
    assert "checklist" in actions[0].label.casefold()


def test_unknown_popup_exposes_only_task_relevant_checklist_option() -> None:
    spec = TaskInterpretation(
        purpose="shopping", item_candidates=["Eier"], content_type="CHECKLIST",
        title="USE_PURPOSE_AS_TITLE", task_spec=task_spec_from_goal(GOAL),
    ).to_task_spec()
    popup = SemanticState(app="notes", fingerprint="popup", dialog=DialogInfo(kind=DialogKind.UNKNOWN), elements=[
        _element(id="photo", label="Take photo"),
        _element(id="boxes", label="Checkboxes"),
    ])
    actions = build_candidates(popup, GOAL, task_spec=spec)

    assert [item.target_element_id for item in actions if item.kind == ActionKind.TAP] == ["boxes"]


def test_ordered_empty_list_items_produce_one_next_write_target() -> None:
    spec = TaskInterpretation(
        purpose="shopping", item_candidates=["Eier", "Brot"], content_type="CHECKLIST",
        title="NO_TITLE", task_spec=task_spec_from_goal(GOAL),
    ).to_task_spec()
    editor = SemanticState(app="notes", fingerprint="list", elements=[
        _element(id="item1", role="text_field", label=None, editable=True, field_role="list_item", field_name="List item 1"),
        _element(id="item2", role="text_field", label=None, editable=True, field_role="list_item", field_name="List item 2"),
    ])
    actions = build_candidates(editor, GOAL, task_spec=spec)

    writes = [action for action in actions if action.kind == ActionKind.TYPE_TEXT]
    assert [(action.target_element_id, action.text) for action in writes] == [("item1", "Eier")]


def test_filled_checklist_requests_persistence_transition() -> None:
    spec = TaskInterpretation(
        purpose="shopping", item_candidates=["Eier", "Brot"], content_type="CHECKLIST",
        title="USE_PURPOSE_AS_TITLE", task_spec=task_spec_from_goal(GOAL),
    ).to_task_spec()
    editor = SemanticState(app="notes", fingerprint="filled", elements=[
        _element(id="title", role="text_field", label=None, value="Shopping", editable=True, field_role="title"),
        _element(id="one", role="text_field", label=None, value="Eier", editable=True, field_role="list_item"),
        _element(id="two", role="text_field", label=None, value="Brot", editable=True, field_role="list_item"),
    ])
    actions = build_candidates(editor, GOAL, task_spec=spec)

    assert actions[0].kind == ActionKind.BACK
    assert "persisted" in actions[0].label


def test_title_requirement_evidence_uses_the_title_role_not_matching_content() -> None:
    spec = task_spec_from_goal("Create a Google Keep note titled Shopping with body Shopping")
    state = SemanticState(app="com.google.android.keep", fingerprint="editor", raw_snapshot_id="s5", elements=[
        _element(id="body", role="text_field", value="Shopping", editable=True, field_role="body"),
        _element(id="title", role="text_field", value="Shopping", editable=True, field_role="title"),
    ])

    requirements = RequirementEvaluator().evaluate(spec, state)
    title = next(requirement for requirement in requirements if requirement.kind == "title_equals")

    assert title.evidence["semantic_role"] == "title"
    assert title.evidence["observed_value"] == "Shopping"
    assert "ref" not in title.evidence

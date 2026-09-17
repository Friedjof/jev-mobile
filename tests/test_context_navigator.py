from jev_mobile.actions.models import ActionKind, CandidateAction
from jev_mobile.controller.context import AgentContext
from jev_mobile.controller.navigator import SemanticNavigator
from jev_mobile.state.models import SemanticElement, SemanticState
from jev_mobile.tasks import TaskSpec


def _state(*elements: SemanticElement, fingerprint: str = "screen") -> SemanticState:
    return SemanticState(app="com.example", fingerprint=fingerprint, elements=list(elements))


def _button(index: int) -> SemanticElement:
    return SemanticElement(
        id=f"e{index}", role="button", label=f"Action {index}", clickable=True, editable=False,
        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=index,
    )


def test_failed_action_is_retained_in_durable_context() -> None:
    state = _state(_button(1))
    context = AgentContext("Browse", TaskSpec())
    context.record_action(CandidateAction(id="A1", kind=ActionKind.TAP, label="Tap Action 1", target_element_id="e1"), state)
    context.observe(state)

    assert "tap:e1" in context.failed_paths
    assert context.recent_transitions[-1]["outcome"] == "no_effect"

    navigator = SemanticNavigator(context)
    page = navigator.build(state, "Browse", TaskSpec(), can_complete=False, return_mode=False,
                           action_page_size=10, max_action_pages=3)
    assert not any(action.target_element_id == "e1" for action in page.actions)


def test_semantic_group_pagination_reaches_later_control() -> None:
    state = _state(*[_button(index) for index in range(20)])
    context = AgentContext("Browse", TaskSpec())
    navigator = SemanticNavigator(context, flat_limit=12)

    screen = navigator.build(state, "Browse", TaskSpec(), can_complete=False, return_mode=False,
                             action_page_size=10, max_action_pages=3)
    group = next(action for action in screen.actions if action.id == "GROUP:primary_actions")
    assert navigator.local_action(group, state)
    first = navigator.build(state, "Browse", TaskSpec(), can_complete=False, return_mode=False,
                            action_page_size=10, max_action_pages=3)
    assert not any(action.target_element_id == "e15" for action in first.actions)
    more = next(action for action in first.actions if action.kind == ActionKind.MORE_ACTIONS)
    second = navigator.build(state, "Browse", TaskSpec(), can_complete=False, return_mode=False,
                             action_page_size=10, max_action_pages=3, page_index=1)

    assert more.kind == ActionKind.MORE_ACTIONS
    assert any(action.target_element_id == "e15" for action in second.actions)


def test_context_keeps_twelve_recent_semantic_transitions() -> None:
    context = AgentContext("Browse", TaskSpec())
    for index in range(14):
        before = _state(_button(index), fingerprint=f"before-{index}")
        after = _state(_button(index), fingerprint=f"after-{index}")
        action = CandidateAction(id="A1", kind=ActionKind.TAP, label=f"Tap Action {index}", target_element_id=f"e{index}")
        context.record_action(action, before)
        context.observe(after)

    compact = context.compact()
    assert len(compact["recent_transitions"]) == 12
    assert compact["recent_transitions"][-1]["outcome"] == "progress"


def test_ambiguous_text_mutation_is_recorded_before_transport_and_resolved_by_observation() -> None:
    before = _state(SemanticElement(
        id="body", role="text_field", field_name="Body", field_role="body", value=None,
        clickable=True, editable=True, enabled=True, selected=False, visible=True,
        scrollable=False, depth=0, raw_index=0,
    ))
    after = before.model_copy(update={"fingerprint": "after", "elements": [
        before.elements[0].model_copy(update={"value": "Frischkäse"}),
    ]})
    context = AgentContext("Write", TaskSpec())
    action = CandidateAction(id="A1", kind=ActionKind.TYPE_TEXT, label="Set body", target_element_id="body", text="Frischkäse")

    context.begin_action(action, before)
    context.mark_transport_outcome("unknown")
    context.observe(after)

    assert context.recent_transitions[-1]["outcome"] == "success"
    assert "type_text:body" not in context.failed_paths

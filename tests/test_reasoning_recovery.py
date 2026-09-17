from jev_mobile.agent.mobile_agent import MobileAgent
from jev_mobile.agent.grounding import EntityOwnership, InteractionContext, context_type, ownership_for
from jev_mobile.requirements.requirement import Requirement
from jev_mobile.tasks import RequirementStatus
from jev_mobile.perception import ActionCatalog, SnapshotRefRegistry
from jev_mobile.state.models import RawDeviceElement, RawDeviceState
from jev_mobile.state.normalize import normalize


def test_title_field_keeps_text_affordance_when_matching_list_text_exists() -> None:
    state = normalize(RawDeviceState(elements=[
        RawDeviceElement(node_id="old", text="Shopping", clickable=True),
        RawDeviceElement(node_id="title", hint="Title", resource_id="x:id/title", editable=True),
    ]))
    catalog = ActionCatalog.build(state, SnapshotRefRegistry())
    title = next(item for item in catalog.actions if item.semantic_role == "title")
    assert "set_text" in title.capabilities


def test_no_effect_signature_is_suppressed_only_in_same_semantic_state() -> None:
    state = normalize(RawDeviceState(elements=[RawDeviceElement(node_id="old", text="Shopping", clickable=True)]))
    registry = SnapshotRefRegistry(); catalog = ActionCatalog.build(state, registry)
    action = type("A", (), {"kind": type("K", (), {"value": "tap"})(), "text": None})()
    signature = MobileAgent._attempt_signature(action, catalog.actions[0])
    requirements = []
    actions, _ = MobileAgent._candidates(catalog, registry, requirements, type("S", (), {"app_package": None})(), "x", [{"signature": signature, "state": state.fingerprint, "mutation": "executed_no_effect"}] * 2, state.fingerprint)
    assert not any(item.kind.value == "tap" for item in actions)


def test_foreign_editor_is_not_writable_for_a_fresh_create_task() -> None:
    state = normalize(RawDeviceState(elements=[
        RawDeviceElement(node_id="title", hint="Title", text="Old note", editable=True),
        RawDeviceElement(node_id="body", hint="Note", text="Existing content", editable=True),
    ]))
    assert context_type(state) == InteractionContext.EDITOR
    assert ownership_for(state, {}) == EntityOwnership.FOREIGN
    catalog = ActionCatalog.build(state, SnapshotRefRegistry())
    requirements = [Requirement(key="note_created", kind="note_created", status=RequirementStatus.UNSATISFIED)]
    actions, _ = MobileAgent._candidates(catalog, SnapshotRefRegistry(), requirements, type("S", (), {"app_package": None})(), "x", [], state.fingerprint, InteractionContext.EDITOR, EntityOwnership.FOREIGN)
    assert not any(action.kind.value == "type_text" for action in actions)

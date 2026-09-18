from jev_mobile.agent.mobile_agent import MobileAgent
from jev_mobile.actions.models import ActionKind
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


def test_semantic_reverse_navigation_cycle_is_detected_and_edge_suppressed() -> None:
    history = [
        {"state": "A", "to_state": "B", "family": "navigation", "requirements": (("read", "unsatisfied"),)},
        {"state": "B", "to_state": "A", "family": "navigation", "requirements": (("read", "unsatisfied"),)},
    ]
    assert MobileAgent._navigation_cycle(history)

    state = normalize(RawDeviceState(elements=[RawDeviceElement(node_id="root", text="Root", clickable=True)]))
    registry = SnapshotRefRegistry()
    catalog = ActionCatalog.build(state, registry)
    action = type("A", (), {"kind": type("K", (), {"value": "tap"})(), "text": None})()
    signature = MobileAgent._attempt_signature(action, catalog.actions[0])
    history = [{"signature": signature, "state": state.fingerprint, "mutation": "executed_confirmed", "cycle": True}]
    actions, _ = MobileAgent._candidates(
        catalog, registry, [], type("S", (), {"app_package": None})(), "x", history, state.fingerprint,
    )
    assert not any(item.kind.value == "tap" for item in actions)


def test_scroll_reverse_and_three_state_cycles_are_detected() -> None:
    requirements = (("read", "unsatisfied"),)
    reverse = [
        {"state": "bottom", "to_state": "middle", "family": "scroll", "requirements": requirements},
        {"state": "middle", "to_state": "bottom", "family": "scroll", "requirements": requirements},
    ]
    longer = [
        {"state": "A", "to_state": "B", "family": "navigation", "requirements": requirements},
        {"state": "B", "to_state": "C", "family": "scroll", "requirements": requirements},
        {"state": "C", "to_state": "A", "family": "navigation", "requirements": requirements},
    ]

    assert MobileAgent._semantic_cycle_length(reverse) == 2
    assert MobileAgent._semantic_cycle_length(longer) == 3


def test_cycle_suppression_is_scoped_to_semantic_source_state() -> None:
    bottom = normalize(RawDeviceState(elements=[
        RawDeviceElement(
            node_id="list", text="Settings", scrollable=True,
            available_actions=["SCROLL_BACKWARD"],
        ),
    ]))
    registry = SnapshotRefRegistry()
    catalog = ActionCatalog.build(bottom, registry)
    scroll = type("A", (), {"kind": ActionKind.SCROLL_UP, "text": None})()
    signature = MobileAgent._attempt_signature(scroll, catalog.actions[0])
    history = [{
        "signature": signature,
        "state": bottom.fingerprint,
        "mutation": "executed_confirmed",
        "cycle": True,
    }]

    suppressed, _ = MobileAgent._candidates(
        catalog, registry, [], type("S", (), {"app_package": None})(),
        "x", history, bottom.fingerprint,
    )
    available_elsewhere, _ = MobileAgent._candidates(
        catalog, registry, [], type("S", (), {"app_package": None})(),
        "x", history, "materially-different-state",
    )

    assert not any(item.kind == ActionKind.SCROLL_UP for item in suppressed)
    assert any(item.kind == ActionKind.SCROLL_UP for item in available_elsewhere)


def test_model_history_contains_readable_scroll_and_loop_context() -> None:
    state = normalize(RawDeviceState(package="com.android.settings", activity="Settings", elements=[
        RawDeviceElement(
            node_id="list", class_name="android.widget.ScrollView", scrollable=True,
            available_actions=["SCROLL_BACKWARD"],
        ),
        RawDeviceElement(node_id="about", parent_id="list", text="About phone", clickable=True),
    ]))

    summary = MobileAgent._history_state(state)
    loop = MobileAgent._loop_status([{
        "state": state.fingerprint,
        "action": "Scroll up",
        "family": "scroll",
        "mutation": "executed_confirmed",
        "requirement_progress": [],
        "cycle": True,
    }], state.fingerprint)

    assert summary["package"] == "com.android.settings"
    assert summary["visible_landmarks"] == ["About phone"]
    assert summary["scroll_contexts"][0]["position"] == "bottom"
    assert loop["detected"] is True
    assert loop["current_state_has_suppressed_edge"] is True


def test_target_package_companion_is_same_app_context() -> None:
    assert MobileAgent._same_app_context("com.android.settings.intelligence", "com.android.settings")
    assert not MobileAgent._same_app_context("com.example.other", "com.android.settings")


def test_repeated_no_effect_scroll_is_suppressed() -> None:
    state = normalize(RawDeviceState(elements=[
        RawDeviceElement(node_id="list", text="Content", scrollable=True),
    ]))
    registry = SnapshotRefRegistry()
    catalog = ActionCatalog.build(state, registry)
    action = type("A", (), {"kind": type("K", (), {"value": "scroll_down"})(), "text": None})()
    signature = MobileAgent._attempt_signature(action, catalog.actions[0])
    history = [
        {"signature": signature, "state": state.fingerprint, "mutation": "executed_no_effect"},
        {"signature": signature, "state": state.fingerprint, "mutation": "executed_no_effect"},
    ]

    actions, _ = MobileAgent._candidates(
        catalog, registry, [], type("S", (), {"app_package": None})(), "x", history, state.fingerprint,
    )

    assert not any(item.kind == ActionKind.SCROLL_DOWN for item in actions)

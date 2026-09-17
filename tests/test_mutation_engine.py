from jev_mobile.actions.mutation_engine import MutationEngine
from jev_mobile.actions.mutation_family import MutationFamily
from jev_mobile.actions.mutation_journal import MutationJournal, MutationOutcome
from jev_mobile.perception import SnapshotRefRegistry
from jev_mobile.state.models import RawDeviceElement, RawDeviceState
from jev_mobile.state.normalize import normalize


async def test_stale_reference_does_not_execute_an_operation() -> None:
    registry = SnapshotRefRegistry()
    state = normalize(RawDeviceState(elements=[RawDeviceElement(node_id="one", text="One", clickable=True)]))
    registry.register(state)
    registry.register(state)
    called = False

    async def operation(_: str):
        nonlocal called
        called = True

    async def observe(): return state
    entry = await MutationEngine(registry, MutationJournal(), observe).execute("tap", "s1:e1", "open", operation, lambda _: True)
    assert entry.outcome == MutationOutcome.TARGET_STALE
    assert not called


def test_family_filtered_fault_ignores_non_create_and_fires_once(monkeypatch) -> None:
    journal = MutationJournal()
    navigation = journal.begin_action(action="tap", snapshot_id="s1", intended_effect="back", family=MutationFamily.NAVIGATION)
    text = journal.begin_action(action="type_text", snapshot_id="s1", intended_effect="text", family=MutationFamily.TEXT_WRITE)
    create = journal.begin_action(action="tap", snapshot_id="s1", intended_effect="new entity", family=MutationFamily.CREATE_ENTITY)
    monkeypatch.setenv("JEV_MOBILE_FAULT_POINT", "after_device_execute")
    monkeypatch.setenv("JEV_MOBILE_FAULT_FAMILY", "create_entity")
    fired: list[str] = []
    monkeypatch.setattr("jev_mobile.actions.mutation_engine.os._exit", lambda _: (_ for _ in ()).throw(RuntimeError("fault")))
    assert MutationEngine._fault("after_device_execute", navigation, lambda *_: fired.append("navigation") or True) is None
    assert MutationEngine._fault("after_device_execute", text, lambda *_: fired.append("text") or True) is None
    try:
        MutationEngine._fault("after_device_execute", create, lambda *_: fired.append("create") or True)
    except RuntimeError as error:
        assert str(error) == "fault"
    else:
        raise AssertionError("create family must inject the configured fault")
    assert fired == ["create"]

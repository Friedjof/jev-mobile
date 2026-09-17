from jev_mobile.actions.mutation_journal import MutationJournal, MutationOutcome
from jev_mobile.perception import ActionCatalog, SnapshotRefRegistry, StaleSnapshotReference
from jev_mobile.state.models import RawDeviceElement, RawDeviceState
from jev_mobile.state.normalize import normalize
from jev_mobile.task_store import TaskStatus, TaskStore
from jev_mobile.tasks import TaskSpec


def test_catalog_refs_are_bound_to_one_snapshot() -> None:
    state = normalize(RawDeviceState(elements=[RawDeviceElement(node_id="native", text="Save", clickable=True)]))
    registry = SnapshotRefRegistry()
    catalog = ActionCatalog.build(state, registry)
    assert catalog.actions[0].ref == "s1:e1"
    ActionCatalog.build(state, registry)
    try:
        registry.resolve("s1:e1")
    except StaleSnapshotReference:
        pass
    else:
        raise AssertionError("old snapshot references must be rejected")


def test_mutation_is_journaled_before_it_is_resolved() -> None:
    journal = MutationJournal()
    entry = journal.begin_action(action="tap", snapshot_id="s12", intended_effect="open editor")
    assert entry.outcome is None
    assert journal.resolve_action(entry, MutationOutcome.TRANSPORT_FAILED).outcome == MutationOutcome.TRANSPORT_FAILED


def test_task_store_persists_canonical_task_spec(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    task = store.create("create note", TaskSpec(intent="create_note", items=["Milk"]))
    task.status = TaskStatus.RUNNING
    store.save(task)
    loaded = store.get(task.id)
    assert loaded is not None
    assert loaded.task_spec.items == ["Milk"]
    assert loaded.status == TaskStatus.RUNNING
    store.close()


def test_expired_running_task_is_reclaimed_with_its_pending_mutation(tmp_path) -> None:
    """Recovery keeps the task identity and intent; it never creates a replay task."""
    store = TaskStore(tmp_path / "tasks.sqlite")
    task = store.create("create note", TaskSpec(intent="create_note", items=["CrashText"]))
    first = store.claim_next_task("worker-a", "phone-a", lease_seconds=0)
    assert first and first.id == task.id
    first.pending_mutation = {"action": "type_text", "target_ref": "s9:e4", "text": "CrashText"}
    store.save(first, "phone-a")
    assert store.recover_expired() == 1
    recovered = store.get(task.id)
    assert recovered and recovered.status == TaskStatus.RECOVERING
    assert recovered.pending_mutation and recovered.pending_mutation["target_ref"] == "s9:e4"
    second = store.claim_next_task("worker-b", "phone-a")
    assert second and second.id == task.id
    assert second.pending_mutation == recovered.pending_mutation
    events = store.events(task.id)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert [event["event_type"] for event in events][-2:] == ["TASK_RECOVERING", "TASK_STARTED"]
    store.close()


def test_events_can_be_read_incrementally(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    task = store.create("create note", TaskSpec(intent="create_note"))
    store.event(task.id, "SNAPSHOT_OBSERVED", {"snapshot": "s1"})
    assert [event["seq"] for event in store.events(task.id, after_seq=1)] == [2]
    store.close()


def test_persisted_cancellation_survives_an_agent_checkpoint(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    task = store.create("create note", TaskSpec(intent="create_note"))
    stale_agent_copy = store.get(task.id)
    assert stale_agent_copy
    task.cancellation_requested = True
    store.save(task)
    # A stale worker checkpoint must not erase a request made by another
    # client/process.
    stale_agent_copy.status = TaskStatus.CANCELLED
    store.save(stale_agent_copy)
    restored = store.get(task.id)
    assert restored and restored.status == TaskStatus.CANCELLED
    assert restored.cancellation_requested
    store.close()

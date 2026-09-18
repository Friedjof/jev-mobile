from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jev_mobile import mcp_server
from jev_mobile.agent.mobile_agent import MobileAgent
from jev_mobile.config import Settings
from jev_mobile.providers.mock import MockProvider
from jev_mobile.providers.base import ProviderUnavailable
from jev_mobile.state.models import RawDeviceState
from jev_mobile.task_store import TaskStore
from jev_mobile.task_store import TaskStatus
from jev_mobile.tasks import (
    CompletionRequirement,
    MAX_ORDERED_SUBTASKS,
    SubtaskRequest,
    SubtaskStatus,
    TaskContractStatus,
    TaskPolicy,
    TaskPolicyMode,
    TaskSpec,
    build_ordered_subtasks,
)


class _ObservedApp:
    enters = 0

    async def __aenter__(self) -> "_ObservedApp":
        type(self).enters += 1
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def observe(self) -> RawDeviceState:
        return RawDeviceState(package="com.example.app", activity="Main", snapshot_id=f"native-{self.enters}")


def _open_spec() -> TaskSpec:
    return TaskSpec(
        intent="open_app",
        app="Example",
        app_package="com.example.app",
        policy=TaskPolicy(mode=TaskPolicyMode.MUTATING),
        completion=[CompletionRequirement(type="app_open", value="com.example.app")],
    )


def _subtask_agent(store: TaskStore, tmp_path) -> MobileAgent:
    settings = Settings((), None, tmp_path / "traces", 0.01, 0.1, 0.8)
    return MobileAgent(store, _ObservedApp, MockProvider(), settings)


class _RecordingProvider:
    name = "recording"

    def __init__(self) -> None:
        self.agent_context = None

    async def decide(self, goal, state, actions):
        self.agent_context = state.agent_context
        raise ProviderUnavailable("defer after recording context")


def test_ordered_subtasks_are_validated_and_persisted(tmp_path) -> None:
    subtasks = build_ordered_subtasks([
        SubtaskRequest(id="open-settings", instruction="  Open Android Settings.  "),
        SubtaskRequest(instruction="Read the current device name."),
    ])
    store = TaskStore(tmp_path / "tasks.sqlite3")

    task = store.create("Inspect the device", mcp_server.task_spec_from_goal("Inspect the device"), subtasks)
    restored = store.get(task.id)

    assert restored is not None
    assert [(item.id, item.instruction, item.position) for item in restored.subtasks] == [
        ("open-settings", "Open Android Settings.", 1),
        (subtasks[1].id, "Read the current device name.", 2),
    ]
    assert restored.subtasks[1].id.startswith("subtask-")
    assert store.events(task.id)[0]["payload"]["subtask_count"] == 2


def test_legacy_task_creation_has_no_subtasks(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")

    task = store.create("Open Settings", mcp_server.task_spec_from_goal("Open Settings"))

    assert task.subtasks == []
    assert store.get(task.id).subtasks == []  # type: ignore[union-attr]


@pytest.mark.parametrize("subtasks", [
    [],
    [SubtaskRequest(id="same", instruction="First"), SubtaskRequest(id="same", instruction="Second")],
    [SubtaskRequest(instruction=f"Step {index}") for index in range(MAX_ORDERED_SUBTASKS + 1)],
])
def test_invalid_subtask_plans_are_rejected(subtasks) -> None:
    with pytest.raises(ValueError):
        build_ordered_subtasks(subtasks)


@pytest.mark.parametrize("payload", [
    {"instruction": "   "},
    {"id": "bad id", "instruction": "Valid"},
    {"id": "valid", "instruction": "Valid", "target_ref": "s1:e2"},
])
def test_invalid_subtask_entries_are_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        SubtaskRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_start_task_accepts_optional_subtasks_and_returns_immediately(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    monkeypatch.setattr(mcp_server, "_task_store", store)

    result = await mcp_server.start_task(
        "Prepare this phone",
        [SubtaskRequest(id="inspect", instruction="Inspect the current setting")],
    )

    task = store.get(str(result["task_id"]))
    assert result["status"] == "queued"
    assert task is not None and task.subtasks[0].id == "inspect"
    assert task.worker_id is None


def test_public_mcp_schema_keeps_six_tools_and_exposes_optional_subtasks() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp_server.server.list_tools())}

    assert set(tools) == {
        "start_task", "get_task", "get_task_events", "cancel_task", "answer_task", "get_device_status",
    }
    schema = tools["start_task"].input_schema
    assert schema["required"] == ["instruction"]
    subtask_schema = schema["properties"]["subtasks"]["anyOf"][0]
    assert subtask_schema["type"] == "array"
    assert subtask_schema["minItems"] == 1
    assert subtask_schema["maxItems"] == MAX_ORDERED_SUBTASKS


@pytest.mark.asyncio
async def test_ordered_subtasks_advance_one_at_a_time_and_finish_parent(tmp_path) -> None:
    _ObservedApp.enters = 0
    store = TaskStore(tmp_path / "tasks.sqlite3")
    subtasks = build_ordered_subtasks([
        SubtaskRequest(id="first", instruction="Open Example first"),
        SubtaskRequest(id="second", instruction="Verify Example second"),
    ])
    for subtask in subtasks:
        subtask.task_spec = _open_spec()
        subtask.contract_status = TaskContractStatus.READY
    task = store.create("Complete the ordered checks", TaskSpec(), subtasks)
    agent = _subtask_agent(store, tmp_path)

    await agent.run(task.id)
    after_first = store.get(task.id)

    assert after_first is not None and after_first.status == TaskStatus.QUEUED
    assert [item.status for item in after_first.subtasks] == [SubtaskStatus.SUCCEEDED, SubtaskStatus.QUEUED]
    assert after_first.active_subtask_id is None
    assert _ObservedApp.enters == 1

    await agent.run(task.id)
    completed = store.get(task.id)

    assert completed is not None and completed.status == TaskStatus.SUCCEEDED
    assert all(item.status == SubtaskStatus.SUCCEEDED for item in completed.subtasks)
    assert completed.result is not None and completed.result["verified"] is True
    assert completed.step_number == 2
    assert [item.step_number for item in completed.subtasks] == [1, 1]
    assert _ObservedApp.enters == 2
    events = store.events(task.id)
    assert [event["payload"]["subtask_id"] for event in events if event["event_type"] == "SUBTASK_STARTED"] == [
        "first", "second",
    ]
    assert [event["payload"]["subtask_id"] for event in events if event["event_type"] == "SUBTASK_SUCCEEDED"] == [
        "first", "second",
    ]


@pytest.mark.asyncio
async def test_recovering_parent_resumes_same_active_subtask_without_second_start(tmp_path, monkeypatch) -> None:
    _ObservedApp.enters = 0
    store = TaskStore(tmp_path / "tasks.sqlite3")
    subtasks = build_ordered_subtasks([SubtaskRequest(id="resume-me", instruction="Open Example")])
    subtasks[0].task_spec = _open_spec()
    subtasks[0].contract_status = TaskContractStatus.READY
    subtasks[0].status = SubtaskStatus.RUNNING
    subtasks[0].started_at = datetime.now(UTC)
    task = store.create("Resume the check", TaskSpec(), subtasks)
    task.status = TaskStatus.RECOVERING
    task.active_subtask_id = "resume-me"
    task.agent_context["resume_requires_fresh_observation"] = "worker-restart"
    task.pending_mutation = {
        "action": "set_text",
        "family": "text_write",
        "target_ref": "s-old:e-old",
        "text": "not present",
        "intended_effect": "historical only",
    }
    store.save(task)

    await _subtask_agent(store, tmp_path).run(task.id)

    completed = store.get(task.id)
    assert completed is not None and completed.status == TaskStatus.SUCCEEDED
    assert completed.subtasks[0].status == SubtaskStatus.SUCCEEDED
    events = store.events(task.id)
    assert not any(event["event_type"] == "SUBTASK_STARTED" for event in events)
    assert any(event["event_type"] == "INPUT_RESUME_OBSERVATION" for event in events)
    reconciliation_seq = next(event["seq"] for event in events if event["event_type"] == "PENDING_MUTATION_RECONCILED")
    success_seq = next(event["seq"] for event in events if event["event_type"] == "SUBTASK_SUCCEEDED")
    assert reconciliation_seq < success_seq
    assert not any(event["event_type"] == "ACTION_SELECTED" for event in events)
    monkeypatch.setattr(mcp_server, "_task_store", store)
    public_events = await mcp_server.get_task_events(task.id)
    assert "s-old:e-old" not in str(public_events)
    assert [event["seq"] for event in public_events["events"]] == sorted(
        event["seq"] for event in public_events["events"]
    )


@pytest.mark.asyncio
async def test_waiting_answer_requeues_active_subtask_without_inline_android(tmp_path, monkeypatch) -> None:
    _ObservedApp.enters = 0
    store = TaskStore(tmp_path / "tasks.sqlite3")
    subtasks = build_ordered_subtasks([SubtaskRequest(id="clarify", instruction="Read information")])
    task = store.create("Read requested information", TaskSpec(), subtasks)
    monkeypatch.setattr(mcp_server, "_task_store", store)

    await _subtask_agent(store, tmp_path).run(task.id)
    waiting = store.get(task.id)

    assert waiting is not None and waiting.status == TaskStatus.WAITING_FOR_USER
    assert waiting.subtasks[0].status == SubtaskStatus.WAITING_FOR_USER
    assert waiting.waiting_question is not None
    question_id = str(waiting.waiting_question["id"])
    waiting_public = await mcp_server.get_task(task.id)
    assert waiting_public["waiting_for_user"]["subtask_id"] == "clarify"
    result = await mcp_server.answer_task(task.id, question_id, "Android Settings")
    requeued = store.get(task.id)

    assert result["status"] == "queued"
    assert requeued is not None and requeued.status == TaskStatus.QUEUED
    assert requeued.subtasks[0].status == SubtaskStatus.RUNNING
    assert requeued.subtasks[0].input_history[-1].question_id == question_id
    assert _ObservedApp.enters == 0
    requeued_public = await mcp_server.get_task(task.id)
    assert requeued_public["waiting_for_user"] is None


@pytest.mark.asyncio
async def test_waiting_parent_cancellation_cancels_active_and_remaining_subtasks(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    subtasks = build_ordered_subtasks([
        SubtaskRequest(id="active", instruction="Read information"),
        SubtaskRequest(id="later", instruction="Open Example"),
    ])
    task = store.create("Do both", TaskSpec(), subtasks)
    monkeypatch.setattr(mcp_server, "_task_store", store)
    await _subtask_agent(store, tmp_path).run(task.id)

    result = await mcp_server.cancel_task(task.id)
    cancelled = store.get(task.id)

    assert result["status"] == "cancelled"
    assert cancelled is not None and cancelled.status == TaskStatus.CANCELLED
    assert all(item.status == SubtaskStatus.CANCELLED for item in cancelled.subtasks)
    assert sum(event["event_type"] == "SUBTASK_CANCELLED" for event in store.events(task.id)) == 2


@pytest.mark.asyncio
async def test_get_task_exposes_bounded_subtask_progress_and_verified_result(tmp_path, monkeypatch) -> None:
    _ObservedApp.enters = 0
    store = TaskStore(tmp_path / "tasks.sqlite3")
    subtasks = build_ordered_subtasks([SubtaskRequest(id="only", instruction="Open Example")])
    subtasks[0].task_spec = _open_spec()
    subtasks[0].contract_status = TaskContractStatus.READY
    task = store.create("Complete the check", TaskSpec(), subtasks)
    monkeypatch.setattr(mcp_server, "_task_store", store)

    await _subtask_agent(store, tmp_path).run(task.id)
    public = await mcp_server.get_task(task.id)

    assert public["subtasks"]["completed"] == 1
    assert public["subtasks"]["total"] == 1
    assert public["subtasks"]["current"] is None
    assert public["result"]["verified"] is True
    assert public["result"]["subtasks"][0]["id"] == "only"


@pytest.mark.asyncio
async def test_failed_required_subtask_fails_parent_and_does_not_run_later_work(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    subtasks = build_ordered_subtasks([
        SubtaskRequest(id="unsupported", instruction="Do an unspecified thing"),
        SubtaskRequest(id="must-not-run", instruction="Open Example"),
    ])
    subtasks[1].task_spec = _open_spec()
    subtasks[1].contract_status = TaskContractStatus.READY
    task = store.create("Complete both", TaskSpec(), subtasks)

    await _subtask_agent(store, tmp_path).run(task.id)

    failed = store.get(task.id)
    assert failed is not None and failed.status == TaskStatus.FAILED
    assert failed.subtasks[0].status == SubtaskStatus.FAILED
    assert failed.subtasks[1].status == SubtaskStatus.QUEUED
    assert not any(
        event["event_type"] == "SUBTASK_STARTED" and event["payload"]["subtask_id"] == "must-not-run"
        for event in store.events(task.id)
    )


@pytest.mark.asyncio
async def test_verified_prior_result_is_semantic_context_for_next_subtask(tmp_path) -> None:
    _ObservedApp.enters = 0
    store = TaskStore(tmp_path / "tasks.sqlite3")
    subtasks = build_ordered_subtasks([
        SubtaskRequest(id="completed", instruction="Open Example"),
        SubtaskRequest(id="next", instruction="Open Other"),
    ])
    subtasks[0].task_spec = _open_spec()
    subtasks[0].contract_status = TaskContractStatus.READY
    subtasks[1].task_spec = _open_spec().model_copy(update={
        "app": "Other",
        "app_package": "com.example.other",
        "completion": [CompletionRequirement(type="app_open", value="com.example.other")],
    })
    subtasks[1].contract_status = TaskContractStatus.READY
    task = store.create("Run in order", TaskSpec(), subtasks)
    provider = _RecordingProvider()
    settings = Settings((), None, tmp_path / "traces", 0.01, 0.1, 0.8)
    agent = MobileAgent(store, _ObservedApp, provider, settings)

    await agent.run(task.id)
    await agent.run(task.id)

    assert provider.agent_context is not None
    assert provider.agent_context["subtask"] == {"id": "next", "position": 2, "total": 2}
    assert provider.agent_context["completed_subtasks"][0]["id"] == "completed"

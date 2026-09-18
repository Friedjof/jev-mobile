from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jev_mobile.agent.mobile_agent import MobileAgent
from jev_mobile import mcp_server
from jev_mobile.config import Settings
from jev_mobile.mcp_server import _public_contract, _public_result
from jev_mobile.providers.mock import MockProvider
from jev_mobile.state.models import RawDeviceState
from jev_mobile.task_store import MobileTask, TaskStatus, TaskStore
from jev_mobile.tasks import (
    CompletionRequirement,
    RequestedOutput,
    TaskContractStatus,
    TaskInterpretation,
    TaskPolicy,
    TaskPolicyMode,
    TaskSpec,
    task_spec_from_goal,
    validate_task_contract,
)


def _inspection_spec() -> TaskSpec:
    return TaskSpec(
        intent="inspect_profile",
        app="Example Social",
        app_package="com.example.social",
        policy=TaskPolicy(mode=TaskPolicyMode.READ_ONLY),
        requested_outputs=[RequestedOutput(key="login_state", description="Current login state")],
        completion=[
            CompletionRequirement(type="information_observed", output_key="login_state"),
        ],
    )


def test_known_deterministic_task_is_queued_with_ready_contract(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    spec = task_spec_from_goal("Create a Google Keep checklist titled Shopping with Eggs and Milk")

    task = store.create("known task", spec)

    assert task.contract_status == TaskContractStatus.READY
    assert validate_task_contract(task.task_spec).valid
    event = store.events(task.id)[0]
    assert event["payload"]["contract_status"] == "ready"


class _ContractProvider:
    name = "contract-provider"

    def __init__(self) -> None:
        self.calls = 0

    async def interpret_task(self, goal, state):
        self.calls += 1
        return TaskInterpretation(task_spec=_inspection_spec())


@pytest.mark.asyncio
async def test_generic_contract_is_resolved_once_and_survives_restart(tmp_path) -> None:
    database = tmp_path / "tasks.sqlite3"
    store = TaskStore(database)
    task = store.create("Inspect my current profile", TaskSpec())
    settings = Settings((), None, tmp_path / "traces", 0.01, 0.1, 0.8)
    provider = _ContractProvider()
    agent = MobileAgent(store, lambda: None, provider, settings)  # type: ignore[arg-type]

    assert task.contract_status == TaskContractStatus.PENDING
    resolved = await agent._resolve_task_contract(task)

    assert resolved is not None
    assert resolved.contract_status == TaskContractStatus.READY
    assert resolved.task_spec.intent == "inspect_profile"
    assert provider.calls == 1
    assert await agent._resolve_task_contract(resolved) is resolved
    assert provider.calls == 1
    assert [event["event_type"] for event in store.events(task.id)][-1] == "TASK_CONTRACT_RESOLVED"
    store.close()

    reopened = TaskStore(database).get(task.id)
    assert reopened is not None
    assert reopened.contract_status == TaskContractStatus.READY
    assert reopened.task_spec == resolved.task_spec
    restarted_provider = _ContractProvider()
    restarted_agent = MobileAgent(
        TaskStore(database), lambda: None, restarted_provider, settings,  # type: ignore[arg-type]
    )
    assert await restarted_agent._resolve_task_contract(reopened) is reopened
    assert restarted_provider.calls == 0


def test_ambiguous_read_contract_is_rejected() -> None:
    spec = TaskSpec(
        intent="read_information",
        app="Example",
        policy=TaskPolicy(mode=TaskPolicyMode.READ_ONLY),
        completion=[CompletionRequirement(type="app_open", value="Example")],
    )

    validation = validate_task_contract(spec)

    assert validation.valid is False
    assert "read-only task has no requested outputs" in validation.errors


def test_required_output_must_be_linked_to_completion_evidence() -> None:
    spec = TaskSpec(
        intent="read_information",
        app="Example",
        policy=TaskPolicy(mode=TaskPolicyMode.READ_ONLY),
        requested_outputs=[RequestedOutput(key="value", description="Requested value")],
        completion=[CompletionRequirement(type="app_open", value="Example")],
    )

    validation = validate_task_contract(spec)

    assert validation.valid is False
    assert "required output has no completion requirement" in validation.errors


def test_legacy_task_derives_contract_state_when_field_is_missing() -> None:
    task = MobileTask.model_validate({
        "id": "legacy",
        "instruction": "known",
        "task_spec": task_spec_from_goal("Create a Google Keep note titled Test with body Hello"),
        "created_at": datetime.now(UTC),
    })

    assert task.contract_status == TaskContractStatus.READY


def test_public_contract_is_semantic_and_contains_no_task_content_values() -> None:
    task = MobileTask(
        id="contract",
        instruction="inspect",
        task_spec=_inspection_spec(),
        created_at=datetime.now(UTC),
    )

    public = _public_contract(task)

    assert public["status"] == "ready"
    assert public["policy"] == {
        "mode": "read_only",
        "external_interactions": False,
        "sensitive_data": False,
    }
    assert public["requested_outputs"][0]["key"] == "login_state"


@pytest.mark.asyncio
async def test_get_task_exposes_persisted_contract_summary(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create("inspect", _inspection_spec())
    monkeypatch.setattr(mcp_server, "_task_store", store)

    result = await mcp_server.get_task(task.id)

    assert result["contract"]["status"] == "ready"
    assert result["contract"]["intent"] == "inspect_profile"
    assert result["contract"]["target"]["package"] == "com.example.social"


@pytest.mark.asyncio
async def test_agent_success_persists_evidence_for_every_required_criterion(tmp_path) -> None:
    class ObservedApp:
        async def __aenter__(self) -> "ObservedApp":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def observe(self) -> RawDeviceState:
            return RawDeviceState(package="com.example.app", activity="Main", snapshot_id="native-1")

    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create("Open Example", TaskSpec(
        intent="open_app",
        app="Example",
        app_package="com.example.app",
        policy=TaskPolicy(mode=TaskPolicyMode.MUTATING),
        completion=[CompletionRequirement(type="app_open", value="com.example.app")],
    ))
    settings = Settings((), None, tmp_path / "traces", 0.01, 0.1, 0.8)
    agent = MobileAgent(store, ObservedApp, MockProvider(), settings)

    await agent.run(task.id)

    completed = store.get(task.id)
    assert completed is not None and completed.status == TaskStatus.SUCCEEDED
    assert completed.result is not None and completed.result["verified"] is True
    assert len(completed.requirement_evidence) == len(completed.requirements)
    public = _public_result(completed)
    assert public is not None and public["verified"] is True
    assert all(item["evidence"]["source"]["package"] == "com.example.app" for item in public["requirements"])

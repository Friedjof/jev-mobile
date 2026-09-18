from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jev_mobile import mcp_server
from jev_mobile.actions.models import ActionKind
from jev_mobile.agent.mobile_agent import MobileAgent
from jev_mobile.config import Settings
from jev_mobile.providers.mock import MockProvider
from jev_mobile.state.models import RawDeviceElement, RawDeviceState
from jev_mobile.task_store import MobileTask, TaskStatus, TaskStore
from jev_mobile.tasks import (
    AnswerEffect,
    AnswerEffectKind,
    CompletionRequirement,
    InformationRequest,
    QuestionOption,
    QuestionType,
    RequestedOutput,
    TaskPolicy,
    TaskPolicyMode,
    TaskSpec,
    make_question,
    task_spec_from_goal,
)


def _settings_read_spec() -> TaskSpec:
    return TaskSpec(
        intent="read_information",
        app="Android Settings",
        app_package="com.android.settings",
        content_type="information",
        policy=TaskPolicy(mode=TaskPolicyMode.READ_ONLY),
        information_requests=[InformationRequest(
            key="device_name",
            question="What is the device name?",
            semantic_hints=["device name"],
        )],
        requested_outputs=[RequestedOutput(key="device_name", description="Device name")],
        completion=[CompletionRequirement(type="information_observed", output_key="device_name")],
    )


@pytest.mark.asyncio
async def test_missing_app_durably_requests_structured_input(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create(
        "Give me the version shown in this app.",
        task_spec_from_goal("Give me the version shown in this app."),
    )
    agent = MobileAgent(
        store,
        lambda: None,  # type: ignore[arg-type]
        MockProvider(),
        Settings((), None, tmp_path / "traces", 0.01, 0.1, 0.8),
    )

    assert await agent._resolve_task_contract(task) is None
    waiting = store.get(task.id)
    assert waiting is not None and waiting.status == TaskStatus.WAITING_FOR_USER
    question = waiting.waiting_question
    assert question is not None
    assert question["type"] == "text"
    assert question["reason"] == "MISSING_TARGET_APP"
    assert question["answer_schema"] == {"type": "text", "min_length": 1, "max_length": 240}
    assert question["answer_effect"] == {"kind": "set_target_app", "parameter": "target_app"}


@pytest.mark.asyncio
async def test_answer_task_validates_persists_and_only_requeues(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create("read version", task_spec_from_goal("Give me the version shown in this app."))
    question = make_question(
        question_type=QuestionType.TEXT,
        reason="MISSING_TARGET_APP",
        prompt_key="target_app",
        text="Which app?",
        effect=AnswerEffect(kind=AnswerEffectKind.SET_TARGET_APP, parameter="target_app"),
    )
    task.status = TaskStatus.WAITING_FOR_USER
    task.waiting_question = question.model_dump(mode="json")
    store.save(task)
    monkeypatch.setattr(mcp_server, "_task_store", store)

    result = await mcp_server.answer_task(task.id, question.id, "Android Settings")

    assert result == {"task_id": task.id, "status": "queued"}
    answered = store.get(task.id)
    assert answered is not None
    assert answered.task_spec.app_package == "com.android.settings"
    assert answered.input_history[0].prompt_key == "target_app"
    assert answered.agent_context["resume_requires_fresh_observation"] == question.id
    assert answered.pending_mutation is None
    event = store.events(task.id)[-1]
    assert event["event_type"] == "USER_ANSWER_RECEIVED"
    assert event["payload"]["answer_kind"] == "text"
    assert "Android Settings" not in str(event["payload"])


@pytest.mark.asyncio
async def test_invalid_option_and_stale_answer_are_rejected(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = MobileTask(
        id="task_options",
        instruction="choose",
        task_spec=_settings_read_spec(),
        created_at=datetime.now(UTC),
        status=TaskStatus.WAITING_FOR_USER,
    )
    question = make_question(
        question_type=QuestionType.SELECT_OPTION,
        reason="AMBIGUOUS",
        prompt_key="select_information:device_name",
        text="Which value?",
        options=[QuestionOption(id="one", label="One"), QuestionOption(id="two", label="Two")],
        effect=AnswerEffect(kind=AnswerEffectKind.SELECT_SEMANTIC_OPTION, parameter="device_name"),
    )
    task.waiting_question = question.model_dump(mode="json")
    store.save(task)
    monkeypatch.setattr(mcp_server, "_task_store", store)

    with pytest.raises(ValueError, match="option IDs"):
        await mcp_server.answer_task(task.id, question.id, "three")
    with pytest.raises(ValueError, match="snapshot references"):
        await mcp_server.answer_task(task.id, question.id, "s42:e7")
    await mcp_server.answer_task(task.id, question.id, "two")
    with pytest.raises(ValueError, match="not waiting"):
        await mcp_server.answer_task(task.id, question.id, "two")


def test_ambiguity_becomes_a_request_input_candidate() -> None:
    task = MobileTask(
        id="task_ambiguity",
        instruction="read",
        task_spec=_settings_read_spec(),
        created_at=datetime.now(UTC),
    )
    requirement = type("Requirement", (), {
        "output_key": "device_name",
        "evidence": {"ambiguity": [
            {"id": "one", "label": "Device name", "value": "Phone A"},
            {"id": "two", "label": "Device name", "value": "Phone B"},
        ]},
    })()
    question = MobileAgent._ambiguity_question(task, [requirement])
    assert question is not None

    actions, mapping = MobileAgent._candidates(
        type("Catalog", (), {"actions": []})(), None, [], task.task_spec, None, [], "state",
        input_question=question,
    )

    assert [action.kind for action in actions] == [ActionKind.REQUEST_INPUT]
    assert actions[0].question["options"][1]["id"] == "two"  # type: ignore[index]
    assert mapping[actions[0].id][1:] == (None, None)


@pytest.mark.asyncio
async def test_answer_resume_begins_with_fresh_observation(tmp_path) -> None:
    class Device:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def observe(self):
            return RawDeviceState(
                package="com.android.settings",
                snapshot_id="fresh-native-snapshot",
                elements=[
                    RawDeviceElement(node_id="row", child_ids=["label", "value"]),
                    RawDeviceElement(node_id="label", parent_id="row", text="Device name", class_name="TextView"),
                    RawDeviceElement(node_id="value", parent_id="row", text="Example Phone", class_name="TextView"),
                ],
            )

    store = TaskStore(tmp_path / "tasks.db")
    task = store.create("read device name", _settings_read_spec())
    task.agent_context["resume_requires_fresh_observation"] = "q_previous"
    store.save(task)
    agent = MobileAgent(
        store, Device, MockProvider(),
        Settings((), None, tmp_path / "traces", 0.01, 0.1, 0.8),
    )

    await agent.run(task.id)

    completed = store.get(task.id)
    assert completed is not None and completed.status == TaskStatus.SUCCEEDED
    events = store.events(task.id)
    fresh = next(event for event in events if event["event_type"] == "INPUT_RESUME_OBSERVATION")
    assert fresh["payload"]["question_id"] == "q_previous"
    assert completed.result["observations"]["device_name"]["value"] == "Example Phone"  # type: ignore[index]


@pytest.mark.asyncio
async def test_personal_information_requires_separate_approval(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create(
        "Which account is currently selected in Android Settings?",
        task_spec_from_goal("Which account is currently selected in Android Settings?"),
    )
    agent = MobileAgent(
        store,
        lambda: None,  # type: ignore[arg-type]
        MockProvider(),
        Settings((), None, tmp_path / "traces", 0.01, 0.1, 0.8),
    )

    assert await agent._resolve_task_contract(task) is None
    waiting = store.get(task.id)
    assert waiting is not None and waiting.waiting_question is not None
    assert waiting.waiting_question["reason"] == "PERSONAL_INFORMATION_DISCLOSURE"
    monkeypatch.setattr(mcp_server, "_task_store", store)
    await mcp_server.answer_task(task.id, str(waiting.waiting_question["id"]), "allow")
    authorized = store.get(task.id)
    assert authorized is not None
    assert "selected_account" in authorized.agent_context["information_authorizations"]
    assert await agent._resolve_task_contract(authorized) is not None

"""Stdio MCP server for the standalone Jev Mobile controller.

The server deliberately exposes high-level controller operations rather than
ADB commands or arbitrary taps. The controller constructs and validates every
candidate action before a provider can choose it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import argparse
import os
import re
from typing import Literal

from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from .actions.builder import build_action_page
from .config import Settings
from .controller.loop import MobileController
from .device.accessibility_adb import AccessibilityAdbAdapter
from .device.mobile_mcp import MobileMcpAdapter
from .device.portal_adb import PortalAdbDeviceAdapter
from .agent.mobile_agent import MobileAgent
from .requirements.generators import generate_requirements
from .task_store import TaskStatus, TaskStore
from .tasks import (
    AnswerEffect,
    AnswerEffectKind,
    AnswerSchema,
    QuestionOption,
    QuestionSpec,
    QuestionType,
    SubtaskStatus,
    SubtaskRequestList,
    TaskInputAnswer,
    apply_answer_effect,
    build_ordered_subtasks,
    contract_status_for,
    task_spec_from_goal,
    validate_question_answer,
)
from .providers.heuristic import HeuristicProvider
from .providers.jev import JevProvider, probability_margin
from .providers.llm import LLMProvider
from .providers.mock import MockProvider
from .providers.system_one_llm import SystemOneLLMProvider
from .state.normalize import normalize
from .tracing.trace import TraceWriter

server = MCPServer(
    name="jev-mobile",
    title="Jev Mobile",
    description="Bounded Android observe-decide-act control through structured candidate actions.",
)

Backend = Literal["mobile-mcp", "bridge", "portal-adb"]
ProviderName = Literal["jev", "heuristic", "mock", "llm", "system-one-llm"]


def _device(backend: Backend, settings: Settings, serial: str | None):
    selected_serial = serial or settings.device_serial
    if backend == "mobile-mcp":
        return MobileMcpAdapter(settings.mobile_mcp_command, selected_serial)
    if backend == "portal-adb":
        if not selected_serial:
            raise ValueError("serial is required when backend is 'portal-adb'")
        return PortalAdbDeviceAdapter(selected_serial)
    if not selected_serial:
        raise ValueError("serial is required when backend is 'bridge'")
    return AccessibilityAdbAdapter(
        selected_serial,
        port=settings.bridge_port,
        bridge_token=settings.bridge_token,
    )


def _provider(name: ProviderName, settings: Settings):
    if name == "heuristic":
        return HeuristicProvider()
    if name == "mock":
        return MockProvider()
    if name == "llm":
        return LLMProvider(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.agent_system_prompt)
    if name == "jev":
        return JevProvider(settings.typesafe_api_key, settings.agent_system_prompt)
    return SystemOneLLMProvider(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        settings.agent_system_prompt,
    )


# Deliberately process-local worker registry; records themselves live in SQLite.
_task_store = TaskStore()
_agents: dict[tuple[str, str | None, str], MobileAgent] = {}


def _agent(backend: Backend, provider: ProviderName, settings: Settings, serial: str | None) -> MobileAgent:
    key = (backend, serial or settings.device_serial, provider)
    if key not in _agents:
        _agents[key] = MobileAgent(_task_store, lambda: _device(backend, settings, serial), _provider(provider, settings), settings)
    return _agents[key]


@server.tool(name="start_task", description="Delegate a high-level mobile task. Returns immediately; the durable worker executes it.")
async def start_task(instruction: str, subtasks: SubtaskRequestList | None = None) -> dict[str, object]:
    spec = task_spec_from_goal(instruction)
    ordered_subtasks = build_ordered_subtasks(subtasks)
    # A separate durable worker claims queued tasks. MCP never owns device execution.
    task = _task_store.create(instruction, spec, ordered_subtasks)
    return {"task_id": task.id, "status": task.status.value}


@server.tool(name="get_task", description="Return concise parent-agent-friendly state for a delegated task.")
async def get_task(task_id: str) -> dict[str, object]:
    task = _task_store.get(task_id)
    if not task: raise ValueError("unknown task_id")
    requirements = list(task.requirements.values())
    subtask_items = [
        {
            "id": item.id,
            "position": item.position,
            "instruction": item.instruction,
            "status": item.status.value,
            "summary": item.result.get("summary") if item.result else None,
            "failure_reason": item.failure_reason,
        }
        for item in task.subtasks
    ]
    current_subtask = next((item for item in subtask_items if item["id"] == task.active_subtask_id), None)
    waiting_for_user = task.waiting_question or ({"text": task.waiting_reason} if task.waiting_reason else None)
    if waiting_for_user and task.active_subtask_id:
        waiting_for_user = {**waiting_for_user, "subtask_id": task.active_subtask_id}
    return {
        "task_id": task.id,
        "status": task.status.value,
        "instruction": task.instruction,
        "contract": _public_contract(task),
        "current_subgoal": task.current_subgoal,
        "progress": {"requirements_satisfied": sum(status.value == "satisfied" for status in requirements), "requirements_total": len(requirements)},
        "subtasks": {
            "completed": sum(item.status == SubtaskStatus.SUCCEEDED for item in task.subtasks),
            "total": len(task.subtasks),
            "current": current_subtask,
            "items": subtask_items,
        } if task.subtasks else None,
        "waiting_for_user": waiting_for_user,
        "result": _public_result(task),
    }


@server.tool(name="get_task_events", description="Read incremental durable task progress after a task-local sequence number.")
async def get_task_events(task_id: str, after_seq: int = 0) -> dict[str, object]:
    if not _task_store.get(task_id): raise ValueError("unknown task_id")
    if after_seq < 0: raise ValueError("after_seq must be non-negative")
    events = _task_store.events(task_id, after_seq=after_seq)
    return {"task_id": task_id, "events": [_public_event(event) for event in events], "next_after_seq": events[-1]["seq"] if events else after_seq}


@server.tool(name="cancel_task", description="Cancel a queued, running, or waiting high-level mobile task.")
async def cancel_task(task_id: str) -> dict[str, object]:
    task = _task_store.get(task_id)
    if not task: raise ValueError("unknown task_id")
    if task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        task.cancellation_requested = True
        cancelled_subtasks = []
        if task.status == TaskStatus.WAITING_FOR_USER:
            # There is no worker claim while waiting, hence no active mutation
            # to resolve.  Finish the cancellation durably instead of leaving
            # an abandoned approval request indefinitely waiting.
            task.status, task.finished_at = TaskStatus.CANCELLED, datetime.now(UTC)
            for subtask in task.subtasks:
                if subtask.status in {
                    SubtaskStatus.QUEUED, SubtaskStatus.RUNNING, SubtaskStatus.WAITING_FOR_USER,
                }:
                    subtask.status = SubtaskStatus.CANCELLED
                    subtask.finished_at = task.finished_at
                    cancelled_subtasks.append(subtask)
        _task_store.save(task)
        _task_store.event(task.id, "TASK_CANCELLATION_REQUESTED", {})
        if task.status == TaskStatus.CANCELLED:
            for subtask in cancelled_subtasks:
                _task_store.event(task.id, "SUBTASK_CANCELLED", {
                    "subtask_id": subtask.id,
                    "position": subtask.position,
                })
            _task_store.event(task.id, "TASK_CANCELLED", {})
    return {"task_id": task.id, "status": task.status.value, "cancellation_requested": task.cancellation_requested}


@server.tool(name="answer_task", description="Provide information requested by a waiting task; no UI action is exposed.")
async def answer_task(task_id: str, question_id: str, answer: str) -> dict[str, object]:
    task = _task_store.get(task_id)
    if not task: raise ValueError("unknown task_id")
    if task.status != TaskStatus.WAITING_FOR_USER: raise ValueError("task is not waiting for user input")
    if not task.waiting_question or task.waiting_question.get("id") != question_id: raise ValueError("unknown question_id")
    question = _question_spec(task.waiting_question)
    normalized_answer = validate_question_answer(question, answer)
    task_answer = TaskInputAnswer(
        question_id=question.id,
        prompt_key=question.prompt_key,
        answer=normalized_answer,
        effect=question.answer_effect,
        answered_at=datetime.now(UTC).isoformat(),
    )
    task.input_history.append(task_answer)
    active_subtask = next((item for item in task.subtasks if item.id == task.active_subtask_id), None)
    if active_subtask:
        active_subtask.input_history.append(task_answer)
    task.agent_context.setdefault("answers", []).append({
        "question_id": question_id,
        "prompt_key": question.prompt_key,
        "answer": normalized_answer,
        "effect": question.answer_effect.model_dump(mode="json"),
    })
    if question.answer_effect.kind in {
        AnswerEffectKind.AUTHORIZE_OPERATION,
        AnswerEffectKind.AUTHORIZE_INFORMATION,
    }:
        if normalized_answer == "allow":
            if question.answer_effect.kind == AnswerEffectKind.AUTHORIZE_OPERATION:
                task.agent_context["lifecycle_authorization"] = {
                    "task_id": task.id, "question_id": question_id,
                    "operation": question.operation or question.answer_effect.parameter,
                    "package": question.package, "consumed": False,
                }
            else:
                authorizations = task.agent_context.setdefault("information_authorizations", {})
                for key in question.answer_effect.parameter.split(","):
                    authorizations[key] = {"question_id": question.id, "consumed": False}
        else:
            task.agent_context.setdefault("denied_operations", []).append({
                "operation": question.operation or question.answer_effect.parameter,
                "package": question.package,
                "question_id": question.id,
            })
            task.status = TaskStatus.FAILED
            task.failure_reason = (
                "ORIENTATION_BLOCKED_BY_USER_DENIAL"
                if question.answer_effect.kind == AnswerEffectKind.AUTHORIZE_OPERATION
                else "SENSITIVE_INFORMATION_DENIED"
            )
            task.finished_at = datetime.now(UTC)
            if active_subtask:
                active_subtask.status = SubtaskStatus.FAILED
                active_subtask.failure_reason = task.failure_reason
                active_subtask.finished_at = task.finished_at
    elif question.answer_effect.kind == AnswerEffectKind.SELECT_SEMANTIC_OPTION:
        task.agent_context.setdefault("semantic_selections", {})[question.answer_effect.parameter] = normalized_answer
    else:
        if active_subtask and active_subtask.task_spec:
            active_subtask.task_spec = apply_answer_effect(active_subtask.task_spec, question, normalized_answer)
            active_subtask.contract_status = contract_status_for(active_subtask.task_spec)
            active_subtask.contract_errors = []
        else:
            task.task_spec = apply_answer_effect(task.task_spec, question, normalized_answer)
            task.contract_status = contract_status_for(task.task_spec)
            task.contract_errors = []
    if task.status != TaskStatus.FAILED:
        task.status = TaskStatus.QUEUED
        if active_subtask:
            active_subtask.status = SubtaskStatus.RUNNING
        task.agent_context["resume_requires_fresh_observation"] = question.id
    task.waiting_reason, task.waiting_question = None, None
    _task_store.save(task)
    if active_subtask and active_subtask.status == SubtaskStatus.FAILED:
        _task_store.event(task.id, "SUBTASK_FAILED", {
            "subtask_id": active_subtask.id,
            "position": active_subtask.position,
            "reason": active_subtask.failure_reason,
        })
    _task_store.event(task.id, "USER_ANSWER_RECEIVED", {
        "question_id": question_id,
        "prompt_key": question.prompt_key,
        "answer_kind": question.type.value,
        "selected_option": normalized_answer if question.type != QuestionType.TEXT else None,
        "effect": question.answer_effect.kind.value,
        "subtask_id": active_subtask.id if active_subtask else None,
    })
    return {"task_id": task.id, "status": task.status.value}


def _question_spec(payload: dict[str, object]) -> QuestionSpec:
    """Read current questions and migrate pre-schema lifecycle questions."""
    if "answer_schema" in payload and "answer_effect" in payload:
        return QuestionSpec.model_validate(payload)
    options = [
        QuestionOption(id=str(option), label=str(option).title())
        for option in payload.get("options", []) if isinstance(option, str)
    ]
    operation = str(payload.get("operation") or "reset_app_task")
    return QuestionSpec(
        id=str(payload["id"]),
        type=QuestionType.APPROVAL,
        reason="LIFECYCLE_RECOVERY_REQUIRES_APPROVAL",
        prompt_key=f"approve:{operation}:{payload.get('package') or ''}",
        text=str(payload.get("text") or "Approval required"),
        options=options,
        answer_schema=AnswerSchema(type=QuestionType.APPROVAL),
        answer_effect=AnswerEffect(kind=AnswerEffectKind.AUTHORIZE_OPERATION, parameter=operation),
        operation=operation,
        package=str(payload["package"]) if payload.get("package") else None,
    )


def _public_result(task) -> dict[str, object] | None:
    if not task.result:
        if task.status == TaskStatus.FAILED:
            if task.failure_reason in {"NO_VERIFIABLE_COMPLETION_CRITERIA", "TASK_CONTRACT_INCOMPLETE"}:
                return {
                    "status": "failed",
                    "failure": {
                        "category": "UNSUPPORTED_TASK",
                        "reason": task.failure_reason,
                        "message": (
                            "The task could not be executed because it has no observable completion criteria."
                            if task.failure_reason == "NO_VERIFIABLE_COMPLETION_CRITERIA"
                            else "The task could not be converted into a complete, safe execution contract."
                        ),
                        "recoverable": True,
                    },
                }
            if task.failure_reason == "ORIENTATION_BLOCKED_BY_PERSISTED_APP_STATE":
                return {
                    "status": "failed",
                    "failure": {
                        "category": "SAFETY_BLOCKED",
                        "reason": "PERSISTED_FOREIGN_APP_STATE",
                        "message": "The target app persistently restores unrelated content. Continuing would require destructive application-state reset.",
                        "recoverable": False,
                    },
                }
            if task.failure_reason == "ORIENTATION_BLOCKED_BY_USER_DENIAL":
                return {
                    "status": "failed",
                    "failure": {
                        "category": "SAFETY_BLOCKED",
                        "reason": "USER_DENIED_RECOVERY",
                        "message": "The requested recovery operation was denied; no Android action was executed.",
                        "recoverable": False,
                    },
                }
            if task.failure_reason == "SENSITIVE_INFORMATION_DENIED":
                return {
                    "status": "failed",
                    "failure": {
                        "category": "SAFETY_BLOCKED",
                        "reason": "USER_DENIED_INFORMATION_DISCLOSURE",
                        "message": "The requested personal information was not read or returned.",
                        "recoverable": False,
                    },
                }
            return {"status": "failed", "failure": {"category": "AGENT", "message": task.failure_reason or "Task failed", "recoverable": False}}
        return None
    if task.subtasks:
        return _public_subtask_result(task)
    requirements = task.result.get("requirements", [])
    safe_requirements = []
    if isinstance(requirements, list):
        for requirement in requirements:
            if not isinstance(requirement, dict):
                continue
            evidence = requirement.get("evidence")
            safe_evidence = _public_evidence(evidence) if isinstance(evidence, dict) else None
            safe_requirements.append({
                "key": requirement.get("key"),
                "kind": requirement.get("kind"),
                "status": requirement.get("status"),
                "evidence": safe_evidence,
            })
    raw_observations = task.result.get("observations", {})
    allowed_outputs = {output.key for output in task.task_spec.requested_outputs}
    observations: dict[str, object] = {}
    if isinstance(raw_observations, dict):
        for key in allowed_outputs:
            value = raw_observations.get(key)
            if not isinstance(value, dict):
                continue
            observations[key] = {
                "status": value.get("status") if value.get("status") in {"observed", "unknown", "unavailable"} else "unknown",
                "value": _safe_observed_value(value.get("value")),
            }
    required_keys = {requirement.key for requirement in generate_requirements(task.task_spec) if requirement.required}
    result_keys = {str(requirement.get("key")) for requirement in safe_requirements}
    durable_requirements_complete = all(
        task.requirements.get(key) is not None and task.requirements[key].value == "satisfied"
        for key in required_keys
    )
    evidence_complete = (
        bool(required_keys)
        and required_keys == result_keys
        and all(
            requirement.get("status") == "satisfied"
            and isinstance(requirement.get("evidence"), dict)
            and bool(requirement["evidence"].get("source"))
            for requirement in safe_requirements
        )
        and durable_requirements_complete
    )
    outputs_complete = all(
        not output.required
        or isinstance(observations.get(output.key), dict)
        and observations[output.key].get("status") == "observed"
        for output in task.task_spec.requested_outputs
    )
    verified = (
        bool(task.result.get("verified"))
        and task.status == TaskStatus.SUCCEEDED
        and evidence_complete
        and outputs_complete
    )
    return {
        "status": task.status.value,
        "summary": task.result.get("summary"),
        "verified": verified,
        "observations": observations,
        "answers": [
            {"key": key, "value": value.get("value")}
            for key, value in observations.items()
            if value.get("status") == "observed"
        ],
        "requirements": safe_requirements,
        "steps": task.result.get("steps", task.step_number),
    }


def _public_subtask_result(task) -> dict[str, object]:
    """Return bounded verified outcomes, never per-step Android state."""
    items: list[dict[str, object]] = []
    for subtask in task.subtasks:
        result = subtask.result or {}
        spec = subtask.task_spec
        required_keys = {
            requirement.key for requirement in generate_requirements(spec) if requirement.required
        } if spec else set()
        requirements_verified = bool(required_keys) and all(
            subtask.requirements.get(key) is not None
            and subtask.requirements[key].value == "satisfied"
            and isinstance(subtask.requirement_evidence.get(key), dict)
            for key in required_keys
        )
        raw_observations = result.get("observations", {})
        observations: dict[str, object] = {}
        if spec and isinstance(raw_observations, dict):
            for output in spec.requested_outputs:
                value = raw_observations.get(output.key)
                if isinstance(value, dict):
                    observations[output.key] = {
                        "status": value.get("status") if value.get("status") in {
                            "observed", "unknown", "unavailable",
                        } else "unknown",
                        "value": _safe_observed_value(value.get("value")),
                    }
        outputs_verified = bool(spec) and all(
            not output.required
            or isinstance(observations.get(output.key), dict)
            and observations[output.key].get("status") == "observed"
            for output in spec.requested_outputs
        )
        verified = (
            subtask.status == SubtaskStatus.SUCCEEDED
            and bool(result.get("verified"))
            and requirements_verified
            and outputs_verified
        )
        items.append({
            "id": subtask.id,
            "position": subtask.position,
            "status": subtask.status.value,
            "summary": result.get("summary"),
            "verified": verified,
            "observations": observations,
            "steps": subtask.step_number,
            "failure_reason": subtask.failure_reason,
        })
    return {
        "status": task.status.value,
        "summary": task.result.get("summary"),
        "verified": bool(items) and all(bool(item["verified"]) for item in items),
        "subtasks": items,
        "steps": task.step_number,
    }


def _public_contract(task) -> dict[str, object]:
    spec = task.task_spec
    return {
        "version": spec.contract_version,
        "status": task.contract_status.value,
        "intent": spec.intent,
        "target": {"app": spec.app, "package": spec.app_package},
        "policy": spec.policy.model_dump(mode="json") if spec.policy else None,
        "requested_outputs": [output.model_dump(mode="json") for output in spec.requested_outputs],
        "information_requests": [
            {
                "key": request.key,
                "question": request.question,
                "sensitivity": request.sensitivity.value,
            }
            for request in spec.information_requests
        ],
        "completion": [
            {"type": item.type, "field_role": item.field_role, "output_key": item.output_key}
            for item in spec.completion
        ],
        "errors": task.contract_errors,
    }


def _public_evidence(evidence: dict[str, object]) -> dict[str, object]:
    source = evidence.get("source")
    safe_source = {}
    if isinstance(source, dict):
        allowed = {
            "snapshot_id", "package", "semantic_role", "label", "observed_value", "confidence", "verification",
        }
        safe_source = {key: source[key] for key in allowed if key in source}
        if "observed_value" in safe_source:
            safe_source["observed_value"] = _safe_observed_value(safe_source["observed_value"])
    return {
        "requirement": evidence.get("requirement"),
        "kind": evidence.get("kind"),
        "output_key": evidence.get("output_key"),
        "source": safe_source,
    }


def _safe_observed_value(value: object) -> object:
    """Allow semantic scalar output, never arbitrary nested UI payloads."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, list) and all(item is None or isinstance(item, (bool, int, float, str)) for item in value):
        return [item[:4000] if isinstance(item, str) else item for item in value[:100]]
    if isinstance(value, dict) and set(value).issubset({"title", "items", "value", "state"}):
        return {key: _safe_observed_value(item) for key, item in value.items()}
    return None


def _public_event(event: dict[str, object]) -> dict[str, object]:
    # Accessibility snapshots and executable refs deliberately remain internal.
    event_type = str(event["event_type"]).lower()
    payload = event.get("payload")
    return {
        "seq": event["seq"],
        "timestamp": event["timestamp"],
        "type": event_type,
        "worker_id": event["worker_id"],
        "payload": _safe_public_event_value(payload),
    }


def _safe_public_event_value(value: object) -> object:
    """Keep semantic progress while stripping snapshot-local execution data."""
    if isinstance(value, dict):
        blocked = {
            "snapshot", "snapshot_id", "target", "target_ref", "raw_accessibility_tree",
            "before", "after", "intermediate",
        }
        return {
            str(key): _safe_public_event_value(item)
            for key, item in value.items()
            if str(key) not in blocked and not str(key).startswith("raw_")
        }
    if isinstance(value, list):
        return [_safe_public_event_value(item) for item in value[:100]]
    if isinstance(value, str):
        return re.sub(r"\bs\d+:e\d+\b", "[ui-ref]", value, flags=re.I)[:4000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4000]


@server.tool(name="get_device_status", description="Read the selected Android runtime status without performing a mutation.")
async def get_device_status(serial: str | None = None, backend: Backend = "portal-adb") -> dict[str, object]:
    """Read worker-persisted telemetry; this process never opens ADB itself."""
    settings = Settings.from_env()
    selected_serial = serial or settings.device_serial
    if not selected_serial:
        return {"connected": False, "backend": backend, "reason": "no configured device serial"}
    status = _task_store.get_device_status(selected_serial)
    if not status:
        return {"connected": False, "backend": backend, "reason": "worker has not published device status"}
    return {"connected": bool(status.get("device_available")), "backend": backend, **status}


def _state_payload(state) -> dict[str, object]:
    """Return the compact semantic state exposed to MCP clients."""
    return {
        "app": state.app,
        "screen_hint": state.screen_hint,
        "loading": state.loading,
        "dialog": state.dialog.model_dump(mode="json") if state.dialog else None,
        "elements": [
            {
                "id": element.id,
                "role": element.role,
                "label": element.label,
                "hint": element.hint,
                "enabled": element.enabled,
                "clickable": element.clickable,
                "editable": element.editable,
                "checked": element.checked if element.checkable else None,
            }
            for element in state.elements
            if element.visible
        ],
    }


async def mobile_agent_inspect(
    serial: str | None = None,
    backend: Backend = "bridge",
) -> dict[str, object]:
    """Inspect a connected phone without changing it."""
    settings = Settings.from_env()
    try:
        async with _device(backend, settings, serial) as device:
            state = normalize(await device.observe())
        page = build_action_page(
            state,
            "",
            page_size=settings.action_page_size,
            max_pages=settings.max_action_pages,
        )
    except Exception as error:
        raise RuntimeError(f"Device inspection failed: {error}") from error
    return {
        "state": _state_payload(state),
        "actions": [action.model_dump(mode="json") for action in page.actions],
        "page": page.model_dump(mode="json", exclude={"actions"}),
    }


async def mobile_agent_decide(
    goal: str,
    provider: ProviderName = "jev",
    serial: str | None = None,
    backend: Backend = "bridge",
) -> dict[str, object]:
    """Make a dry-run decision using only controller-generated actions."""
    settings = Settings.from_env()
    decision_provider = _provider(provider, settings)
    try:
        async with _device(backend, settings, serial) as device:
            state = normalize(await device.observe())
        page = build_action_page(
            state,
            goal,
            page_size=settings.action_page_size,
            max_pages=settings.max_action_pages,
        )
        decision = await decision_provider.decide(goal, state, page.actions)
    except Exception as error:
        raise RuntimeError(f"Mobile decision failed: {error}") from error
    return {
        "state": _state_payload(state),
        "actions": [action.model_dump(mode="json") for action in page.actions],
        "decision": decision.model_dump(mode="json"),
        "probability_margin": probability_margin(decision.probabilities or {}),
    }


async def mobile_agent_run(
    goal: str,
    provider: ProviderName = "jev",
    serial: str | None = None,
    backend: Backend = "bridge",
    max_steps: int = 20,
) -> dict[str, object]:
    """Run the controller with its normal confidence, loop, and risk guards."""
    if not 1 <= max_steps <= 100:
        raise ValueError("max_steps must be between 1 and 100")
    settings = Settings.from_env()
    settings = replace(settings, max_steps=max_steps)
    decision_provider = _provider(provider, settings)
    try:
        async with _device(backend, settings, serial) as device:
            trace = TraceWriter(settings.trace_dir)
            result = await MobileController(device, decision_provider, settings, trace).run(goal)
    except Exception as error:
        raise RuntimeError(f"Mobile task failed: {error}") from error
    return {
        "status": result.status.value,
        "task_id": result.task_id,
        "message": result.message,
        "trace_path": str(trace.path),
    }


class _McpHttpSecurityMiddleware(BaseHTTPMiddleware):
    """Restrict internal HTTP MCP access without weakening Host validation."""

    def __init__(self, app, *, origins: set[str], bearer_token: str | None) -> None:
        super().__init__(app); self.origins, self.bearer_token = origins, bearer_token

    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/health":
            origin = request.headers.get("origin")
            if origin and origin not in self.origins:
                return JSONResponse({"error": "origin not allowed"}, status_code=403)
            if self.bearer_token and request.headers.get("authorization") != f"Bearer {self.bearer_token}":
                return JSONResponse({"error": "bearer token required"}, status_code=401)
        return await call_next(request)


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": TaskStore().healthcheck()})


def run_streamable_http(host: str, port: int) -> None:
    """Serve the same six-tool MCP instance as stateless Streamable HTTP."""
    allowed_hosts = {item.strip() for item in os.getenv("JEV_MOBILE_MCP_ALLOWED_HOSTS", "jev-mobile-mcp,localhost,127.0.0.1").split(",") if item.strip()}
    allowed_origins = {item.strip() for item in os.getenv("JEV_MOBILE_MCP_ALLOWED_ORIGINS", "").split(",") if item.strip()}
    # JSON responses are the preferred stateless deployment.  Some clients
    # require the standard streaming response framing, so compatibility can be
    # selected explicitly without changing task or tool semantics.
    json_response = os.getenv("JEV_MOBILE_MCP_JSON_RESPONSE", "true").casefold() in {"1", "true", "yes"}
    app = server.streamable_http_app(streamable_http_path="/mcp", json_response=json_response, stateless_http=True, host=host)
    app.router.routes.append(Route("/health", _health, methods=["GET"]))
    app.add_middleware(_McpHttpSecurityMiddleware, origins=allowed_origins, bearer_token=os.getenv("JEV_MOBILE_MCP_BEARER_TOKEN") or None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    """Run Jev Mobile MCP over stdio or stateless Streamable HTTP."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8851)
    args = parser.parse_args()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        run_streamable_http(args.host, args.port)


if __name__ == "__main__":
    main()

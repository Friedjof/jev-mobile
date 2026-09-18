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
from .task_store import TaskStatus, TaskStore
from .tasks import task_spec_from_goal
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
async def start_task(instruction: str) -> dict[str, object]:
    spec = task_spec_from_goal(instruction)
    # A separate durable worker claims queued tasks. MCP never owns device execution.
    task = _task_store.create(instruction, spec)
    return {"task_id": task.id, "status": task.status.value}


@server.tool(name="get_task", description="Return concise parent-agent-friendly state for a delegated task.")
async def get_task(task_id: str) -> dict[str, object]:
    task = _task_store.get(task_id)
    if not task: raise ValueError("unknown task_id")
    requirements = list(task.requirements.values())
    return {
        "task_id": task.id,
        "status": task.status.value,
        "instruction": task.instruction,
        "current_subgoal": task.current_subgoal,
        "progress": {"requirements_satisfied": sum(status.value == "satisfied" for status in requirements), "requirements_total": len(requirements)},
        "waiting_for_user": task.waiting_question or ({"text": task.waiting_reason} if task.waiting_reason else None),
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
        if task.status == TaskStatus.WAITING_FOR_USER:
            # There is no worker claim while waiting, hence no active mutation
            # to resolve.  Finish the cancellation durably instead of leaving
            # an abandoned approval request indefinitely waiting.
            task.status, task.finished_at = TaskStatus.CANCELLED, datetime.now(UTC)
        _task_store.save(task)
        _task_store.event(task.id, "TASK_CANCELLATION_REQUESTED", {})
        if task.status == TaskStatus.CANCELLED:
            _task_store.event(task.id, "TASK_CANCELLED", {})
    return {"task_id": task.id, "status": task.status.value, "cancellation_requested": task.cancellation_requested}


@server.tool(name="answer_task", description="Provide information requested by a waiting task; no UI action is exposed.")
async def answer_task(task_id: str, question_id: str, answer: str) -> dict[str, object]:
    task = _task_store.get(task_id)
    if not task: raise ValueError("unknown task_id")
    if task.status != TaskStatus.WAITING_FOR_USER: raise ValueError("task is not waiting for user input")
    if not task.waiting_question or task.waiting_question.get("id") != question_id: raise ValueError("unknown question_id")
    task.agent_context.setdefault("answers", []).append({"question_id": question_id, "answer": answer})
    if task.waiting_question.get("type") in {"approve_lifecycle_recovery", "approve_lifecycle_reset"} and answer.casefold() == "allow":
        task.agent_context["lifecycle_authorization"] = {"task_id": task.id, "question_id": question_id, "operation": task.waiting_question.get("operation"), "package": task.waiting_question.get("package"), "consumed": False}
    task.status, task.waiting_reason, task.waiting_question = TaskStatus.QUEUED, None, None
    _task_store.save(task)
    _task_store.event(task.id, "USER_ANSWER_RECEIVED", {"question_id": question_id, "answer": answer.casefold()})
    return {"task_id": task.id, "status": task.status.value}


def _public_result(task) -> dict[str, object] | None:
    if not task.result:
        if task.status == TaskStatus.FAILED:
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
            return {"status": "failed", "failure": {"category": "AGENT", "message": task.failure_reason or "Task failed", "recoverable": False}}
        return None
    return {"status": task.status.value, "summary": task.result.get("summary"), "verified": task.status == TaskStatus.SUCCEEDED, "steps": task.result.get("steps", task.step_number)}


def _public_event(event: dict[str, object]) -> dict[str, object]:
    # Accessibility snapshots and executable refs deliberately remain internal.
    event_type = str(event["event_type"]).lower()
    return {"seq": event["seq"], "timestamp": event["timestamp"], "type": event_type, "worker_id": event["worker_id"], "payload": event["payload"]}


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

"""Stdio MCP server for the standalone Jev Mobile controller.

The server deliberately exposes high-level controller operations rather than
ADB commands or arbitrary taps. The controller constructs and validates every
candidate action before a provider can choose it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from mcp.server.mcpserver import MCPServer

from .actions.builder import build_action_page
from .config import Settings
from .controller.loop import MobileController
from .device.accessibility_adb import AccessibilityAdbAdapter
from .device.mobile_mcp import MobileMcpAdapter
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

Backend = Literal["mobile-mcp", "bridge"]
ProviderName = Literal["jev", "heuristic", "mock", "llm", "system-one-llm"]


def _device(backend: Backend, settings: Settings, serial: str | None):
    selected_serial = serial or settings.device_serial
    if backend == "mobile-mcp":
        return MobileMcpAdapter(settings.mobile_mcp_command, selected_serial)
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


@server.tool(
    name="mobile_agent_inspect",
    description="Read the current Android UI and return a compact semantic state and valid actions. Does not act.",
)
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


@server.tool(
    name="mobile_agent_decide",
    description="Choose exactly one valid Android action through a structured provider. Does not act.",
)
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


@server.tool(
    name="mobile_agent_run",
    description=(
        "Run a bounded Android task through validated candidate actions. "
        "External-effect and sensitive actions are escalated rather than executed."
    ),
)
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


def main() -> None:
    """Run Jev Mobile as a stdio MCP server."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

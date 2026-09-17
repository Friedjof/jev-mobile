"""Standalone command-line interface for inspection and safe controller runs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
import typer
from .actions.builder import build_candidates
from .cli_output import RunReporter, console, render_actions, render_decision, render_result, render_state
from .config import Settings
from .controller.loop import MobileController
from .device.mobile_mcp import MobileMcpAdapter
from .providers.heuristic import HeuristicProvider
from .providers.jev import JevProvider
from .providers.llm import LLMProvider
from .providers.mock import MockProvider
from .providers.jev import probability_margin
from .providers.system_one_llm import SystemOneLLMProvider
from .providers.base import ProviderUnavailable
from .state.normalize import normalize
from .tracing.trace import TraceWriter

app = typer.Typer(no_args_is_help=True)


def _provider(name: str, settings: Settings):
    if name == "heuristic": return HeuristicProvider()
    if name == "mock": return MockProvider()
    if name == "llm": return LLMProvider(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    if name == "jev": return JevProvider(settings.typesafe_api_key)
    if name == "system-one-llm": return SystemOneLLMProvider(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    raise typer.BadParameter("provider must be mock, heuristic, llm, system-one-llm, or jev")


@app.command()
def devices() -> None:
    """List Android devices through the configured Mobile MCP server."""
    async def run() -> None:
        settings = Settings.from_env()
        async with MobileMcpAdapter(settings.mobile_mcp_command, connect=False) as device:
            devices = await device.list_devices()
            if not devices:
                console.print("[yellow]No devices found.[/]")
                return
            from rich.table import Table
            table = Table(title="Android devices")
            table.add_column("Serial", style="cyan")
            table.add_column("Name")
            table.add_column("Android")
            table.add_column("Status")
            for item in devices:
                table.add_row(item.get("id", "?"), item.get("name", "?"), item.get("version", "?"), item.get("state", "?"))
            console.print(table)
    asyncio.run(run())


@app.command()
def inspect(serial: str | None = typer.Option(None)) -> None:
    """Observe without performing an action, then print semantic state and candidates."""
    async def run() -> None:
        settings = Settings.from_env()
        async with MobileMcpAdapter(settings.mobile_mcp_command, serial or settings.device_serial) as device:
            state = normalize(await device.observe())
            render_state(state)
            render_actions(build_candidates(state, ""))
    asyncio.run(run())


@app.command()
def decide(
    goal: str = typer.Option(...),
    provider: str = typer.Option("jev"),
    serial: str | None = typer.Option(None),
) -> None:
    """Observe and obtain a decision without changing the phone."""
    async def evaluate() -> None:
        settings = Settings.from_env()
        decision_provider = _provider(provider, settings)
        if provider == "jev" and not settings.typesafe_api_key:
            typer.echo("Decision unavailable: TYPESAFE_API_KEY is not configured", err=True)
            raise typer.Exit(2)
        async with MobileMcpAdapter(settings.mobile_mcp_command, serial or settings.device_serial) as device:
            state = normalize(await device.observe())
            actions = build_candidates(state, goal)
            try:
                result = await decision_provider.decide(goal, state, actions)
            except ProviderUnavailable as error:
                typer.echo(f"Decision unavailable: {error}", err=True)
                raise typer.Exit(2) from error
            console.print("[bold blue]Dry run — no phone action will be performed.[/]")
            render_state(state)
            render_actions(actions)
            render_decision(provider, result, probability_margin(result.probabilities or {}))
    asyncio.run(evaluate())


@app.command()
def run(
    goal: str = typer.Option(...),
    provider: str = typer.Option("heuristic"),
    serial: str | None = typer.Option(None),
    start_app: str | None = typer.Option(None, "--start-app", help="Launch a known safe starting app before the Jev loop."),
    confidence_threshold: float | None = typer.Option(None, "--confidence-threshold", min=0.0, max=1.0),
    minimum_probability_margin: float | None = typer.Option(None, "--minimum-probability-margin", min=0.0, max=1.0),
    max_steps: int | None = typer.Option(None, "--max-steps", min=1),
) -> None:
    """Run a bounded safe control task."""
    async def execute() -> None:
        settings = Settings.from_env()
        if confidence_threshold is not None:
            settings = replace(settings, confidence_threshold=confidence_threshold)
        if minimum_probability_margin is not None:
            settings = replace(settings, minimum_probability_margin=minimum_probability_margin)
        if max_steps is not None:
            settings = replace(settings, max_steps=max_steps)
        async with MobileMcpAdapter(settings.mobile_mcp_command, serial or settings.device_serial) as device:
            if start_app:
                console.print(f"[cyan]Bootstrap:[/] launching {start_app}")
                await device.launch_app(start_app)
            trace = TraceWriter(settings.trace_dir)
            result = await MobileController(device, _provider(provider, settings), settings, trace, RunReporter()).run(goal)
            render_result(result.status.value, result.message, trace.path)
    asyncio.run(execute())

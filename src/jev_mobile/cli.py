"""Standalone command-line interface for inspection and safe controller runs."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import typer
from .actions.builder import build_action_page
from .cli_output import RunReporter, console, render_actions, render_decision, render_result, render_state
from .config import Settings
from .controller.loop import MobileController
from .device.mobile_mcp import MobileMcpAdapter, MobileMcpError
from .device.accessibility_adb import AccessibilityAdbAdapter
from .device.portal_adb import PortalAdbDeviceAdapter
from .providers.heuristic import HeuristicProvider
from .providers.jev import JevProvider
from .providers.llm import LLMProvider
from .providers.mock import MockProvider
from .providers.jev import probability_margin
from .providers.system_one_llm import SystemOneLLMProvider
from .providers.base import ProviderUnavailable
from .providers.planned import PlanGuidedProvider
from .escalation.planner import LLMPlanner
from .state.normalize import normalize
from .tracing.trace import TraceWriter
from .agent.mobile_agent import MobileAgent
from .runtime.worker import DurableWorker
from .task_store import TaskStatus, TaskStore
from .tasks import task_spec_from_goal

app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
app.add_typer(task_app, name="task")


def _provider(name: str, settings: Settings):
    if name == "heuristic": return HeuristicProvider()
    if name == "mock": return MockProvider()
    if name == "llm": return LLMProvider(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.agent_system_prompt)
    if name == "jev": return JevProvider(settings.typesafe_api_key, settings.agent_system_prompt)
    if name == "system-one-llm": return SystemOneLLMProvider(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.agent_system_prompt)
    raise typer.BadParameter("provider must be mock, heuristic, llm, system-one-llm, or jev")


def _device(backend: str, settings: Settings, serial: str | None):
    """Create a backend without leaking transport details into controller code."""
    selected_serial = serial or settings.device_serial
    if backend == "mobile-mcp":
        return MobileMcpAdapter(settings.mobile_mcp_command, selected_serial)
    if backend == "bridge":
        if not selected_serial:
            raise typer.BadParameter("--serial (or MOBILE_DEVICE_SERIAL) is required for --backend bridge")
        return AccessibilityAdbAdapter(selected_serial, port=settings.bridge_port, bridge_token=settings.bridge_token)
    if backend == "portal-adb":
        if not selected_serial:
            raise typer.BadParameter("--serial (or MOBILE_DEVICE_SERIAL) is required for --backend portal-adb")
        return PortalAdbDeviceAdapter(selected_serial)
    raise typer.BadParameter("backend must be mobile-mcp, bridge, or portal-adb")


@app.command()
def doctor(
    serial: str | None = typer.Option(None),
    backend: str = typer.Option("portal-adb", "--backend"),
) -> None:
    """Diagnose one backend without selecting a fallback transport."""
    async def command(*args: str) -> tuple[bool, str]:
        process = await asyncio.create_subprocess_exec("adb", "-s", selected, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        return process.returncode == 0, (stdout or stderr).decode(errors="replace").strip()

    async def diagnose() -> int:
        settings = Settings.from_env()
        nonlocal selected
        selected = serial or settings.device_serial
        if not selected:
            typer.echo("ERROR  serial is required", err=True); return 2
        checks: list[tuple[str, str, str]] = []
        ok, output = await command("get-state")
        checks.append(("ADB device", "OK" if ok and output == "device" else "ERROR", output))
        if backend != "portal-adb":
            checks.append(("Backend", "WARNING", "only portal-adb has full local diagnostics"))
        ok, output = await command("shell", "pm", "path", "com.mobilerun.portal")
        checks.append(("Portal package", "OK" if ok and "package:" in output else "ERROR", output))
        ok, output = await command("shell", "settings", "get", "secure", "enabled_accessibility_services")
        checks.append(("Portal accessibility", "OK" if "com.mobilerun.portal.service.MobilerunAccessibilityService" in output else "ERROR", output))
        ok, output = await command("shell", "ime", "list", "-s")
        checks.append(("Portal keyboard", "OK" if "com.mobilerun.portal/.input.MobilerunKeyboardIME" in output else "WARNING", output))
        try:
            async with _device("portal-adb", settings, selected) as device:
                raw = await device.observe()
                image = await device.screenshot()
            checks.extend([("Portal ContentProvider", "OK", "reachable"),
                           ("state_full observation", "OK", f"{raw.package} / {len(raw.elements)} nodes"),
                           ("screenshot", "OK" if image.startswith(b"\x89PNG") else "WARNING", f"{len(image)} bytes")])
        except Exception as error:
            checks.append(("Portal runtime", "ERROR", str(error)))
        for label, status, detail in checks: typer.echo(f"{status:<7} {label}: {detail}")
        return 0 if not any(status == "ERROR" for _, status, _ in checks) else 2

    selected = ""
    raise typer.Exit(asyncio.run(diagnose()))


@app.command()
def health() -> None:
    """Check worker-local runtime dependencies without probing Android."""
    try:
        if not TaskStore().healthcheck():
            raise RuntimeError("TaskStore healthcheck failed")
    except Exception as error:
        typer.echo(f"ERROR worker runtime: {error}", err=True)
        raise typer.Exit(2) from error
    typer.echo("OK worker runtime and TaskStore")


@app.command()
def worker(serial: str | None = typer.Option(None), backend: str = typer.Option("portal-adb"), provider: str = typer.Option("jev")) -> None:
    """Run the long-lived owner of one physical Android device."""
    async def serve() -> None:
        settings = Settings.from_env(); selected = serial or settings.device_serial
        if not selected: raise typer.BadParameter("--serial (or MOBILE_DEVICE_SERIAL) is required")
        store = TaskStore()
        agent = MobileAgent(store, lambda: _device(backend, settings, selected), _provider(provider, settings), settings)
        await DurableWorker(store, agent, selected).run_forever()
    asyncio.run(serve())


@task_app.command("start")
def task_start(instruction: str) -> None:
    task = TaskStore().create(instruction, task_spec_from_goal(instruction))
    typer.echo(f"{task.id}\t{task.status.value}")


@task_app.command("get")
def task_get(task_id: str) -> None:
    task = TaskStore().get(task_id)
    if not task: raise typer.BadParameter("unknown task")
    typer.echo(task.model_dump_json(indent=2))


@task_app.command("events")
def task_events(task_id: str) -> None:
    import json
    typer.echo(json.dumps(TaskStore().events(task_id), indent=2))


@task_app.command("report")
def task_report(task_id: str) -> None:
    """Render persisted task/events; it never talks to the Android device."""
    store = TaskStore(); task = store.get(task_id)
    if not task: raise typer.BadParameter("unknown task")
    typer.echo(f"Task {task.id}: {task.status.value}\nSubgoal: {task.current_subgoal or '-'}\nSteps: {task.step_number}")
    for event in store.events(task_id):
        payload = event["payload"]
        typer.echo(f"\n#{event['seq']} {event['event_type']} {payload}")
    if task.result: typer.echo(f"\nResult:\n{task.result}")


@task_app.command("cancel")
def task_cancel(task_id: str) -> None:
    store = TaskStore(); task = store.get(task_id)
    if not task: raise typer.BadParameter("unknown task")
    if task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        task.cancellation_requested = True; store.save(task); store.event(task_id, "TASK_CANCELLATION_REQUESTED", {})
    typer.echo(f"{task_id}\tcancellation_requested")


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
    try:
        asyncio.run(run())
    except MobileMcpError as error:
        typer.echo(f"Device unavailable: {error}", err=True)
        raise typer.Exit(2) from error


@app.command()
def inspect(
    serial: str | None = typer.Option(None),
    backend: str = typer.Option("mobile-mcp", "--backend", help="mobile-mcp or bridge"),
) -> None:
    """Observe without performing an action, then print semantic state and candidates."""
    async def run() -> None:
        settings = Settings.from_env()
        async with _device(backend, settings, serial) as device:
            state = normalize(await device.observe())
            render_state(state)
            page = build_action_page(state, "")
            render_actions(page.actions, page)
    try:
        asyncio.run(run())
    except MobileMcpError as error:
        typer.echo(f"Device unavailable: {error}", err=True)
        raise typer.Exit(2) from error


@app.command()
def decide(
    goal: str = typer.Option(...),
    provider: str = typer.Option("jev"),
    serial: str | None = typer.Option(None),
    backend: str = typer.Option("mobile-mcp", "--backend", help="mobile-mcp or bridge"),
) -> None:
    """Observe and obtain a decision without changing the phone."""
    async def evaluate() -> None:
        settings = Settings.from_env()
        decision_provider = _provider(provider, settings)
        if provider == "jev" and not settings.typesafe_api_key:
            typer.echo("Decision unavailable: TYPESAFE_API_KEY is not configured", err=True)
            raise typer.Exit(2)
        async with _device(backend, settings, serial) as device:
            state = normalize(await device.observe())
            page = build_action_page(state, goal, page_size=settings.action_page_size, max_pages=settings.max_action_pages)
            actions = page.actions
            try:
                result = await decision_provider.decide(goal, state, actions)
            except ProviderUnavailable as error:
                typer.echo(f"Decision unavailable: {error}", err=True)
                raise typer.Exit(2) from error
            console.print("[bold blue]Dry run — no phone action will be performed.[/]")
            render_state(state)
            render_actions(actions, page)
            render_decision(provider, result, probability_margin(result.probabilities or {}), actions)
    try:
        asyncio.run(evaluate())
    except MobileMcpError as error:
        typer.echo(f"Device unavailable: {error}", err=True)
        raise typer.Exit(2) from error


@app.command()
def run(
    goal: str = typer.Option(...),
    provider: str = typer.Option("heuristic"),
    serial: str | None = typer.Option(None),
    backend: str = typer.Option("mobile-mcp", "--backend", help="mobile-mcp or bridge"),
    start_app: str | None = typer.Option(None, "--start-app", help="Launch a known safe starting app before the Jev loop."),
    confidence_threshold: float | None = typer.Option(None, "--confidence-threshold", min=0.0, max=1.0),
    minimum_probability_margin: float | None = typer.Option(None, "--minimum-probability-margin", min=0.0, max=1.0),
    max_steps: int | None = typer.Option(None, "--max-steps", min=1),
    plan_first: bool = typer.Option(False, "--plan-first", help="Ask the configured LLM for a structured task plan before the first action."),
    plan_on_escalation: bool = typer.Option(False, "--plan-on-escalation", help="Let the configured LLM create one text-only recovery plan when the fast provider is uncertain."),
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
        decision_provider = _provider(provider, settings)
        if plan_on_escalation or plan_first:
            if not all((settings.llm_base_url, settings.llm_api_key, settings.llm_model)):
                typer.echo(
                    "LLM recovery planning requires LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL in .env",
                    err=True,
                )
                raise typer.Exit(2)
            planner = LLMPlanner(settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                                 settings.agent_system_prompt)
            decision_provider = PlanGuidedProvider(
                decision_provider, planner, confidence_threshold=settings.confidence_threshold,
                minimum_probability_margin=settings.minimum_probability_margin,
                max_recoveries=settings.max_plan_recoveries,
                plan_first=plan_first,
            )
        async with _device(backend, settings, serial) as device:
            if start_app:
                console.print(f"[cyan]Bootstrap:[/] launching {start_app}")
                await device.launch_app(start_app)
            trace = TraceWriter(settings.trace_dir)
            result = await MobileController(device, decision_provider, settings, trace, RunReporter()).run(goal)
            render_result(result.status.value, result.message, trace.path)
    try:
        asyncio.run(execute())
    except MobileMcpError as error:
        typer.echo(f"Device unavailable: {error}", err=True)
        raise typer.Exit(2) from error

"""Human-friendly Rich rendering for the standalone CLI."""

from __future__ import annotations

from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .actions.models import CandidateAction, Decision
from .state.models import SemanticState


console = Console()


def _labels(state: SemanticState, limit: int = 8) -> list[str]:
    content = [
        element.label for element in state.elements
        if element.visible and element.label and (element.bounds is None or element.bounds.y >= 100)
    ]
    return content[:limit]


def render_state(state: SemanticState, *, step: int | None = None, stabilize_ms: float | None = None) -> None:
    title = f"Step {step} · UI READY" if step else "Current UI"
    details = [f"[bold]App:[/] {state.app or 'unknown'}", f"[bold]Visible elements:[/] {len(state.elements)}"]
    if stabilize_ms is not None:
        details.append(f"[bold]Stable after:[/] {stabilize_ms:.0f} ms")
    labels = _labels(state)
    if labels:
        details.append("[bold]Visible:[/] " + " · ".join(f"{label!r}" for label in labels))
    console.print(Panel("\n".join(details), title=title, border_style="cyan"))


def render_actions(actions: list[CandidateAction]) -> None:
    table = Table(title="Valid actions", box=box.SIMPLE_HEAD, show_edge=False)
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Action")
    table.add_column("Risk", style="dim", no_wrap=True)
    for action in actions:
        table.add_row(action.id, action.label, action.risk.value)
    console.print(table)


def render_decision(provider: str, decision: Decision, margin: float | None) -> None:
    confidence = decision.confidence
    color = "green" if confidence >= 0.8 else "yellow" if confidence >= 0.5 else "red"
    body = Text()
    body.append(f"{provider.upper()} → {decision.action_id}\n", style="bold")
    body.append("Confidence: ")
    body.append(f"{confidence:.0%}", style=color)
    if margin is not None:
        body.append(f"    Margin: {margin:.0%}", style="cyan")
    metadata = decision.reasoning_metadata or {}
    if latency := metadata.get("latency_ms"):
        body.append(f"    Decision: {float(latency):.0f} ms", style="dim")
    console.print(Panel(body, title="Decision", border_style=color))


class RunReporter:
    """Render concise controller events while the JSONL trace remains complete."""

    def __call__(self, event: str, payload: dict[str, object]) -> None:
        if event == "started":
            console.print(Panel(str(payload["goal"]), title="Mobile task started", border_style="blue"))
        elif event == "state_ready":
            render_state(payload["state"], step=int(payload["step"]), stabilize_ms=float(payload["stabilize_ms"]))
        elif event == "candidates":
            render_actions(payload["actions"])
        elif event == "decision":
            render_decision(str(payload["provider"]), payload["decision"], payload["margin"])
        elif event == "action":
            console.print(f"[green]✓ Executing[/] {payload['action'].label}")
        elif event == "completed":
            console.print(Panel("The verified UI transition completed the task.", title="✓ Task completed", border_style="green"))
        elif event == "escalated":
            console.print(Panel(f"Reason: {payload['reason']}", title="⚠ Escalated", border_style="yellow"))


def render_result(status: str, message: str, trace_path: Path) -> None:
    color = "green" if status == "completed" else "yellow"
    console.print(Panel(f"{message}\n[dim]Trace: {trace_path}[/]", title=status.upper(), border_style=color))

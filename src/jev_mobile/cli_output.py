"""Human-friendly Rich rendering for the standalone CLI."""

from __future__ import annotations

from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .actions.models import ActionPage, CandidateAction, Decision
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
    if state.dialog:
        details.append(f"[bold yellow]Popup:[/] {state.dialog.kind.value}" + (
            f" — {state.dialog.title!r}" if state.dialog.title else ""
        ))
    labels = _labels(state)
    if labels:
        details.append("[bold]Visible:[/] " + " · ".join(f"{label!r}" for label in labels))
    console.print(Panel("\n".join(details), title=title, border_style="cyan"))


def render_actions(actions: list[CandidateAction], page: ActionPage | None = None) -> None:
    title = "Valid actions"
    if page and page.total_pages > 1:
        title += f" · page {page.index + 1}/{page.total_pages} · {page.total_device_actions} ranked"
    table = Table(title=title, box=box.SIMPLE_HEAD, show_edge=False)
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Action")
    table.add_column("Risk", style="dim", no_wrap=True)
    for action in actions:
        table.add_row(action.id, action.label, action.risk.value)
    console.print(table)


def render_decision(provider: str, decision: Decision, margin: float | None,
                    actions: list[CandidateAction] | None = None) -> None:
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
    if decision.probabilities:
        ordered_ids = [action.id for action in actions or []]
        ordered_ids.extend(action_id for action_id in decision.probabilities if action_id not in ordered_ids)
        probabilities = Text("Probabilities for the actions above: ", style="dim")
        probabilities.append("  ".join(
            f"{action_id} {decision.probabilities[action_id]:.0%}"
            for action_id in ordered_ids if action_id in decision.probabilities
        ), style="cyan")
        console.print(probabilities)


class RunReporter:
    """Render concise controller events while the JSONL trace remains complete."""

    def __init__(self) -> None:
        self._actions: list[CandidateAction] = []

    def __call__(self, event: str, payload: dict[str, object]) -> None:
        if event == "started":
            console.print(Panel(str(payload["goal"]), title="Mobile task started", border_style="blue"))
        elif event == "state_ready":
            render_state(payload["state"], step=int(payload["step"]), stabilize_ms=float(payload["stabilize_ms"]))
        elif event == "candidates":
            page = payload["page"]
            self._actions = page.actions
            render_actions(page.actions, page)
        elif event == "decision":
            render_decision(str(payload["provider"]), payload["decision"], payload["margin"], self._actions)
        elif event == "action":
            console.print(f"[green]✓ Executing[/] {payload['action'].label}")
        elif event == "retry":
            console.print(f"[yellow]↻ Retrying[/] {payload['action'].label} (attempt {payload['attempt']})")
        elif event == "page_advanced":
            console.print(f"[cyan]→ More actions[/] showing page {payload['page']}")
        elif event == "recovery_plan":
            metadata = payload["metadata"]
            body = f"{metadata.get('plan_summary', 'A structured recovery plan was created.')}\n"
            body += f"[dim]{metadata.get('plan_steps', 0)} checkpoints · planner {float(metadata.get('planner_latency_ms') or 0):.0f} ms[/]"
            console.print(Panel(body, title="✦ LLM recovery plan", border_style="magenta"))
        elif event == "task_plan":
            plan = payload["plan"]
            criteria = "\n".join(f"• {item}" for item in plan.completion_criteria) or "• Verify the requested result"
            body = f"{plan.summary}\n\n[bold]Completion checks[/]\n{criteria}"
            console.print(Panel(body, title="✦ LLM task plan", border_style="magenta"))
        elif event == "completion_review":
            result = payload["result"]
            style = "green" if result.complete else "yellow"
            console.print(Panel(result.reason, title=("✓ LLM completion review" if result.complete else "↻ LLM review: continue"), border_style=style))
        elif event == "completed":
            console.print(Panel("The verified UI transition completed the task.", title="✓ Task completed", border_style="green"))
        elif event == "escalated":
            console.print(Panel(f"Reason: {payload['reason']}", title="⚠ Escalated", border_style="yellow"))


def render_result(status: str, message: str, trace_path: Path) -> None:
    color = "green" if status == "completed" else "yellow"
    console.print(Panel(f"{message}\n[dim]Trace: {trace_path}[/]", title=status.upper(), border_style=color))

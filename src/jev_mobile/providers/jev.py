"""TypeSafe System-One decision provider for Jev.

The provider deliberately sends only a compact semantic state and existing
candidate-action identifiers. Jev never receives coordinates or MCP details.
"""

from __future__ import annotations

import time
from typing import Any

from typesafe_sdk import AsyncTypeSafeClient, Choice

from ..actions.models import CandidateAction, Decision
from ..state.models import SemanticState
from .base import ProviderUnavailable


def build_jev_state(
    goal: str, state: SemanticState, actions: list[CandidateAction] | None = None,
) -> dict[str, Any]:
    """Build the deliberately small state payload used by System One."""
    target_ids = {action.target_element_id for action in actions or [] if action.target_element_id}
    elements = [element for element in state.elements if element.visible]
    if target_ids:
        elements = [element for element in elements if element.id in target_ids]
    elif actions and any(action.kind.value == "launch_app" for action in actions):
        elements = []
    else:
        elements = [
            element for element in elements
            if element.package != "com.android.systemui" and (element.label or element.clickable or element.editable)
        ][:16]
    return {
        "goal": goal,
        "app": state.app,
        "screen": {
            "hint": state.screen_hint,
            "elements": [
                {
                    "id": element.id,
                    "role": element.role,
                    "text": element.label,
                    "clickable": element.clickable,
                    "editable": element.editable,
                    "enabled": element.enabled,
                    "selected": element.selected,
                }
                for element in elements
            ][:24],
        },
        "recent_context": [],
    }


def probability_margin(probabilities: dict[str, float]) -> float | None:
    """Return the top-one versus top-two probability gap when available."""
    values = sorted(probabilities.values(), reverse=True)
    return values[0] - values[1] if len(values) >= 2 else None


def usage_metadata(usage: object) -> dict[str, object] | None:
    """Extract only token accounting from the SDK's msgspec response object."""
    fields = getattr(usage, "__struct_fields__", ())
    if not fields:
        return None
    return {field: getattr(usage, field) for field in fields}


def _safe_error_summary(error: Exception) -> str:
    """Expose validation diagnostics without leaking headers or request payloads."""
    status = getattr(error, "status", None)
    body = getattr(error, "body", None)
    detail: object | None = None
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message") or body.get("error")
    suffix = f", status={status}" if status is not None else ""
    if isinstance(detail, str):
        return f"{type(error).__name__}{suffix}: {detail[:300]}"
    return f"{type(error).__name__}{suffix}"


class JevProvider:
    """Native asynchronous TypeSafe SDK provider."""

    name = "jev"

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    async def decide(self, goal: str, state: SemanticState, actions: list[CandidateAction]) -> Decision:
        if not self._api_key:
            raise ProviderUnavailable("TYPESAFE_API_KEY is not configured")
        if not actions:
            raise ProviderUnavailable("Jev cannot decide without candidate actions")

        compact_state = build_jev_state(goal, state, actions)
        criteria = {action.id: action.label for action in actions}
        started = time.perf_counter()
        try:
            async with AsyncTypeSafeClient(api_key=self._api_key) as client:
                request = {
                    "state": compact_state,
                    "questions": {
                        "next_action": Choice(
                            instructions=(
                                "This is one step in a multi-step control loop. Choose exactly one listed "
                                "safe next action that advances the goal; do not require it to finish the "
                                "whole goal in this step. "
                                "Choose the escalate action when no listed action is clearly appropriate."
                            ),
                            criteria=criteria,
                        )
                    },
                }
                response = await client.system_one(**request)
        except Exception as error:
            # Do not include request headers, credentials, or server bodies in traces.
            raise ProviderUnavailable(f"Jev request failed ({_safe_error_summary(error)})") from error

        answer = response.answers["next_action"]
        selected = answer.choice
        probabilities = {key: float(value) for key, value in answer.probabilities.items()}
        confidence = probabilities.get(selected, float(answer.confidence))
        usage = usage_metadata(response.usage)
        metadata: dict[str, object] = {
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "probability_margin": probability_margin(probabilities),
            "state_elements": len(compact_state["screen"]["elements"]),
            "action_count": len(actions),
            "model": response.model,
        }
        if usage is not None:
            metadata["usage"] = usage
        return Decision(
            action_id=selected,
            confidence=confidence,
            probabilities=probabilities,
            reasoning_metadata=metadata,
        )

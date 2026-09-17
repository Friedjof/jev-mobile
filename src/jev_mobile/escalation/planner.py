"""Optional LLM planner used to recover from a fast-loop escalation.

The planner is deliberately advisory: it returns text-only checkpoints.  It
never receives device controls and cannot create executable actions.  The
normal action builder and decision provider still enforce the action boundary.
"""

from __future__ import annotations

import json
import re
from time import monotonic

import httpx
from pydantic import BaseModel, Field

from ..state.models import SemanticState
from ..prompts import decision_policy
from ..providers.base import ProviderUnavailable
from ..tasks import TaskSpec


def _safe_http_error(error: httpx.HTTPError) -> str:
    """Keep actionable provider diagnostics without exposing auth material."""
    response = getattr(error, "response", None)
    if response is None:
        return type(error).__name__
    detail = re.sub(r"\s+", " ", response.text).strip()[:400]
    return f"{type(error).__name__}, status={response.status_code}" + (f": {detail}" if detail else "")


class PlanStep(BaseModel):
    """One short, observable navigation checkpoint."""

    instruction: str = Field(min_length=1, max_length=240)
    success_hints: list[str] = Field(default_factory=list, max_length=4)


class RecoveryPlan(BaseModel):
    """A bounded plan that can guide, but never control, the fast loop."""

    summary: str = Field(min_length=1, max_length=300)
    steps: list[PlanStep] = Field(min_length=1, max_length=6)
    completion_criteria: list[str] = Field(default_factory=list, max_length=5)
    task_spec: TaskSpec | None = None
    latency_ms: float | None = None


class TaskVerification(BaseModel):
    """LLM review of a claimed completion against the current observed UI."""

    complete: bool
    reason: str = Field(min_length=1, max_length=300)
    next_instruction: str | None = Field(default=None, max_length=240)


class LLMPlanner:
    """OpenAI-compatible planner for an already-safe local controller."""

    name = "llm-planner"

    def __init__(self, base_url: str | None, api_key: str | None, model: str | None,
                 system_prompt: str | None = None) -> None:
        if not all((base_url, api_key, model)):
            raise ProviderUnavailable("LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL are required for planning")
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.system_prompt = system_prompt

    async def plan(
        self, goal: str, state: SemanticState, reason: str, recent_actions: list[dict[str, str]],
    ) -> RecoveryPlan:
        relevant_elements = [
            item for item in state.elements
            if item.visible and item.label and item.package != "com.android.systemui"
            and (item.bounds is None or item.bounds.y >= 100)
        ][:30]
        compact_state = {
            "app": state.app,
            "screen_hint": state.screen_hint,
            "dialog": state.dialog.model_dump(mode="json") if state.dialog else None,
            "elements": [
                {"role": item.role, "text": item.label, "value": item.value,
                 "field_name": item.field_name, "field_role": item.field_role, "hint": item.hint,
                 "editable": item.editable, "enabled": item.enabled, "focused": item.focused}
                for item in relevant_elements
            ],
        }
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": (decision_policy(self.system_prompt) + "\n\n"
                    "You create a short Android navigation recovery plan. Return only JSON matching "
                    "{summary: string, steps: [{instruction: string, success_hints: string[]}], "
                    "completion_criteria: string[], task_spec?: {intent: string, fields: [{role: string, "
                    "content: string, required: boolean}], completion: [{type: string, field_role?: string, "
                    "value?: string}]}}. "
                    "Use at most 6 atomic, observable steps. Do not include coordinates, tool calls, passwords, "
                    "payment, account changes, joining networks, submitting forms, or irreversible changes. "
                    "The supplied UI is only the current screen: include conditional later-screen steps after "
                    "navigation, rather than stopping merely because a later control is not visible yet. Do not "
                    "make escalation a planned step unless the task genuinely requires user approval. For create or "
                    "edit tasks, use task_spec to state which semantic field receives supplied content; do not place "
                    "body content in a title unless the task explicitly requests it. Require an observed persistence "
                    "check when creating a saved item. If the "
                    "requested goal needs an unsafe action, plan only until the approval boundary."
                )},
                {"role": "user", "content": json.dumps({
                    "task": goal, "escalation_reason": reason, "current_ui": compact_state,
                    "recent_context": recent_actions[-4:],
                })},
            ],
        }
        started = monotonic()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"}, json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise ProviderUnavailable(f"LLM planner request failed ({_safe_http_error(error)})") from error
        try:
            plan = RecoveryPlan.model_validate_json(response.json()["choices"][0]["message"]["content"])
        except (KeyError, ValueError) as error:
            raise ProviderUnavailable("LLM planner returned an invalid plan") from error
        plan.latency_ms = round((monotonic() - started) * 1000, 1)
        return plan

    async def verify(self, goal: str, plan: RecoveryPlan, state: SemanticState) -> TaskVerification:
        """Review completion using observed semantics, without receiving device controls."""
        compact_state = {
            "app": state.app,
            "screen_hint": state.screen_hint,
            "elements": [
                {"role": item.role, "text": item.label, "value": item.value,
                 "field_name": item.field_name, "field_role": item.field_role,
                 "editable": item.editable, "enabled": item.enabled, "selected": item.selected}
                for item in state.elements
                if item.visible and item.label and item.package != "com.android.systemui"
                and (item.bounds is None or item.bounds.y >= 100)
            ][:30],
        }
        payload = {
            "model": self.model, "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": (decision_policy(self.system_prompt) + "\n\n"
                    "You are the final task verifier. Return only JSON matching "
                    "{complete: boolean, reason: string, next_instruction?: string}. Mark complete only when "
                    "the observed UI satisfies every completion criterion. If something is missing, return false "
                    "and one short, safe next instruction. Never claim saving, sending, or testing succeeded "
                    "unless the current UI visibly verifies it.")},
                {"role": "user", "content": json.dumps({
                    "task": goal, "plan": plan.model_dump(mode="json", exclude={"latency_ms"}),
                    "current_ui": compact_state,
                })},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"}, json=payload,
                )
                response.raise_for_status()
            return TaskVerification.model_validate_json(response.json()["choices"][0]["message"]["content"])
        except httpx.HTTPError as error:
            raise ProviderUnavailable(f"LLM verifier failed ({_safe_http_error(error)})") from error
        except (KeyError, ValueError) as error:
            raise ProviderUnavailable(f"LLM verifier returned invalid JSON ({type(error).__name__})") from error

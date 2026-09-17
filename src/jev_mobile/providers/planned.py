"""A recovery wrapper: one LLM plan, then the normal bounded action loop."""

from __future__ import annotations

from ..actions.models import CandidateAction, Decision
from ..escalation.planner import LLMPlanner, RecoveryPlan, TaskVerification
from ..state.models import SemanticState
from .base import DecisionProvider
from .jev import probability_margin


class PlanGuidedProvider:
    """Ask an LLM for text-only checkpoints when the fast provider is uncertain.

    The wrapped provider still chooses an ID from ``actions``. This object has
    no device reference and cannot turn an LLM plan into an unvalidated tap.
    """

    def __init__(self, provider: DecisionProvider, planner: LLMPlanner, *, confidence_threshold: float,
                 minimum_probability_margin: float, max_recoveries: int = 1, plan_first: bool = False) -> None:
        self.provider, self.planner = provider, planner
        self.name = f"{provider.name}+plan"
        self.confidence_threshold = confidence_threshold
        self.minimum_probability_margin = minimum_probability_margin
        self.max_recoveries = max_recoveries
        self.plan: RecoveryPlan | None = None
        self.step_index = 0
        self.recoveries = 0
        self._last_advanced_fingerprint: str | None = None
        self._plan_origin_fingerprint: str | None = None
        self.plan_first = plan_first
        self.verification_instruction: str | None = None

    async def ensure_plan(self, goal: str, state: SemanticState) -> RecoveryPlan | None:
        """Create the initial plan once before the fast loop acts."""
        if self.plan or not self.plan_first:
            return None
        self.plan = await self.planner.plan(goal, state, "initial_task_decomposition", [])
        self._plan_origin_fingerprint = state.fingerprint
        self.recoveries += 1
        return self.plan

    def contextual_goal(self, goal: str, state: SemanticState) -> str:
        self._advance_if_observed(state)
        if self.verification_instruction:
            return f"{goal}\n\nVerification gap: {self.verification_instruction}"
        if not self.plan or self.step_index >= len(self.plan.steps):
            return goal
        step = self.plan.steps[self.step_index]
        return f"{goal}\n\nRecovery-plan checkpoint {self.step_index + 1}/{len(self.plan.steps)}: {step.instruction}"

    async def verify_completion(self, goal: str, state: SemanticState) -> TaskVerification | None:
        if not self.plan:
            return None
        result = await self.planner.verify(goal, self.plan, state)
        self.verification_instruction = None if result.complete else result.next_instruction
        return result

    def _advance_if_observed(self, state: SemanticState) -> None:
        if not self.plan or self.step_index >= len(self.plan.steps):
            return
        if self._plan_origin_fingerprint is None:
            self._plan_origin_fingerprint = state.fingerprint
            return
        if state.fingerprint == self._plan_origin_fingerprint:
            return
        if state.fingerprint == self._last_advanced_fingerprint:
            return
        hints = [hint.casefold() for hint in self.plan.steps[self.step_index].success_hints if hint.strip()]
        labels = " ".join(item.label.casefold() for item in state.elements if item.visible and item.label)
        if hints and any(hint in labels for hint in hints):
            self.step_index += 1
            self._last_advanced_fingerprint = state.fingerprint
            self._plan_origin_fingerprint = state.fingerprint

    async def decide(self, goal: str, state: SemanticState, actions: list[CandidateAction]) -> Decision:
        contextual_goal = self.contextual_goal(goal, state)
        decision = await self.provider.decide(contextual_goal, state, actions)
        margin = probability_margin(decision.probabilities or {})
        uncertain = (
            decision.action_id == "ESCALATE"
            or decision.confidence < self.confidence_threshold
            or (margin is not None and margin < self.minimum_probability_margin)
        )
        if not uncertain or self.recoveries >= self.max_recoveries:
            return decision

        self.plan = await self.planner.plan(goal, state, "fast_provider_uncertain", [])
        self.step_index = 0
        self._last_advanced_fingerprint = None
        self._plan_origin_fingerprint = state.fingerprint
        self.recoveries += 1
        metadata = dict(decision.reasoning_metadata or {})
        metadata.update({
            "recovery_plan_created": True,
            "plan_summary": self.plan.summary,
            "plan_steps": len(self.plan.steps),
            "planner_latency_ms": self.plan.latency_ms,
        })
        return decision.model_copy(update={"reasoning_metadata": metadata})

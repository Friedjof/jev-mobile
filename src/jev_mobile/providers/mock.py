"""Deterministic decisions for tests and scripted demonstrations."""

from __future__ import annotations

from ..actions.models import CandidateAction, Decision
from ..state.models import SemanticState


class MockProvider:
    name = "mock"
    def __init__(self, action_ids: list[str] | None = None) -> None: self.action_ids = action_ids or []
    async def decide(self, goal: str, state: SemanticState, actions: list[CandidateAction]) -> Decision:
        requested = self.action_ids.pop(0) if self.action_ids else actions[0].id
        return Decision(action_id=requested, confidence=1.0)

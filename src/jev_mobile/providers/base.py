"""Decision-provider protocol independent of Jev or any LLM."""

from __future__ import annotations

from typing import Protocol
from ..actions.models import CandidateAction, Decision
from ..state.models import SemanticState


class DecisionProvider(Protocol):
    name: str
    async def decide(self, goal: str, state: SemanticState, actions: list[CandidateAction]) -> Decision: ...


class ProviderUnavailable(RuntimeError):
    """Raised when a selected provider has no supported configured transport."""

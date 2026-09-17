"""Resumable escalation payloads."""

from __future__ import annotations

from pydantic import BaseModel
from ..actions.models import CandidateAction
from ..state.models import SemanticState


class EscalationResult(BaseModel):
    task_id: str
    goal: str
    reason: str
    state: SemanticState
    candidate_actions: list[CandidateAction]
    recent_actions: list[str]
    screenshot_path: str | None = None

"""Validated controller actions and safety classifications."""

from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel


class ActionKind(StrEnum):
    TAP = "tap"
    LONG_PRESS = "long_press"
    TYPE_TEXT = "type_text"
    SCROLL_DOWN = "scroll_down"
    SCROLL_UP = "scroll_up"
    BACK = "back"
    LAUNCH_APP = "launch_app"
    WAIT = "wait"
    ESCALATE = "escalate"
    DONE = "done"


class ActionRisk(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    EXTERNAL_EFFECT = "external_effect"
    SENSITIVE = "sensitive"


class CandidateAction(BaseModel):
    id: str
    kind: ActionKind
    label: str
    target_element_id: str | None = None
    text: str | None = None
    package: str | None = None
    risk: ActionRisk = ActionRisk.READ_ONLY


class Decision(BaseModel):
    action_id: str
    confidence: float
    probabilities: dict[str, float] | None = None
    reasoning_metadata: dict[str, object] | None = None

"""Validated controller actions and safety classifications."""

from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel
from .mutation_family import MutationFamily


class ActionKind(StrEnum):
    TAP = "tap"
    LONG_PRESS = "long_press"
    TYPE_TEXT = "type_text"
    SCROLL_DOWN = "scroll_down"
    SCROLL_UP = "scroll_up"
    BACK = "back"
    LAUNCH_APP = "launch_app"
    OPEN_APP_ROOT = "open_app_root"
    WAIT = "wait"
    MORE_ACTIONS = "more_actions"
    SELECT_GROUP = "select_group"
    NEXT_GROUP = "next_group"
    PREVIOUS_GROUP = "previous_group"
    SEARCH_RELEVANT = "search_relevant"
    ESCALATE = "escalate"
    REQUEST_INPUT = "request_input"
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
    target_descriptor: dict[str, object] | None = None
    text: str | None = None
    package: str | None = None
    risk: ActionRisk = ActionRisk.READ_ONLY
    goal_directed: bool = False
    mutation_family: MutationFamily = MutationFamily.OTHER
    question: dict[str, object] | None = None


class ActionPage(BaseModel):
    """A bounded, ranked slice of valid actions for one stable UI state."""

    index: int
    total_pages: int
    total_device_actions: int
    actions: list[CandidateAction]


class Decision(BaseModel):
    action_id: str
    confidence: float
    probabilities: dict[str, float] | None = None
    reasoning_metadata: dict[str, object] | None = None

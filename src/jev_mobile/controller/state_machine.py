"""Explicit lifecycle names used in traces and integrations."""

from enum import StrEnum


class ControllerStatus(StrEnum):
    OBSERVE = "observe"
    STABILIZE = "stabilize"
    DECIDE = "decide"
    EXECUTE = "execute"
    ESCALATED = "escalated"
    COMPLETED = "completed"
    FAILED = "failed"

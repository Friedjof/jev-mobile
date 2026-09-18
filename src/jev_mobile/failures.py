"""Stable public failure classification for operators and parent agents."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureCategory(StrEnum):
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    UNSUPPORTED_TASK = "UNSUPPORTED_TASK"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    AGENT_BUG = "AGENT_BUG"


@dataclass(frozen=True, slots=True)
class PublicFailure:
    category: FailureCategory
    reason: str
    message: str
    recoverable: bool


def classify_failure(reason: str | None) -> PublicFailure:
    """Map internal diagnostics to a bounded, stable public failure model."""
    normalized = (reason or "UNKNOWN_AGENT_FAILURE").upper()
    if normalized in {"NO_VERIFIABLE_COMPLETION_CRITERIA", "TASK_CONTRACT_INCOMPLETE"}:
        message = (
            "The task could not be executed because it has no observable completion criteria."
            if normalized == "NO_VERIFIABLE_COMPLETION_CRITERIA"
            else "The task could not be converted into a complete, safe execution contract."
        )
        return PublicFailure(FailureCategory.UNSUPPORTED_TASK, normalized, message, True)
    safety_messages = {
        "ORIENTATION_BLOCKED_BY_PERSISTED_APP_STATE": (
            "PERSISTED_FOREIGN_APP_STATE",
            "The target app persistently restores unrelated content. Continuing would require destructive application-state reset.",
        ),
        "ORIENTATION_BLOCKED_BY_USER_DENIAL": (
            "USER_DENIED_RECOVERY",
            "The requested recovery operation was denied; no Android action was executed.",
        ),
        "SENSITIVE_INFORMATION_DENIED": (
            "USER_DENIED_INFORMATION_DISCLOSURE",
            "The requested personal information was not read or returned.",
        ),
    }
    if normalized in safety_messages:
        public_reason, message = safety_messages[normalized]
        return PublicFailure(FailureCategory.SAFETY_BLOCKED, public_reason, message, False)
    if any(marker in normalized for marker in (
        "PROVIDER_UNAVAILABLE", "JEV REQUEST FAILED", "JEV INTERPRETATION FAILED", "TYPESAFE",
    )):
        return PublicFailure(FailureCategory.PROVIDER_UNAVAILABLE, normalized, reason or normalized, True)
    if "ADB" in normalized or "DEVICE" in normalized or "USB" in normalized:
        return PublicFailure(FailureCategory.DEVICE_UNAVAILABLE, normalized, reason or normalized, True)
    if "PORTAL" in normalized or "BACKEND" in normalized or "ACCESSIBILITY" in normalized:
        return PublicFailure(FailureCategory.BACKEND_UNAVAILABLE, normalized, reason or normalized, True)
    return PublicFailure(FailureCategory.AGENT_BUG, normalized, reason or "Task failed", False)

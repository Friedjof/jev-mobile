"""Text input is a verified operation, not a generic button click."""

from __future__ import annotations

from enum import StrEnum


class TextInputOutcome(StrEnum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    ACCEPTED_UNVERIFIED = "accepted_unverified"
    COMMIT_OUTCOME_UNKNOWN = "commit_outcome_unknown"
    INPUT_SESSION_CHANGED = "input_session_changed"
    TARGET_LOST = "target_lost"

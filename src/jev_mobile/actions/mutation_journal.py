"""Write intent before touching Android, so timeouts are never retried blindly."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from ..perception.target_descriptor import TargetDescriptor
from .mutation_family import MutationFamily


class MutationOutcome(StrEnum):
    EXECUTED_CONFIRMED = "executed_confirmed"
    EXECUTED_NO_EFFECT = "executed_no_effect"
    EXECUTED_AMBIGUOUS = "executed_ambiguous"
    NOT_EXECUTED = "not_executed"
    TARGET_STALE = "target_stale"
    TRANSPORT_FAILED = "transport_failed"


class MutationEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    action: str
    family: MutationFamily = MutationFamily.OTHER
    snapshot_id: str
    target: TargetDescriptor | None = None
    intended_effect: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    outcome: MutationOutcome | None = None
    transport: str | None = None
    resolved_at: datetime | None = None
    post_state_fingerprint: str | None = None


class MutationJournal:
    def __init__(self) -> None:
        self.entries: list[MutationEntry] = []

    def begin_action(self, *, action: str, snapshot_id: str, intended_effect: str, family: MutationFamily = MutationFamily.OTHER,
                     target: TargetDescriptor | None = None) -> MutationEntry:
        entry = MutationEntry(action=action, family=family, snapshot_id=snapshot_id, target=target, intended_effect=intended_effect)
        self.entries.append(entry)
        return entry

    def resolve_action(self, entry: MutationEntry, outcome: MutationOutcome) -> MutationEntry:
        entry.outcome, entry.resolved_at = outcome, datetime.now(UTC)
        return entry

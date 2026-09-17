from __future__ import annotations

from pydantic import BaseModel, Field

from ..tasks import RequirementStatus


class Requirement(BaseModel):
    key: str
    kind: str
    expected: str | None = None
    required: bool = True
    status: RequirementStatus = RequirementStatus.UNSATISFIED
    evidence: dict[str, object] = Field(default_factory=dict)
    reason: str | None = None

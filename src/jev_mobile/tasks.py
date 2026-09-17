"""Declarative task semantics shared by planning, candidates, and verification."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FieldRequirement(BaseModel):
    """Content expected in a semantic text-field role, never a device command."""

    role: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=4000)
    required: bool = True


class CompletionRequirement(BaseModel):
    """An observable condition required before a task may be declared complete."""

    type: str = Field(min_length=1, max_length=40)
    field_role: str | None = Field(default=None, max_length=40)
    value: str | None = Field(default=None, max_length=4000)


class TaskSpec(BaseModel):
    """Small semantic decomposition supplied by a planner or safe local parsing."""

    intent: str = Field(default="generic", max_length=80)
    fields: list[FieldRequirement] = Field(default_factory=list, max_length=6)
    completion: list[CompletionRequirement] = Field(default_factory=list, max_length=8)


class TaskInterpretation(BaseModel):
    """Cheap semantic interpretation before optional System-2 planning."""

    purpose: str | None = None
    item_candidates: list[str] = Field(default_factory=list)
    content_type: str = "UNKNOWN"
    title: str = "UNKNOWN"
    task_spec: TaskSpec = Field(default_factory=TaskSpec)


def task_spec_from_goal(goal: str) -> TaskSpec:
    """Infer only unambiguous explicit text; ambiguous language remains an LLM job."""
    normalized = goal.casefold()
    _, separator, supplied = goal.partition(":")
    content = supplied.strip()
    creates_note = any(term in normalized for term in ("new note", "create a note", "notiz", "shopping list"))
    if creates_note and content:
        requirement = FieldRequirement(role="body", content=content)
        return TaskSpec(
            intent="create_note",
            fields=[requirement],
            completion=[CompletionRequirement(type="field_contains", field_role="body", value=content)],
        )
    return TaskSpec()


def deterministic_interpretation(goal: str) -> TaskInterpretation:
    """Extract explicitly supplied task text without guessing language intent."""
    spec = task_spec_from_goal(goal)
    _, separator, supplied = goal.partition(":")
    items = [item.strip() for item in supplied.split(",") if item.strip()] if separator else []
    normalized = goal.casefold()
    purpose = "shopping" if any(term in normalized for term in ("shopping", "einkauf")) else None
    content_type = "CHECKLIST" if any(term in normalized for term in ("checklist", "check list", "todo")) else "TEXT_NOTE"
    title = "UNKNOWN"
    return TaskInterpretation(
        purpose=purpose, item_candidates=items, content_type=content_type, title=title, task_spec=spec,
    )

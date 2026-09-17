"""Declarative task semantics shared by planning, candidates, and verification."""

from __future__ import annotations

from enum import StrEnum
import re

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


class RequirementStatus(StrEnum):
    UNSATISFIED = "unsatisfied"
    IN_PROGRESS = "in_progress"
    NEEDS_VERIFICATION = "needs_verification"
    SATISFIED = "satisfied"


class TaskSpec(BaseModel):
    """Small semantic decomposition supplied by a planner or safe local parsing."""

    intent: str = Field(default="generic", max_length=80)
    app: str | None = Field(default=None, max_length=160)
    app_package: str | None = Field(default=None, max_length=240)
    content_type: str = Field(default="UNKNOWN", max_length=40)
    title: str | None = Field(default=None, max_length=240)
    fields: list[FieldRequirement] = Field(default_factory=list, max_length=6)
    items: list[str] = Field(default_factory=list, max_length=100)
    completion: list[CompletionRequirement] = Field(default_factory=list, max_length=8)


class TaskInterpretation(BaseModel):
    """Cheap semantic interpretation before optional System-2 planning."""

    purpose: str | None = None
    item_candidates: list[str] = Field(default_factory=list)
    content_type: str = "UNKNOWN"
    title: str = "UNKNOWN"
    task_spec: TaskSpec = Field(default_factory=TaskSpec)

    def to_task_spec(self) -> TaskSpec:
        """Create the one authoritative specification used by the controller."""
        content_type = self.content_type.casefold()
        title = self.title
        if title in {"UNKNOWN", "NO_TITLE", ""}:
            title = None
        elif title == "USE_PURPOSE_AS_TITLE":
            title = self.purpose.title() if self.purpose else None
        spec = self.task_spec.model_copy(update={
            "content_type": content_type,
            "title": title,
            "fields": [] if content_type == "checklist" else self.task_spec.fields,
            "items": self.item_candidates if content_type == "checklist" else [],
        })
        completion = [item for item in spec.completion if not (content_type == "checklist" and item.type == "field_contains")]
        if spec.intent == "create_note":
            if content_type == "checklist":
                completion.extend([
                    CompletionRequirement(type="content_mode", value="checklist"),
                    CompletionRequirement(type="checklist_items"),
                ])
            elif spec.fields:
                completion.extend([
                    CompletionRequirement(type="field_contains", field_role=field.role, value=field.content)
                    for field in spec.fields
                ])
            if title:
                completion.append(CompletionRequirement(type="title_equals", value=title))
            completion.append(CompletionRequirement(type="persisted"))
        return spec.model_copy(update={"completion": _unique_completion(completion)})


def _unique_completion(values: list[CompletionRequirement]) -> list[CompletionRequirement]:
    unique: dict[tuple[str, str | None, str | None], CompletionRequirement] = {}
    for value in values:
        unique[(value.type, value.field_role, value.value)] = value
    return list(unique.values())


def task_spec_from_goal(goal: str) -> TaskSpec:
    """Infer only unambiguous explicit text; ambiguous language remains an LLM job."""
    normalized = goal.casefold()
    if "fixture" in normalized and ("create an item" in normalized or "create item" in normalized):
        title_match = re.search(r"(?:titled|title)\s+[\"']?([^\n\"']+?)[\"']?(?:\s+(?:with|body)\b|$)", goal, re.I)
        body_match = re.search(r"(?:with\s+body|body)\s*:?\s*(.+)$", goal, re.I | re.S)
        title = title_match.group(1).strip() if title_match else None
        body = body_match.group(1).strip() if body_match else None
        fields = [FieldRequirement(role="body", content=body)] if body else []
        completion = [CompletionRequirement(type="field_contains", field_role="body", value=body)] if body else []
        if title: completion.append(CompletionRequirement(type="title_equals", value=title))
        completion.append(CompletionRequirement(type="persisted"))
        return TaskSpec(intent="create_note", app="Jev Mobile Fixture", app_package="io.jev.mobile.fixture", content_type="text_note", title=title, fields=fields, completion=completion)
    # This is language/task parsing only. It never contains UI or app-flow knowledge.
    checklist = "checklist" in normalized or "check list" in normalized
    note_target = "keep" in normalized or "note" in normalized or "notiz" in normalized
    if checklist and note_target:
        title_match = re.search(r"(?:titled|title)\s+[\"']?([^\n\"']+?)[\"']?(?:\s+(?:containing|with|mit)\b|$)", goal, re.I)
        title = title_match.group(1).strip() if title_match else None
        tail = re.split(r"\b(?:containing|with|mit)\b", goal, maxsplit=1, flags=re.I)
        item_text = tail[1] if len(tail) == 2 else ""
        items = [part.strip(" \t\n,-•.") for part in re.split(r"[,\n]|\band\b|\bund\b", item_text, flags=re.I) if part.strip(" \t\n,-•.")]
        return TaskSpec(intent="create_note", app="Google Keep", app_package="com.google.android.keep", content_type="checklist", title=title,
            items=items, completion=[CompletionRequirement(type="content_mode", value="checklist"),
                                      CompletionRequirement(type="checklist_items"),
                                      CompletionRequirement(type="title_equals", value=title) if title else CompletionRequirement(type="persisted"),
                                      CompletionRequirement(type="persisted")])
    if note_target and any(token in normalized for token in ("text note", "normal", "body")):
        title_match = re.search(r"(?:titled|title)\s+[\"']?([^\n\"']+?)[\"']?(?:\s+(?:with|mit)\s+(?:the\s+)?body\s*:?|$)", goal, re.I)
        body_match = re.search(r"(?:body\s*:?|text\s*:)\s*(.+)$", goal, re.I | re.S)
        title = title_match.group(1).strip() if title_match else None
        body = body_match.group(1).strip() if body_match else None
        fields = [FieldRequirement(role="body", content=body)] if body else []
        completion = [CompletionRequirement(type="field_contains", field_role="body", value=body)] if body else []
        if title: completion.append(CompletionRequirement(type="title_equals", value=title))
        completion.append(CompletionRequirement(type="persisted"))
        return TaskSpec(intent="create_note", app="Google Keep", app_package="com.google.android.keep", content_type="text_note", title=title, fields=fields, completion=completion)
    _, separator, supplied = goal.partition(":")
    content = supplied.strip()
    creates_note = any(term in normalized for term in ("new note", "create a note", "notiz", "shopping list"))
    if creates_note and content:
        requirement = FieldRequirement(role="body", content=content)
        return TaskSpec(
            intent="create_note", content_type="text_note",
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
    # A shopping list is normally a checklist. This is a deterministic task
    # interpretation, not a device-specific shortcut.
    content_type = "CHECKLIST" if (purpose == "shopping" or any(term in normalized for term in ("checklist", "check list", "todo"))) else "TEXT_NOTE"
    title = "USE_PURPOSE_AS_TITLE" if purpose and spec.intent == "create_note" else "UNKNOWN"
    return TaskInterpretation(
        purpose=purpose, item_candidates=items, content_type=content_type, title=title, task_spec=spec,
    )

from __future__ import annotations

from ..tasks import TaskSpec
from .requirement import Requirement


def generate_requirements(task_spec: TaskSpec) -> list[Requirement]:
    """Make every declared completion condition explicit before planning."""
    values = [Requirement(key="app_open", kind="app_open", expected=task_spec.app_package or task_spec.app)] if task_spec.app else []
    values.extend(
        Requirement(key=f"completion:{index}", kind=item.type, expected=item.value)
        for index, item in enumerate(task_spec.completion, start=1)
    )
    values.extend(Requirement(key=f"field:{item.role}", kind="field_contains", expected=item.content,
                              required=item.required) for item in task_spec.fields)
    values.extend(Requirement(key=f"item:{index}", kind="checklist_item", expected=item)
                  for index, item in enumerate(task_spec.items, start=1))
    if task_spec.intent == "create_note": values.append(Requirement(key="note_created", kind="note_created"))
    if task_spec.content_type == "checklist": values.append(Requirement(key="checklist_mode", kind="checklist_mode"))
    if task_spec.intent == "create_note" and not any(item.kind == "persisted" for item in values):
        values.append(Requirement(key="persisted", kind="persisted"))
    return values

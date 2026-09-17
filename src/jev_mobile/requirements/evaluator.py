"""Observed-evidence requirement evaluation; transport receipts are not proof."""

from __future__ import annotations

from ..state.models import SemanticState
from ..tasks import RequirementStatus, TaskSpec
from .generators import generate_requirements
from .requirement import Requirement


class RequirementEvaluator:
    def evaluate(self, task_spec: TaskSpec, state: SemanticState) -> list[Requirement]:
        values = generate_requirements(task_spec)
        visible = " ".join(item.value or item.label or "" for item in state.elements if item.visible)
        folded = " ".join(visible.casefold().split())
        for requirement in values:
            if requirement.kind in {"checklist_item", "checklist_items"}:
                expected = requirement.expected or ""
                satisfied = expected.casefold() in folded if expected else all(item.casefold() in folded for item in task_spec.items)
            elif requirement.kind == "field_contains":
                satisfied = any(item.editable and item.visible and (requirement.expected or "").casefold() in (item.value or "").casefold() for item in state.elements)
            elif requirement.kind == "title_equals":
                satisfied = any(item.visible and item.editable and item.field_role == "title" and (item.value or "").casefold() == (requirement.expected or "").casefold() for item in state.elements)
            elif requirement.kind == "app_open":
                satisfied = bool(state.app and requirement.expected and requirement.expected.casefold() in state.app.casefold())
            elif requirement.kind == "note_created":
                satisfied = bool(task_spec.title and task_spec.title.casefold() in folded)
            elif requirement.kind == "persisted":
                # Evidence must be outside an editor and independently visible.
                editor = any(item.visible and item.editable and item.field_role in {"title", "body", "list_item"} for item in state.elements)
                expected = ([task_spec.title] if task_spec.title else []) + task_spec.items
                satisfied = not editor and bool(expected) and all(item.casefold() in folded for item in expected)
            elif requirement.kind in {"content_mode", "checklist_mode"}:
                satisfied = any(item.visible and item.checkable for item in state.elements)
            else:
                satisfied = False
            requirement.status = RequirementStatus.SATISFIED if satisfied else RequirementStatus.UNSATISFIED
            if satisfied:
                element = next((item for item in state.elements if item.visible and (requirement.expected or "").casefold() in (item.value or item.label or "").casefold()), None)
                requirement.evidence = {"snapshot": state.raw_snapshot_id, "ref": element.id if element else None,
                                        "observed_text": element.value or element.label if element else visible[:400]}
                requirement.reason = "observed on the current UI snapshot"
            else:
                requirement.reason = "not observed on the current UI snapshot"
        return values

    @staticmethod
    def current_subgoal(requirements: list[Requirement]) -> str:
        next_requirement = next((item for item in requirements if item.required and item.status != RequirementStatus.SATISFIED), None)
        if not next_requirement: return "Verify all requirements and complete the task"
        if next_requirement.kind == "persisted": return "Verify that the note has been persisted"
        return f"Satisfy requirement: {next_requirement.key}"

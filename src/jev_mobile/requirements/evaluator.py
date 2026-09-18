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
        visible_elements = [item for item in state.elements if item.visible]
        for requirement in values:
            evidence_element = None
            observed_value: object | None = None
            if requirement.kind in {"checklist_item", "checklist_items"}:
                expected = requirement.expected or ""
                satisfied = expected.casefold() in folded if expected else all(item.casefold() in folded for item in task_spec.items)
                if expected:
                    evidence_element = next(
                        (item for item in visible_elements if expected.casefold() in (item.value or item.label or "").casefold()),
                        None,
                    )
                    observed_value = evidence_element.value or evidence_element.label if evidence_element else expected
                else:
                    observed_value = [
                        next(
                            (
                                item.value or item.label
                                for item in visible_elements
                                if expected_item.casefold() in (item.value or item.label or "").casefold()
                            ),
                            None,
                        )
                        for expected_item in task_spec.items
                    ]
            elif requirement.kind == "field_contains":
                evidence_element = next(
                    (item for item in visible_elements if item.editable and (requirement.expected or "").casefold() in (item.value or "").casefold()),
                    None,
                )
                satisfied = evidence_element is not None
                observed_value = evidence_element.value if evidence_element else None
            elif requirement.kind == "title_equals":
                evidence_element = next(
                    (item for item in visible_elements if item.editable and item.field_role == "title"
                     and (item.value or "").casefold() == (requirement.expected or "").casefold()),
                    None,
                )
                satisfied = evidence_element is not None
                observed_value = evidence_element.value if evidence_element else None
            elif requirement.kind == "app_open":
                satisfied = bool(state.app and requirement.expected and requirement.expected.casefold() in state.app.casefold())
                observed_value = state.app if satisfied else None
            elif requirement.kind == "note_created":
                satisfied = bool(task_spec.title and task_spec.title.casefold() in folded)
                evidence_element = next(
                    (item for item in visible_elements if task_spec.title
                     and task_spec.title.casefold() in (item.value or item.label or "").casefold()),
                    None,
                )
                observed_value = evidence_element.value or evidence_element.label if evidence_element else task_spec.title
            elif requirement.kind == "persisted":
                # Evidence must be outside an editor and independently visible.
                editor = any(item.visible and item.editable and item.field_role in {"title", "body", "list_item"} for item in state.elements)
                expected = ([task_spec.title] if task_spec.title else []) + task_spec.items
                satisfied = not editor and bool(expected) and all(item.casefold() in folded for item in expected)
                evidence_element = next(
                    (item for item in visible_elements if task_spec.title
                     and task_spec.title.casefold() in (item.value or item.label or "").casefold()),
                    None,
                )
                observed_value = {
                    "title": evidence_element.value or evidence_element.label if evidence_element else task_spec.title,
                    "items": [
                        next(
                            (
                                item.value or item.label
                                for item in visible_elements
                                if expected_item.casefold() in (item.value or item.label or "").casefold()
                            ),
                            None,
                        )
                        for expected_item in task_spec.items
                    ],
                }
            elif requirement.kind in {"content_mode", "checklist_mode"}:
                evidence_element = next((item for item in visible_elements if item.checkable), None)
                satisfied = evidence_element is not None
                observed_value = "checklist" if satisfied else None
            else:
                satisfied = False
            requirement.status = RequirementStatus.SATISFIED if satisfied else RequirementStatus.UNSATISFIED
            if satisfied:
                requirement.evidence = {
                    "snapshot_id": state.raw_snapshot_id,
                    "package": state.app,
                    "semantic_role": (
                        evidence_element.field_role or evidence_element.role
                        if evidence_element else requirement.kind
                    ),
                    "label": evidence_element.field_name or evidence_element.label if evidence_element else None,
                    "observed_value": observed_value,
                    "confidence": 1.0,
                }
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

"""Controller-owned, compact working memory for stateless System-One calls."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..actions.models import CandidateAction
from ..state.models import SemanticState
from ..tasks import RequirementStatus, TaskSpec


def _field_values(state: SemanticState) -> dict[str, str]:
    return {
        element.field_role or element.id: element.value or ""
        for element in state.elements
        if element.visible and element.editable
    }


@dataclass(slots=True)
class AgentContext:
    """Durable semantic progress plus a bounded, detailed transition window."""

    original_goal: str
    task_spec: TaskSpec
    current_subgoal: str = "Understand the current screen and make safe progress"
    navigation_mode: str = "screen"
    current_group: str | None = None
    group_cursor: int = 0
    focused_target_descriptor: str | None = None
    visited_groups: dict[str, int] = field(default_factory=dict)
    discovered_fields: dict[str, str] = field(default_factory=dict)
    discovered_routes: list[str] = field(default_factory=list)
    successful_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    completed_requirements: list[str] = field(default_factory=list)
    requirement_states: dict[str, RequirementStatus] = field(default_factory=dict)
    recent_transitions: list[dict[str, object]] = field(default_factory=list)
    exploration_branches: int = 0
    transition_sequence: int = 0
    _before_values: dict[str, str] = field(default_factory=dict)
    _before_target_value: str | None = None
    _before_fingerprint: str | None = None
    _before_app: str | None = None
    _pending_action: CandidateAction | None = None
    _pending_receipt_status: str | None = None

    def __post_init__(self) -> None:
        self._ensure_requirement_states()

    def _ensure_requirement_states(self) -> None:
        for requirement in self.task_spec.completion:
            self.requirement_states.setdefault(self._requirement_key(requirement.type, requirement.field_role, requirement.value), RequirementStatus.UNSATISFIED)

    def set_task_spec(self, task_spec: TaskSpec) -> None:
        """Replace task semantics without retaining obsolete requirements."""
        previous = self.requirement_states
        self.task_spec = task_spec
        self.requirement_states = {}
        self._ensure_requirement_states()
        for key in self.requirement_states:
            if previous.get(key) == RequirementStatus.SATISFIED:
                self.requirement_states[key] = RequirementStatus.SATISFIED
        self._update_subgoal()

    @staticmethod
    def _requirement_key(kind: str, role: str | None = None, value: str | None = None) -> str:
        return ":".join(part for part in (kind, role or "", value or "") if part)

    def observe(self, state: SemanticState) -> None:
        """Update discovery and close a pending transition from observed semantics."""
        for element in state.elements:
            if element.visible and element.editable:
                self.discovered_fields[element.field_role or element.id] = element.field_name or "Text"
        self._evaluate_requirements(state)
        if self._pending_action is None:
            self._update_subgoal()
            return
        action = self._pending_action
        after_values = _field_values(state)
        before = self._before_values
        changed = before != after_values or state.fingerprint != self._before_fingerprint
        if action.kind.value == "type_text" and action.target_element_id:
            target = next((item for item in state.elements if item.id == action.target_element_id), None)
            after = (target.value if target else None) or ""
            expected = action.text or ""
            success = expected.casefold() in after.casefold()
            outcome = "success" if success else "no_effect"
            target_name = target.field_name if target else action.target_element_id
            if success and target:
                requirement = target.field_role or action.target_element_id
                if requirement not in self.completed_requirements:
                    self.completed_requirements.append(requirement)
        else:
            outcome = "progress" if changed else "no_effect"
            target_name = action.target_element_id or action.label
            after = state.app or state.screen_hint
        if (
            action.kind.value == "tap"
            and self.task_spec.content_type == "checklist"
            and "format" in action.label.casefold()
            and not any(element.visible and element.checkable for element in state.elements)
        ):
            # Semantic outcome, not an app-specific rule: opening a text-only
            # formatting surface did not advance the unresolved checklist-mode
            # requirement, even when the screen itself changed.
            outcome = "wrong_direction"
        path = f"{action.kind}:{action.target_element_id or action.label}"
        if outcome in {"no_effect", "wrong_direction"} and path not in self.failed_paths:
            self.failed_paths.append(path)
        if outcome in {"success", "progress"} and path not in self.successful_paths:
            self.successful_paths.append(path)
        if state.app != self._before_app:
            # Semantic groups are screen-specific. Never carry a launcher
            # group into a newly opened app where it hides relevant controls.
            self.leave_group()
            self.group_cursor = 0
        self.transition_sequence += 1
        self.recent_transitions.append({
            "sequence": self.transition_sequence,
            "action": action.label,
            "target": target_name,
            "outcome": outcome,
            "before": self._before_target_value if action.kind.value == "type_text" else None,
            "after": after,
            "requirement_changes": list(self.completed_requirements),
        })
        del self.recent_transitions[:-12]
        self._pending_action = None
        self._pending_receipt_status = None
        self._evaluate_requirements(state)
        self._update_subgoal()

    def begin_action(self, action: CandidateAction, state: SemanticState) -> None:
        """Persist mutation intent before transport; timeouts are ambiguous, not absent."""
        self._pending_action = action
        self._before_values = _field_values(state)
        self._before_fingerprint = state.fingerprint
        self._before_app = state.app
        if action.target_element_id:
            target = next((item for item in state.elements if item.id == action.target_element_id), None)
            self.focused_target_descriptor = target.field_name if target and target.editable else action.label
            self._before_target_value = (target.value or "") if target and target.editable else None

    # Kept temporarily for integrations compiled against the former name.
    record_action = begin_action

    def mark_transport_outcome(self, status: str) -> None:
        self._pending_receipt_status = status

    def _evaluate_requirements(self, state: SemanticState) -> None:
        self._ensure_requirement_states()
        normalized = lambda value: " ".join(value.casefold().split())
        for requirement in self.task_spec.completion:
            key = self._requirement_key(requirement.type, requirement.field_role, requirement.value)
            status = self.requirement_states.get(key, RequirementStatus.UNSATISFIED)
            if requirement.type == "field_contains":
                matches = [element.value or "" for element in state.elements if element.visible and element.editable and element.field_role == requirement.field_role]
                is_satisfied = bool(requirement.value and any(normalized(requirement.value) in normalized(value) for value in matches))
            elif requirement.type == "title_equals":
                titles = [element.value or "" for element in state.elements if element.visible and element.editable and element.field_role == "title"]
                is_satisfied = bool(requirement.value and any(normalized(requirement.value) == normalized(value) for value in titles))
            elif requirement.type == "checklist_items":
                labels = " ".join((element.value or element.label or "") for element in state.elements if element.visible)
                is_satisfied = bool(self.task_spec.items and all(normalized(item) in normalized(labels) for item in self.task_spec.items))
            elif requirement.type == "content_mode":
                labels = " ".join((element.label or "") for element in state.elements if element.visible).casefold()
                is_satisfied = requirement.value == "checklist" and (
                    any(element.visible and element.checkable for element in state.elements)
                    or any(term in labels for term in ("add list item", "list options", "unchecked", "checked"))
                )
            elif requirement.type == "persisted":
                # A draft editor is never persistence evidence. Outside it,
                # require the expected entity semantics to reappear in a list
                # or detail view, not merely any successful prior mutation.
                editor_open = any(element.visible and element.editable and element.field_role in {"body", "title"} for element in state.elements)
                visible = normalized(" ".join(element.value or element.label or "" for element in state.elements if element.visible))
                expected = list(self.task_spec.items)
                if self.task_spec.title:
                    expected.append(self.task_spec.title)
                expected.extend(field.content for field in self.task_spec.fields)
                is_satisfied = not editor_open and bool(expected) and all(normalized(value) in visible for value in expected)
            else:
                is_satisfied = False
            if is_satisfied:
                self.requirement_states[key] = RequirementStatus.SATISFIED
            elif status == RequirementStatus.IN_PROGRESS:
                self.requirement_states[key] = RequirementStatus.NEEDS_VERIFICATION

    def _update_subgoal(self) -> None:
        unsatisfied = [key for key, status in self.requirement_states.items() if status != RequirementStatus.SATISFIED]
        if not unsatisfied:
            self.current_subgoal = "Verify all requirements and complete the task"
            return
        key = unsatisfied[0]
        if key.startswith("content_mode:checklist"):
            self.current_subgoal = "Enable checklist mode"
        elif key.startswith("title_equals"):
            self.current_subgoal = f"Set title to {self.task_spec.title}"
        elif key.startswith("checklist_items"):
            remaining = ", ".join(self.task_spec.items[:6])
            self.current_subgoal = f"Add checklist items: {remaining}"
        elif key.startswith("field_contains:body"):
            self.current_subgoal = "Set the note body"
        elif key.startswith("persisted"):
            self.current_subgoal = "Leave editor and verify persistence"
        else:
            self.current_subgoal = "Understand the current screen and make safe progress"

    def select_group(self, group: str) -> None:
        self.navigation_mode, self.current_group = "group", group
        self.visited_groups[group] = self.visited_groups.get(group, 0) + 1

    def leave_group(self) -> None:
        self.navigation_mode, self.current_group = "screen", None

    def compact(self) -> dict[str, object]:
        return {
            "original_goal": self.original_goal,
            "task_spec": self.task_spec.model_dump(mode="json"),
            "current_subgoal": self.current_subgoal,
            "requirements": {"states": {key: value.value for key, value in self.requirement_states.items()}},
            "navigation": {
                "mode": self.navigation_mode,
                "current_group": self.current_group,
                "focused_target": self.focused_target_descriptor,
                "group_cursor": self.group_cursor,
                "visited_groups": self.visited_groups,
                "exploration_branches": self.exploration_branches,
            },
            "durable_memory": {
                "discovered_fields": self.discovered_fields,
                "successful_paths": self.successful_paths[-8:],
                "failed_paths": self.failed_paths[-8:],
            },
            "recent_transitions": self.recent_transitions[-12:],
        }

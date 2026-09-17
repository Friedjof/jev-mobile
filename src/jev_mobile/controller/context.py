"""Controller-owned, compact working memory for stateless System-One calls."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..actions.models import CandidateAction
from ..state.models import SemanticState
from ..tasks import TaskSpec


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
    recent_transitions: list[dict[str, object]] = field(default_factory=list)
    exploration_branches: int = 0
    _before_values: dict[str, str] = field(default_factory=dict)
    _before_target_value: str | None = None
    _before_fingerprint: str | None = None
    _before_app: str | None = None
    _pending_action: CandidateAction | None = None

    def observe(self, state: SemanticState) -> None:
        """Update discovery and close a pending transition from observed semantics."""
        for element in state.elements:
            if element.visible and element.editable:
                self.discovered_fields[element.field_role or element.id] = element.field_name or "Text"
        if self._pending_action is None:
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
            if success:
                requirement = target.field_role if target else action.target_element_id
                if requirement not in self.completed_requirements:
                    self.completed_requirements.append(requirement)
        else:
            outcome = "progress" if changed else "no_effect"
            target_name = action.target_element_id or action.label
            after = state.app or state.screen_hint
        path = f"{action.kind}:{action.target_element_id or action.label}"
        if outcome == "no_effect" and path not in self.failed_paths:
            self.failed_paths.append(path)
        if outcome in {"success", "progress"} and path not in self.successful_paths:
            self.successful_paths.append(path)
        if state.app != self._before_app:
            # Semantic groups are screen-specific. Never carry a launcher
            # group into a newly opened app where it hides relevant controls.
            self.leave_group()
            self.group_cursor = 0
        self.recent_transitions.append({
            "action": action.label,
            "target": target_name,
            "outcome": outcome,
            "before": self._before_target_value if action.kind.value == "type_text" else None,
            "after": after,
            "requirement_changes": list(self.completed_requirements),
        })
        del self.recent_transitions[:-12]
        self._pending_action = None

    def record_action(self, action: CandidateAction, state: SemanticState) -> None:
        self._pending_action = action
        self._before_values = _field_values(state)
        self._before_fingerprint = state.fingerprint
        self._before_app = state.app
        if action.target_element_id:
            target = next((item for item in state.elements if item.id == action.target_element_id), None)
            self.focused_target_descriptor = target.field_name if target and target.editable else action.label
            self._before_target_value = (target.value or "") if target and target.editable else None

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
            "requirements": {
                "completed": self.completed_requirements,
                "remaining": [field.role for field in self.task_spec.fields if field.role not in self.completed_requirements],
            },
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

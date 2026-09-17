"""TalkBack-like semantic grouping for large Android screens."""

from __future__ import annotations

from ..actions.builder import build_action_page, checklist_content_present
from ..actions.models import ActionKind, ActionRisk, ActionPage, CandidateAction
from ..state.models import SemanticElement, SemanticState
from ..tasks import TaskSpec
from .context import AgentContext

GROUP_ORDER = ("navigation", "text_fields", "primary_actions", "editor_actions", "menus", "checkables", "content", "system")


def semantic_group(element: SemanticElement) -> str:
    """Classify controls semantically; no raw node-index pagination."""
    label = (element.label or element.field_name or "").casefold()
    resource = (element.resource_id or "").casefold()
    # Never let status-bar controls compete with app controls merely because
    # they look like generic clickable images or buttons.
    if element.package == "com.android.systemui":
        return "system"
    if element.editable:
        return "text_fields"
    if element.checkable or element.role in {"checkbox", "switch"}:
        return "checkables"
    if any(word in f"{label} {resource}" for word in ("back", "navigate", "drawer", "home", "up")):
        return "navigation"
    if any(word in f"{label} {resource}" for word in ("menu", "more", "overflow", "settings")):
        return "menus"
    if any(word in f"{label} {resource}" for word in ("save", "done", "send", "add", "create", "format")):
        return "editor_actions"
    if element.clickable and element.role in {"button", "image"}:
        return "primary_actions"
    return "content"


def grouped_elements(state: SemanticState) -> dict[str, list[SemanticElement]]:
    groups: dict[str, list[SemanticElement]] = {group: [] for group in GROUP_ORDER}
    for element in state.elements:
        if element.visible and element.enabled and (element.clickable or element.editable or element.checkable):
            groups[semantic_group(element)].append(element)
    return {group: elements for group, elements in groups.items() if elements}


class SemanticNavigator:
    """Keeps wide screens searchable through cheap local Jev choices."""

    def __init__(self, context: AgentContext, *, group_page_size: int = 4, flat_limit: int = 12) -> None:
        self.context = context
        self.group_page_size = group_page_size
        self.flat_limit = flat_limit

    def build(self, state: SemanticState, goal: str, task_spec: TaskSpec, *, can_complete: bool,
              return_mode: bool, action_page_size: int, max_action_pages: int, page_index: int = 0) -> ActionPage:
        if task_spec.intent == "create_note" and checklist_content_present(state, task_spec):
            return self._filter_failed(build_action_page(
                state, goal, page_index=0, page_size=action_page_size, max_pages=max_action_pages,
                can_complete=can_complete, return_mode=return_mode, task_spec=task_spec,
            ))
        groups = grouped_elements(state)
        total_controls = sum(len(items) for items in groups.values())
        # Small, direct screens stay fast. Large or unfocused screens enter
        # semantic navigation instead of exposing raw node pages.
        if total_controls <= self.flat_limit and self.context.navigation_mode == "screen":
            return self._filter_failed(build_action_page(
                state, goal, page_index=page_index, page_size=action_page_size, max_pages=max_action_pages,
                can_complete=can_complete, return_mode=return_mode, task_spec=task_spec,
            ))
        if self.context.navigation_mode == "group" and self.context.current_group in groups:
            selected = groups[self.context.current_group]
            subset = state.model_copy(update={"elements": selected})
            return self._filter_failed(build_action_page(
                subset, goal, page_index=page_index, page_size=action_page_size, max_pages=max_action_pages,
                can_complete=can_complete, return_mode=return_mode, task_spec=task_spec, mechanical=True,
            ))
        self.context.leave_group()
        available = list(groups)
        start = self.context.group_cursor * self.group_page_size
        visible = available[start:start + self.group_page_size]
        actions = [
            CandidateAction(
                id=f"GROUP:{group}", kind=ActionKind.SELECT_GROUP,
                label=f'Explore {group.replace("_", " ")} ({len(groups[group])} controls)',
                risk=ActionRisk.READ_ONLY,
                goal_directed=group in {"text_fields", "editor_actions", "primary_actions"},
            )
            for group in visible
        ]
        if start + self.group_page_size < len(available):
            actions.append(CandidateAction(id="NEXT_GROUP", kind=ActionKind.NEXT_GROUP, label="Next semantic groups"))
        if self.context.group_cursor > 0:
            actions.append(CandidateAction(id="PREVIOUS_GROUP", kind=ActionKind.PREVIOUS_GROUP, label="Previous semantic groups"))
        actions.extend([
            CandidateAction(id="SEARCH_RELEVANT", kind=ActionKind.SEARCH_RELEVANT,
                            label=f"Ask Jev to choose the most relevant group for: {self.context.current_subgoal}", risk=ActionRisk.READ_ONLY),
            CandidateAction(id="BACK", kind=ActionKind.BACK, label="Go back"),
            CandidateAction(id="WAIT", kind=ActionKind.WAIT, label="Wait for UI"),
            CandidateAction(id="ESCALATE", kind=ActionKind.ESCALATE, label="Escalate"),
        ])
        pages = max(1, (len(available) + self.group_page_size - 1) // self.group_page_size)
        return ActionPage(index=self.context.group_cursor, total_pages=pages,
                          total_device_actions=total_controls, actions=actions)

    def _filter_failed(self, page: ActionPage) -> ActionPage:
        """Never offer a previously observed no-effect device action again."""
        filtered = [
            action for action in page.actions
            if f"{action.kind}:{action.target_element_id or action.label}" not in self.context.failed_paths
        ]
        return page.model_copy(update={"actions": filtered})

    def local_action(self, action: CandidateAction, state: SemanticState) -> bool:
        """Apply a local hierarchy transition. Returns true when no phone action occurs."""
        if action.kind == ActionKind.SELECT_GROUP:
            self.context.select_group(action.id.removeprefix("GROUP:"))
            return True
        groups = list(grouped_elements(state))
        if action.kind == ActionKind.NEXT_GROUP:
            self.context.group_cursor = min(self.context.group_cursor + 1, max(0, (len(groups) - 1) // self.group_page_size))
            return True
        if action.kind == ActionKind.PREVIOUS_GROUP:
            self.context.group_cursor = max(0, self.context.group_cursor - 1)
            return True
        if action.kind == ActionKind.SEARCH_RELEVANT:
            unvisited = [group for group in groups if self.context.visited_groups.get(group, 0) < 2]
            if unvisited:
                # Group candidates on the screen are the actual Jev selection
                # interface. SEARCH_RELEVANT only resets a stale cursor so the
                # next cheap Jev call can choose among them; it never silently
                # picks a raw first-unvisited group.
                self.context.leave_group()
                self.context.group_cursor = 0
            return True
        if action.kind == ActionKind.BACK and self.context.navigation_mode == "group":
            self.context.leave_group()
            return True
        return False

    def explore_next_branch(self, state: SemanticState, *, max_group_revisits: int,
                            max_safe_exploration_branches: int) -> bool:
        """Use a cheap unvisited semantic branch before escalating to System 2."""
        if self.context.exploration_branches >= max_safe_exploration_branches:
            return False
        groups = list(grouped_elements(state))
        subgoal = self.context.current_subgoal.casefold()
        preferred = (
            ("editor_actions", "menus", "checkables", "text_fields", "primary_actions", "navigation", "content", "system")
            if any(term in subgoal for term in ("checklist", "mode", "item"))
            else ("text_fields", "editor_actions", "primary_actions", "menus", "checkables", "navigation", "content", "system")
            if any(term in subgoal for term in ("title", "body", "text"))
            else GROUP_ORDER
        )
        ordered = [group for group in preferred if group in groups]
        # Exhaust every unvisited relevant branch before a revisit. This
        # prevents navigation controls from consuming the exploration budget.
        candidates = [group for group in ordered if self.context.visited_groups.get(group, 0) == 0]
        if not candidates:
            candidates = [group for group in ordered if self.context.visited_groups.get(group, 0) < max_group_revisits]
        if candidates:
            self.context.select_group(candidates[0])
            self.context.exploration_branches += 1
            return True
        return False

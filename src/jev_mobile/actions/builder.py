"""Create only actions that the active adapter can execute."""

from __future__ import annotations

from .models import ActionKind, CandidateAction
from ..state.models import SemanticState


def goal_keywords(goal: str) -> tuple[str, ...]:
    """Small Settings vocabulary for candidate pruning and known search text."""
    normalized = goal.casefold()
    if any(word in normalized for word in ("network", "internet", "wifi", "wi-fi")):
        return ("network", "internet", "wifi", "wi-fi", "netzwerk")
    if any(word in normalized for word in ("display", "anzeige")):
        return ("display", "anzeige")
    if "screen" in normalized:
        return ("screen",)
    return ()


def goal_search_text(goal: str) -> str | None:
    keywords = goal_keywords(goal)
    if "network" in keywords or "internet" in keywords:
        return "Network & internet"
    if "display" in keywords:
        return "Display"
    return None


def build_candidates(state: SemanticState, goal: str, can_complete: bool = False, return_mode: bool = False) -> list[CandidateAction]:
    if return_mode and can_complete:
        return [
            CandidateAction(id="A1", kind=ActionKind.DONE, label="Task complete"),
            CandidateAction(id="A2", kind=ActionKind.ESCALATE, label="Escalate"),
        ]
    if return_mode:
        return [
            CandidateAction(id="A1", kind=ActionKind.BACK, label="Go back to the Settings main screen to complete the goal"),
            CandidateAction(id="A2", kind=ActionKind.WAIT, label="Wait for UI"),
            CandidateAction(id="A3", kind=ActionKind.ESCALATE, label="Escalate"),
        ]

    candidates: list[CandidateAction] = []
    navigating_to_settings = state.app != "com.android.settings" and "settings" in goal.casefold()
    if navigating_to_settings:
        candidates.append(CandidateAction(id="A1", kind=ActionKind.LAUNCH_APP, label="Open Settings", package="com.android.settings"))
    if not return_mode and not navigating_to_settings:
        clickable = []
        for element in state.elements:
            is_status_bar = element.bounds is not None and element.bounds.y < 100
            if (element.visible and element.enabled and element.clickable
                    and element.package != "com.android.systemui" and not is_status_bar):
                clickable.append(element)
        keywords = goal_keywords(goal)
        matching = [
            element for element in clickable
            if keywords and any(keyword in (element.label or "").casefold() for keyword in keywords)
        ]
        search = [element for element in clickable if "search settings" in (element.label or "").casefold()]
        editable = [element for element in state.elements if element.visible and element.enabled and element.editable]
        if editable and goal_search_text(goal):
            candidates.append(CandidateAction(
                id=f"A{len(candidates)+1}", kind=ActionKind.TYPE_TEXT,
                label=f'Type "{goal_search_text(goal)}" into search',
                target_element_id=editable[0].id, text=goal_search_text(goal),
            ))
            clickable = []
        elif matching:
            clickable = matching
        elif keywords and search:
            clickable = search
        for element in clickable:
            candidates.append(CandidateAction(id=f"A{len(candidates)+1}", kind=ActionKind.TAP,
                label=f'Tap "{element.label or element.role}"', target_element_id=element.id))
    if (not return_mode and not navigating_to_settings and not goal_keywords(goal)
            and any(item.scrollable for item in state.elements)):
        candidates.append(CandidateAction(id=f"A{len(candidates)+1}", kind=ActionKind.SCROLL_DOWN, label="Scroll down"))
    candidates.extend([
        CandidateAction(id=f"A{len(candidates)+1}", kind=ActionKind.BACK, label="Go back"),
        CandidateAction(id=f"A{len(candidates)+2}", kind=ActionKind.WAIT, label="Wait for UI"),
        CandidateAction(id=f"A{len(candidates)+3}", kind=ActionKind.ESCALATE, label="Escalate"),
    ])
    if can_complete:
        candidates.append(CandidateAction(id=f"A{len(candidates)+1}", kind=ActionKind.DONE, label="Task complete"))
    return candidates

"""Create only actions that the active adapter can execute."""

from __future__ import annotations

from .models import ActionKind, ActionPage, ActionRisk, CandidateAction
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
    if any(word in normalized for word in ("checklist", "check list", "checkbox", "todo")):
        return ("checklist", "check list", "checkbox", "todo")
    if any(word in normalized for word in ("note", "notes", "notiz", "notizen", "shopping list", "einkaufsliste")):
        return ("note", "notiz", "list", "liste")
    return ()


def goal_note_text(goal: str) -> str | None:
    """Return explicitly supplied note text; generation remains an escalation task."""
    if not any(word in goal.casefold() for word in ("note", "notiz", "shopping list", "einkaufsliste")):
        return None
    _, separator, content = goal.partition(":")
    return content.strip() if separator and content.strip() else None


def known_note_text_present(state: SemanticState, goal: str) -> bool:
    """Verify a supplied note body from the semantic UI, never from an action."""
    expected = goal_note_text(goal)
    if not expected or state.app != "com.google.android.keep":
        return False
    visible_text = " ".join(element.label or "" for element in state.elements if element.visible)
    normalize = lambda value: " ".join(value.casefold().split())
    return normalize(expected) in normalize(visible_text)


def _app_intent_labels(goal: str) -> tuple[str, ...]:
    """Known local app intents used only to remove launcher noise."""
    normalized = goal.casefold()
    if any(word in normalized for word in (
        "note", "notes", "notiz", "notizen", "shopping list", "einkaufsliste", "checklist", "check list",
    )):
        return ("keep notes", "google keep", "notes", "notizen")
    return ()


def goal_search_text(goal: str) -> str | None:
    keywords = goal_keywords(goal)
    if "network" in keywords or "internet" in keywords:
        return "Network & internet"
    if "display" in keywords:
        return "Display"
    return None


def _actionable_elements(state: SemanticState):
    """Keep only visible controls and collapse duplicate accessibility nodes."""
    unique = {}
    for element in state.elements:
        is_status_bar = element.bounds is not None and element.bounds.y < 100
        if not (element.visible and element.enabled and element.clickable and element.label):
            continue
        if element.package == "com.android.systemui" or is_status_bar:
            continue
        key = (element.label.casefold(), element.bounds.bucket() if element.bounds else None)
        current = unique.get(key)
        quality = int(element.resource_id == "android:id/title") * 3 + int(element.role == "button")
        current_quality = (int(current.resource_id == "android:id/title") * 3 + int(current.role == "button")) if current else -1
        if quality > current_quality:
            unique[key] = element
    return list(unique.values())


def _ranked_device_actions(state: SemanticState, goal: str, return_mode: bool) -> list[CandidateAction]:
    if state.dialog:
        if state.dialog.kind.value != "safe_dismissible":
            # Android Back cancels/dismisses a modal; it never accepts a
            # permission or confirmation. It is therefore the only automatic
            # recovery action for unknown, permission, and sensitive dialogs.
            return [CandidateAction(id="BACK", kind=ActionKind.BACK, label="Dismiss popup with Back")]
        actions = [
            CandidateAction(id=f"A{index}", kind=ActionKind.TAP,
                            label=f'Dismiss popup: "{next(e.label for e in state.elements if e.id == element_id)}"',
                            target_element_id=element_id)
            for index, element_id in enumerate(state.dialog.safe_dismiss_element_ids, start=1)
        ]
        actions.append(CandidateAction(id="BACK", kind=ActionKind.BACK, label="Dismiss popup with Back"))
        return actions
    if return_mode:
        return [CandidateAction(id="A1", kind=ActionKind.BACK,
                                label="Go back to the Settings main screen to complete the goal")]
    navigating_to_settings = state.app != "com.android.settings" and "settings" in goal.casefold()
    if navigating_to_settings:
        return [CandidateAction(id="A1", kind=ActionKind.LAUNCH_APP, label="Open Settings",
                                package="com.android.settings", goal_directed=True)]

    keywords, elements = goal_keywords(goal), _actionable_elements(state)
    app_targets = _app_intent_labels(goal)
    app_matches = [
        element for element in elements
        if element.label.casefold() in app_targets
    ]
    if app_matches:
        return [CandidateAction(
            id=f"A{index}", kind=ActionKind.TAP,
            label=(f'Open "{element.label}" — the only safe first step toward creating the requested note'),
            target_element_id=element.id, goal_directed=True,
        ) for index, element in enumerate(app_matches, start=1)]
    note_text = goal_note_text(goal)
    if note_text:
        create_note_controls = [
            element for element in elements
            if any(phrase in element.label.casefold() for phrase in (
                "create a note", "new note", "take a note", "add note", "notiz erstellen", "neue notiz",
            ))
        ]
        if create_note_controls:
            return [CandidateAction(
                id=f"A{index}", kind=ActionKind.TAP, label=f'Tap "{element.label}"',
                target_element_id=element.id, risk=ActionRisk.REVERSIBLE, goal_directed=True,
            ) for index, element in enumerate(create_note_controls, start=1)]
    editable = [element for element in state.elements if element.visible and element.enabled and element.editable]
    if editable and note_text:
        existing_text = " ".join(element.label or "" for element in state.elements).casefold()
        if note_text.casefold() not in existing_text:
            return [CandidateAction(id="A1", kind=ActionKind.TYPE_TEXT,
                                    label="Enter the supplied note text", target_element_id=editable[0].id,
                                    text=note_text, risk=ActionRisk.REVERSIBLE, goal_directed=True)]
    if editable and goal_search_text(goal):
        return [CandidateAction(id="A1", kind=ActionKind.TYPE_TEXT,
                                label=f'Type "{goal_search_text(goal)}" into search',
                                target_element_id=editable[0].id, text=goal_search_text(goal),
                                risk=ActionRisk.REVERSIBLE, goal_directed=True)]
    matches = [element for element in elements if any(word in element.label.casefold() for word in keywords)] if keywords else []
    search = [element for element in elements if "search" in element.label.casefold()]
    selected = matches or (search if keywords else elements)
    preferred_titles = [
        element for element in selected
        if element.resource_id == "android:id/title" or element.label.casefold() in keywords
    ]
    if preferred_titles:
        selected = preferred_titles
    ranked = []
    for element in selected:
        score = sum(word in element.label.casefold() for word in keywords) * 100
        score += int(element.resource_id == "android:id/title") * 30 + int(element.role == "button") * 10
        ranked.append((score, element))
    ranked.sort(key=lambda item: (-item[0], item[1].raw_index))
    actions = [CandidateAction(id=f"A{index}", kind=ActionKind.TAP,
                               label=f'Tap "{element.label}"', target_element_id=element.id,
                               risk=(ActionRisk.REVERSIBLE if goal_note_text(goal) and any(
                                   word in element.label.casefold() for word in ("new", "create", "add", "neu", "erstellen")
                               ) else ActionRisk.READ_ONLY),
                               goal_directed=bool(keywords and any(word in element.label.casefold() for word in keywords)))
               for index, (_, element) in enumerate(ranked, start=1)]
    if not actions and any(element.scrollable for element in state.elements):
        actions.append(CandidateAction(id="A1", kind=ActionKind.SCROLL_DOWN, label="Scroll down"))
    return actions


def build_action_page(state: SemanticState, goal: str, *, page_index: int = 0, page_size: int = 10,
                      max_pages: int = 3, can_complete: bool = False, return_mode: bool = False) -> ActionPage:
    """Build one ranked, bounded action page plus global safety controls."""
    device_actions = _ranked_device_actions(state, goal, return_mode)
    if can_complete:
        return ActionPage(index=0, total_pages=1, total_device_actions=0, actions=[
            CandidateAction(id="DONE", kind=ActionKind.DONE, label="Task complete"),
            CandidateAction(id="ESCALATE", kind=ActionKind.ESCALATE, label="Escalate"),
        ])
    usable_pages = max(1, min(max_pages, (len(device_actions) + page_size - 1) // page_size))
    page_index = min(page_index, usable_pages - 1)
    start = page_index * page_size
    actions = device_actions[start:start + page_size]
    if page_index + 1 < usable_pages:
        actions.append(CandidateAction(id="MORE", kind=ActionKind.MORE_ACTIONS,
                                       label=f"More actions ({len(device_actions) - start - len(actions) + 1} remaining)"))
    if not state.dialog and not any(action.kind == ActionKind.BACK for action in actions):
        actions.extend([
            CandidateAction(id="BACK", kind=ActionKind.BACK, label="Go back"),
            CandidateAction(id="WAIT", kind=ActionKind.WAIT, label="Wait for UI"),
        ])
    actions.append(CandidateAction(id="ESCALATE", kind=ActionKind.ESCALATE, label="Escalate"))
    return ActionPage(index=page_index, total_pages=usable_pages, total_device_actions=len(device_actions), actions=actions)


def build_candidates(state: SemanticState, goal: str, can_complete: bool = False, return_mode: bool = False) -> list[CandidateAction]:
    """Compatibility helper returning the first page for inspect and tests."""
    return build_action_page(state, goal, can_complete=can_complete, return_mode=return_mode).actions

"""Create only actions that the active adapter can execute."""

from __future__ import annotations

from .models import ActionKind, ActionPage, ActionRisk, CandidateAction
from ..state.models import SemanticState
from ..tasks import TaskSpec, task_spec_from_goal


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


def expected_fields_present(state: SemanticState, task_spec: TaskSpec) -> bool:
    """Check expected content in the intended semantic field, never anywhere on screen."""
    required = [field for field in task_spec.fields if field.required]
    if not required:
        return False
    normalized = lambda value: " ".join(value.casefold().split())
    for requirement in required:
        values = [
            element.value or element.label or ""
            for element in state.elements
            if element.visible and element.editable and element.field_role == requirement.role
        ]
        if not any(normalized(requirement.content) in normalized(value) for value in values):
            return False
    return True


def checklist_content_present(state: SemanticState, task_spec: TaskSpec) -> bool:
    """Verify an in-editor checklist before offering the persistence transition."""
    if task_spec.content_type != "checklist" or not task_spec.items:
        return False
    normalized = lambda value: " ".join(value.casefold().split())
    text = normalized(" ".join(element.value or element.label or "" for element in state.elements if element.visible))
    title_ok = not task_spec.title or any(
        element.visible and element.editable and element.field_role == "title"
        and normalized(element.value or "") == normalized(task_spec.title)
        for element in state.elements
    )
    return title_ok and all(normalized(item) in text for item in task_spec.items)


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


def target_descriptor(state: SemanticState, element) -> dict[str, object]:
    """Strong semantic identity carried alongside a weak accessibility path."""
    return {
        "snapshot_sequence": state.raw_snapshot_id,
        "window_id": element.window_id,
        "path": element.id,
        "resource_id": element.resource_id,
        "role": element.role,
        "accessible_name": element.accessible_label or element.label,
        "field_role": element.field_role,
        "bounds_bucket": element.bounds.bucket() if element.bounds else None,
        "ancestor_signature": element.parent_id,
    }


def _ranked_device_actions(
    state: SemanticState, goal: str, return_mode: bool, task_spec: TaskSpec, *, mechanical: bool = False,
) -> list[CandidateAction]:
    if state.dialog:
        if state.dialog.kind.value == "unknown" and task_spec.content_type == "checklist":
            mode_options = [
                element for element in state.elements
                if element.visible and element.enabled and element.clickable and element.label
                and any(term in f"{element.label} {element.resource_id or ''}".casefold() for term in ("checkbox", "checklist", "list"))
            ]
            if mode_options:
                return [CandidateAction(
                    id=f"A{index}", kind=ActionKind.TAP,
                    label=f'Enable checklist mode: "{element.label}"', target_element_id=element.id,
                    target_descriptor=target_descriptor(state, element), risk=ActionRisk.REVERSIBLE,
                    goal_directed=True,
                ) for index, element in enumerate(mode_options, start=1)]
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
    if mechanical:
        writable = [item for item in state.elements if item.visible and item.enabled and item.editable]
        if task_spec.intent == "create_note" and task_spec.content_type == "checklist":
            if task_spec.title:
                titles = [field for field in writable if field.field_role == "title" and (field.value or "").casefold() != task_spec.title.casefold()]
                if titles:
                    return [CandidateAction(
                        id=f"A{index}", kind=ActionKind.TYPE_TEXT,
                        label=f'Set title "{task_spec.title}" in "{field.field_name or "Title"}"',
                        target_element_id=field.id, target_descriptor=target_descriptor(state, field), text=task_spec.title,
                        risk=ActionRisk.REVERSIBLE, goal_directed=True,
                    ) for index, field in enumerate(titles, start=1)]
            values = " ".join(field.value or "" for field in writable).casefold()
            missing = next((item for item in task_spec.items if item.casefold() not in values), None)
            bodies = [field for field in writable if field.field_role in {"body", "text", "list_item"}]
            empty_bodies = [field for field in bodies if not (field.value or "").strip()]
            if missing and empty_bodies:
                focused_empty = [field for field in empty_bodies if field.focused]
                # Checklist entries are an ordered repeated semantic field:
                # choose its next empty slot, rather than forcing Jev to pick
                # among several otherwise identical blank rows.
                preferred = focused_empty or ([empty_bodies[0]] if any(
                    field.field_role == "list_item" for field in empty_bodies
                ) else empty_bodies)
                return [CandidateAction(
                    id=f"A{index}", kind=ActionKind.TYPE_TEXT,
                    label=f'Add checklist item "{missing}" in "{field.field_name or "List item"}"',
                    target_element_id=field.id, target_descriptor=target_descriptor(state, field), text=missing,
                    risk=ActionRisk.REVERSIBLE, goal_directed=True,
                ) for index, field in enumerate(preferred, start=1)]
        actions = [
            CandidateAction(id=f"A{index}", kind=ActionKind.TAP, label=f'Tap "{element.label}"',
                            target_element_id=element.id, target_descriptor=target_descriptor(state, element), risk=ActionRisk.READ_ONLY)
            for index, element in enumerate(elements, start=1)
        ]
        for field in writable:
            actions.append(CandidateAction(
                id=f"A{len(actions) + 1}", kind=ActionKind.TAP,
                label=f'Focus "{field.field_name or "Text"}"', target_element_id=field.id,
                target_descriptor=target_descriptor(state, field),
                risk=ActionRisk.REVERSIBLE,
            ))
        for element in [item for item in state.elements if item.visible and item.enabled and item.checkable]:
            actions.append(CandidateAction(
                id=f"A{len(actions) + 1}", kind=ActionKind.TAP,
                label=f'{"Uncheck" if element.checked else "Check"} "{element.label or element.state_description or "option"}"',
                target_element_id=element.id, target_descriptor=target_descriptor(state, element), risk=ActionRisk.REVERSIBLE,
            ))
        if not actions and any(element.scrollable for element in state.elements):
            actions.append(CandidateAction(id="A1", kind=ActionKind.SCROLL_DOWN, label="Scroll down"))
        return actions
    app_targets = _app_intent_labels(goal)
    app_matches = [
        element for element in elements
        if element.label.casefold() in app_targets
    ]
    if app_matches:
        return [CandidateAction(
            id=f"A{index}", kind=ActionKind.TAP,
            label=(f'Open "{element.label}" — the only safe first step toward creating the requested note'),
            target_element_id=element.id, target_descriptor=target_descriptor(state, element), goal_directed=True,
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
                target_element_id=element.id, target_descriptor=target_descriptor(state, element), risk=ActionRisk.REVERSIBLE, goal_directed=True,
            ) for index, element in enumerate(create_note_controls, start=1)]
    editable = [element for element in state.elements if element.visible and element.enabled and element.editable]
    if task_spec.intent == "create_note" and task_spec.content_type == "checklist":
        # Before writing title/items, expose generic mode-creation controls.
        # This is intentionally semantic rather than app-specific: editors
        # commonly surface list/check controls through add/formatting menus.
        has_checklist_semantics = any(
            element.checkable
            or any(term in f"{element.label or ''} {element.resource_id or ''}".casefold()
                   for term in ("add list item", "list options", "checked", "unchecked"))
            for element in state.elements if element.visible
        )
        if not has_checklist_semantics:
            mode_controls = [
                element for element in elements
                if any(term in f"{element.label or ''} {element.resource_id or ''}".casefold()
                       for term in ("check", "list", "add", "format"))
            ]
            if mode_controls:
                actions = []
                for index, element in enumerate(mode_controls, start=1):
                    terms = f"{element.label or ''} {element.resource_id or ''}".casefold()
                    if "checkbox" in terms or "checklist" in terms:
                        description = f'Enable checklist mode: "{element.label}"'
                    elif "add" in terms:
                        description = f'Open "{element.label}" to find a checklist or list insertion control'
                    elif "format" in terms and "hide" in terms:
                        description = f'Close "{element.label}" and explore another checklist mode control'
                    elif "format" in terms:
                        description = f'Open "{element.label}" only if it offers a checklist/list control'
                    else:
                        description = f'Explore checklist mode via "{element.label}"'
                    actions.append(CandidateAction(
                        id=f"A{index}", kind=ActionKind.TAP, label=description,
                        target_element_id=element.id, target_descriptor=target_descriptor(state, element),
                        risk=ActionRisk.READ_ONLY, goal_directed=True,
                    ))
                return actions
    if task_spec.intent == "create_note" and (
        expected_fields_present(state, task_spec) or checklist_content_present(state, task_spec)
    ):
        return [CandidateAction(
            id="A1", kind=ActionKind.BACK,
            label="Leave editor and verify the note is persisted outside the draft",
            goal_directed=True,
        )]
    if task_spec.intent == "create_note" and task_spec.content_type == "checklist" and editable:
        if task_spec.title:
            title_fields = [field for field in editable if field.field_role == "title"]
            incomplete_titles = [field for field in title_fields if (field.value or "").casefold() != task_spec.title.casefold()]
            if incomplete_titles:
                return [CandidateAction(
                    id=f"A{index}", kind=ActionKind.TYPE_TEXT,
                    label=f'Set title "{task_spec.title}" in "{field.field_name or "Title"}"',
                    target_element_id=field.id, target_descriptor=target_descriptor(state, field), text=task_spec.title,
                    risk=ActionRisk.REVERSIBLE, goal_directed=True,
                ) for index, field in enumerate(incomplete_titles, start=1)]
        visible_text = " ".join(element.value or element.label or "" for element in state.elements if element.visible).casefold()
        missing = next((item for item in task_spec.items if item.casefold() not in visible_text), None)
        if missing:
            checklist_fields = [field for field in editable if field.field_role in {"body", "text", "list_item"} and not (field.value or "").strip()]
            if not checklist_fields:
                checklist_fields = editable
            if any(field.field_role == "list_item" for field in checklist_fields):
                checklist_fields = [next(field for field in checklist_fields if field.field_role == "list_item")]
            return [CandidateAction(
                id=f"A{index}", kind=ActionKind.TYPE_TEXT,
                label=f'Add checklist item "{missing}" in "{field.field_name or "List item"}"',
                target_element_id=field.id, target_descriptor=target_descriptor(state, field), text=missing,
                risk=ActionRisk.REVERSIBLE, goal_directed=True,
            ) for index, field in enumerate(checklist_fields, start=1)]
    if editable and task_spec.fields:
        actions: list[CandidateAction] = []
        for requirement in task_spec.fields:
            matching_fields = [field for field in editable if field.field_role == requirement.role]
            # A generic field requirement deliberately exposes each writable
            # field. We never select based on accessibility tree order.
            if not matching_fields and requirement.role == "text":
                matching_fields = editable
            if not matching_fields and len(editable) == 1:
                matching_fields = editable
            for field in matching_fields:
                current = field.value or ""
                if requirement.content.casefold() in current.casefold():
                    continue
                focused = " [focused]" if field.focused else ""
                actions.append(CandidateAction(
                    id=f"A{len(actions) + 1}", kind=ActionKind.TYPE_TEXT,
                    label=f'Enter supplied text into "{field.field_name or "Text"}"{focused}',
                    target_element_id=field.id, target_descriptor=target_descriptor(state, field), text=requirement.content,
                    risk=ActionRisk.REVERSIBLE, goal_directed=True,
                ))
        if actions:
            return actions
    if editable and goal_search_text(goal):
        return [CandidateAction(id="A1", kind=ActionKind.TYPE_TEXT,
                                label=f'Type "{goal_search_text(goal)}" into search',
                                target_element_id=editable[0].id, target_descriptor=target_descriptor(state, editable[0]), text=goal_search_text(goal),
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
                               target_descriptor=target_descriptor(state, element),
                               risk=(ActionRisk.REVERSIBLE if goal_note_text(goal) and any(
                                   word in element.label.casefold() for word in ("new", "create", "add", "neu", "erstellen")
                               ) else ActionRisk.READ_ONLY),
                               goal_directed=bool(keywords and any(word in element.label.casefold() for word in keywords)))
               for index, (_, element) in enumerate(ranked, start=1)]
    if not actions and any(element.scrollable for element in state.elements):
        actions.append(CandidateAction(id="A1", kind=ActionKind.SCROLL_DOWN, label="Scroll down"))
    return actions


def build_action_page(state: SemanticState, goal: str, *, page_index: int = 0, page_size: int = 10,
                      max_pages: int = 3, can_complete: bool = False, return_mode: bool = False,
                      task_spec: TaskSpec | None = None, mechanical: bool = False) -> ActionPage:
    """Build one ranked, bounded action page plus global safety controls."""
    device_actions = _ranked_device_actions(state, goal, return_mode, task_spec or task_spec_from_goal(goal), mechanical=mechanical)
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


def build_candidates(state: SemanticState, goal: str, can_complete: bool = False, return_mode: bool = False,
                     task_spec: TaskSpec | None = None) -> list[CandidateAction]:
    """Compatibility helper returning the first page for inspect and tests."""
    return build_action_page(
        state, goal, can_complete=can_complete, return_mode=return_mode, task_spec=task_spec,
    ).actions

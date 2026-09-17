"""Normalize backend snapshots into compact deterministic semantic state."""

from __future__ import annotations

import re
from .fingerprint import semantic_fingerprint
from .models import DialogInfo, DialogKind, RawDeviceElement, RawDeviceState, SemanticElement, SemanticState


_SAFE_DISMISS_LABELS = {
    "cancel", "close", "not now", "no thanks", "dismiss",
    "abbrechen", "schließen", "schliessen", "nicht jetzt", "nein danke",
}
_SENSITIVE_TERMS = ("delete", "purchase", "pay", "payment", "password", "security", "buy", "kaufen", "löschen")


def _clean(value: str | None) -> str | None:
    return re.sub(r"\s+", " ", value).strip() if value and value.strip() else None


def _role(element: RawDeviceElement) -> str:
    name = (element.class_name or "").rsplit(".", 1)[-1].lower()
    if "dialog" in name: return "dialog"
    if element.editable or "edittext" in name: return "text_field"
    if "checkbox" in name: return "checkbox"
    if "switch" in name: return "switch"
    if "progress" in name: return "progressbar"
    if "image" in name: return "image"
    if "button" in name or element.clickable: return "button"
    if "text" in name: return "text"
    return name or "view"


def _dialog(elements: list[SemanticElement]) -> DialogInfo | None:
    """Classify visible modal UI conservatively from accessibility metadata."""
    labels = [element.label for element in elements if element.visible and element.label]
    text = " ".join(labels).casefold()
    has_dialog_container = any(
        element.role == "dialog"
        or any(token in element.role.casefold() for token in ("popup", "modal", "bottomsheet"))
        for element in elements
    )
    has_dialog_resource = any(
        element.resource_id and any(token in element.resource_id.casefold() for token in (
            "alerttitle", "button1", "button2", "button3", "parentpanel", "buttonpanel", "title_template",
        ))
        for element in elements
    )
    permission = any(
        element.package and "permission" in element.package.casefold() for element in elements
    ) or "allow" in text and "permission" in text
    if not (has_dialog_container or has_dialog_resource or permission):
        return None
    dismiss_ids = [
        element.id for element in elements
        if element.visible and element.enabled and element.clickable and element.label
        and element.label.casefold().strip() in _SAFE_DISMISS_LABELS
    ]
    kind = DialogKind.PERMISSION if permission else (
        DialogKind.SENSITIVE if any(term in text for term in _SENSITIVE_TERMS) else
        DialogKind.SAFE_DISMISSIBLE if dismiss_ids else DialogKind.UNKNOWN
    )
    title = next((element.label for element in elements if element.role in {"dialog", "text"} and element.label), None)
    return DialogInfo(kind=kind, title=title, safe_dismiss_element_ids=dismiss_ids)


def normalize(raw: RawDeviceState) -> SemanticState:
    elements: list[SemanticElement] = []
    loading = False
    for index, item in enumerate(raw.elements):
        label, role = _clean(item.text) or _clean(item.content_description), _role(item)
        if role == "progressbar" or (label and label.casefold() in {"loading", "laden", "please wait"}): loading = True
        elements.append(SemanticElement(
            id=item.node_id or f"e{index + 1}", role=role, label=label, resource_id=item.resource_id, package=item.package, bounds=item.bounds,
            clickable=item.clickable, editable=item.editable, enabled=item.enabled, selected=item.selected,
            visible=item.visible, scrollable=item.scrollable, focused=item.focused, focusable=item.focusable,
            checkable=item.checkable, checked=item.checked, password=item.password, multiline=item.multiline,
            hint=_clean(item.hint), state_description=_clean(item.state_description),
            available_actions=item.available_actions, window_id=item.window_id, parent_id=item.parent_id,
            child_ids=item.child_ids, depth=item.depth, raw_index=index))
    state = SemanticState(app=raw.package, screen_hint=raw.activity, elements=elements, fingerprint="",
                          loading=loading, dialog=_dialog(elements), truncated=raw.truncated, raw_snapshot_id=raw.snapshot_id,
                          keyboard_visible=raw.keyboard_visible, focused_element_id=raw.focused_element_id,
                          active_window_id=raw.active_window_id)
    return state.model_copy(update={"fingerprint": semantic_fingerprint(state)})

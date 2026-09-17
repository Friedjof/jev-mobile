"""Normalize backend snapshots into compact deterministic semantic state."""

from __future__ import annotations

import re
from .fingerprint import semantic_fingerprint
from .models import RawDeviceElement, RawDeviceState, SemanticElement, SemanticState


def _clean(value: str | None) -> str | None:
    return re.sub(r"\s+", " ", value).strip() if value and value.strip() else None


def _role(element: RawDeviceElement) -> str:
    name = (element.class_name or "").rsplit(".", 1)[-1].lower()
    if element.editable or "edittext" in name: return "text_field"
    if "checkbox" in name: return "checkbox"
    if "switch" in name: return "switch"
    if "progress" in name: return "progressbar"
    if "image" in name: return "image"
    if "button" in name or element.clickable: return "button"
    if "text" in name: return "text"
    return name or "view"


def normalize(raw: RawDeviceState) -> SemanticState:
    elements: list[SemanticElement] = []
    loading = False
    for index, item in enumerate(raw.elements):
        label, role = _clean(item.text) or _clean(item.content_description), _role(item)
        if role == "progressbar" or (label and label.casefold() in {"loading", "laden", "please wait"}): loading = True
        elements.append(SemanticElement(
            id=f"e{index + 1}", role=role, label=label, resource_id=item.resource_id, package=item.package, bounds=item.bounds,
            clickable=item.clickable, editable=item.editable, enabled=item.enabled, selected=item.selected,
            visible=item.visible, scrollable=item.scrollable, depth=item.depth, raw_index=index))
    state = SemanticState(app=raw.package, screen_hint=raw.activity, elements=elements, fingerprint="",
                          loading=loading, truncated=raw.truncated, raw_snapshot_id=raw.snapshot_id)
    return state.model_copy(update={"fingerprint": semantic_fingerprint(state)})

"""Backend-neutral raw and semantic UI state models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field


class Bounds(BaseModel):
    x: int
    y: int
    width: int
    height: int

    def bucket(self, size: int = 24) -> tuple[int, int, int, int]:
        return tuple(round(value / size) for value in (self.x, self.y, self.width, self.height))


class RawDeviceElement(BaseModel):
    node_id: str | None = None
    text: str | None = None
    content_description: str | None = None
    resource_id: str | None = None
    class_name: str | None = None
    package: str | None = None
    bounds: Bounds | None = None
    clickable: bool = False
    editable: bool = False
    enabled: bool = True
    selected: bool = False
    visible: bool = True
    scrollable: bool = False
    focused: bool = False
    focusable: bool = False
    checkable: bool = False
    checked: bool = False
    password: bool = False
    multiline: bool = False
    hint: str | None = None
    state_description: str | None = None
    available_actions: list[str] = Field(default_factory=list)
    window_id: int | None = None
    parent_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    depth: int = 0
    extras: dict[str, Any] = Field(default_factory=dict)


class RawDeviceState(BaseModel):
    package: str | None = None
    activity: str | None = None
    elements: list[RawDeviceElement] = Field(default_factory=list)
    screenshot: bytes | None = None
    snapshot_id: str | None = None
    truncated: bool = False
    observed_at_monotonic: float = 0.0
    keyboard_visible: bool = False
    focused_element_id: str | None = None
    active_window_id: int | None = None


class DialogKind(StrEnum):
    SAFE_DISMISSIBLE = "safe_dismissible"
    PERMISSION = "permission"
    SENSITIVE = "sensitive"
    UNKNOWN = "unknown"


class DialogInfo(BaseModel):
    kind: DialogKind
    title: str | None = None
    message: str | None = None
    safe_dismiss_element_ids: list[str] = Field(default_factory=list)


class SemanticElement(BaseModel):
    id: str
    role: str
    label: str | None = None
    resource_id: str | None = None
    package: str | None = None
    bounds: Bounds | None = None
    clickable: bool
    editable: bool
    enabled: bool
    selected: bool
    visible: bool
    scrollable: bool
    focused: bool = False
    focusable: bool = False
    checkable: bool = False
    checked: bool = False
    password: bool = False
    multiline: bool = False
    hint: str | None = None
    state_description: str | None = None
    available_actions: list[str] = Field(default_factory=list)
    window_id: int | None = None
    parent_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    depth: int
    raw_index: int


class SemanticState(BaseModel):
    app: str | None = None
    screen_hint: str | None = None
    elements: list[SemanticElement]
    fingerprint: str
    loading: bool = False
    dialog: DialogInfo | None = None
    truncated: bool = False
    raw_snapshot_id: str | None = None
    keyboard_visible: bool = False
    focused_element_id: str | None = None
    active_window_id: int | None = None

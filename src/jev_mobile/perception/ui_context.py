"""Semantic, goal-independent description of a normalized Android screen."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..state.models import SemanticState


class UIContext(BaseModel):
    snapshot_id: str
    app: str | None = None
    screen: str | None = None
    headings: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    keyboard_visible: bool = False
    dialog: dict[str, object] | None = None
    navigation_context: str | None = None

    @classmethod
    def from_state(cls, snapshot_id: str, state: SemanticState) -> "UIContext":
        text = [item.label for item in state.elements if item.visible and item.label]
        headings = [item.label for item in state.elements if item.visible and item.role in {"heading", "title"} and item.label]
        return cls(snapshot_id=snapshot_id, app=state.app, screen=state.screen_hint,
                   headings=headings, visible_text=text, keyboard_visible=state.keyboard_visible,
                   dialog=state.dialog.model_dump(mode="json") if state.dialog else None)

"""Technically executable actions only; no task or app-specific ranking."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..actions.models import ActionRisk
from ..state.models import SemanticState
from .snapshot_refs import SnapshotRefRegistry


class CatalogAction(BaseModel):
    ref: str
    label: str
    role: str
    semantic_role: str
    current_value: str | None = None
    capabilities: list[str]
    risk: ActionRisk = ActionRisk.READ_ONLY
    bounds: tuple[int, int, int, int] | None = None


class ActionCatalog(BaseModel):
    snapshot_id: str
    actions: list[CatalogAction] = Field(default_factory=list)

    @classmethod
    def build(cls, state: SemanticState, registry: SnapshotRefRegistry) -> "ActionCatalog":
        snapshot_id, targets = registry.register(state)
        actions: list[CatalogAction] = []
        for descriptor in targets.values():
            element = next(item for item in state.elements if item.id == descriptor.backend_node_id)
            capabilities: list[str] = []
            if element.clickable and element.enabled: capabilities.append("activate")
            if element.editable and element.enabled: capabilities.extend(["set_text", "append_text", "clear_text"])
            if element.scrollable and element.enabled: capabilities.append("scroll")
            if not capabilities: continue
            actions.append(CatalogAction(ref=descriptor.ref, label=descriptor.text or element.field_name or element.role,
                         role=element.role, semantic_role=element.field_role or element.role,
                         current_value=element.value if element.editable else None, capabilities=capabilities,
                         risk=ActionRisk.REVERSIBLE if element.editable or element.checkable else ActionRisk.READ_ONLY,
                         bounds=tuple(descriptor.bounds.model_dump().values()) if descriptor.bounds else None))
        return cls(snapshot_id=snapshot_id, actions=actions)

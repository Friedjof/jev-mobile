"""Backend-neutral identity for a UI target in one observation."""

from __future__ import annotations

from pydantic import BaseModel

from ..state.models import Bounds, SemanticElement


class TargetDescriptor(BaseModel):
    snapshot_id: str
    ref: str
    resource_id: str | None = None
    text: str | None = None
    role: str | None = None
    bounds: Bounds | None = None
    editable: bool = False
    package: str | None = None
    window_id: int | None = None
    backend_node_id: str | None = None

    @classmethod
    def from_element(cls, snapshot_id: str, ref: str, element: SemanticElement) -> "TargetDescriptor":
        return cls(snapshot_id=snapshot_id, ref=ref, resource_id=element.resource_id,
                   text=element.accessible_label or element.label, role=element.role,
                   bounds=element.bounds, editable=element.editable, package=element.package,
                   window_id=element.window_id, backend_node_id=element.id)

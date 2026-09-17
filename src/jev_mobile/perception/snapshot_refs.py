"""Snapshot-local references; old UI references are deliberately invalid."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..state.models import SemanticState
from .target_descriptor import TargetDescriptor


class StaleSnapshotReference(ValueError):
    pass


@dataclass(slots=True)
class SnapshotRefRegistry:
    _sequence: int = 0
    _current_id: str | None = None
    _targets: dict[str, TargetDescriptor] = field(default_factory=dict)

    def register(self, state: SemanticState) -> tuple[str, dict[str, TargetDescriptor]]:
        self._sequence += 1
        snapshot_id = f"s{self._sequence}"
        self._current_id, self._targets = snapshot_id, {}
        for index, element in enumerate(state.elements, start=1):
            ref = f"{snapshot_id}:e{index}"
            self._targets[ref] = TargetDescriptor.from_element(snapshot_id, ref, element)
        return snapshot_id, dict(self._targets)

    @property
    def current_snapshot_id(self) -> str | None:
        return self._current_id

    def resolve(self, ref: str) -> TargetDescriptor:
        if not self._current_id or not ref.startswith(f"{self._current_id}:"):
            raise StaleSnapshotReference(f"{ref!r} does not belong to current snapshot {self._current_id!r}")
        try:
            return self._targets[ref]
        except KeyError as error:
            raise StaleSnapshotReference(f"unknown element reference: {ref}") from error

"""One mutation transaction: journal intent, execute once, then observe."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from ..device.base import MutationOutcomeUnknown, MutationRejected
from ..perception.snapshot_refs import SnapshotRefRegistry, StaleSnapshotReference
from ..state.models import SemanticState
from .mutation_family import MutationFamily
from .mutation_journal import MutationEntry, MutationJournal, MutationOutcome


class MutationEngine:
    def __init__(self, registry: SnapshotRefRegistry, journal: MutationJournal,
                 observe: Callable[[], Awaitable[SemanticState]]) -> None:
        self.registry, self.journal, self.observe = registry, journal, observe

    async def execute(self, action: str, target_ref: str, intended_effect: str,
                      operation: Callable[[str], Awaitable[object]],
                      effect_observed: Callable[[SemanticState], bool], family: MutationFamily = MutationFamily.OTHER,
                      fault_callback: Callable[[str, MutationEntry], bool] | None = None) -> MutationEntry:
        try:
            target = self.registry.resolve(target_ref)
        except StaleSnapshotReference:
            entry = self.journal.begin_action(action=action, snapshot_id=target_ref.split(":", 1)[0], intended_effect=intended_effect, family=family)
            return self.journal.resolve_action(entry, MutationOutcome.TARGET_STALE)
        entry = self.journal.begin_action(action=action, snapshot_id=target.snapshot_id,
                                          target=target, intended_effect=intended_effect, family=family)
        try:
            await operation(target.backend_node_id or "")
            self._fault("after_device_execute", entry, fault_callback)
        except MutationRejected:
            return self.journal.resolve_action(entry, MutationOutcome.NOT_EXECUTED)
        except MutationOutcomeUnknown:
            # The required observation happens before any possible retry decision.
            state = await self.observe()
            entry.post_state_fingerprint = state.fingerprint
            return self.journal.resolve_action(entry, MutationOutcome.EXECUTED_CONFIRMED if effect_observed(state) else MutationOutcome.EXECUTED_AMBIGUOUS)
        state = await self.observe()
        entry.post_state_fingerprint = state.fingerprint
        self._fault("after_post_mutation_observe", entry, fault_callback)
        self._fault("before_mutation_resolve", entry, fault_callback)
        return self.journal.resolve_action(entry, MutationOutcome.EXECUTED_CONFIRMED if effect_observed(state) else MutationOutcome.EXECUTED_NO_EFFECT)

    @staticmethod
    def _fault(point: str, entry: MutationEntry, callback: Callable[[str, MutationEntry], bool] | None) -> None:
        """Development-only crash-window probes; inert unless explicitly enabled."""
        wanted_family = os.getenv("JEV_MOBILE_FAULT_FAMILY", "").casefold()
        if os.getenv("JEV_MOBILE_FAULT_POINT", "").casefold() == point and (not wanted_family or wanted_family == entry.family.value) and (callback is None or callback(point, entry)):
            os._exit(97)

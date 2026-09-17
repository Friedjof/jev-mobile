"""Verified text mutations over a snapshot-bound semantic target."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..device.base import DeviceAdapter, MutationOutcomeUnknown, MutationRejected
from ..perception.target_descriptor import TargetDescriptor
from ..state.models import SemanticState
from .text_input import TextInputOutcome


class TextInputService:
    def __init__(self, device: DeviceAdapter, observe: Callable[[], Awaitable[SemanticState]]) -> None:
        self.device, self.observe = device, observe

    async def set_text(self, target: TargetDescriptor, text: str) -> TextInputOutcome:
        return await self._write("type_text", target, text, text)

    async def append_text(self, target: TargetDescriptor, text: str, expected: str) -> TextInputOutcome:
        return await self._write("append_text", target, text, expected)

    async def clear_text(self, target: TargetDescriptor) -> TextInputOutcome:
        return await self._write("clear_text", target, None, "")

    async def focus(self, target: TargetDescriptor) -> TextInputOutcome:
        try:
            await self.device.tap(target.backend_node_id or "")
        except (MutationRejected, MutationOutcomeUnknown):
            return TextInputOutcome.COMMIT_OUTCOME_UNKNOWN
        return TextInputOutcome.ACCEPTED_UNVERIFIED

    async def verify_text(self, target: TargetDescriptor, expected: str) -> TextInputOutcome:
        state = await self.observe()
        element = self._relocate(state, target)
        if element is None: return TextInputOutcome.TARGET_LOST
        actual = element.value if element.value is not None else element.label or ""
        return TextInputOutcome.VERIFIED if actual == expected else TextInputOutcome.ACCEPTED_UNVERIFIED

    async def _write(self, method: str, target: TargetDescriptor, text: str | None, expected: str) -> TextInputOutcome:
        try:
            operation = getattr(self.device, method)
            if text is None: await operation(target.backend_node_id)
            else: await operation(text, target.backend_node_id)
        except MutationRejected:
            return TextInputOutcome.REJECTED
        except MutationOutcomeUnknown:
            # Always observe before classifying; never replay an uncertain write.
            result = await self.verify_text(target, expected)
            return TextInputOutcome.VERIFIED if result == TextInputOutcome.VERIFIED else TextInputOutcome.COMMIT_OUTCOME_UNKNOWN
        return await self.verify_text(target, expected)

    @staticmethod
    def _relocate(state: SemanticState, target: TargetDescriptor):
        candidates = [item for item in state.elements if item.visible and item.editable]
        exact = [item for item in candidates if target.resource_id and item.resource_id == target.resource_id]
        if len(exact) == 1: return exact[0]
        identity = [item for item in candidates if item.id == target.backend_node_id and item.role == target.role]
        return identity[0] if len(identity) == 1 else None

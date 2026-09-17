"""Adaptive semantic-state stabilization without fixed multi-second sleeps."""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from ..device.base import DeviceAdapter
from .models import SemanticState
from .normalize import normalize


class UiReadiness(StrEnum):
    UNCHANGED = "unchanged"
    CHANGING = "changing"
    LOADING = "loading"
    READY = "ready"
    STUCK = "stuck"


class StabilizationResult:
    def __init__(self, readiness: UiReadiness, state: SemanticState, elapsed_ms: float) -> None:
        self.readiness, self.state, self.elapsed_ms = readiness, state, elapsed_ms


class UIStateStabilizer:
    def __init__(self, minimum_stable_time: float = 0.25, timeout: float = 5.0) -> None:
        self.minimum_stable_time, self.timeout = minimum_stable_time, timeout

    async def wait_ready(self, device: DeviceAdapter, previous_fingerprint: str | None = None) -> StabilizationResult:
        started, stable_since, previous, last_state = time.monotonic(), None, None, None
        changed, interval = previous_fingerprint is None, 0.05
        while time.monotonic() - started < self.timeout:
            state, now = normalize(await device.observe()), time.monotonic()
            last_state = state
            changed = changed or (previous_fingerprint is not None and state.fingerprint != previous_fingerprint)
            if previous and previous.fingerprint == state.fingerprint and not state.loading:
                stable_since = stable_since or now
                if changed and now - stable_since >= self.minimum_stable_time:
                    return StabilizationResult(UiReadiness.READY, state, (now - started) * 1000)
            else: stable_since = None
            previous = state
            await asyncio.sleep(interval)
            interval = min(0.2, interval * 1.5)
        assert last_state is not None
        readiness = UiReadiness.LOADING if last_state.loading else (UiReadiness.UNCHANGED if not changed else UiReadiness.STUCK)
        return StabilizationResult(readiness, last_state, (time.monotonic() - started) * 1000)

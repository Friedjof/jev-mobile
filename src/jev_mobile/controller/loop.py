"""Fast structured observe-decide-act controller."""

from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Callable
from ..actions.builder import build_candidates, goal_keywords
from ..actions.models import ActionKind, ActionRisk, CandidateAction
from ..config import Settings
from ..device.base import DeviceAdapter
from ..escalation.handler import LocalEscalationHandler
from ..escalation.models import EscalationResult
from ..providers.base import DecisionProvider, ProviderUnavailable
from ..providers.jev import probability_margin
from ..state.stabilizer import UIStateStabilizer, UiReadiness
from ..tracing.trace import TraceWriter
from .recovery import LoopGuard
from .state_machine import ControllerStatus


@dataclass(slots=True)
class RunResult:
    status: ControllerStatus
    task_id: str
    message: str


class MobileController:
    def __init__(self, device: DeviceAdapter, provider: DecisionProvider, settings: Settings, trace: TraceWriter,
                 on_event: Callable[[str, dict[str, object]], None] | None = None) -> None:
        self.device, self.provider, self.settings, self.trace = device, provider, settings, trace
        self.stabilizer = UIStateStabilizer(settings.minimum_stable_time_seconds, settings.stabilizer_timeout_seconds)
        self.escalations = LocalEscalationHandler(settings.trace_dir)
        self.on_event = on_event

    def _emit(self, event: str, **payload: object) -> None:
        if self.on_event:
            self.on_event(event, payload)

    async def run(self, goal: str) -> RunResult:
        started, previous, recent = time.monotonic(), None, []
        guard, target_open, can_complete = LoopGuard(self.settings.max_same_state_count), False, False
        settings_root_fingerprint: str | None = None
        settings_root_labels: set[str] = set()
        self.trace.write(event="task_started", goal=goal, provider=self.provider.name)
        self._emit("started", goal=goal)
        for step in range(1, self.settings.max_steps + 1):
            if time.monotonic() - started > self.settings.max_runtime_seconds:
                return await self._escalate(goal, "runtime_limit", None, [], recent)
            stabilized = await self.stabilizer.wait_ready(self.device, previous)
            state, previous = stabilized.state, stabilized.state.fingerprint
            self.trace.write(event="state", step=step, readiness=stabilized.readiness,
                             stabilize_ms=round(stabilized.elapsed_ms, 1), state=state.model_dump(mode="json"))
            self._emit("state_ready", step=step, state=state, stabilize_ms=stabilized.elapsed_ms)
            if stabilized.readiness != UiReadiness.READY or guard.observe(state.fingerprint):
                return await self._escalate(goal, stabilized.readiness.value, state, [], recent)
            if state.app == "com.android.settings":
                if settings_root_fingerprint is None:
                    settings_root_fingerprint = state.fingerprint
                    settings_root_labels = {
                        element.label.casefold() for element in state.elements
                        if (element.visible and element.clickable and element.label
                            and element.resource_id == "android:id/title")
                    }
                elif target_open:
                    current_labels = {
                        element.label.casefold() for element in state.elements
                        if (element.visible and element.clickable and element.label
                            and element.resource_id == "android:id/title")
                    }
                    returned_to_root = state.fingerprint == settings_root_fingerprint or len(
                        settings_root_labels & current_labels
                    ) >= 3
                    if returned_to_root:
                        can_complete = True
            actions = build_candidates(state, goal, can_complete=can_complete, return_mode=target_open)
            self._emit("candidates", actions=actions)
            if can_complete:
                self.trace.write(event="task_completed", step=step, completion="verified_return_transition")
                self._emit("completed")
                return RunResult(ControllerStatus.COMPLETED, self.trace.task_id, "Task completed")
            decision_started = time.monotonic()
            try:
                decision = await self.provider.decide(goal, state, actions)
            except ProviderUnavailable as error:
                return await self._escalate(goal, str(error), state, actions, recent)
            decision_ms = (time.monotonic() - decision_started) * 1000
            selected = next((item for item in actions if item.id == decision.action_id), None)
            margin = probability_margin(decision.probabilities or {})
            self.trace.write(event="decision", step=step, decision_ms=round(decision_ms, 1),
                             action_id=decision.action_id, confidence=decision.confidence,
                             probability_margin=margin, provider_metadata=decision.reasoning_metadata)
            self._emit("decision", provider=self.provider.name, decision=decision, margin=margin)
            if (selected is None or decision.confidence < self.settings.confidence_threshold
                    or (margin is not None and margin < self.settings.minimum_probability_margin)):
                return await self._escalate(goal, "invalid_or_low_confidence_decision", state, actions, recent)
            if selected.kind == ActionKind.ESCALATE:
                return await self._escalate(goal, "provider_requested", state, actions, recent)
            if selected.kind == ActionKind.DONE:
                self.trace.write(event="task_completed", step=step)
                return RunResult(ControllerStatus.COMPLETED, self.trace.task_id, "Task completed")
            if selected.risk in {ActionRisk.EXTERNAL_EFFECT, ActionRisk.SENSITIVE}:
                return await self._escalate(goal, "action_requires_approval", state, actions, recent)
            if guard.action(state.fingerprint, selected.id):
                return await self._escalate(goal, "action_loop", state, actions, recent)
            await self._execute(selected)
            self._emit("action", action=selected)
            recent.append(selected.label)
            if selected.kind == ActionKind.TAP and any(
                keyword in selected.label.casefold() for keyword in goal_keywords(goal)
            ):
                target_open = True
                if "back" not in goal.casefold() and "return" not in goal.casefold():
                    can_complete = True
            if selected.kind == ActionKind.BACK and target_open:
                # The stabilizer verifies a subsequent state transition before DONE is offered.
                can_complete = True
            self.trace.write(event="action", step=step, action=selected.model_dump(mode="json"))
        return await self._escalate(goal, "step_limit", None, [], recent)

    async def _execute(self, action: CandidateAction) -> None:
        if action.kind == ActionKind.TAP: await self.device.tap(action.target_element_id or "")
        elif action.kind == ActionKind.LONG_PRESS: await self.device.long_press(action.target_element_id or "")
        elif action.kind == ActionKind.TYPE_TEXT: await self.device.type_text(action.text or "", action.target_element_id)
        elif action.kind == ActionKind.SCROLL_DOWN: await self.device.swipe("down")
        elif action.kind == ActionKind.SCROLL_UP: await self.device.swipe("up")
        elif action.kind == ActionKind.BACK: await self.device.back()
        elif action.kind == ActionKind.LAUNCH_APP: await self.device.launch_app(action.package or "")

    async def _escalate(self, goal: str, reason: str, state, actions, recent: list[str]) -> RunResult:
        if state is None:
            state = (await self.stabilizer.wait_ready(self.device)).state
        payload = EscalationResult(task_id=self.trace.task_id, goal=goal, reason=reason, state=state,
                                   candidate_actions=actions, recent_actions=recent)
        path = self.escalations.pause(payload)
        self.trace.write(event="escalated", reason=reason, checkpoint=str(path))
        self._emit("escalated", reason=reason)
        return RunResult(ControllerStatus.ESCALATED, self.trace.task_id, f"Escalated: {path}")

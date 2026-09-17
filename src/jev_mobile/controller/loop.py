"""Fast structured observe-decide-act controller."""

from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Callable
from ..actions.builder import build_action_page, goal_keywords, known_note_text_present
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
        last_action: CandidateAction | None = None
        retry_counts: dict[tuple[str, str], int] = {}
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
            if step == 1:
                try:
                    plan = await self._ensure_plan(goal, state)
                except ProviderUnavailable as error:
                    return await self._escalate(goal, str(error), state, [], recent)
                if plan:
                    self.trace.write(event="task_plan", step=step, summary=plan.summary,
                                     plan_steps=len(plan.steps), completion_criteria=plan.completion_criteria)
                    self._emit("task_plan", plan=plan)
            if known_note_text_present(state, goal):
                if await self._completion_verified(goal, state, "verified_note_text"):
                    return RunResult(ControllerStatus.COMPLETED, self.trace.task_id, "Task completed")
                previous = None
                continue
            if stabilized.readiness != UiReadiness.READY and not state.dialog:
                retry_key = (state.fingerprint, last_action.id) if last_action else None
                can_retry = (
                    stabilized.readiness == UiReadiness.UNCHANGED and last_action is not None
                    and last_action.kind in {ActionKind.TAP, ActionKind.TYPE_TEXT}
                    and retry_key is not None and retry_counts.get(retry_key, 0) < self.settings.max_action_retries
                )
                if can_retry:
                    retry_counts[retry_key] = retry_counts.get(retry_key, 0) + 1
                    self.trace.write(event="action_retry", step=step, action_id=last_action.id,
                                     attempt=retry_counts[retry_key])
                    self._emit("retry", action=last_action, attempt=retry_counts[retry_key])
                    await self._execute(last_action)
                    continue
                return await self._escalate(goal, f"{stabilized.readiness.value}_after_action", state, [], recent)
            if guard.observe(state.fingerprint):
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
            page_index = 0
            goal_for_step = self._contextual_goal(goal, state)
            plan_refreshed = False
            while True:
                page = build_action_page(
                    state, goal_for_step, page_index=page_index, page_size=self.settings.action_page_size,
                    max_pages=self.settings.max_action_pages, can_complete=can_complete, return_mode=target_open,
                )
                actions = page.actions
                self._emit("candidates", page=page)
                if can_complete:
                    if await self._completion_verified(goal, state, "verified_return_transition"):
                        return RunResult(ControllerStatus.COMPLETED, self.trace.task_id, "Task completed")
                    can_complete, target_open, goal_for_step = False, False, self._contextual_goal(goal, state)
                    continue
                decision_started = time.monotonic()
                try:
                    decision = await self.provider.decide(goal, state, actions)
                except ProviderUnavailable as error:
                    return await self._escalate(goal, str(error), state, actions, recent)
                decision_ms = (time.monotonic() - decision_started) * 1000
                selected = next((item for item in actions if item.id == decision.action_id), None)
                margin = probability_margin(decision.probabilities or {})
                self.trace.write(event="decision", step=step, page=page.index + 1, decision_ms=round(decision_ms, 1),
                                 action_id=decision.action_id, confidence=decision.confidence,
                                 probability_margin=margin, provider_metadata=decision.reasoning_metadata)
                self._emit("decision", provider=self.provider.name, decision=decision, margin=margin)
                if (decision.reasoning_metadata or {}).get("recovery_plan_created") and not plan_refreshed:
                    plan_refreshed = True
                    page_index = 0
                    goal_for_step = self._contextual_goal(goal, state)
                    self.trace.write(event="recovery_plan", step=step,
                                     summary=(decision.reasoning_metadata or {}).get("plan_summary"),
                                     plan_steps=(decision.reasoning_metadata or {}).get("plan_steps"))
                    self._emit("recovery_plan", metadata=decision.reasoning_metadata or {})
                    continue
                safe_single_target = (
                    selected is not None
                    and selected.goal_directed
                    and selected.risk == ActionRisk.READ_ONLY
                    and len([action for action in actions if action.goal_directed]) == 1
                    and decision.confidence >= self.settings.single_safe_action_confidence_threshold
                )
                if (selected is None or (decision.confidence < self.settings.confidence_threshold and not safe_single_target)
                        or (margin is not None and margin < self.settings.minimum_probability_margin)):
                    return await self._escalate(goal, "invalid_or_low_confidence_decision", state, actions, recent)
                if selected.kind == ActionKind.MORE_ACTIONS:
                    self.trace.write(event="action_page_advanced", step=step, page=page.index + 2)
                    self._emit("page_advanced", page=page.index + 2)
                    page_index += 1
                    continue
                break
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
            last_action = selected
            # Verified-return completion is currently implemented only for the
            # bounded Settings-navigation MVP. A generic match such as
            # "Open Keep Notes" must never complete a goal that still asks us
            # to create or fill in content.
            if (state.app == "com.android.settings" and selected.kind == ActionKind.TAP and any(
                keyword in selected.label.casefold() for keyword in goal_keywords(goal)
            )):
                target_open = True
                if "back" not in goal.casefold() and "return" not in goal.casefold():
                    can_complete = True
            if selected.kind == ActionKind.BACK and target_open:
                # The stabilizer verifies a subsequent state transition before DONE is offered.
                can_complete = True
            self.trace.write(event="action", step=step, action=selected.model_dump(mode="json"))
            if selected.kind == ActionKind.WAIT:
                previous = None
        return await self._escalate(goal, "step_limit", None, [], recent)

    def _contextual_goal(self, goal: str, state) -> str:
        contextualize = getattr(self.provider, "contextual_goal", None)
        return contextualize(goal, state) if callable(contextualize) else goal

    async def _ensure_plan(self, goal: str, state):
        ensure = getattr(self.provider, "ensure_plan", None)
        return await ensure(goal, state) if callable(ensure) else None

    async def _completion_verified(self, goal: str, state, completion: str) -> bool:
        verify = getattr(self.provider, "verify_completion", None)
        if not callable(verify):
            self.trace.write(event="task_completed", completion=completion)
            self._emit("completed")
            return True
        try:
            result = await verify(goal, state)
        except ProviderUnavailable:
            return False
        self.trace.write(event="completion_review", complete=result.complete, reason=result.reason,
                         next_instruction=result.next_instruction)
        self._emit("completion_review", result=result)
        if result.complete:
            self.trace.write(event="task_completed", completion=completion)
            self._emit("completed")
        return result.complete

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
        screenshot_path = None
        if self.settings.capture_escalation_screenshots:
            try:
                screenshot = await self.device.screenshot()
                path = self.trace.path.parent / f"{self.trace.task_id}.escalation.png"
                path.write_bytes(screenshot)
                screenshot_path = str(path)
            except Exception:
                # Escalation must remain reliable when screenshots are unsupported.
                screenshot_path = None
        payload = EscalationResult(task_id=self.trace.task_id, goal=goal, reason=reason, state=state,
                                   candidate_actions=actions, recent_actions=recent, screenshot_path=screenshot_path)
        path = self.escalations.pause(payload)
        self.trace.write(event="escalated", reason=reason, checkpoint=str(path))
        self._emit("escalated", reason=reason)
        return RunResult(ControllerStatus.ESCALATED, self.trace.task_id, f"Escalated: {path}")

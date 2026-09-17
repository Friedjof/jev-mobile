"""Fast structured observe-decide-act controller."""

from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Callable
from ..actions.builder import goal_keywords
from ..actions.models import ActionKind, ActionRisk, CandidateAction
from ..actions.mutation_journal import MutationJournal, MutationOutcome
from ..config import Settings
from ..device.base import DeviceAdapter, MutationOutcomeUnknown, MutationRejected
from ..escalation.handler import LocalEscalationHandler
from ..escalation.models import EscalationResult
from ..providers.base import DecisionProvider, ProviderUnavailable
from ..providers.jev import probability_margin
from ..state.stabilizer import UIStateStabilizer, UiReadiness
from ..tracing.trace import TraceWriter
from ..tasks import TaskSpec, task_spec_from_goal
from .context import AgentContext
from .navigator import SemanticNavigator
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
        self.mutation_journal = MutationJournal()
        self._pending_mutation = None

    def _emit(self, event: str, **payload: object) -> None:
        if self.on_event:
            self.on_event(event, payload)

    async def run(self, goal: str, task_spec: TaskSpec | None = None) -> RunResult:
        started, previous, recent = time.monotonic(), None, []
        supplied_task_spec = task_spec is not None
        task_spec = task_spec or self._task_spec(goal)
        agent_context = AgentContext(goal, task_spec)
        navigator = SemanticNavigator(agent_context)
        last_action: CandidateAction | None = None
        guard, target_open, can_complete = LoopGuard(self.settings.max_same_state_count), False, False
        pending_persistence_check = False
        jev_steps_without_progress = 0
        last_counted_transition = 0
        metrics = {
            "jev_calls_total": 0, "llm_calls_total": 0, "device_mutations_total": 0,
            "ambiguous_mutations_total": 0, "verified_mutations_total": 0,
            "semantic_groups_visited": 0, "stale_decisions_discarded": 0,
        }
        settings_root_fingerprint: str | None = None
        settings_root_labels: set[str] = set()
        self.trace.write(event="task_started", goal=goal, provider=self.provider.name)
        self._emit("started", goal=goal)
        for step in range(1, self.settings.max_steps + 1):
            if time.monotonic() - started > self.settings.max_runtime_seconds:
                return await self._escalate(goal, "runtime_limit", None, [], recent)
            stabilized = await self.stabilizer.wait_ready(self.device, previous)
            state, previous = stabilized.state, stabilized.state.fingerprint
            agent_context.observe(state)
            if self._pending_mutation is not None:
                transition = agent_context.recent_transitions[-1] if agent_context.recent_transitions else {}
                transport = getattr(self._pending_mutation, "transport", None)
                if transport == "rejected": outcome = MutationOutcome.NOT_EXECUTED
                elif transport == "unknown": outcome = MutationOutcome.EXECUTED_AMBIGUOUS
                elif transition.get("outcome") in {"success", "progress"}: outcome = MutationOutcome.EXECUTED_CONFIRMED
                else: outcome = MutationOutcome.EXECUTED_NO_EFFECT
                entry = self.mutation_journal.resolve_action(self._pending_mutation, outcome)
                self.trace.write(event="mutation_resolved", step=step, mutation_id=entry.id,
                                 mutation_outcome=outcome, snapshot_id=state.raw_snapshot_id)
                self._pending_mutation = None
            metrics["semantic_groups_visited"] = len(agent_context.visited_groups)
            if agent_context.recent_transitions:
                transition = agent_context.recent_transitions[-1]
                sequence = int(transition.get("sequence", 0))
                if sequence > last_counted_transition:
                    outcome = transition["outcome"]
                    jev_steps_without_progress = 0 if outcome in {"progress", "success"} else jev_steps_without_progress + 1
                    if outcome in {"progress", "success"}:
                        metrics["verified_mutations_total"] += 1
                    last_counted_transition = sequence
            compact_context = agent_context.compact()
            state = state.model_copy(update={
                "recent_context": agent_context.recent_transitions[-12:],
                "agent_context": compact_context,
            })
            self.trace.write(event="state", step=step, readiness=stabilized.readiness,
                             stabilize_ms=round(stabilized.elapsed_ms, 1), state=state.model_dump(mode="json"))
            self._emit("state_ready", step=step, state=state, stabilize_ms=stabilized.elapsed_ms)
            if step == 1 and not supplied_task_spec:
                try:
                    interpretation = await self._interpret_task(goal, state)
                except ProviderUnavailable:
                    interpretation = None
                if interpretation is not None:
                    task_spec = interpretation.to_task_spec()
                    agent_context.set_task_spec(task_spec)
                    agent_context.observe(state)
                    self.trace.write(
                        event="task_interpretation", step=step,
                        content_type=interpretation.content_type, title=interpretation.title,
                        item_candidates=len(interpretation.item_candidates),
                    )
                try:
                    plan = await self._ensure_plan(goal, state)
                except ProviderUnavailable as error:
                    return await self._escalate(goal, str(error), state, [], recent)
                if plan:
                    # A plan may add semantic requirements, but it must not
                    # discard the already resolved Jev task interpretation.
                    agent_context.set_task_spec(plan.task_spec or task_spec)
                    agent_context.observe(state)
                    self.trace.write(event="task_plan", step=step, summary=plan.summary,
                                     plan_steps=len(plan.steps), completion_criteria=plan.completion_criteria)
                    self._emit("task_plan", plan=plan)
            if pending_persistence_check:
                persisted = any(
                    key.startswith("persisted") and value.value == "satisfied"
                    for key, value in agent_context.requirement_states.items()
                )
                if persisted or await self._completion_verified(goal, state, "verified_persistence"):
                    return RunResult(ControllerStatus.COMPLETED, self.trace.task_id, "Task completed")
                pending_persistence_check = False
            if stabilized.readiness != UiReadiness.READY and not state.dialog:
                # A stable unchanged screen is evidence to re-decide, never
                # permission to replay a potentially applied mutation.
                if last_action and last_action.kind in {ActionKind.TAP, ActionKind.TYPE_TEXT}:
                    self.trace.write(event="mutation_no_blind_retry", step=step, action_id=last_action.id)
                    # The observation resolved the previous mutation as no
                    # visible effect. Keep the screen and re-decide with its
                    # failed-path memory instead of replaying or terminating.
                else:
                    return await self._escalate(goal, f"{stabilized.readiness.value}_after_action", state, [], recent)
            if last_action is not None and guard.observe(state.fingerprint):
                return await self._escalate(goal, stabilized.readiness.value, state, [], recent)
            # The just-observed action has been semantically resolved. Local
            # group/page choices must not count it again as a repeated device
            # mutation on later outer-loop iterations.
            last_action = None
            if jev_steps_without_progress >= self.settings.max_jev_steps_without_progress:
                if navigator.explore_next_branch(
                    state,
                    max_group_revisits=self.settings.max_group_revisits,
                    max_safe_exploration_branches=self.settings.max_safe_exploration_branches,
                ):
                    jev_steps_without_progress = 0
                elif await self._recover_after_exploration(goal, state, "jev_progress_budget_exhausted"):
                    jev_steps_without_progress = 0
                else:
                    return await self._escalate(goal, "jev_progress_budget_exhausted", state, [], recent)
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
            task_spec = agent_context.task_spec
            plan_refreshed = False
            while True:
                page = navigator.build(
                    state, goal_for_step, task_spec, can_complete=can_complete, return_mode=target_open,
                    action_page_size=self.settings.action_page_size, max_action_pages=self.settings.max_action_pages,
                    page_index=page_index,
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
                if self.provider.name.startswith("jev"):
                    metrics["jev_calls_total"] += 1
                selected = next((item for item in actions if item.id == decision.action_id), None)
                margin = probability_margin(decision.probabilities or {})
                question_type = "semantic_group" if agent_context.navigation_mode == "screen" and page.total_device_actions > 12 else "action"
                self.trace.write(event="decision", step=step, page=page.index + 1, decision_ms=round(decision_ms, 1),
                                 action_id=decision.action_id, confidence=decision.confidence,
                                 probability_margin=margin, provider_metadata=decision.reasoning_metadata,
                                 current_subgoal=agent_context.current_subgoal,
                                 requirement_summary=agent_context.compact()["requirements"],
                                 recent_context_count=len(agent_context.recent_transitions),
                                 navigation=agent_context.compact()["navigation"], question_type=question_type,
                                 action_candidates=[action.label for action in actions])
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
                deterministic_writes = [
                    action for action in actions
                    if action.kind == ActionKind.TYPE_TEXT and action.goal_directed and action.text
                ]
                # A known literal mapped to one strongly-described editable
                # field is a controller operation, not generated language or
                # an arbitrary device command. Do not waste the zero-LLM path
                # when Jev merely declines an otherwise unambiguous write.
                deterministic_selected = len(deterministic_writes) == 1 and (
                    selected is None or selected.kind == ActionKind.ESCALATE
                    or decision.confidence < self.settings.confidence_threshold
                )
                if deterministic_selected:
                    selected = deterministic_writes[0]
                    self.trace.write(event="deterministic_requirement_action", step=step, action_id=selected.id)
                if (selected is None or (decision.confidence < self.settings.confidence_threshold and not safe_single_target and not deterministic_selected)
                        or (margin is not None and margin < self.settings.minimum_probability_margin)):
                    if navigator.explore_next_branch(
                        state,
                        max_group_revisits=self.settings.max_group_revisits,
                        max_safe_exploration_branches=self.settings.max_safe_exploration_branches,
                    ):
                        self.trace.write(
                            event="jev_exploration", step=step, reason="low_confidence_or_margin",
                            navigation=agent_context.compact()["navigation"],
                        )
                        state = state.model_copy(update={"agent_context": agent_context.compact()})
                        continue
                    if await self._recover_after_exploration(goal, state, "low_confidence_or_margin"):
                        agent_context.set_task_spec(self._task_spec(goal))
                        task_spec = agent_context.task_spec
                        state = state.model_copy(update={"agent_context": agent_context.compact()})
                        continue
                    return await self._escalate(goal, "invalid_or_low_confidence_decision", state, actions, recent)
                if selected.kind == ActionKind.MORE_ACTIONS:
                    self.trace.write(event="action_page_advanced", step=step, page=page.index + 2)
                    self._emit("page_advanced", page=page.index + 2)
                    page_index += 1
                    continue
                if navigator.local_action(selected, state):
                    state = state.model_copy(update={
                        "recent_context": agent_context.recent_transitions[-12:],
                        "agent_context": agent_context.compact(),
                    })
                    continue
                break
            if selected.kind == ActionKind.ESCALATE:
                if navigator.explore_next_branch(
                    state,
                    max_group_revisits=self.settings.max_group_revisits,
                    max_safe_exploration_branches=self.settings.max_safe_exploration_branches,
                ):
                    state = state.model_copy(update={"agent_context": agent_context.compact()})
                    # This is a controller-local navigation transition, not a
                    # phone mutation. A fresh readiness cycle must not treat
                    # the unchanged screen as a failed action.
                    previous = None
                    continue
                if await self._recover_after_exploration(goal, state, "provider_requested"):
                    agent_context.set_task_spec(self._task_spec(goal))
                    task_spec = agent_context.task_spec
                    state = state.model_copy(update={"agent_context": agent_context.compact()})
                    continue
                return await self._escalate(goal, "provider_requested", state, actions, recent)
            if selected.kind == ActionKind.DONE:
                self.trace.write(event="task_completed", step=step)
                return RunResult(ControllerStatus.COMPLETED, self.trace.task_id, "Task completed")
            if selected.risk in {ActionRisk.EXTERNAL_EFFECT, ActionRisk.SENSITIVE}:
                return await self._escalate(goal, "action_requires_approval", state, actions, recent)
            if not self._target_is_fresh(selected, state):
                key = f"{selected.kind}:{selected.target_element_id or selected.label}"
                if key not in agent_context.failed_paths:
                    agent_context.failed_paths.append(key)
                self.trace.write(event="stale_decision_discarded", step=step, action_id=selected.id)
                metrics["stale_decisions_discarded"] += 1
                last_action = None
                continue
            if guard.action(state.fingerprint, selected.id):
                return await self._escalate(goal, "action_loop", state, actions, recent)
            agent_context.begin_action(selected, state)
            # The durable intent exists before the adapter receives any mutation.
            entry = self.mutation_journal.begin_action(action=selected.kind.value,
                snapshot_id=state.raw_snapshot_id or state.fingerprint, intended_effect=selected.label)
            metrics["device_mutations_total"] += 1
            try:
                receipt = await self._execute(selected)
                agent_context.mark_transport_outcome(getattr(receipt, "status", "executed"))
                entry.transport = getattr(receipt, "status", "executed")
            except MutationOutcomeUnknown:
                # The action may have happened. The next observation resolves
                # the intent; it must never be replayed automatically.
                agent_context.mark_transport_outcome("unknown")
                entry.transport = "unknown"
                self.trace.write(event="mutation_outcome_unknown", step=step, action_id=selected.id)
                metrics["ambiguous_mutations_total"] += 1
            except MutationRejected as error:
                agent_context.mark_transport_outcome("rejected")
                entry.transport = "rejected"
                self.trace.write(event="mutation_rejected", step=step, action_id=selected.id, reason=str(error))
            except Exception:
                # Non-bridge adapters preserve their legacy errors. Record
                # intent first and let observation determine any side effect.
                agent_context.mark_transport_outcome("unknown")
                entry.transport = "unknown"
                self.trace.write(event="mutation_outcome_unknown", step=step, action_id=selected.id)
            self._emit("action", action=selected)
            recent.append(selected.label)
            last_action = selected
            self._pending_mutation = entry
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
            if (
                selected.kind == ActionKind.BACK
                and task_spec.intent == "create_note"
                and any(element.visible and element.editable for element in state.elements)
            ):
                pending_persistence_check = True
            self.trace.write(event="action", step=step, action=selected.model_dump(mode="json"))
            self.trace.write(event="metrics", step=step, **metrics,
                             jev_calls_since_progress=jev_steps_without_progress)
            if selected.kind == ActionKind.WAIT:
                previous = None
        return await self._escalate(goal, "step_limit", None, [], recent)

    def _contextual_goal(self, goal: str, state) -> str:
        contextualize = getattr(self.provider, "contextual_goal", None)
        return contextualize(goal, state) if callable(contextualize) else goal

    def _task_spec(self, goal: str) -> TaskSpec:
        task_spec = getattr(self.provider, "task_spec", None)
        return task_spec(goal) if callable(task_spec) else task_spec_from_goal(goal)

    @staticmethod
    def _observed_outcome(state) -> str:
        """Compact observed outcome for the next Jev or planner decision."""
        fields = [
            element.field_name or "Text"
            for element in state.elements
            if element.visible and element.editable
        ][:3]
        suffix = f"; writable fields: {', '.join(fields)}" if fields else ""
        return f"Observed {state.app or 'Android UI'}{suffix}"

    async def _ensure_plan(self, goal: str, state):
        ensure = getattr(self.provider, "ensure_plan", None)
        return await ensure(goal, state) if callable(ensure) else None

    async def _interpret_task(self, goal: str, state):
        interpret = getattr(self.provider, "interpret_task", None)
        return await interpret(goal, state) if callable(interpret) else None

    async def _recover_after_exploration(self, goal: str, state, reason: str) -> bool:
        recover = getattr(self.provider, "recover", None)
        if not callable(recover):
            return False
        plan = await recover(goal, state, reason)
        if not plan:
            return False
        self.trace.write(
            event="llm_recovery", reason=reason, summary=plan.summary,
            plan_steps=len(plan.steps), planner_latency_ms=plan.latency_ms,
        )
        self._emit("recovery_plan", metadata={"plan_summary": plan.summary, "plan_steps": len(plan.steps)})
        return True

    async def _completion_verified(self, goal: str, state, completion: str) -> bool:
        verify = getattr(self.provider, "verify_completion", None)
        if not callable(verify):
            if completion == "verified_persistence":
                # A generic fast provider cannot prove a draft was saved.
                return False
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

    async def _execute(self, action: CandidateAction):
        if action.kind == ActionKind.TAP: return await self.device.tap(action.target_element_id or "")
        elif action.kind == ActionKind.LONG_PRESS: await self.device.long_press(action.target_element_id or "")
        elif action.kind == ActionKind.TYPE_TEXT:
            if self.settings.text_input_strategy == "ime":
                ime_input = getattr(self.device, "type_text_ime", None)
                if callable(ime_input):
                    return await ime_input(action.text or "")
            return await self.device.type_text(action.text or "", action.target_element_id)
        elif action.kind == ActionKind.SCROLL_DOWN: await self.device.swipe("down")
        elif action.kind == ActionKind.SCROLL_UP: await self.device.swipe("up")
        elif action.kind == ActionKind.BACK: await self.device.back()
        elif action.kind == ActionKind.LAUNCH_APP: await self.device.launch_app(action.package or "")

    @staticmethod
    def _target_is_fresh(action: CandidateAction, state) -> bool:
        """Reject a decision if its weak tree path no longer denotes the same control."""
        descriptor = action.target_descriptor
        if not descriptor or not action.target_element_id:
            return True
        candidates = [element for element in state.elements if element.visible and element.enabled]
        matches = [element for element in candidates if element.id == action.target_element_id]
        if len(matches) != 1:
            return False
        element = matches[0]
        for key, actual in {
            "window_id": element.window_id,
            "resource_id": element.resource_id,
            "role": element.role,
            "field_role": element.field_role,
        }.items():
            expected = descriptor.get(key)
            if expected is not None and expected != actual:
                return False
        expected_name = descriptor.get("accessible_name")
        if expected_name and expected_name not in {element.label, element.accessible_label}:
            return False
        return True

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

"""The canonical production observe-decide-mutate-verify loop."""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime

from ..actions.models import ActionKind, ActionRisk, CandidateAction
from ..actions.mutation_engine import MutationEngine
from ..actions.mutation_family import MutationFamily
from ..actions.mutation_journal import MutationJournal, MutationOutcome
from ..config import Settings
from ..device.adapter import DeviceAdapter
from ..perception import ActionCatalog, SnapshotRefRegistry
from ..providers.base import ProviderUnavailable
from ..requirements import RequirementEvaluator
from ..requirements.generators import generate_requirements
from ..state.normalize import normalize
from ..state.models import SemanticState
from ..task_store import MobileTask, TaskStatus, TaskStore
from ..tasks import (
    AnswerEffect,
    AnswerEffectKind,
    QuestionOption,
    QuestionSpec,
    QuestionType,
    RequirementStatus,
    TaskContractStatus,
    TaskPolicyMode,
    TaskSpec,
    contract_question,
    information_authorization_question,
    make_question,
    task_spec_from_goal,
    validate_task_contract,
)
from ..tracing.trace import TraceWriter
from .grounding import EntityOwnership, InteractionContext, context_type, evaluate_safe_creation_context, ownership_for
from ..safety.lifecycle_reset import LifecycleResetDecision, evaluate_reset_safety, reset_question


class MobileAgent:
    def __init__(self, store: TaskStore, device_factory: Callable[[], DeviceAdapter], provider: object, settings: Settings) -> None:
        self.store, self.device_factory, self.provider, self.settings = store, device_factory, provider, settings
        self._workers: dict[str, asyncio.Task[None]] = {}

    def start(self, instruction: str, task_spec: TaskSpec | None = None) -> MobileTask:
        task = self.store.create(instruction, task_spec or task_spec_from_goal(instruction))
        return task

    async def step(self, task_id: str) -> MobileTask:
        await self.run(task_id)
        task = self.store.get(task_id); assert task
        return task

    async def run(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if not task or task.status in {TaskStatus.CANCELLED, TaskStatus.SUCCEEDED, TaskStatus.FAILED}: return
        task.status, task.started_at = TaskStatus.RUNNING, task.started_at or datetime.now(UTC); self.store.save(task)
        task = await self._resolve_task_contract(task)
        if task is None:
            return
        declared_requirements = [item for item in generate_requirements(task.task_spec) if item.required]
        if not declared_requirements:
            task.status = TaskStatus.FAILED
            task.failure_reason = "NO_VERIFIABLE_COMPLETION_CRITERIA"
            task.finished_at = datetime.now(UTC)
            self.store.save(task)
            self.store.event(task.id, "NO_VERIFIABLE_COMPLETION_CRITERIA", {
                "intent": task.task_spec.intent,
                "reason": "The task contract contains no required observable completion criteria.",
            }, task.worker_id)
            return
        refs, journal, evaluator, history = SnapshotRefRegistry(), MutationJournal(), RequirementEvaluator(), []
        persistence_list_seen = False
        trace = TraceWriter(self.settings.trace_dir)
        try:
            async with self.device_factory() as device:
                for step in range(1, self.settings.max_steps + 1):
                    latest = self.store.get(task.id)
                    cancellation_requested = bool(latest and latest.cancellation_requested)
                    before = normalize(await device.observe())
                    resumed_question = task.agent_context.pop("resume_requires_fresh_observation", None)
                    if resumed_question:
                        self.store.save(task)
                        self.store.event(task.id, "INPUT_RESUME_OBSERVATION", {
                            "question_id": resumed_question,
                            "snapshot": before.fingerprint,
                        }, task.worker_id)
                    interaction_context = context_type(before)
                    ownership = ownership_for(before, task.agent_context)
                    if task.pending_mutation:
                        # A resumed task has no executable UI objects. Record the
                        # mandatory fresh observation before reconciling intent.
                        self.store.event(task.id, "RECOVERY_OBSERVATION", {"snapshot": before.fingerprint}, task.worker_id)
                        pending = task.pending_mutation
                        expected = pending.get("text")
                        created_editor = pending.get("family") == MutationFamily.CREATE_ENTITY.value and any(e.visible and e.editable for e in before.elements)
                        applied = bool(created_editor or (expected and any((e.value or "") == expected for e in before.elements if e.editable)))
                        outcome = "executed_confirmed" if applied else "executed_ambiguous"
                        if created_editor:
                            task.agent_context["entity_ownership"] = EntityOwnership.CURRENT_TASK.value
                            # The reconciliation observation is authoritative:
                            # do not let the pre-reconciliation FOREIGN value
                            # send this newly-created editor back to a list or
                            # make another create action eligible.
                            ownership = EntityOwnership.CURRENT_TASK
                        self.store.event(task.id, "PENDING_MUTATION_RECONCILED", {"outcome": outcome}, task.worker_id)
                        task.pending_mutation = None; self.store.save(task)
                    # A root entry that returns to the same foreign editor is
                    # a lifecycle boundary, not an invitation to keep pressing
                    # Back. Resetting that task may lose user state, so ask.
                    if (task.agent_context.get("root_attempted") and interaction_context == InteractionContext.EDITOR
                            and ownership == EntityOwnership.FOREIGN and task.task_spec.app_package):
                        authorization = task.agent_context.get("lifecycle_authorization")
                        if isinstance(authorization, dict) and authorization.get("operation") == "restart_app_process" and not authorization.get("consumed"):
                            entry = journal.begin_action(action="restart_app_process", snapshot_id=before.raw_snapshot_id or "recovery",
                                                         intended_effect="Restart target app process", family=MutationFamily.NAVIGATION)
                            task.pending_mutation = {"id": entry.id, "action": "restart_app_process", "family": "navigation", "intended_effect": entry.intended_effect}
                            authorization["consumed"] = True
                            self.store.save(task)
                            self.store.event(task.id, "PROCESS_RESTART_EXECUTION_STARTED", {"mutation_id": entry.id, "package": task.task_spec.app_package})
                            await device.restart_app_process(task.task_spec.app_package)
                            await asyncio.sleep(0.8)
                            intermediate = normalize(await device.observe())
                            await asyncio.sleep(0.5)
                            settled = normalize(await device.observe())
                            result = "stable_progress" if intermediate.fingerprint == settled.fingerprint and context_type(settled) != InteractionContext.EDITOR else ("transient_progress" if intermediate.fingerprint != settled.fingerprint else "no_progress")
                            task.pending_mutation = None
                            task.agent_context.setdefault("recovery_history", []).append({"operation": "restart_app_process", "result": result, "before": before.fingerprint, "after": settled.fingerprint})
                            self.store.save(task)
                            self.store.event(task.id, "PROCESS_RESTART_RESULT", {"result": result, "before": before.fingerprint, "intermediate": intermediate.fingerprint, "after": settled.fingerprint})
                            if result != "stable_progress":
                                task.status = TaskStatus.FAILED
                                task.failure_reason = "ORIENTATION_BLOCKED_BY_PERSISTED_APP_STATE"
                                self.store.save(task)
                                return
                            task.agent_context["root_attempted"] = False
                            continue
                        reset_was_ineffective = isinstance(authorization, dict) and authorization.get("operation") == "reset_app_task" and authorization.get("consumed")
                        if reset_was_ineffective:
                            task.agent_context.setdefault("recovery_history", []).append({"operation": "reset_app_task", "result": "no_progress", "state": before.fingerprint})
                            question = reset_question(task.task_spec.app_package, "restart_app_process")
                            task.status, task.waiting_question, task.waiting_reason = TaskStatus.WAITING_FOR_USER, question, str(question["text"])
                            self.store.save(task)
                            self.store.event(task.id, "APP_INTERNAL_ROOT_RECOVERY_FAILED", {"result": "no_progress"})
                            self.store.event(task.id, "PROCESS_RESTART_REQUIRES_APPROVAL", {"package": task.task_spec.app_package})
                            self.store.event(task.id, "TASK_WAITING_FOR_USER", {"question": question})
                            return
                        decision = evaluate_reset_safety(task_id=task.id, package=task.task_spec.app_package,
                                                         ownership=ownership, context=interaction_context,
                                                         authorization=authorization)
                        if decision == LifecycleResetDecision.REQUIRE_APPROVAL:
                            question = reset_question(task.task_spec.app_package)
                            task.status = TaskStatus.WAITING_FOR_USER
                            task.waiting_question = question
                            task.waiting_reason = str(question["text"])
                            self.store.save(task)
                            self.store.event(task.id, "LIFECYCLE_RESET_REQUIRES_APPROVAL", {"package": task.task_spec.app_package})
                            self.store.event(task.id, "TASK_WAITING_FOR_USER", {"question": question})
                            return
                        if decision == LifecycleResetDecision.ALLOW:
                            entry = journal.begin_action(action="reset_app_task", snapshot_id=before.raw_snapshot_id or "recovery",
                                                         intended_effect="Rebuild target app activity task", family=MutationFamily.NAVIGATION)
                            task.pending_mutation = {"id": entry.id, "action": "reset_app_task", "family": "navigation", "intended_effect": entry.intended_effect}
                            authorization = task.agent_context.get("lifecycle_authorization")
                            if isinstance(authorization, dict): authorization["consumed"] = True
                            self.store.save(task)
                            self.store.event(task.id, "LIFECYCLE_RESET_EXECUTED", {"mutation_id": entry.id, "package": task.task_spec.app_package})
                            await device.reset_app_task(task.task_spec.app_package)
                            await asyncio.sleep(0.8)
                            task.pending_mutation = None
                            task.agent_context["root_attempted"] = False
                            self.store.save(task)
                            self.store.event(task.id, "LIFECYCLE_RESET_RESULT", {"result": "pending_fresh_observation"})
                            continue
                    if cancellation_requested:
                        # Cancellation is observed only at a safe boundary:
                        # any pre-existing mutation has been reconciled above,
                        # and no new device mutation is selected below.
                        task.cancellation_requested = True
                        task.status = TaskStatus.CANCELLED
                        self.store.event(task.id, "TASK_CANCELLED", {}, task.worker_id)
                        break
                    catalog = ActionCatalog.build(before, refs)
                    safe_creation = evaluate_safe_creation_context(before, target_package=task.task_spec.app_package, ownership=ownership, actions=catalog.actions)
                    selections = task.agent_context.get("semantic_selections")
                    requirements = evaluator.evaluate(
                        task.task_spec,
                        before,
                        selections if isinstance(selections, dict) else None,
                    )
                    observed_evidence = {
                        requirement.key: self._requirement_evidence(requirement)
                        for requirement in requirements
                        if requirement.status == RequirementStatus.SATISFIED and requirement.evidence
                    }
                    # A requirement verified in its correct semantic context
                    # remains evidenced when navigation later hides that field.
                    for requirement in requirements:
                        if requirement.kind != "persisted" and task.requirements.get(requirement.key) == RequirementStatus.SATISFIED:
                            requirement.status = RequirementStatus.SATISFIED
                    persisted = next((r for r in requirements if r.kind == "persisted"), None)
                    editor_open = any(e.visible and e.editable for e in before.elements)
                    if persisted:
                        if not persistence_list_seen and persisted.status == RequirementStatus.SATISFIED and not editor_open:
                            persistence_list_seen = True; persisted.status = RequirementStatus.NEEDS_VERIFICATION
                            persisted.reason = "entity found outside editor; reopening required"
                        elif persistence_list_seen and editor_open and any(e.editable and (e.value or "") == (task.task_spec.title or "") for e in before.elements):
                            persisted.status = RequirementStatus.SATISFIED; persisted.reason = "content independently re-observed after reopening"
                            persisted.evidence = {
                                "snapshot_id": before.raw_snapshot_id,
                                "package": before.app,
                                "semantic_role": "entity_editor",
                                "label": "reopened entity",
                                "observed_value": task.task_spec.title,
                                "confidence": 1.0,
                                "verification": "independently_reopened",
                            }
                            observed_evidence[persisted.key] = self._requirement_evidence(persisted)
                        elif persistence_list_seen:
                            persisted.status = RequirementStatus.NEEDS_VERIFICATION
                    task.requirement_evidence.update(observed_evidence)
                    task.requirements = {r.key: r.status for r in requirements}
                    task.current_subgoal = evaluator.current_subgoal(requirements)
                    orientation = "READY" if safe_creation["safe"] else task.agent_context.get("orientation", "UNORIENTED")
                    if orientation != "READY" and interaction_context == InteractionContext.EDITOR and ownership != EntityOwnership.CURRENT_TASK:
                        task.current_subgoal = "Reach a safe context for creating a new entity"
                        orientation = "ESCAPING_FOREIGN_CONTEXT"
                    elif orientation == "READY" and any(r.kind == "note_created" and r.status != RequirementStatus.SATISFIED for r in requirements):
                        task.current_subgoal = "Create the required new entity"
                    task.step_number = step
                    task.agent_context = {**task.agent_context, "snapshot": catalog.snapshot_id, "subgoal": task.current_subgoal, "interaction_context": interaction_context.value, "entity_ownership": ownership.value, "orientation": orientation, "orientation_evidence": safe_creation}
                    self.store.save(task); self.store.event(task.id, "SNAPSHOT_OBSERVED", {"step": step, "snapshot": catalog.snapshot_id})
                    required_requirements = [r for r in requirements if r.required]
                    if required_requirements and all(r.status == RequirementStatus.SATISFIED for r in required_requirements):
                        missing_evidence = [r.key for r in required_requirements if not task.requirement_evidence.get(r.key)]
                        if missing_evidence:
                            task.current_subgoal = "Collect observable evidence for all required criteria"
                            self.store.event(task.id, "VERIFICATION_EVIDENCE_INCOMPLETE", {
                                "missing_requirements": missing_evidence,
                            }, task.worker_id)
                            self.store.save(task)
                            continue
                        task.status = TaskStatus.SUCCEEDED
                        task.result = self._verified_result(task, requirements, step, trace)
                        break
                    context = before.model_copy(update={"agent_context": {"task_spec": task.task_spec.model_dump(mode="json"), "current_subgoal": task.current_subgoal, "requirements": {r.key: r.status.value for r in requirements}, "history": history[-6:]}})
                    input_question = self._ambiguity_question(task, requirements)
                    candidates, mapping = self._candidates(
                        catalog, refs, requirements, task.task_spec, before.app, history,
                        before.fingerprint, interaction_context, ownership, orientation,
                        input_question=input_question,
                    )
                    started = time.monotonic()
                    try: decision = await self.provider.decide(task.instruction, context, candidates)
                    except ProviderUnavailable as error:
                        # Jev/network availability is not evidence that the
                        # Android task failed. Keep the task resumable and let
                        # the durable worker re-claim after a short lease.
                        task.status = TaskStatus.RECOVERING; task.failure_reason = str(error)
                        task.lease_expires_at = datetime.fromtimestamp(time.time() + 20, UTC)
                        self.store.save(task); self.store.event(task.id, "RECOVERY_STARTED", {"category": "JEV_DECISION", "reason": str(error)})
                        return
                    selected = next((a for a in candidates if a.id == decision.action_id), None)
                    if selected is None: raise RuntimeError("Jev selected an invalid action")
                    if selected.kind == ActionKind.REQUEST_INPUT:
                        if not selected.question:
                            raise RuntimeError("REQUEST_INPUT action has no QuestionSpec")
                        self._pause_for_input(task, QuestionSpec.model_validate(selected.question))
                        return
                    action, ref, catalog_target = mapping[selected.id]
                    family = action.mutation_family
                    if action.kind == ActionKind.OPEN_APP_ROOT:
                        task.agent_context["root_attempted"] = True
                        self.store.save(task); self.store.event(task.id, "APP_ROOT_ENTRY_ATTEMPTED", {"package": action.package})
                    engine = MutationEngine(refs, journal, lambda: self._observe(device))
                    task.pending_mutation = {"action": action.kind.value, "family": family.value, "target_ref": ref, "text": action.text, "intended_effect": action.label}
                    self.store.save(task); self.store.event(task.id, "MUTATION_STARTED", task.pending_mutation)
                    if os.getenv("JEV_MOBILE_FAULT_AFTER_MUTATION_BEGIN") == "1":
                        # Development-only crash-window probe. The pending intent
                        # is already durable; a replacement worker must observe
                        # reality and never replay this ref blindly.
                        os._exit(97)
                    entry = await engine.execute(action.kind.value, ref, action.label, self._operation(device, action), lambda after: after.fingerprint != before.fingerprint or self._text_effect(after, action), family, lambda point, entry: self._record_fault(task, point, entry))
                    if family == MutationFamily.CREATE_ENTITY and entry.outcome == MutationOutcome.EXECUTED_CONFIRMED:
                        task.agent_context["entity_ownership"] = EntityOwnership.CURRENT_TASK.value
                    task.pending_mutation = None; self.store.save(task); self.store.event(task.id, "MUTATION_RESOLVED", {"id": entry.id, "outcome": entry.outcome.value})
                    signature = self._attempt_signature(action, catalog_target)
                    history.append({"snapshot": catalog.snapshot_id, "state": before.fingerprint, "subgoal": task.current_subgoal, "action": action.label, "signature": signature, "target_ref": ref, "mutation": entry.outcome.value})
                    repeated = [item for item in history[-3:] if item.get("signature") == signature and item.get("state") == before.fingerprint and item["mutation"] == MutationOutcome.EXECUTED_NO_EFFECT.value]
                    if len(repeated) >= 3:
                        self.store.event(task.id, "RECOVERY_STARTED", {"category": "LOOP_DETECTION", "action": action.label})
                        raise RuntimeError("LOOP_DETECTION: repeated no-effect semantic action")
                    trace.write(event="agent_step", task_id=task.id, step=step, snapshot_id=catalog.snapshot_id, current_subgoal=task.current_subgoal, requirements={r.key: r.status.value for r in requirements}, selected_action=action.model_dump(mode="json"), target_ref=ref, mutation_id=entry.id, mutation_outcome=entry.outcome.value, jev_latency_ms=round((time.monotonic()-started)*1000,1))
                    if entry.outcome in {MutationOutcome.TARGET_STALE, MutationOutcome.EXECUTED_AMBIGUOUS}: continue
                else: raise RuntimeError("agent step limit reached")
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED; raise
        except Exception as error:
            task.status, task.failure_reason = TaskStatus.FAILED, str(error)
        finally:
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                task.finished_at = datetime.now(UTC)
            self.store.save(task)

    @staticmethod
    async def _observe(device: DeviceAdapter): return normalize(await device.observe())

    async def _resolve_task_contract(self, task: MobileTask) -> MobileTask | None:
        validation = validate_task_contract(task.task_spec)
        if validation.valid:
            authorizations = task.agent_context.get("information_authorizations")
            authorized_keys = set(authorizations) if isinstance(authorizations, dict) else set()
            authorization_question = information_authorization_question(task.task_spec, authorized_keys)
            if authorization_question:
                self._pause_for_input(task, authorization_question)
                return None
            if task.contract_status != TaskContractStatus.READY or task.contract_errors:
                task.contract_status = TaskContractStatus.READY
                task.contract_errors = []
                self.store.save(task)
            return task

        interpretation = None
        interpret = getattr(self.provider, "interpret_task", None)
        if callable(interpret):
            try:
                interpretation = await interpret(
                    task.instruction,
                    SemanticState(app=None, elements=[], fingerprint="task-contract"),
                )
            except ProviderUnavailable as error:
                task.status = TaskStatus.RECOVERING
                task.failure_reason = str(error)
                task.contract_errors = validation.errors
                task.lease_expires_at = datetime.fromtimestamp(time.time() + 20, UTC)
                self.store.save(task)
                self.store.event(task.id, "TASK_CONTRACT_INTERPRETATION_DEFERRED", {
                    "reason": str(error),
                }, task.worker_id)
                return None
        if interpretation is not None:
            proposed = interpretation.to_task_spec()
            proposed_validation = validate_task_contract(proposed)
            if proposed_validation.valid:
                task.task_spec = proposed
                task.contract_status = TaskContractStatus.READY
                task.contract_errors = []
                task.failure_reason = None
                self.store.save(task)
                self.store.event(task.id, "TASK_CONTRACT_RESOLVED", {
                    "contract_version": proposed.contract_version,
                    "intent": proposed.intent,
                    "target_app": proposed.app,
                    "policy": proposed.policy.mode.value if proposed.policy else None,
                    "requirements": len(proposed.completion),
                }, task.worker_id)
                return task
            task.task_spec = proposed
            validation = proposed_validation

        answered_prompt_keys = {answer.prompt_key for answer in task.input_history}
        question = contract_question(task.task_spec, validation.errors, answered_prompt_keys)
        if question:
            task.contract_status = TaskContractStatus.PENDING
            task.contract_errors = validation.errors
            self._pause_for_input(task, question)
            return None

        declared = [item for item in generate_requirements(task.task_spec) if item.required]
        reason = "NO_VERIFIABLE_COMPLETION_CRITERIA" if not declared else "TASK_CONTRACT_INCOMPLETE"
        task.status = TaskStatus.FAILED
        task.contract_status = TaskContractStatus.UNSUPPORTED
        task.contract_errors = validation.errors
        task.failure_reason = reason
        task.finished_at = datetime.now(UTC)
        self.store.save(task)
        event_type = reason if reason == "NO_VERIFIABLE_COMPLETION_CRITERIA" else "TASK_CONTRACT_UNSUPPORTED"
        self.store.event(task.id, event_type, {
            "reason": reason,
            "errors": validation.errors,
        }, task.worker_id)
        return None

    @staticmethod
    def _requirement_evidence(requirement) -> dict[str, object]:
        return {
            "requirement": requirement.key,
            "kind": requirement.kind,
            "output_key": requirement.output_key,
            "source": dict(requirement.evidence),
        }

    @staticmethod
    def _verified_result(task: MobileTask, requirements, step: int, trace: TraceWriter) -> dict[str, object]:
        observations: dict[str, object] = {}
        for output in task.task_spec.requested_outputs:
            matching = next(
                (evidence for evidence in task.requirement_evidence.values() if evidence.get("output_key") == output.key),
                None,
            )
            source = matching.get("source", {}) if matching else {}
            observations[output.key] = {
                "status": "observed" if matching else "unknown",
                "value": source.get("observed_value") if isinstance(source, dict) else None,
            }
        return {
            "summary": "Task completed and verified.",
            "verified": True,
            "observations": observations,
            "requirements": [
                {
                    "key": requirement.key,
                    "kind": requirement.kind,
                    "status": requirement.status.value,
                    "evidence": task.requirement_evidence[requirement.key],
                }
                for requirement in requirements
                if requirement.required
            ],
            "steps": step,
            "trace_path": str(trace.path),
        }

    @staticmethod
    def _candidates(catalog, refs, requirements, spec, current_package, history, state_fingerprint, interaction_context=InteractionContext.UNKNOWN, ownership=EntityOwnership.UNKNOWN, orientation="UNORIENTED", input_question: QuestionSpec | None = None):
        actions, mapping = [], {}
        if input_question:
            action = CandidateAction(
                id="request_input",
                kind=ActionKind.REQUEST_INPUT,
                label=f"Pause and request structured input: {input_question.reason}",
                question=input_question.model_dump(mode="json"),
            )
            return [action], {action.id: (action, None, None)}
        texts = [r.expected for r in requirements if r.kind in {"title_equals", "checklist_item", "field_contains"} and r.status != RequirementStatus.SATISFIED and r.expected]
        entity_missing = any(r.kind == "note_created" and r.status != RequirementStatus.SATISFIED for r in requirements) and ownership != EntityOwnership.CURRENT_TASK
        policy = getattr(spec, "policy", None)
        read_only = bool(policy and policy.mode == TaskPolicyMode.READ_ONLY)
        suppressed = {item.get("signature") for item in history if item.get("state") == state_fingerprint and item.get("mutation") == MutationOutcome.EXECUTED_NO_EFFECT.value and sum(1 for other in history if other.get("signature") == item.get("signature") and other.get("state") == state_fingerprint and other.get("mutation") == MutationOutcome.EXECUTED_NO_EFFECT.value) >= 2}
        ordered = sorted(catalog.actions, key=lambda item: (0 if "set_text" in item.capabilities and item.semantic_role == "title" else 1, item.ref))
        for item in ordered:
            foreign_editor = interaction_context == InteractionContext.EDITOR and ownership != EntityOwnership.CURRENT_TASK
            if foreign_editor:
                # Existing editor content is read-only for a create task. Only
                # explicit, non-destructive parent navigation is selectable.
                navigation_label = (item.label or "").casefold()
                if not (item.semantic_role in {"image", "button"} and any(word in navigation_label for word in ("back", "navigate up", "up", "close", "cancel"))):
                    continue
            if "activate" in item.capabilities:
                if read_only and not MobileAgent._read_navigation_allowed(item, spec):
                    continue
                family = MobileAgent._family_for_activate(item, requirements, ownership)
                first_label = (item.label or "").casefold().strip().split(maxsplit=1)
                if ownership == EntityOwnership.CURRENT_TASK and first_label and first_label[0] in {"create", "new", "add", "compose"}:
                    continue
                a = CandidateAction(id=f"a{len(actions)}", kind=ActionKind.TAP, label=f"Activate role={item.semantic_role} label={item.label!r} ({item.ref})", risk=item.risk, mutation_family=family)
                if MobileAgent._attempt_signature(a, item) not in suppressed: actions.append(a); mapping[a.id] = (a, item.ref, item)
            # An editor already open when this task starts may belong to an
            # unrelated entity.  Do not mutate it until this task has observed
            # creation/existence of its own entity.
            if "set_text" in item.capabilities and not entity_missing and not foreign_editor and not read_only:
                for text in texts:
                    a = CandidateAction(id=f"a{len(actions)}", kind=ActionKind.TYPE_TEXT, label=f'Set text "{text}" in role={item.semantic_role} current={item.current_value!r} ({item.ref})', text=text, risk=ActionRisk.REVERSIBLE, mutation_family=MutationFamily.TEXT_WRITE); actions.append(a); mapping[a.id] = (a, item.ref, item)
            if "scroll" in item.capabilities:
                a = CandidateAction(id=f"a{len(actions)}", kind=ActionKind.SCROLL_DOWN, label=f"Scroll {item.label} ({item.ref})", mutation_family=MutationFamily.SCROLL); actions.append(a); mapping[a.id] = (a, item.ref, item)
        if catalog.actions:
            if spec.app_package and current_package != spec.app_package:
                # Orientation uses root entry, not launcher-icon resume: the
                # latter may restore a foreign activity task.
                a = CandidateAction(id=f"a{len(actions)}", kind=ActionKind.OPEN_APP_ROOT, label=f"Open requested app root ({catalog.actions[0].ref})", package=spec.app_package, mutation_family=MutationFamily.NAVIGATION); actions.append(a); mapping[a.id] = (a, catalog.actions[0].ref, catalog.actions[0])
            a = CandidateAction(id=f"a{len(actions)}", kind=ActionKind.BACK, label="Back", mutation_family=MutationFamily.NAVIGATION); actions.append(a); mapping[a.id] = (a, catalog.actions[0].ref, catalog.actions[0])
        # Requirement-aware orientation: once a safe visible creation
        # affordance exists, do not let an unconstrained policy wander back to
        # an old entity or the launcher.  This remains app-independent.
        creates = [action for action in actions if action.mutation_family == MutationFamily.CREATE_ENTITY]
        roots = [action for action in actions if action.kind == ActionKind.OPEN_APP_ROOT]
        if entity_missing and spec.app_package and current_package != spec.app_package and roots:
            allowed = {action.id for action in roots}
            return [action for action in actions if action.id in allowed], {key: value for key, value in mapping.items() if key in allowed}
        if read_only and spec.app_package and current_package != spec.app_package and roots:
            allowed = {action.id for action in roots}
            return [action for action in actions if action.id in allowed], {
                key: value for key, value in mapping.items() if key in allowed
            }
        if entity_missing and orientation == "READY" and creates:
            allowed = {action.id for action in creates}
            return [action for action in actions if action.id in allowed], {key: value for key, value in mapping.items() if key in allowed}
        if entity_missing and interaction_context != InteractionContext.EDITOR and creates:
            allowed = {action.id for action in creates}
            actions = [action for action in actions if action.id in allowed]
            mapping = {key: value for key, value in mapping.items() if key in allowed}
        return actions, mapping

    @staticmethod
    def _read_navigation_allowed(item, spec: TaskSpec) -> bool:
        if item.risk != ActionRisk.READ_ONLY or item.semantic_role in {"checkbox", "switch", "toggle"}:
            return False
        label = (item.label or "").casefold()
        if any(term in label for term in (
            "delete", "remove", "save", "submit", "send", "buy", "purchase", "pay",
            "löschen", "entfernen", "speichern", "senden", "kaufen",
        )):
            return False
        hints = {
            token
            for request in spec.information_requests
            for hint in request.semantic_hints
            for token in hint.casefold().split()
            if len(token) > 2
        }
        navigation = {
            "about", "settings", "system", "device", "account", "profile", "information", "details",
            "phone", "version", "info", "advanced", "preferences", "über", "gerät", "konto",
            "einstellungen", "system",
        }
        return bool(set(label.replace("&", " ").split()) & (hints | navigation))

    def _pause_for_input(self, task: MobileTask, question: QuestionSpec) -> None:
        task.status = TaskStatus.WAITING_FOR_USER
        task.waiting_question = question.model_dump(mode="json")
        task.waiting_reason = question.text
        task.lease_expires_at = None
        self.store.save(task)
        self.store.event(task.id, "TASK_WAITING_FOR_USER", {
            "question": question.model_dump(mode="json"),
        }, task.worker_id)

    @staticmethod
    def _ambiguity_question(task: MobileTask, requirements) -> QuestionSpec | None:
        answered = {answer.prompt_key for answer in task.input_history}
        for requirement in requirements:
            ambiguity = requirement.evidence.get("ambiguity") if isinstance(requirement.evidence, dict) else None
            if not isinstance(ambiguity, list) or not requirement.output_key:
                continue
            prompt_key = f"select_information:{requirement.output_key}"
            if prompt_key in answered:
                continue
            options = [
                QuestionOption(
                    id=str(candidate["id"]),
                    label=f"{candidate.get('label')}: {candidate.get('value')}",
                )
                for candidate in ambiguity
                if isinstance(candidate, dict) and candidate.get("id") and candidate.get("value") is not None
            ]
            if len(options) < 2:
                continue
            return make_question(
                question_type=QuestionType.SELECT_OPTION,
                reason="AMBIGUOUS_OBSERVED_INFORMATION",
                prompt_key=prompt_key,
                text="Multiple matching values are visible. Which one should be returned?",
                options=options,
                effect=AnswerEffect(
                    kind=AnswerEffectKind.SELECT_SEMANTIC_OPTION,
                    parameter=requirement.output_key,
                ),
            )
        return None

    @staticmethod
    def _operation(device, action):
        async def execute(target):
            if action.kind == ActionKind.TAP: return await device.tap(target)
            if action.kind == ActionKind.TYPE_TEXT: return await device.type_text(action.text or "", target)
            if action.kind == ActionKind.SCROLL_DOWN: return await device.swipe("down")
            if action.kind == ActionKind.BACK: return await device.back()
            if action.kind == ActionKind.LAUNCH_APP: return await device.launch_app(action.package or "")
            if action.kind == ActionKind.OPEN_APP_ROOT:
                result = await device.open_app_root(action.package or "")
                # Android task/root transitions are asynchronous. Observe the
                # resulting semantic surface, not the outgoing launcher frame.
                await asyncio.sleep(0.8)
                return result
            raise RuntimeError("unsupported semantic action")
        return execute

    @staticmethod
    def _text_effect(state, action): return bool(action.text and any(action.text.casefold() == (e.value or "").casefold() for e in state.elements if e.editable))

    @staticmethod
    def _attempt_signature(action, item):
        return f"{action.kind.value}:{item.semantic_role}:{item.label.casefold()}:{action.text or ''}"

    @staticmethod
    def _family_for_activate(item, requirements, ownership=EntityOwnership.UNKNOWN) -> MutationFamily:
        missing_entity = any(r.kind == "note_created" and r.status != RequirementStatus.SATISFIED for r in requirements) and ownership != EntityOwnership.CURRENT_TASK
        label = f"{item.label or ''} {getattr(item, 'hint', '') or ''}".casefold().strip()
        # Generic creation semantics: requirements demand a missing entity and
        # this control advertises a new/add/create affordance. No app/package
        # knowledge is involved.
        control_role = item.semantic_role in {"button", "menu_item", "image", "fab"}
        affordance = label.split(maxsplit=1)[0] if label else ""
        if missing_entity and control_role and affordance in {"create", "new", "add"}:
            return MutationFamily.CREATE_ENTITY
        return MutationFamily.NAVIGATION

    def _record_fault(self, task: MobileTask, point: str, entry) -> bool:
        if os.getenv("JEV_MOBILE_FAULT_ONCE") == "1" and any(event["event_type"] == "FAULT_INJECTED" for event in self.store.events(task.id)):
            return False
        self.store.event(task.id, "FAULT_INJECTED", {"mutation_id": entry.id, "mutation_family": entry.family.value, "fault_point": point, "worker_id": task.worker_id, "step": task.step_number, "target": entry.target.model_dump(mode="json") if entry.target else None}, task.worker_id)
        return True

    def cancel(self, task_id: str) -> MobileTask | None:
        task = self.store.get(task_id)
        if not task: return None
        # Cancellation is cooperative: never interrupt an in-flight mutation.
        task.cancellation_requested = True
        self.store.save(task)
        self.store.event(task.id, "TASK_CANCELLATION_REQUESTED", {})
        return task

"""Single-device durable worker; clients only enqueue SQLite tasks."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import signal
import time
from uuid import uuid4

from ..agent.mobile_agent import MobileAgent
from ..failures import classify_failure
from ..operations import operational_event
from ..task_store import TaskStatus, TaskStore
from ..tracing.trace import TraceWriter
from .readiness import RuntimeReadinessError, validate_adb_runtime


class DurableWorker:
    def __init__(self, store: TaskStore, agent: MobileAgent, serial: str, poll_seconds: float = 1.0) -> None:
        self.store, self.agent, self.serial, self.poll_seconds = store, agent, serial, poll_seconds
        self.worker_id = f"worker_{uuid4().hex}"
        self._stopping = False
        self._last_device_status_at = 0.0
        self._ready = False
        self._last_reported_ready: bool | None = None

    async def run_forever(self) -> None:
        operational_event("worker_started", worker_id=self.worker_id, device_serial=self.serial)
        retention_days = getattr(self.agent.settings, "event_retention_days", 30)
        trace_retention_days = getattr(self.agent.settings, "trace_retention_days", 30)
        if retention_days:
            removed = self.store.prune_completed_events(datetime.now(UTC) - timedelta(days=retention_days))
            operational_event("event_retention_applied", removed=removed, retention_days=retention_days)
        if trace_retention_days:
            removed = TraceWriter.prune(
                self.agent.settings.trace_dir, datetime.now(UTC) - timedelta(days=trace_retention_days),
            )
            operational_event("trace_retention_applied", removed=removed, retention_days=trace_retention_days)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try: loop.add_signal_handler(sig, self.stop)
            except NotImplementedError: pass
        try:
            while not self._stopping:
                claimed = await self.run_once()
                if not claimed:
                    await asyncio.sleep(self.poll_seconds)
        finally:
            # Graceful shutdown does not fail a task; it makes its persisted
            # checkpoint immediately recoverable by the replacement worker.
            self.store.release_worker(self.worker_id)
            operational_event("worker_stopped", worker_id=self.worker_id, device_serial=self.serial)

    async def run_once(self) -> bool:
        """Probe readiness and execute at most one task.

        A queued task is never claimed before a fresh successful probe.  Idle
        probes are rate-limited because they are telemetry, not execution
        authorization.
        """
        self.store.recover_expired()
        pending = self.store.has_claimable_task(self.serial)
        ready = await self._refresh_device_status(force=pending)
        if not pending or not ready:
            return False
        task = self.store.claim_next_task(self.worker_id, self.serial)
        if not task:
            return False
        operational_event("task_claimed", task_id=task.id, worker_id=self.worker_id, device_serial=self.serial)
        await self._run_claimed(task.id)
        return True

    async def _run_claimed(self, task_id: str) -> None:
        running = asyncio.create_task(self.agent.run(task_id))
        while not running.done():
            await asyncio.sleep(3)
            task = self.store.get(task_id)
            if task and task.status == TaskStatus.RUNNING: self.store.heartbeat(task); self.store.event(task_id, "WORKER_HEARTBEAT", {}, self.worker_id, self.serial)
        await running
        task = self.store.get(task_id)
        if task:
            fields: dict[str, object] = {
                "task_id": task.id,
                "worker_id": self.worker_id,
                "device_serial": self.serial,
                "status": task.status.value,
            }
            if task.status == TaskStatus.FAILED:
                failure = classify_failure(task.failure_reason)
                fields.update(
                    category=failure.category.value,
                    reason=failure.reason,
                    recoverable=failure.recoverable,
                )
            event_name = {
                TaskStatus.WAITING_FOR_USER: "task_waiting",
                TaskStatus.RECOVERING: "task_recovering",
            }.get(task.status, f"task_{task.status.value}")
            operational_event(event_name, **fields)
        if task and task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            # Terminal tasks leave the phone in a neutral state for the next
            # independent request. HOME is non-destructive and deliberately
            # runs outside Jev's selectable action space. A paused task must
            # keep its current UI and therefore never enters this branch.
            try:
                async with self.agent.device_factory() as device:
                    await device.home()
                self.store.event(task_id, "TASK_RETURNED_HOME", {}, self.worker_id, self.serial)
            except Exception as error:
                # Cleanup failure cannot rewrite an already verified task
                # result, but remains visible for operations and benchmarks.
                self.store.event(task_id, "TASK_RETURN_HOME_FAILED", {
                    "error": f"{type(error).__name__}: {error}",
                }, self.worker_id, self.serial)

    async def _refresh_device_status(self, *, force: bool = False) -> bool:
        """Persist a read-only status snapshot for MCP-only processes.

        The worker is the sole owner of the physical device.  A transiently
        unplugged phone is recorded as unavailable but does not terminate the
        worker or trigger a container restart.
        """
        now = time.monotonic()
        if not force and now - self._last_device_status_at < 15:
            return self._ready
        self._last_device_status_at = now
        try:
            if not self.store.readinesscheck():
                raise RuntimeReadinessError("TASK_STORE_UNAVAILABLE", "TaskStore is not writable")
            configuration_errors = getattr(self.agent.settings, "configuration_errors", ())
            if configuration_errors:
                raise RuntimeReadinessError("CONFIGURATION_INVALID", "; ".join(configuration_errors))
            provider_check = getattr(self.agent.provider, "readiness_error", None)
            provider_error = provider_check() if provider_check else None
            if provider_error:
                raise RuntimeReadinessError("PROVIDER_UNAVAILABLE", provider_error)
            adb_details = validate_adb_runtime()
            async with self.agent.device_factory() as device:
                state = await device.observe()
            self.store.save_device_status(self.serial, {
                "worker_id": self.worker_id,
                "worker_active": True,
                "worker_ready": True,
                "device_available": True,
                "readiness_category": "READY",
                "package": state.package,
                "activity": state.activity,
                "elements": len(state.elements),
                **adb_details,
            })
            self._ready = True
        except Exception as error:
            category = error.category if isinstance(error, RuntimeReadinessError) else "BACKEND_UNAVAILABLE"
            try:
                self.store.save_device_status(self.serial, {
                    "worker_id": self.worker_id,
                    "worker_active": True,
                    "worker_ready": False,
                    "device_available": False,
                    "readiness_category": category,
                    "reason": str(error),
                })
            except Exception:
                # If SQLite itself is unavailable there is nowhere durable to
                # report readiness. Keep the process alive and retry; never
                # claim work on the failed probe.
                pass
            self._ready = False
        if self._last_reported_ready != self._ready:
            operational_event(
                "device_connected" if self._ready else "device_disconnected",
                worker_id=self.worker_id,
                device_serial=self.serial,
                category="READY" if self._ready else category,
            )
            if self._ready:
                operational_event("worker_ready", worker_id=self.worker_id, device_serial=self.serial)
            self._last_reported_ready = self._ready
        return self._ready

    def stop(self) -> None: self._stopping = True

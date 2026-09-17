"""Single-device durable worker; clients only enqueue SQLite tasks."""
from __future__ import annotations

import asyncio
import signal
import time
from uuid import uuid4

from ..agent.mobile_agent import MobileAgent
from ..task_store import TaskStatus, TaskStore


class DurableWorker:
    def __init__(self, store: TaskStore, agent: MobileAgent, serial: str, poll_seconds: float = 1.0) -> None:
        self.store, self.agent, self.serial, self.poll_seconds = store, agent, serial, poll_seconds
        self.worker_id = f"worker_{uuid4().hex}"
        self._stopping = False
        self._last_device_status_at = 0.0

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try: loop.add_signal_handler(sig, self.stop)
            except NotImplementedError: pass
        try:
            while not self._stopping:
                self.store.recover_expired()
                task = self.store.claim_next_task(self.worker_id, self.serial)
                if not task:
                    await self._refresh_device_status()
                    await asyncio.sleep(self.poll_seconds); continue
                await self._run_claimed(task.id)
        finally:
            # Graceful shutdown does not fail a task; it makes its persisted
            # checkpoint immediately recoverable by the replacement worker.
            self.store.release_worker(self.worker_id)

    async def _run_claimed(self, task_id: str) -> None:
        running = asyncio.create_task(self.agent.run(task_id))
        while not running.done():
            await asyncio.sleep(3)
            task = self.store.get(task_id)
            if task and task.status == TaskStatus.RUNNING: self.store.heartbeat(task); self.store.event(task_id, "WORKER_HEARTBEAT", {}, self.worker_id, self.serial)
        await running

    async def _refresh_device_status(self) -> None:
        """Persist a read-only status snapshot for MCP-only processes.

        The worker is the sole owner of the physical device.  A transiently
        unplugged phone is recorded as unavailable but does not terminate the
        worker or trigger a container restart.
        """
        now = time.monotonic()
        if now - self._last_device_status_at < 15:
            return
        self._last_device_status_at = now
        try:
            async with self.agent.device_factory() as device:
                state = await device.observe()
            self.store.save_device_status(self.serial, {
                "worker_id": self.worker_id,
                "worker_active": True,
                "device_available": True,
                "package": state.package,
                "activity": state.activity,
                "elements": len(state.elements),
            })
        except Exception as error:
            self.store.save_device_status(self.serial, {
                "worker_id": self.worker_id,
                "worker_active": True,
                "device_available": False,
                "reason": str(error),
            })

    def stop(self) -> None: self._stopping = True

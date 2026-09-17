"""Durable, asynchronous task records. MCP requests never own task execution."""

from __future__ import annotations

import sqlite3
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from .tasks import RequirementStatus, TaskSpec


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RECOVERING = "recovering"
    WAITING_FOR_USER = "waiting_for_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MobileTask(BaseModel):
    checkpoint_version: int = 1
    id: str
    instruction: str
    task_spec: TaskSpec
    status: TaskStatus = TaskStatus.QUEUED
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    current_subgoal: str | None = None
    requirements: dict[str, RequirementStatus] = Field(default_factory=dict)
    result: dict[str, object] | None = None
    failure_reason: str | None = None
    waiting_reason: str | None = None
    waiting_question: dict[str, object] | None = None
    worker_id: str | None = None
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    cancellation_requested: bool = False
    step_number: int = 0
    agent_context: dict[str, object] = Field(default_factory=dict)
    pending_mutation: dict[str, object] | None = None


class TaskStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or os.getenv("JEV_MOBILE_DB", "jev-mobile-tasks.sqlite3"))
        self._connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
            device_serial TEXT, worker_id TEXT, lease_expires_at TEXT
        )""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL, payload TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0,
            worker_id TEXT, device_serial TEXT
        )""")
        # This is operational telemetry, not agent state.  It lets an MCP
        # process report worker-observed device availability without receiving
        # ADB/USB permissions itself.
        self._connection.execute("""CREATE TABLE IF NOT EXISTS device_status (
            device_serial TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        for column, definition in (("status", "TEXT NOT NULL DEFAULT 'queued'"), ("device_serial", "TEXT"), ("worker_id", "TEXT"), ("lease_expires_at", "TEXT")):
            try: self._connection.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError: pass
        for column, definition in (("seq", "INTEGER NOT NULL DEFAULT 0"), ("worker_id", "TEXT"), ("device_serial", "TEXT")):
            try: self._connection.execute(f"ALTER TABLE task_events ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError: pass

    def create(self, instruction: str, task_spec: TaskSpec) -> MobileTask:
        task = MobileTask(id=f"task_{uuid4().hex}", instruction=instruction, task_spec=task_spec,
                          created_at=datetime.now(UTC))
        self.save(task)
        self.event(task.id, "TASK_QUEUED", {"instruction": instruction})
        return task

    def get(self, task_id: str) -> MobileTask | None:
        row = self._connection.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return MobileTask.model_validate_json(row[0]) if row else None

    def save(self, task: MobileTask, device_serial: str | None = None) -> None:
        # A client can request cancellation while the worker has an older
        # in-memory checkpoint.  Cancellation is monotonic: no later worker
        # checkpoint may erase it before the worker reaches a safe boundary.
        existing = self._connection.execute("SELECT payload FROM tasks WHERE id=?", (task.id,)).fetchone()
        if existing and MobileTask.model_validate_json(existing[0]).cancellation_requested:
            task.cancellation_requested = True
        payload = task.model_dump_json()
        self._connection.execute("""INSERT INTO tasks(id, payload, status, device_serial, worker_id, lease_expires_at)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,
            status=excluded.status, device_serial=COALESCE(excluded.device_serial, tasks.device_serial),
            worker_id=excluded.worker_id, lease_expires_at=excluded.lease_expires_at""",
            (task.id, payload, task.status.value, device_serial, task.worker_id,
             task.lease_expires_at.isoformat() if task.lease_expires_at else None))

    def claim_next_task(self, worker_id: str, device_serial: str, lease_seconds: float = 20) -> MobileTask | None:
        now = datetime.now(UTC); expiry = now.timestamp() + lease_seconds
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            occupied = self._connection.execute("SELECT 1 FROM tasks WHERE device_serial=? AND status='running' AND worker_id<>? LIMIT 1", (device_serial, worker_id)).fetchone()
            if occupied:
                self._connection.execute("COMMIT"); return None
            row = self._connection.execute("""SELECT id, payload FROM tasks WHERE (device_serial IS NULL OR device_serial=?)
                AND status IN ('queued','recovering') AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                ORDER BY rowid LIMIT 1""", (device_serial, now.isoformat())).fetchone()
            if not row:
                self._connection.execute("COMMIT"); return None
            task = MobileTask.model_validate_json(row[1])
            task.status, task.worker_id, task.claimed_at, task.heartbeat_at = TaskStatus.RUNNING, worker_id, now, now
            task.lease_expires_at = datetime.fromtimestamp(expiry, UTC)
            self.save(task, device_serial)
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK"); raise
        self.event(task.id, "TASK_STARTED", {"worker_id": worker_id}, worker_id, device_serial)
        return task

    def heartbeat(self, task: MobileTask, lease_seconds: float = 20) -> None:
        now = datetime.now(UTC); task.heartbeat_at = now; task.lease_expires_at = datetime.fromtimestamp(now.timestamp()+lease_seconds, UTC); self.save(task)

    def recover_expired(self) -> int:
        now = datetime.now(UTC).isoformat()
        rows = self._connection.execute("SELECT id, payload FROM tasks WHERE status='running' AND lease_expires_at < ?", (now,)).fetchall()
        for task_id, payload in rows:
            task = MobileTask.model_validate_json(payload); task.status = TaskStatus.RECOVERING; task.worker_id = None; task.lease_expires_at = None; self.save(task); self.event(task_id, "TASK_RECOVERING", {})
        return len(rows)

    def event(self, task_id: str, event_type: str, payload: dict[str, object], worker_id: str | None = None, device_serial: str | None = None) -> None:
        import json
        seq = self._connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0]
        self._connection.execute("INSERT INTO task_events(task_id,timestamp,event_type,payload,seq,worker_id,device_serial) VALUES(?,?,?,?,?,?,?)", (task_id, datetime.now(UTC).isoformat(), event_type, json.dumps(payload, default=str), seq, worker_id, device_serial))

    def events(self, task_id: str, *, after_seq: int = 0) -> list[dict[str, object]]:
        import json
        return [{"id": row[0], "timestamp": row[1], "event_type": row[2], "payload": json.loads(row[3]), "seq": row[4], "worker_id": row[5], "device_serial": row[6]} for row in self._connection.execute("SELECT id,timestamp,event_type,payload,seq,worker_id,device_serial FROM task_events WHERE task_id=? AND seq>? ORDER BY seq", (task_id, after_seq))]

    def save_device_status(self, device_serial: str, status: dict[str, object]) -> None:
        import json
        self._connection.execute(
            """INSERT INTO device_status(device_serial, payload, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(device_serial) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
            (device_serial, json.dumps(status, default=str), datetime.now(UTC).isoformat()),
        )

    def get_device_status(self, device_serial: str) -> dict[str, object] | None:
        import json
        row = self._connection.execute("SELECT payload, updated_at FROM device_status WHERE device_serial=?", (device_serial,)).fetchone()
        if not row:
            return None
        status = json.loads(row[0])
        status["updated_at"] = row[1]
        return status

    def release_worker(self, worker_id: str) -> None:
        rows = self._connection.execute("SELECT payload FROM tasks WHERE status='running' AND worker_id=?", (worker_id,)).fetchall()
        for (payload,) in rows:
            task = MobileTask.model_validate_json(payload); task.status = TaskStatus.RECOVERING; task.worker_id = None; task.lease_expires_at = None
            self.save(task); self.event(task.id, "WORKER_RELEASED", {}, worker_id)

    def close(self) -> None: self._connection.close()

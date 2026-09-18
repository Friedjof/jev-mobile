"""Durable, asynchronous task records. MCP requests never own task execution."""

from __future__ import annotations

import sqlite3
import os
import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .tasks import (
    OrderedSubtask,
    RequirementStatus,
    SubtaskStatus,
    TaskContractStatus,
    TaskInputAnswer,
    TaskSpec,
    contract_status_for,
)
from .operations import operational_event


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
    subtasks: list[OrderedSubtask] = Field(default_factory=list)
    active_subtask_id: str | None = None
    contract_status: TaskContractStatus | None = None
    contract_errors: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.QUEUED
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    current_subgoal: str | None = None
    requirements: dict[str, RequirementStatus] = Field(default_factory=dict)
    requirement_evidence: dict[str, dict[str, object]] = Field(default_factory=dict)
    result: dict[str, object] | None = None
    failure_reason: str | None = None
    waiting_reason: str | None = None
    waiting_question: dict[str, object] | None = None
    input_history: list[TaskInputAnswer] = Field(default_factory=list)
    worker_id: str | None = None
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    cancellation_requested: bool = False
    step_number: int = 0
    agent_context: dict[str, object] = Field(default_factory=dict)
    pending_mutation: dict[str, object] | None = None
    idempotency_key: str | None = None
    request_fingerprint: str | None = None

    @model_validator(mode="after")
    def migrate_contract_status(self) -> "MobileTask":
        """Older checkpoints gain a deterministic contract state on read."""
        if self.contract_status is None:
            self.contract_status = contract_status_for(self.task_spec)
        return self


class TaskStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or os.getenv("JEV_MOBILE_DB", "jev-mobile-tasks.sqlite3"))
        self._connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=15000")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
            device_serial TEXT, worker_id TEXT, lease_expires_at TEXT,
            idempotency_key TEXT, request_fingerprint TEXT, created_at TEXT, finished_at TEXT
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
        for column, definition in (("status", "TEXT NOT NULL DEFAULT 'queued'"), ("device_serial", "TEXT"), ("worker_id", "TEXT"), ("lease_expires_at", "TEXT"), ("idempotency_key", "TEXT"), ("request_fingerprint", "TEXT"), ("created_at", "TEXT"), ("finished_at", "TEXT")):
            try: self._connection.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError: pass
        for column, definition in (("seq", "INTEGER NOT NULL DEFAULT 0"), ("worker_id", "TEXT"), ("device_serial", "TEXT")):
            try: self._connection.execute(f"ALTER TABLE task_events ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError: pass
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS tasks_idempotency_key ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
        self._backfill_task_columns()

    def _backfill_task_columns(self) -> None:
        rows = self._connection.execute(
            """SELECT id, payload FROM tasks WHERE created_at IS NULL OR
            (finished_at IS NULL AND status IN ('succeeded','failed','cancelled'))""",
        ).fetchall()
        for task_id, payload in rows:
            task = MobileTask.model_validate_json(payload)
            self._connection.execute(
                "UPDATE tasks SET created_at=?, finished_at=? WHERE id=?",
                (task.created_at.isoformat(), task.finished_at.isoformat() if task.finished_at else None, task_id),
            )

    def create(
        self, instruction: str, task_spec: TaskSpec, subtasks: list[OrderedSubtask] | None = None,
        idempotency_key: str | None = None,
    ) -> MobileTask:
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        request_fingerprint = self._request_fingerprint(instruction, task_spec, subtasks or [])
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            if idempotency_key:
                row = self._connection.execute(
                    "SELECT payload, request_fingerprint FROM tasks WHERE idempotency_key=?", (idempotency_key,),
                ).fetchone()
                if row:
                    if row[1] and row[1] != request_fingerprint:
                        raise ValueError("idempotency_key is already associated with a different task request")
                    task = MobileTask.model_validate_json(row[0])
                    self._connection.execute("COMMIT")
                    return task
            task = MobileTask(id=f"task_{uuid4().hex}", instruction=instruction, task_spec=task_spec,
                              subtasks=subtasks or [], contract_status=contract_status_for(task_spec),
                              created_at=datetime.now(UTC), idempotency_key=idempotency_key,
                              request_fingerprint=request_fingerprint)
            self._save_row(task)
            self._event_row(task.id, "TASK_QUEUED", {
                "contract_status": task.contract_status.value,
                "contract_version": task.task_spec.contract_version,
                "subtask_count": len(task.subtasks),
            })
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        operational_event("task_queued", task_id=task.id, status=task.status.value)
        return task

    @staticmethod
    def _validate_idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or len(value) > 160:
            raise ValueError("idempotency_key must contain between 1 and 160 characters")
        return value

    @staticmethod
    def _request_fingerprint(instruction: str, task_spec: TaskSpec, subtasks: list[OrderedSubtask]) -> str:
        payload = {
            "instruction": instruction,
            "task_spec": task_spec.model_dump(mode="json"),
            "subtasks": [item.model_dump(mode="json") for item in subtasks],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

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
        self._save_row(task, device_serial)

    def _save_row(self, task: MobileTask, device_serial: str | None = None) -> None:
        payload = task.model_dump_json()
        self._connection.execute("""INSERT INTO tasks(id, payload, status, device_serial, worker_id, lease_expires_at,
            idempotency_key, request_fingerprint, created_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,
            status=excluded.status, device_serial=COALESCE(excluded.device_serial, tasks.device_serial),
            worker_id=excluded.worker_id, lease_expires_at=excluded.lease_expires_at,
            idempotency_key=excluded.idempotency_key, request_fingerprint=excluded.request_fingerprint,
            created_at=excluded.created_at, finished_at=excluded.finished_at""",
            (task.id, payload, task.status.value, device_serial, task.worker_id,
             task.lease_expires_at.isoformat() if task.lease_expires_at else None,
             task.idempotency_key, task.request_fingerprint, task.created_at.isoformat(),
             task.finished_at.isoformat() if task.finished_at else None))

    def list_tasks(
        self, *, statuses: set[TaskStatus] | None = None, since: datetime | None = None, limit: int = 50,
    ) -> list[MobileTask]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        clauses: list[str] = []
        values: list[object] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            values.extend(item.value for item in statuses)
        if since:
            clauses.append("created_at >= ?")
            values.append(since.astimezone(UTC).isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        rows = self._connection.execute(
            f"SELECT payload FROM tasks{where} ORDER BY created_at DESC, rowid DESC LIMIT ?", values,
        ).fetchall()
        return [MobileTask.model_validate_json(row[0]) for row in rows]

    def retry_task(self, task_id: str) -> MobileTask:
        """Requeue a failed task only when no Android mutation ever began."""
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("unknown task")
            task = MobileTask.model_validate_json(row[0])
            if task.status != TaskStatus.FAILED:
                raise ValueError("only failed tasks can be retried")
            mutation = self._connection.execute(
                "SELECT 1 FROM task_events WHERE task_id=? AND event_type='MUTATION_STARTED' LIMIT 1", (task_id,),
            ).fetchone()
            if mutation or task.pending_mutation:
                raise ValueError("retry is unsafe because a device mutation may have begun")
            task.status = TaskStatus.QUEUED
            task.finished_at = None
            task.failure_reason = None
            task.result = None
            task.waiting_reason = None
            task.waiting_question = None
            task.worker_id = None
            task.claimed_at = None
            task.heartbeat_at = None
            task.lease_expires_at = None
            task.cancellation_requested = False
            task.current_subgoal = None
            task.requirements = {}
            task.requirement_evidence = {}
            task.agent_context = {}
            for subtask in task.subtasks:
                if subtask.status == SubtaskStatus.FAILED:
                    subtask.status = SubtaskStatus.QUEUED
                    subtask.failure_reason = None
                    subtask.finished_at = None
            self._save_row(task)
            self._event_row(task.id, "TASK_RETRY_QUEUED", {"reason": "safe_pre_mutation_retry"})
            self._connection.execute("COMMIT")
            return task
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def prune_completed_events(self, older_than: datetime) -> int:
        """Remove event streams only for terminal tasks beyond configured retention."""
        cursor = self._connection.execute(
            """DELETE FROM task_events WHERE task_id IN (
                SELECT id FROM tasks WHERE status IN ('succeeded','failed','cancelled')
                AND finished_at IS NOT NULL AND finished_at < ?
            )""",
            (older_than.astimezone(UTC).isoformat(),),
        )
        return cursor.rowcount

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

    def has_claimable_task(self, device_serial: str) -> bool:
        """Read-only hint used to avoid expensive device probes while idle."""
        now = datetime.now(UTC).isoformat()
        return self._connection.execute(
            """SELECT 1 FROM tasks WHERE (device_serial IS NULL OR device_serial=?)
            AND status IN ('queued','recovering') AND (lease_expires_at IS NULL OR lease_expires_at < ?)
            LIMIT 1""",
            (device_serial, now),
        ).fetchone() is not None

    def heartbeat(self, task: MobileTask, lease_seconds: float = 20) -> None:
        now = datetime.now(UTC); task.heartbeat_at = now; task.lease_expires_at = datetime.fromtimestamp(now.timestamp()+lease_seconds, UTC); self.save(task)

    def recover_expired(self) -> int:
        now = datetime.now(UTC).isoformat()
        rows = self._connection.execute("SELECT id, payload FROM tasks WHERE status='running' AND lease_expires_at < ?", (now,)).fetchall()
        for task_id, payload in rows:
            task = MobileTask.model_validate_json(payload); previous_worker = task.worker_id
            task.status = TaskStatus.RECOVERING; task.worker_id = None; task.lease_expires_at = None
            self.save(task); self.event(task_id, "TASK_RECOVERING", {})
            operational_event("task_recovering", task_id=task_id, previous_worker_id=previous_worker)
        return len(rows)

    def event(self, task_id: str, event_type: str, payload: dict[str, object], worker_id: str | None = None, device_serial: str | None = None) -> None:
        own_transaction = not self._connection.in_transaction
        if own_transaction:
            self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._event_row(task_id, event_type, payload, worker_id, device_serial)
            if own_transaction:
                self._connection.execute("COMMIT")
        except Exception:
            if own_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def _event_row(self, task_id: str, event_type: str, payload: dict[str, object], worker_id: str | None = None, device_serial: str | None = None) -> None:
        seq = self._connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0]
        self._connection.execute("INSERT INTO task_events(task_id,timestamp,event_type,payload,seq,worker_id,device_serial) VALUES(?,?,?,?,?,?,?)", (task_id, datetime.now(UTC).isoformat(), event_type, json.dumps(payload, default=str), seq, worker_id, device_serial))

    def events(self, task_id: str, *, after_seq: int = 0) -> list[dict[str, object]]:
        return [{"id": row[0], "timestamp": row[1], "event_type": row[2], "payload": json.loads(row[3]), "seq": row[4], "worker_id": row[5], "device_serial": row[6]} for row in self._connection.execute("SELECT id,timestamp,event_type,payload,seq,worker_id,device_serial FROM task_events WHERE task_id=? AND seq>? ORDER BY seq", (task_id, after_seq))]

    def save_device_status(self, device_serial: str, status: dict[str, object]) -> None:
        self._connection.execute(
            """INSERT INTO device_status(device_serial, payload, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(device_serial) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
            (device_serial, json.dumps(status, default=str), datetime.now(UTC).isoformat()),
        )

    def get_device_status(self, device_serial: str) -> dict[str, object] | None:
        row = self._connection.execute("SELECT payload, updated_at FROM device_status WHERE device_serial=?", (device_serial,)).fetchone()
        if not row:
            return None
        status = json.loads(row[0])
        status["device_serial"] = device_serial
        status["status_timestamp"] = row[1]
        status["updated_at"] = row[1]
        return status

    def release_worker(self, worker_id: str) -> None:
        rows = self._connection.execute("SELECT payload FROM tasks WHERE status='running' AND worker_id=?", (worker_id,)).fetchall()
        for (payload,) in rows:
            task = MobileTask.model_validate_json(payload); task.status = TaskStatus.RECOVERING; task.worker_id = None; task.lease_expires_at = None
            self.save(task); self.event(task.id, "WORKER_RELEASED", {}, worker_id)

    def close(self) -> None: self._connection.close()

    def healthcheck(self) -> bool:
        """Check local SQLite availability without touching a device."""
        return self._connection.execute("SELECT 1").fetchone() == (1,)

    def readinesscheck(self) -> bool:
        """Verify SQLite can acquire a short write transaction."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute("UPDATE device_status SET updated_at=updated_at WHERE 0")
            self._connection.execute("ROLLBACK")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            return False
        return True

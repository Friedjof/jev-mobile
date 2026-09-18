from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
import os
import sqlite3

import pytest

from jev_mobile import mcp_server
from jev_mobile.agent.mobile_agent import MobileAgent
from jev_mobile.failures import FailureCategory, classify_failure
from jev_mobile.operations import operational_event
from jev_mobile.task_store import MobileTask, TaskStatus, TaskStore
from jev_mobile.tasks import CompletionRequirement, TaskSpec
from jev_mobile.tracing.trace import TraceWriter


def _spec() -> TaskSpec:
    return TaskSpec(app="Example", completion=[CompletionRequirement(type="app_open", value="example.app")])


def test_idempotency_key_returns_original_task_without_duplicate_event(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")

    first = store.create("Open Example", _spec(), idempotency_key="parent-request-17")
    repeated = store.create("Open Example", _spec(), idempotency_key="parent-request-17")

    assert repeated.id == first.id
    assert len(store.list_tasks()) == 1
    assert [event["event_type"] for event in store.events(first.id)] == ["TASK_QUEUED"]


def test_existing_task_database_is_migrated_in_place(tmp_path) -> None:
    path = tmp_path / "old.sqlite3"
    task = MobileTask(
        id="task-old", instruction="Open Example", task_spec=_spec(), created_at=datetime.now(UTC),
    )
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE tasks (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
            device_serial TEXT, worker_id TEXT, lease_expires_at TEXT
        )""",
    )
    connection.execute(
        "INSERT INTO tasks(id,payload,status) VALUES(?,?,?)", (task.id, task.model_dump_json(), task.status.value),
    )
    connection.commit()
    connection.close()

    migrated = TaskStore(path)
    assert migrated.get(task.id) is not None
    assert migrated.list_tasks()[0].id == task.id
    columns = {row[1] for row in migrated._connection.execute("PRAGMA table_info(tasks)")}  # noqa: SLF001
    assert {"idempotency_key", "request_fingerprint", "created_at", "finished_at"} <= columns


def test_idempotency_key_rejects_different_request(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    store.create("Open Example", _spec(), idempotency_key="same-key")

    with pytest.raises(ValueError, match="different task request"):
        store.create("Open Something Else", _spec(), idempotency_key="same-key")


def test_list_tasks_is_bounded_and_filterable(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    queued = store.create("Open Example", _spec())
    failed = store.create("Open Example again", _spec())
    failed.status = TaskStatus.FAILED
    failed.failure_reason = "PROVIDER_UNAVAILABLE"
    store.save(failed)

    assert [task.id for task in store.list_tasks(statuses={TaskStatus.FAILED})] == [failed.id]
    assert {task.id for task in store.list_tasks(since=datetime.now(UTC) - timedelta(minutes=1))} == {
        queued.id, failed.id,
    }
    with pytest.raises(ValueError, match="between 1 and 100"):
        store.list_tasks(limit=101)


def test_retry_failed_task_is_allowed_only_before_a_mutation(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    safe = store.create("Open Example", _spec())
    safe.status = TaskStatus.FAILED
    safe.failure_reason = "PROVIDER_UNAVAILABLE"
    safe.finished_at = datetime.now(UTC)
    store.save(safe)

    retried = store.retry_task(safe.id)
    assert retried.status == TaskStatus.QUEUED
    assert retried.failure_reason is None
    assert store.events(safe.id)[-1]["event_type"] == "TASK_RETRY_QUEUED"

    unsafe = store.create("Open Example unsafely", _spec())
    unsafe.status = TaskStatus.FAILED
    store.save(unsafe)
    store.event(unsafe.id, "MUTATION_STARTED", {"family": "NAVIGATION"})
    with pytest.raises(ValueError, match="mutation may have begun"):
        store.retry_task(unsafe.id)


@pytest.mark.parametrize(
    ("reason", "category", "recoverable"),
    [
        ("ADB device offline", FailureCategory.DEVICE_UNAVAILABLE, True),
        ("PORTAL_UNAVAILABLE", FailureCategory.BACKEND_UNAVAILABLE, True),
        ("PROVIDER_UNAVAILABLE", FailureCategory.PROVIDER_UNAVAILABLE, True),
        ("NO_VERIFIABLE_COMPLETION_CRITERIA", FailureCategory.UNSUPPORTED_TASK, True),
        ("ORIENTATION_BLOCKED_BY_PERSISTED_APP_STATE", FailureCategory.SAFETY_BLOCKED, False),
        ("Jev selected an invalid action", FailureCategory.AGENT_BUG, False),
        ("unexpected invariant", FailureCategory.AGENT_BUG, False),
    ],
)
def test_failure_categories_are_stable(reason, category, recoverable) -> None:
    failure = classify_failure(reason)
    assert failure.category == category
    assert failure.recoverable is recoverable


def test_operational_logs_and_traces_redact_credentials(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="jev_mobile.operations")
    operational_event(
        "provider_unavailable",
        authorization="Bearer super-secret-token",
        detail="api_key=do-not-log Bearer also-secret",
    )
    logged = caplog.records[-1].message
    assert "super-secret-token" not in logged
    assert "do-not-log" not in logged
    assert "also-secret" not in logged
    assert logged.count("[REDACTED]") >= 2

    trace = TraceWriter(tmp_path, "task-redaction")
    trace.write(event="request", headers={"Authorization": "Bearer trace-secret"}, token="other-secret")
    record = json.loads(trace.path.read_text())
    assert record["headers"]["Authorization"] == "[REDACTED]"
    assert record["token"] == "[REDACTED]"
    assert "trace-secret" not in trace.path.read_text()


def test_completed_event_and_trace_retention_is_bounded(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create("Open Example", _spec())
    task.status = TaskStatus.SUCCEEDED
    task.finished_at = datetime.now(UTC) - timedelta(days=40)
    store.save(task)
    store.event(task.id, "TASK_SUCCEEDED", {})

    assert store.prune_completed_events(datetime.now(UTC) - timedelta(days=30)) == 2
    assert store.events(task.id) == []

    trace = TraceWriter(tmp_path / "traces", task.id)
    trace.write(event="task_started")
    old = (datetime.now(UTC) - timedelta(days=40)).timestamp()
    os.utime(trace.path, (old, old))
    assert TraceWriter.prune(tmp_path / "traces", datetime.now(UTC) - timedelta(days=30)) == 1
    assert not trace.path.exists()


def test_verified_result_never_advertises_a_nonexistent_trace(tmp_path) -> None:
    task = MobileTask(id="task-trace", instruction="Open Example", task_spec=_spec(), created_at=datetime.now(UTC))
    trace = TraceWriter(tmp_path / "traces", task.id)

    without_file = MobileAgent._verified_result(task, task.task_spec, [], 0, trace)
    assert "trace_path" not in without_file

    trace.write(event="task_started")
    with_file = MobileAgent._verified_result(task, task.task_spec, [], 0, trace)
    assert with_file["trace_path"] == str(trace.path)


@pytest.mark.asyncio
async def test_mcp_start_task_idempotency_does_not_expand_public_tool_surface(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    monkeypatch.setattr(mcp_server, "_task_store", store)

    first = await mcp_server.start_task("Open Android Settings", idempotency_key="mcp-call-1")
    second = await mcp_server.start_task("Open Android Settings", idempotency_key="mcp-call-1")
    tools = await mcp_server.server.list_tools()

    assert second == first
    assert [tool.name for tool in tools] == [
        "start_task", "get_task", "get_task_events", "cancel_task", "answer_task", "get_device_status",
    ]
    schema = next(tool.input_schema for tool in tools if tool.name == "start_task")
    assert "idempotency_key" in schema["properties"]


@pytest.mark.asyncio
async def test_http_readiness_requires_fresh_worker_telemetry(tmp_path, monkeypatch) -> None:
    database = tmp_path / "tasks.sqlite3"
    monkeypatch.setenv("JEV_MOBILE_DB", str(database))
    monkeypatch.setenv("MOBILE_DEVICE_SERIAL", "phone-1")
    store = TaskStore(database)
    store.save_device_status("phone-1", {
        "worker_ready": True,
        "device_available": True,
        "readiness_category": "READY",
    })

    fresh = await mcp_server._ready(None)  # type: ignore[arg-type]
    assert fresh.status_code == 200
    assert json.loads(fresh.body)["ready"] is True

    stale_timestamp = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    store._connection.execute(  # noqa: SLF001 - explicit persistence boundary test
        "UPDATE device_status SET updated_at=? WHERE device_serial=?", (stale_timestamp, "phone-1"),
    )
    stale = await mcp_server._ready(None)  # type: ignore[arg-type]
    assert stale.status_code == 503
    assert json.loads(stale.body)["ready"] is False


@pytest.mark.asyncio
async def test_device_status_always_identifies_configured_serial_and_timestamp(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    monkeypatch.setattr(mcp_server, "_task_store", store)
    monkeypatch.setenv("MOBILE_DEVICE_SERIAL", "phone-7")

    missing = await mcp_server.get_device_status()
    assert missing["device_serial"] == "phone-7"
    assert missing["status_timestamp"] is None

    store.save_device_status("phone-7", {"device_available": True, "worker_ready": True})
    available = await mcp_server.get_device_status()
    assert available["connected"] is True
    assert available["device_serial"] == "phone-7"
    assert isinstance(available["status_timestamp"], str)

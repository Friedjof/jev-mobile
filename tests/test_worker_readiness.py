from __future__ import annotations

from types import SimpleNamespace

import pytest

from jev_mobile.providers.mock import MockProvider
from jev_mobile.runtime.worker import DurableWorker
from jev_mobile.state.models import RawDeviceState
from jev_mobile.task_store import TaskStatus, TaskStore
from jev_mobile.tasks import CompletionRequirement, TaskSpec


class _ProbeDevice:
    def __init__(self, available: list[bool]) -> None:
        self.available = available

    async def __aenter__(self) -> "_ProbeDevice":
        if not self.available[0]:
            raise RuntimeError("ADB device offline")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def observe(self) -> RawDeviceState:
        return RawDeviceState(package="example.app", activity="Main", elements=[])


class _CompletingAgent:
    def __init__(self, store: TaskStore, available: list[bool]) -> None:
        self.store = store
        self.available = available
        self.settings = SimpleNamespace(configuration_errors=())
        self.provider = MockProvider()
        self.run_calls = 0

    def device_factory(self) -> _ProbeDevice:
        return _ProbeDevice(self.available)

    async def run(self, task_id: str) -> None:
        self.run_calls += 1
        task = self.store.get(task_id)
        assert task is not None
        task.status = TaskStatus.SUCCEEDED
        self.store.save(task)


@pytest.mark.asyncio
async def test_worker_leaves_task_queued_until_fresh_backend_probe_succeeds(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANDROID_USER_HOME", raising=False)
    monkeypatch.delenv("ADB_VENDOR_KEYS", raising=False)
    store = TaskStore(tmp_path / "tasks.sqlite3")
    spec = TaskSpec(completion=[CompletionRequirement(type="app_open", value="example.app")])
    task = store.create("open example", spec)
    availability = [False]
    agent = _CompletingAgent(store, availability)
    worker = DurableWorker(store, agent, "phone-1")  # type: ignore[arg-type]

    assert await worker.run_once() is False
    unavailable = store.get(task.id)
    assert unavailable is not None and unavailable.status == TaskStatus.QUEUED
    assert agent.run_calls == 0
    status = store.get_device_status("phone-1")
    assert status is not None
    assert status["worker_ready"] is False
    assert status["readiness_category"] == "BACKEND_UNAVAILABLE"

    availability[0] = True
    assert await worker.run_once() is True
    completed = store.get(task.id)
    assert completed is not None and completed.status == TaskStatus.SUCCEEDED
    assert agent.run_calls == 1
    assert store.get_device_status("phone-1")["worker_ready"] is True  # type: ignore[index]


@pytest.mark.asyncio
async def test_configuration_error_prevents_device_probe_and_task_claim(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANDROID_USER_HOME", raising=False)
    monkeypatch.delenv("ADB_VENDOR_KEYS", raising=False)
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create("open example", TaskSpec(app="Example"))
    agent = _CompletingAgent(store, [True])
    agent.settings = SimpleNamespace(configuration_errors=("PROVIDER_SECRET_UNREADABLE: configured file",))
    worker = DurableWorker(store, agent, "phone-1")  # type: ignore[arg-type]

    assert await worker.run_once() is False
    persisted = store.get(task.id)
    assert persisted is not None and persisted.status == TaskStatus.QUEUED
    assert agent.run_calls == 0
    status = store.get_device_status("phone-1")
    assert status is not None and status["readiness_category"] == "CONFIGURATION_INVALID"

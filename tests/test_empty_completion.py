from __future__ import annotations

import pytest

from jev_mobile.agent.mobile_agent import MobileAgent
from jev_mobile.config import Settings
from jev_mobile.providers.mock import MockProvider
from jev_mobile.task_store import TaskStatus, TaskStore
from jev_mobile.tasks import TaskSpec


@pytest.mark.asyncio
async def test_empty_completion_contract_fails_before_device_access(tmp_path) -> None:
    entered = False

    class ForbiddenDevice:
        async def __aenter__(self):
            nonlocal entered
            entered = True
            raise AssertionError("an unverifiable task must not access the device")

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create("read something unspecified", TaskSpec())
    settings = Settings((), None, tmp_path / "traces", 0.01, 0.1, 0.8)
    agent = MobileAgent(store, ForbiddenDevice, MockProvider(), settings)

    await agent.run(task.id)

    persisted = store.get(task.id)
    assert persisted is not None
    assert persisted.status == TaskStatus.FAILED
    assert persisted.failure_reason == "NO_VERIFIABLE_COMPLETION_CRITERIA"
    assert persisted.result is None
    assert persisted.finished_at is not None
    assert entered is False
    assert [event["event_type"] for event in store.events(task.id)][-1] == "NO_VERIFIABLE_COMPLETION_CRITERIA"

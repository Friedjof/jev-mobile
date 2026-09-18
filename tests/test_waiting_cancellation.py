from datetime import UTC, datetime

from jev_mobile import mcp_server
from jev_mobile.task_store import MobileTask, TaskStatus, TaskStore
from jev_mobile.tasks import TaskSpec


async def test_cancelling_waiting_task_is_terminal(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = MobileTask(id="task_wait", instruction="x", task_spec=TaskSpec(), created_at=datetime.now(UTC), status=TaskStatus.WAITING_FOR_USER)
    store.save(task)
    monkeypatch.setattr(mcp_server, "_task_store", store)
    result = await mcp_server.cancel_task("task_wait")
    assert result["status"] == "cancelled"
    assert [event["event_type"] for event in store.events(task.id)] == ["TASK_CANCELLATION_REQUESTED", "TASK_CANCELLED"]

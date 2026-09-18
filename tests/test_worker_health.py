from jev_mobile.task_store import TaskStore


def test_task_store_healthcheck_does_not_require_device(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    assert store.healthcheck() is True

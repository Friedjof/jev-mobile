from jev_mobile.task_store import TaskStore


def test_worker_device_status_is_durable_and_mcp_readable(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    store.save_device_status("serial-1", {
        "worker_id": "worker-1",
        "worker_active": True,
        "device_available": True,
        "package": "example.app",
    })

    observed = store.get_device_status("serial-1")

    assert observed is not None
    assert observed["device_available"] is True
    assert observed["package"] == "example.app"
    assert "updated_at" in observed

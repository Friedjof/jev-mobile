from jev_mobile.cli import _portal_accessibility_enabled
from jev_mobile.task_store import TaskStore


def test_task_store_healthcheck_does_not_require_device(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    assert store.healthcheck() is True


def test_portal_accessibility_healthcheck_accepts_android_component_forms() -> None:
    assert _portal_accessibility_enabled("com.mobilerun.portal/.service.MobilerunAccessibilityService")
    assert _portal_accessibility_enabled(
        "com.mobilerun.portal/com.mobilerun.portal.service.MobilerunAccessibilityService"
    )
    assert not _portal_accessibility_enabled("io.jev.mobile.bridge/.JevAccessibilityService")

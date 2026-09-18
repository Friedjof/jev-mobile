from datetime import UTC, datetime

from jev_mobile.mcp_server import _public_result
from jev_mobile.task_store import MobileTask, TaskStatus
from jev_mobile.tasks import TaskSpec


def test_persisted_foreign_app_state_is_a_safety_boundary() -> None:
    task = MobileTask(
        id="task-safety",
        instruction="create something",
        task_spec=TaskSpec(),
        created_at=datetime.now(UTC),
        status=TaskStatus.FAILED,
        failure_reason="ORIENTATION_BLOCKED_BY_PERSISTED_APP_STATE",
    )

    assert _public_result(task) == {
        "status": "failed",
        "failure": {
            "category": "SAFETY_BLOCKED",
            "reason": "PERSISTED_FOREIGN_APP_STATE",
            "message": "The target app persistently restores unrelated content. Continuing would require destructive application-state reset.",
            "recoverable": False,
        },
    }


def test_unverifiable_task_never_claims_verified_success() -> None:
    task = MobileTask(
        id="task-unverifiable",
        instruction="read something",
        task_spec=TaskSpec(),
        created_at=datetime.now(UTC),
        status=TaskStatus.FAILED,
        failure_reason="NO_VERIFIABLE_COMPLETION_CRITERIA",
    )

    assert _public_result(task) == {
        "status": "failed",
        "failure": {
            "category": "UNSUPPORTED_TASK",
            "reason": "NO_VERIFIABLE_COMPLETION_CRITERIA",
            "message": "The task could not be executed because it has no observable completion criteria.",
            "recoverable": True,
        },
    }

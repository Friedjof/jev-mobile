from datetime import UTC, datetime
import json

from jev_mobile.mcp_server import _public_result
from jev_mobile.task_store import MobileTask, TaskStatus
from jev_mobile.tasks import (
    CompletionRequirement,
    RequestedOutput,
    TaskPolicy,
    TaskPolicyMode,
    TaskSpec,
)


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


def test_unknown_internal_failure_is_exposed_as_stable_agent_bug() -> None:
    task = MobileTask(
        id="task-agent-bug",
        instruction="do something",
        task_spec=TaskSpec(),
        created_at=datetime.now(UTC),
        status=TaskStatus.FAILED,
        failure_reason="Jev selected an invalid action",
    )

    assert _public_result(task) == {
        "status": "failed",
        "failure": {
            "category": "AGENT_BUG",
            "reason": "JEV SELECTED AN INVALID ACTION",
            "message": "Jev selected an invalid action",
            "recoverable": False,
        },
    }


def test_public_success_contains_only_safe_evidence_and_explicit_observations() -> None:
    task = MobileTask(
        id="task-result",
        instruction="read a value",
        task_spec=TaskSpec(
            intent="read_information",
            app="Settings",
            policy=TaskPolicy(mode=TaskPolicyMode.READ_ONLY),
            requested_outputs=[RequestedOutput(key="device_name", description="Device name")],
            completion=[CompletionRequirement(type="information_observed", output_key="device_name")],
        ),
        created_at=datetime.now(UTC),
        status=TaskStatus.SUCCEEDED,
        result={
            "summary": "Observed the requested value.",
            "verified": True,
            "observations": {"device_name": {"status": "observed", "value": "Example Phone"}},
            "requirements": [
                {
                    "key": "app_open",
                    "kind": "app_open",
                    "status": "satisfied",
                    "evidence": {
                        "requirement": "app_open",
                        "kind": "app_open",
                        "output_key": None,
                        "source": {
                            "snapshot_id": "s17",
                            "package": "com.android.settings",
                            "semantic_role": "app_open",
                            "observed_value": "com.android.settings",
                            "confidence": 1.0,
                        },
                    },
                },
                {
                    "key": "completion:1",
                    "kind": "information_observed",
                    "status": "satisfied",
                    "evidence": {
                        "requirement": "completion:1",
                        "kind": "information_observed",
                        "output_key": "device_name",
                        "source": {
                            "snapshot_id": "s17",
                            "package": "com.android.settings",
                            "semantic_role": "value",
                            "label": "Device name",
                            "observed_value": "Example Phone",
                            "confidence": 0.96,
                            "ref": "s17:e9",
                            "raw_accessibility_tree": "must not escape",
                        },
                    },
                },
            ],
            "steps": 7,
        },
        requirements={"app_open": "satisfied", "completion:1": "satisfied"},
    )

    result = _public_result(task)

    assert result is not None and result["verified"] is True
    assert result["observations"]["device_name"]["value"] == "Example Phone"
    serialized = json.dumps(result)
    assert "s17:e9" not in serialized
    assert "raw_accessibility_tree" not in serialized


def test_success_status_alone_cannot_claim_verification() -> None:
    task = MobileTask(
        id="task-unverified-result",
        instruction="do something",
        task_spec=TaskSpec(),
        created_at=datetime.now(UTC),
        status=TaskStatus.SUCCEEDED,
        result={"summary": "Not evidenced", "steps": 1},
    )

    assert _public_result(task)["verified"] is False  # type: ignore[index]


def test_persisted_verified_flag_is_rejected_when_requirement_evidence_is_missing() -> None:
    task = MobileTask(
        id="task-false-verification",
        instruction="do something",
        task_spec=TaskSpec(),
        created_at=datetime.now(UTC),
        status=TaskStatus.SUCCEEDED,
        result={"summary": "Claimed success", "verified": True, "requirements": [], "steps": 1},
    )

    assert _public_result(task)["verified"] is False  # type: ignore[index]

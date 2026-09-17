from jev_mobile.agent.grounding import EntityOwnership, InteractionContext
from jev_mobile.safety.lifecycle_reset import LifecycleResetDecision, evaluate_reset_safety


def test_foreign_editor_requires_scoped_approval() -> None:
    assert evaluate_reset_safety(task_id="task-a", package="app", ownership=EntityOwnership.FOREIGN, context=InteractionContext.EDITOR) == LifecycleResetDecision.REQUIRE_APPROVAL


def test_authorization_is_scoped_and_one_shot() -> None:
    authorization = {"task_id": "task-a", "package": "app", "operation": "reset_app_task", "consumed": False}
    assert evaluate_reset_safety(task_id="task-a", package="app", ownership=EntityOwnership.FOREIGN, context=InteractionContext.EDITOR, authorization=authorization) == LifecycleResetDecision.ALLOW
    assert evaluate_reset_safety(task_id="task-b", package="app", ownership=EntityOwnership.FOREIGN, context=InteractionContext.EDITOR, authorization=authorization) == LifecycleResetDecision.REQUIRE_APPROVAL
    assert evaluate_reset_safety(task_id="task-a", package="other", ownership=EntityOwnership.FOREIGN, context=InteractionContext.EDITOR, authorization=authorization) == LifecycleResetDecision.REQUIRE_APPROVAL
    authorization["consumed"] = True
    assert evaluate_reset_safety(task_id="task-a", package="app", ownership=EntityOwnership.FOREIGN, context=InteractionContext.EDITOR, authorization=authorization) == LifecycleResetDecision.REQUIRE_APPROVAL


def test_reset_approval_does_not_authorize_process_restart() -> None:
    authorization = {"task_id": "task-a", "package": "app", "operation": "reset_app_task", "consumed": False}
    assert evaluate_reset_safety(task_id="task-a", package="app", ownership=EntityOwnership.FOREIGN, context=InteractionContext.EDITOR, authorization=authorization, operation="restart_app_process") == LifecycleResetDecision.REQUIRE_APPROVAL

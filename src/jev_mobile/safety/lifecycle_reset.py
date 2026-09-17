"""Safety policy for Android activity-task recovery."""

from enum import StrEnum

from ..agent.grounding import EntityOwnership, InteractionContext


class LifecycleResetDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


def evaluate_reset_safety(*, task_id: str, package: str, ownership: EntityOwnership,
                          context: InteractionContext, authorization: dict[str, object] | None = None,
                          operation: str = "reset_app_task",
                          foreign_state_persisted: bool = False) -> LifecycleResetDecision:
    """Decide capability separately from permission; never guess foreign safety."""
    if authorization and not authorization.get("consumed") and authorization.get("task_id") == task_id and authorization.get("package") == package and authorization.get("operation") == operation:
        return LifecycleResetDecision.ALLOW
    if ownership == EntityOwnership.CURRENT_TASK or foreign_state_persisted:
        return LifecycleResetDecision.ALLOW
    if ownership == EntityOwnership.FOREIGN and context == InteractionContext.EDITOR:
        return LifecycleResetDecision.REQUIRE_APPROVAL
    return LifecycleResetDecision.DENY


def reset_question(package: str, operation: str = "reset_app_task") -> dict[str, object]:
    from uuid import uuid4
    text = "The app keeps reopening an existing editor. Resetting the app's current navigation task may discard unsaved changes in that editor. May I reset the app task?" if operation == "reset_app_task" else "The app keeps restoring an existing editor even after resetting navigation. Restarting the app may discard unsaved changes in that editor. May I restart the app?"
    return {"id": f"q_{uuid4().hex}", "type": "approve_lifecycle_recovery", "operation": operation, "package": package, "text": text, "options": ["allow", "deny"]}

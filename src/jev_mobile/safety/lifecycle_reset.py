"""Safety policy for Android activity-task recovery."""

from enum import StrEnum

from ..agent.grounding import EntityOwnership, InteractionContext
from ..tasks import AnswerEffect, AnswerEffectKind, QuestionOption, QuestionType, make_question


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
    text = "The app keeps reopening an existing editor. Resetting the app's current navigation task may discard unsaved changes in that editor. May I reset the app task?" if operation == "reset_app_task" else "The app keeps restoring an existing editor even after resetting navigation. Restarting the app may discard unsaved changes in that editor. May I restart the app?"
    return make_question(
        question_type=QuestionType.APPROVAL,
        reason="LIFECYCLE_RECOVERY_REQUIRES_APPROVAL",
        prompt_key=f"approve:{operation}:{package}",
        text=text,
        options=[QuestionOption(id="allow", label="Allow"), QuestionOption(id="deny", label="Deny")],
        effect=AnswerEffect(kind=AnswerEffectKind.AUTHORIZE_OPERATION, parameter=operation),
        operation=operation,
        package=package,
    ).model_dump(mode="json")

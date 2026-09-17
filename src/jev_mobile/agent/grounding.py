"""Ground a durable task in the currently observed, possibly foreign UI."""

from enum import StrEnum

from ..state.models import SemanticState


class InteractionContext(StrEnum):
    UNKNOWN = "unknown"
    COLLECTION = "collection"
    ENTITY_DETAIL = "entity_detail"
    EDITOR = "editor"
    DIALOG = "dialog"


class EntityOwnership(StrEnum):
    UNKNOWN = "unknown"
    FOREIGN = "foreign"
    CURRENT_TASK = "current_task"


def context_type(state: SemanticState) -> InteractionContext:
    if state.dialog:
        return InteractionContext.DIALOG
    if any(element.visible and element.editable for element in state.elements):
        return InteractionContext.EDITOR
    labels = " ".join((element.label or "").casefold() for element in state.elements if element.visible)
    if any(word in labels for word in ("create", "new", "add", "compose")):
        return InteractionContext.COLLECTION
    return InteractionContext.ENTITY_DETAIL if any(element.visible and element.clickable for element in state.elements) else InteractionContext.UNKNOWN


def ownership_for(state: SemanticState, context: dict[str, object]) -> EntityOwnership:
    if context.get("entity_ownership") == EntityOwnership.CURRENT_TASK.value:
        return EntityOwnership.CURRENT_TASK
    return EntityOwnership.FOREIGN if context_type(state) == InteractionContext.EDITOR else EntityOwnership.UNKNOWN


def create_affordances(actions) -> list[dict[str, object]]:
    """Find generic, executable create affordances without app knowledge."""
    result = []
    for action in actions:
        first = (action.label or "").casefold().strip().split(maxsplit=1)
        if "activate" in action.capabilities and action.semantic_role in {"button", "image", "menu_item", "fab"} and first and first[0] in {"create", "new", "add", "compose"}:
            result.append({"ref": action.ref, "role": action.semantic_role, "label": action.label, "confidence": 0.9})
    return result


def evaluate_safe_creation_context(state: SemanticState, *, target_package: str | None,
                                   ownership: EntityOwnership, actions) -> dict[str, object]:
    affordances = create_affordances(actions)
    safe = bool(target_package and state.app == target_package and ownership != EntityOwnership.FOREIGN and not state.dialog and context_type(state) != InteractionContext.EDITOR and affordances)
    return {"safe": safe, "target_app_foreground": state.app == target_package,
            "foreign_context": ownership == EntityOwnership.FOREIGN,
            "blocking_surface": bool(state.dialog), "context": context_type(state).value,
            "create_affordances": affordances}

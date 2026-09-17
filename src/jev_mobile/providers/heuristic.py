"""Small deterministic provider for safe Android Settings navigation demos."""

from __future__ import annotations

from ..actions.models import ActionKind, CandidateAction, Decision
from ..state.models import SemanticState


class HeuristicProvider:
    name = "heuristic"
    async def decide(self, goal: str, state: SemanticState, actions: list[CandidateAction]) -> Decision:
        normalized_goal = goal.casefold()
        done = next((action for action in actions if action.kind == ActionKind.DONE), None)
        if done:
            return Decision(action_id=done.id, confidence=0.99)
        desired = "network & internet" if "network" in normalized_goal or "wifi" in normalized_goal else "display" if "display" in normalized_goal else None
        labels = {action.label.casefold(): action for action in actions}
        if desired and desired in labels:
            return Decision(action_id=labels[desired].id, confidence=0.99)
        if desired and any(desired in (item.label or "").casefold() for item in state.elements):
            chosen = next((action for action in actions if desired in action.label.casefold()), None)
            if chosen:
                return Decision(action_id=chosen.id, confidence=0.99)
        if state.app != "com.android.settings" and "settings" in normalized_goal:
            launch = next((action for action in actions if action.kind == ActionKind.LAUNCH_APP), None)
            if launch: return Decision(action_id=launch.id, confidence=0.99)
        if "go back" in normalized_goal or "then go back" in normalized_goal:
            back = next((action for action in actions if action.kind == ActionKind.BACK), None)
            if back and desired and any(desired in (item.label or "").casefold() for item in state.elements):
                return Decision(action_id=back.id, confidence=0.95)
        return Decision(action_id=next(a.id for a in actions if a.kind == ActionKind.ESCALATE), confidence=0.45)

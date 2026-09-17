from jev_mobile.actions.models import ActionKind, CandidateAction, Decision
from jev_mobile.escalation.planner import PlanStep, RecoveryPlan
from jev_mobile.providers.planned import PlanGuidedProvider
from jev_mobile.state.models import SemanticElement, SemanticState


class EscalatingProvider:
    name = "fake-fast"

    async def decide(self, goal, state, actions):
        return Decision(action_id="ESCALATE", confidence=0.4, probabilities={"ESCALATE": 0.4, "A1": 0.35})


class FakePlanner:
    async def plan(self, goal, state, reason, recent_actions):
        return RecoveryPlan(summary="Open Settings first", steps=[
            PlanStep(instruction="Open Android Settings.", success_hints=["Settings"])
        ], latency_ms=3)


async def test_uncertain_fast_decision_creates_text_only_plan() -> None:
    semantic_state = SemanticState(
        app="com.example.launcher", screen_hint="Home", fingerprint="home", elements=[
            SemanticElement(id="e1", role="button", label="Apps", clickable=True, editable=False,
                            enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0)
        ],
    )
    provider = PlanGuidedProvider(EscalatingProvider(), FakePlanner(), confidence_threshold=.8,
                                  minimum_probability_margin=.15)
    actions = [CandidateAction(id="A1", kind=ActionKind.LAUNCH_APP, label="Open Settings")]
    decision = await provider.decide("Configure Wi-Fi", semantic_state, actions)
    assert decision.reasoning_metadata["recovery_plan_created"] is True
    assert "Recovery-plan checkpoint 1/1" in provider.contextual_goal("Configure Wi-Fi", semantic_state)


async def test_plan_first_creates_plan_before_first_decision() -> None:
    state = SemanticState(
        app="com.example.launcher", screen_hint="Home", fingerprint="home", elements=[
            SemanticElement(id="e1", role="button", label="Apps", clickable=True, editable=False,
                            enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0)
        ],
    )
    provider = PlanGuidedProvider(EscalatingProvider(), FakePlanner(), confidence_threshold=.8,
                                  minimum_probability_margin=.15, plan_first=True)
    plan = await provider.ensure_plan("Configure Wi-Fi", state)
    assert plan is not None
    assert plan.summary == "Open Settings first"
    assert "Recovery-plan checkpoint 1/1" in provider.contextual_goal("Configure Wi-Fi", state)

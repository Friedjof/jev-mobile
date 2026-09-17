from jev_mobile.actions.models import ActionKind, CandidateAction, Decision
from jev_mobile.escalation.planner import PlanStep, RecoveryPlan
from jev_mobile.providers.planned import PlanGuidedProvider
from jev_mobile.state.models import SemanticElement, SemanticState


class EscalatingProvider:
    name = "fake-fast"

    async def decide(self, goal, state, actions):
        return Decision(action_id="ESCALATE", confidence=0.4, probabilities={"ESCALATE": 0.4, "A1": 0.35})


class FakePlanner:
    def __init__(self):
        self.recent_context = None

    async def plan(self, goal, state, reason, recent_actions):
        self.recent_context = recent_actions
        return RecoveryPlan(summary="Open Settings first", steps=[
            PlanStep(instruction="Open Android Settings.", success_hints=["Settings"])
        ], latency_ms=3)


async def test_uncertain_fast_decision_does_not_call_llm_before_controller_exploration() -> None:
    semantic_state = SemanticState(
        app="com.example.launcher", screen_hint="Home", fingerprint="home", elements=[
            SemanticElement(id="e1", role="button", label="Apps", clickable=True, editable=False,
                            enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0)
        ],
    )
    planner = FakePlanner()
    provider = PlanGuidedProvider(EscalatingProvider(), planner, confidence_threshold=.8,
                                  minimum_probability_margin=.15)
    actions = [CandidateAction(id="A1", kind=ActionKind.LAUNCH_APP, label="Open Settings")]
    decision = await provider.decide("Configure Wi-Fi", semantic_state, actions)
    assert decision.action_id == "ESCALATE"
    assert planner.recent_context is None

    plan = await provider.recover("Configure Wi-Fi", semantic_state, "exploration_exhausted")
    assert plan is not None
    assert "Recovery-plan checkpoint 1/1" in provider.contextual_goal("Configure Wi-Fi", semantic_state)
    assert planner.recent_context == []


async def test_plan_first_creates_plan_before_first_decision() -> None:
    state = SemanticState(
        app="com.example.launcher", screen_hint="Home", fingerprint="home", elements=[
            SemanticElement(id="e1", role="button", label="Apps", clickable=True, editable=False,
                            enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0)
        ],
    )
    planner = FakePlanner()
    state = state.model_copy(update={"recent_context": [
        {"action": "Open app", "outcome": "Editor opened"},
    ]})
    provider = PlanGuidedProvider(EscalatingProvider(), planner, confidence_threshold=.8,
                                  minimum_probability_margin=.15, plan_first=True)
    plan = await provider.ensure_plan("Configure Wi-Fi", state)
    assert plan is not None
    assert plan.summary == "Open Settings first"
    assert planner.recent_context == [{"action": "Open app", "outcome": "Editor opened"}]
    assert "Recovery-plan checkpoint 1/1" in provider.contextual_goal("Configure Wi-Fi", state)

from jev_mobile.actions.builder import build_candidates
from jev_mobile.providers.heuristic import HeuristicProvider
from jev_mobile.state.models import SemanticElement, SemanticState


async def test_heuristic_selects_network_and_back() -> None:
    root = SemanticState(app="com.android.settings", screen_hint=None, fingerprint="root", elements=[
        SemanticElement(id="e1", role="button", label="Network & internet", clickable=True, editable=False,
                        enabled=True, selected=False, visible=True, scrollable=False, depth=0, raw_index=0),
    ])
    provider = HeuristicProvider()
    goal = "Open Network & internet settings and then go back"
    first = await provider.decide(goal, root, build_candidates(root, goal))
    assert first.action_id == "A1"
    page = root.model_copy(update={"fingerprint": "page"})
    returned = await provider.decide(goal, page, build_candidates(page, goal, return_mode=True))
    assert next(item.kind.value for item in build_candidates(page, goal, return_mode=True) if item.id == returned.action_id) == "back"

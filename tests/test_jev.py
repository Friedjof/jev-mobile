import pytest

from jev_mobile.providers.jev import build_jev_state, probability_margin
from jev_mobile.state.models import SemanticElement, SemanticState


def test_jev_payload_is_compact_and_margin_is_top_two_gap() -> None:
    state = SemanticState(
        app="com.android.settings",
        screen_hint="Settings",
        fingerprint="state",
        elements=[
            SemanticElement(
                id="e1", role="button", label="Network & internet", clickable=True,
                editable=False, enabled=True, selected=False, visible=True,
                scrollable=False, depth=0, raw_index=0,
            )
        ],
    )
    payload = build_jev_state("Open Network & internet", state)
    assert payload["screen"]["elements"] == [{
        "id": "e1", "role": "button", "text": "Network & internet",
        "clickable": True, "editable": False, "enabled": True, "selected": False,
    }]
    assert probability_margin({"A1": 0.51, "A2": 0.47, "A3": 0.02}) == pytest.approx(0.04)

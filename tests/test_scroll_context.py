from __future__ import annotations

import pytest

from jev_mobile.actions.models import ActionKind
from jev_mobile.agent.mobile_agent import MobileAgent
from jev_mobile.device.portal_adb import PortalAdbDeviceAdapter
from jev_mobile.perception import ActionCatalog, SnapshotRefRegistry
from jev_mobile.providers.jev import build_jev_state
from jev_mobile.state.models import Bounds, RawDeviceElement, RawDeviceState, ScrollPosition
from jev_mobile.state.normalize import normalize
from jev_mobile.tasks import task_spec_from_goal


@pytest.mark.parametrize(
    ("available_actions", "position", "can_up", "can_down"),
    [
        (["SCROLL_FORWARD"], ScrollPosition.TOP, False, True),
        (["ACTION_SCROLL_BACKWARD", "ACTION_SCROLL_FORWARD"], ScrollPosition.MIDDLE, True, True),
        (["SCROLL_BACKWARD"], ScrollPosition.BOTTOM, True, False),
        ([], ScrollPosition.UNKNOWN, False, False),
    ],
)
def test_normalizer_exposes_semantic_scroll_position(
    available_actions: list[str], position: ScrollPosition, can_up: bool, can_down: bool,
) -> None:
    state = normalize(RawDeviceState(elements=[
        RawDeviceElement(
            node_id="list", class_name="androidx.recyclerview.widget.RecyclerView",
            scrollable=True, available_actions=available_actions,
            bounds=Bounds(x=0, y=100, width=1080, height=1800), depth=1,
        ),
        RawDeviceElement(
            node_id="first", parent_id="list", text="First visible row",
            bounds=Bounds(x=0, y=120, width=1080, height=100), depth=2,
        ),
        RawDeviceElement(
            node_id="last", parent_id="list", text="Last visible row",
            bounds=Bounds(x=0, y=1700, width=1080, height=100), depth=2,
        ),
    ]))

    context = state.scroll_contexts[0]
    assert context.position == position
    assert context.can_scroll_up is can_up
    assert context.can_scroll_down is can_down
    assert context.visible_start == "First visible row"
    assert context.visible_end == "Last visible row"


def test_bottom_catalog_and_candidates_offer_only_scroll_up() -> None:
    state = normalize(RawDeviceState(package="com.android.settings", elements=[
        RawDeviceElement(
            node_id="list", class_name="androidx.recyclerview.widget.RecyclerView",
            scrollable=True, available_actions=["SCROLL_BACKWARD"],
        ),
    ]))
    registry = SnapshotRefRegistry()
    catalog = ActionCatalog.build(state, registry)
    scroll_action = catalog.actions[0]

    assert scroll_action.capabilities == ["scroll_up"]

    candidates, _ = MobileAgent._candidates(
        catalog, registry, [],
        task_spec_from_goal("Open Android Settings and read the Android version."),
        "com.android.settings", [], state.fingerprint,
    )
    assert any(candidate.kind == ActionKind.SCROLL_UP for candidate in candidates)
    assert not any(candidate.kind == ActionKind.SCROLL_DOWN for candidate in candidates)


def test_scroll_context_is_visible_to_jev() -> None:
    state = normalize(RawDeviceState(package="com.android.settings", elements=[
        RawDeviceElement(
            node_id="list", class_name="androidx.recyclerview.widget.RecyclerView", scrollable=True,
            available_actions=["SCROLL_BACKWARD"],
        ),
    ]))

    payload = build_jev_state("Read the Android version", state)

    assert payload["scroll_contexts"] == [{
        "container_role": "recyclerview",
        "position": "bottom",
        "can_scroll_up": True,
        "can_scroll_down": False,
        "visible_start": None,
        "visible_end": None,
    }]


def test_portal_preserves_accessibility_action_names_and_strings() -> None:
    adapter = PortalAdbDeviceAdapter("device")
    element = adapter._element({
        "id": "list",
        "scrollable": True,
        "actionList": [
            {"id": 8192, "label": "", "name": "SCROLL_BACKWARD"},
            "ACTION_SCROLL_FORWARD",
        ],
    }, None, 0)

    assert element is not None
    assert element.available_actions == ["SCROLL_BACKWARD", "ACTION_SCROLL_FORWARD"]

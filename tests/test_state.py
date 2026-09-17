from jev_mobile.state.models import Bounds, RawDeviceElement, RawDeviceState
from jev_mobile.state.normalize import normalize


def test_fingerprint_ignores_element_order_irrelevant_runtime_fields() -> None:
    raw = RawDeviceState(package="com.android.settings", activity="Settings", elements=[
        RawDeviceElement(text="Network & internet", class_name="android.widget.TextView", clickable=True,
                         bounds=Bounds(x=12, y=100, width=300, height=80)),
    ])
    first = normalize(raw)
    second = normalize(raw.model_copy(update={"observed_at_monotonic": 99.0}))
    assert first.fingerprint == second.fingerprint
    assert first.elements[0].id == "e1"


def test_normalizer_detects_loading() -> None:
    state = normalize(RawDeviceState(elements=[RawDeviceElement(class_name="android.widget.ProgressBar")]))
    assert state.loading

from jev_mobile.state.models import Bounds, RawDeviceElement, RawDeviceState
from jev_mobile.state.normalize import normalize
from jev_mobile.tasks import deterministic_interpretation


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


def test_normalizer_derives_generic_text_field_semantics_and_focus() -> None:
    state = normalize(RawDeviceState(elements=[
        RawDeviceElement(node_id="title", editable=True, resource_id="example:id/editable_title",
                         class_name="android.widget.EditText"),
        RawDeviceElement(node_id="body", editable=True, focused=True, resource_id="example:id/edit_note_text",
                         class_name="android.widget.EditText"),
        RawDeviceElement(node_id="search", editable=True, resource_id="example:id/search_input",
                         class_name="android.widget.EditText"),
    ]))
    fields = {element.id: element for element in state.elements}

    assert fields["title"].field_role == "title"
    assert fields["body"].field_role == "body"
    assert fields["body"].field_name == "Note body"
    assert fields["search"].field_role == "search"
    assert state.focused_element_id == "body"


def test_deterministic_task_interpretation_extracts_shopping_items_without_llm() -> None:
    interpretation = deterministic_interpretation(
        "create a new note for shopping: Eier, Brot, Fisch, Frischkäse, Salat, Essig"
    )

    assert interpretation.purpose == "shopping"
    assert interpretation.item_candidates == ["Eier", "Brot", "Fisch", "Frischkäse", "Salat", "Essig"]
    assert interpretation.task_spec.fields[0].role == "body"

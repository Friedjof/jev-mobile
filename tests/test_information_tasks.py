from __future__ import annotations

from jev_mobile.actions.models import ActionKind
from jev_mobile.agent.mobile_agent import MobileAgent
from jev_mobile.perception import ActionCatalog, SnapshotRefRegistry
from jev_mobile.requirements.evaluator import RequirementEvaluator
from jev_mobile.requirements.information import InformationMatchStatus, extract_information
from jev_mobile.state.models import RawDeviceElement, RawDeviceState
from jev_mobile.state.normalize import normalize
from jev_mobile.tasks import (
    InformationRequest,
    InformationSensitivity,
    TaskPolicyMode,
    task_spec_from_goal,
    validate_task_contract,
)


def _settings_state(*values: str):
    elements = [RawDeviceElement(node_id="row", child_ids=["label", *[f"value{i}" for i in range(len(values))]])]
    elements.append(RawDeviceElement(
        node_id="label", parent_id="row", text="Device name", class_name="TextView",
    ))
    elements.extend(
        RawDeviceElement(
            node_id=f"value{index}", parent_id="row", text=value, class_name="TextView",
        )
        for index, value in enumerate(values)
    )
    return normalize(RawDeviceState(
        package="com.android.settings", snapshot_id="native-17", elements=elements,
    ))


def test_natural_language_read_task_has_explicit_contract() -> None:
    spec = task_spec_from_goal("Open Android Settings and read the device name.")

    assert spec.intent == "read_information"
    assert spec.policy is not None and spec.policy.mode == TaskPolicyMode.READ_ONLY
    assert spec.app_package == "com.android.settings"
    assert spec.information_requests[0].key == "device_name"
    assert spec.completion[0].type == "information_observed"
    assert validate_task_contract(spec).valid


def test_visible_sibling_label_value_is_observed_with_source() -> None:
    request = InformationRequest(
        key="device_name", question="What is the device name?", semantic_hints=["device name"],
    )

    match = extract_information(request, _settings_state("moto g example"))

    assert match.status == InformationMatchStatus.OBSERVED
    assert match.candidate is not None
    assert match.candidate.value == "moto g example"
    assert match.candidate.label == "Device name"
    assert match.candidate.confidence >= 0.9


def test_multiple_observed_values_require_structured_selection() -> None:
    request = InformationRequest(
        key="device_name", question="What is the device name?", semantic_hints=["device name"],
    )

    ambiguous = extract_information(request, _settings_state("Phone A", "Phone B"))
    selected = extract_information(
        request,
        _settings_state("Phone A", "Phone B"),
        selected_candidate_id=ambiguous.candidates[1].id,
    )

    assert ambiguous.status == InformationMatchStatus.AMBIGUOUS
    assert len(ambiguous.candidates) == 2
    assert selected.status == InformationMatchStatus.OBSERVED
    assert selected.candidate is not None and selected.candidate.value == "Phone B"


def test_password_and_prohibited_requests_are_never_returned() -> None:
    request = InformationRequest(
        key="password", question="Read password", semantic_hints=["password"],
        sensitivity=InformationSensitivity.PROHIBITED,
    )
    state = normalize(RawDeviceState(elements=[RawDeviceElement(
        node_id="password", hint="Password", text="secret-value", editable=True, password=True,
    )]))

    assert extract_information(request, state).status == InformationMatchStatus.PROHIBITED
    spec = task_spec_from_goal("Read the password from Android Settings")
    assert "prohibited sensitive information request" in validate_task_contract(spec).errors


def test_requirement_changes_only_from_observed_information_evidence() -> None:
    spec = task_spec_from_goal("Open Android Settings and read the device name.")
    evaluator = RequirementEvaluator()

    absent = evaluator.evaluate(spec, normalize(RawDeviceState(package="com.android.settings", elements=[])))
    observed = evaluator.evaluate(spec, _settings_state("Example Phone"))

    absent_information = next(item for item in absent if item.kind == "information_observed")
    observed_information = next(item for item in observed if item.kind == "information_observed")
    assert absent_information.status.value == "unsatisfied"
    assert observed_information.status.value == "satisfied"
    assert observed_information.evidence["observed_value"] == "Example Phone"
    assert observed_information.evidence["snapshot_id"] == "native-17"


def test_read_only_candidate_policy_blocks_writes_and_toggles() -> None:
    spec = task_spec_from_goal("Open Android Settings and read the device name.")
    state = normalize(RawDeviceState(package="com.android.settings", elements=[
        RawDeviceElement(node_id="about", text="About phone", clickable=True),
        RawDeviceElement(node_id="toggle", text="Enable feature", clickable=True, checkable=True),
        RawDeviceElement(node_id="field", hint="Name", text="Old", editable=True),
    ]))
    registry = SnapshotRefRegistry()
    catalog = ActionCatalog.build(state, registry)

    actions, _ = MobileAgent._candidates(
        catalog, registry, [], spec, "com.android.settings", [], state.fingerprint,
    )

    assert any(action.kind == ActionKind.TAP and "About phone" in action.label for action in actions)
    assert not any(action.kind == ActionKind.TYPE_TEXT for action in actions)
    assert not any("Enable feature" in action.label for action in actions)

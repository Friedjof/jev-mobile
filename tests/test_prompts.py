from jev_mobile.prompts import DEFAULT_TASK_POLICY, decision_policy


def test_custom_project_prompt_augments_but_never_replaces_safety_policy() -> None:
    combined = decision_policy("Prefer German UI labels.")
    assert DEFAULT_TASK_POLICY in combined
    assert "Prefer German UI labels." in combined
    assert "Opening an app is not completion" in combined

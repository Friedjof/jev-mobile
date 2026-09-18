from __future__ import annotations

import os

import pytest

from jev_mobile.config import Settings, read_secret
from jev_mobile.runtime.readiness import RuntimeReadinessError, validate_adb_runtime


def test_secret_file_takes_precedence_and_strips_whitespace(tmp_path, monkeypatch) -> None:
    secret = tmp_path / "typesafe.secret"
    secret.write_text("  file-value\n", encoding="utf-8")
    monkeypatch.setenv("TYPESAFE_API_KEY", "legacy-environment-value")
    monkeypatch.setenv("TYPESAFE_API_KEY_FILE", str(secret))

    value, error = read_secret("TYPESAFE_API_KEY")

    assert value == "file-value"
    assert error is None


def test_missing_secret_file_fails_closed_without_secret_content(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "not-there"
    monkeypatch.setenv("TYPESAFE_API_KEY", "must-not-be-returned")
    monkeypatch.setenv("TYPESAFE_API_KEY_FILE", str(missing))

    value, error = read_secret("TYPESAFE_API_KEY")

    assert value is None
    assert error is not None and "PROVIDER_SECRET_UNREADABLE" in error
    assert "must-not-be-returned" not in error


def test_empty_secret_file_is_invalid(tmp_path, monkeypatch) -> None:
    secret = tmp_path / "empty.secret"
    secret.write_text(" \n", encoding="utf-8")
    monkeypatch.setenv("TYPESAFE_API_KEY_FILE", str(secret))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    assert read_secret("TYPESAFE_API_KEY") == (
        None,
        f"PROVIDER_SECRET_EMPTY: TYPESAFE_API_KEY_FILE={secret} contains no secret",
    )


def test_settings_retains_categorized_secret_file_error(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY_FILE", str(tmp_path / "missing"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    settings = Settings.from_env()

    assert settings.typesafe_api_key is None
    assert len(settings.configuration_errors) == 1
    assert settings.configuration_errors[0].startswith("PROVIDER_SECRET_UNREADABLE:")


def test_explicit_adb_paths_are_validated_independently_of_home(tmp_path, monkeypatch) -> None:
    adb_state = tmp_path / "adb-state"
    adb_state.mkdir()
    key = adb_state / "adbkey"
    key.write_text("private-key-placeholder", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "read-only-home"))
    monkeypatch.setenv("ANDROID_USER_HOME", str(adb_state))
    monkeypatch.setenv("ADB_VENDOR_KEYS", str(key))

    details = validate_adb_runtime()

    assert details["android_user_home"] == str(adb_state)
    assert details["adb_vendor_key_paths"] == [str(key)]


def test_missing_adb_key_has_stable_readiness_category(tmp_path, monkeypatch) -> None:
    adb_state = tmp_path / "adb-state"
    adb_state.mkdir()
    monkeypatch.setenv("ANDROID_USER_HOME", str(adb_state))
    monkeypatch.setenv("ADB_VENDOR_KEYS", str(adb_state / "missing-adbkey"))

    with pytest.raises(RuntimeReadinessError) as raised:
        validate_adb_runtime()

    assert raised.value.category == "ADB_KEY_UNAVAILABLE"
    assert os.getenv("HOME") not in str(raised.value)

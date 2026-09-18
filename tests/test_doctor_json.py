from __future__ import annotations

import json

from typer.testing import CliRunner

from jev_mobile.cli import app


def test_doctor_json_is_machine_readable_without_exposing_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JEV_MOBILE_DB", str(tmp_path / "tasks.sqlite3"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "doctor-secret-value")
    monkeypatch.delenv("TYPESAFE_API_KEY_FILE", raising=False)
    monkeypatch.setenv("MOBILE_DEVICE_SERIAL", "")
    monkeypatch.delenv("ADB_VENDOR_KEYS", raising=False)

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["device_serial"] is None
    assert {check["name"] for check in payload["checks"]} >= {
        "TaskStore", "ADB key", "Jev provider", "ADB transport", "ADB device",
    }
    assert "doctor-secret-value" not in result.stdout

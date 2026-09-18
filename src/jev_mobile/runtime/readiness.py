"""Fail-closed runtime checks performed before a worker may claim work."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class RuntimeReadinessError(RuntimeError):
    """A categorized local configuration error safe to persist in telemetry."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def _mode(path: Path) -> str:
    return stat.filemode(path.stat().st_mode)


def validate_adb_runtime() -> dict[str, object]:
    """Validate explicitly configured ADB state/key locations.

    Native installations that do not opt into explicit paths retain ADB's
    normal defaults.  Container deployments set both variables and therefore
    receive deterministic startup validation independent of ``HOME``.
    """
    details: dict[str, object] = {}
    user_home_value = os.getenv("ANDROID_USER_HOME")
    if user_home_value:
        user_home = Path(user_home_value)
        if not user_home.is_dir():
            raise RuntimeReadinessError("ADB_STATE_UNAVAILABLE", f"ANDROID_USER_HOME is not a directory: {user_home}")
        if not os.access(user_home, os.R_OK | os.W_OK | os.X_OK):
            raise RuntimeReadinessError(
                "ADB_STATE_PERMISSION_DENIED",
                f"ANDROID_USER_HOME is not readable/writable by uid={os.geteuid()}: {user_home} mode={_mode(user_home)}",
            )
        user_home_stat = user_home.stat()
        details["android_user_home"] = str(user_home)
        details["android_user_home_owner"] = {
            "uid": user_home_stat.st_uid,
            "gid": user_home_stat.st_gid,
            "mode": _mode(user_home),
        }

    vendor_keys_value = os.getenv("ADB_VENDOR_KEYS")
    if vendor_keys_value:
        key_paths = [Path(item) for item in vendor_keys_value.split(os.pathsep) if item]
        if not key_paths:
            raise RuntimeReadinessError("ADB_KEY_UNAVAILABLE", "ADB_VENDOR_KEYS contains no paths")
        for path in key_paths:
            candidates = [path / "adbkey"] if path.is_dir() else [path]
            key = candidates[0]
            if not key.is_file():
                raise RuntimeReadinessError("ADB_KEY_UNAVAILABLE", f"ADB private key does not exist: {key}")
            if not os.access(key, os.R_OK):
                raise RuntimeReadinessError(
                    "ADB_KEY_PERMISSION_DENIED",
                    f"ADB private key is not readable by uid={os.geteuid()}: {key} mode={_mode(key)}",
                )
        details["adb_vendor_key_paths"] = [str(path) for path in key_paths]
    return details

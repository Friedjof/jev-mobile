"""Secret-free structured operational events written to container logs."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import re


_LOGGER = logging.getLogger("jev_mobile.operations")
_SENSITIVE_KEY = re.compile(r"(authorization|api[_-]?key|token|secret|password|credential)", re.I)


def redact_sensitive(value: object, key: str = "") -> object:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact_sensitive(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s,&]+)", r"\1=[REDACTED]", value)
    return value


def operational_event(event: str, **fields: object) -> None:
    """Emit one parseable event without task instructions or UI payloads."""
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
        **redact_sensitive(fields),
    }
    _LOGGER.info(json.dumps(record, separators=(",", ":"), sort_keys=True, default=str))

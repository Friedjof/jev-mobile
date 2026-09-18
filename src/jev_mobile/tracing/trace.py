"""Small JSONL trace writer suitable for later benchmark reduction."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..operations import redact_sensitive


class TraceWriter:
    def __init__(self, directory: Path, task_id: str | None = None) -> None:
        self.task_id = task_id or uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{self.task_id}.jsonl"

    def write(self, **event: object) -> None:
        record = redact_sensitive({"task_id": self.task_id, **event})
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def prune(directory: Path, older_than: datetime) -> int:
        """Delete completed trace files older than the operator retention window."""
        if not directory.exists():
            return 0
        cutoff = older_than.timestamp()
        removed = 0
        for path in directory.glob("*.jsonl"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        return removed

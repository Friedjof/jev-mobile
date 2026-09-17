"""Small JSONL trace writer suitable for later benchmark reduction."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4


class TraceWriter:
    def __init__(self, directory: Path, task_id: str | None = None) -> None:
        self.task_id = task_id or uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{self.task_id}.jsonl"

    def write(self, **event: object) -> None:
        record = {"task_id": self.task_id, **event}
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

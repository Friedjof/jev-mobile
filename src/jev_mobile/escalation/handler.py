"""Default local escalation handler: checkpoint and pause safely."""

from __future__ import annotations

import json
from pathlib import Path
from .models import EscalationResult


class LocalEscalationHandler:
    def __init__(self, directory: Path) -> None: self.directory = directory

    def pause(self, result: EscalationResult) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{result.task_id}.checkpoint.json"
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return path

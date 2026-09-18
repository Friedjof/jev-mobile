"""Semantic fingerprints deliberately exclude volatile rendering details."""

from __future__ import annotations

import hashlib
import json
from .models import SemanticState


def semantic_fingerprint(state: SemanticState) -> str:
    canonical = {
        "app": state.app, "screen": state.screen_hint, "loading": state.loading,
        "elements": [(item.role, item.label, item.resource_id, item.package, item.clickable, item.editable,
                      item.enabled, item.selected, item.visible, item.scrollable,
                      item.focused, item.checkable, item.checked, item.available_actions,
                      item.bounds.bucket() if item.bounds else None, item.depth)
                     for item in state.elements],
        "scroll_contexts": [context.model_dump(mode="json") for context in state.scroll_contexts],
    }
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()[:20]

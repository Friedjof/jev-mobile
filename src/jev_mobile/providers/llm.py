"""OpenAI-compatible structured decision provider."""

from __future__ import annotations

import httpx
import json
from ..actions.models import CandidateAction, Decision
from ..state.models import SemanticState
from .base import ProviderUnavailable


class LLMProvider:
    name = "llm"
    def __init__(self, base_url: str | None, api_key: str | None, model: str | None) -> None:
        if not all((base_url, api_key, model)): raise ProviderUnavailable("LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL are required")
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model

    async def decide(self, goal: str, state: SemanticState, actions: list[CandidateAction]) -> Decision:
        payload = {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [
            {"role": "system", "content": "Select exactly one supplied action. Return JSON: {action_id, confidence}. Never invent actions."},
            {"role": "user", "content": json.dumps({"goal": goal, "state": state.model_dump(mode="json"), "actions": [a.model_dump(mode="json") for a in actions]})},
        ]}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
            response.raise_for_status()
        return Decision.model_validate_json(response.json()["choices"][0]["message"]["content"])

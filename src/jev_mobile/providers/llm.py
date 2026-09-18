"""OpenAI-compatible structured decision provider."""

from __future__ import annotations

import httpx
import json
from ..actions.models import CandidateAction, Decision
from ..state.models import SemanticState
from ..prompts import decision_policy
from .base import ProviderUnavailable


class LLMProvider:
    name = "llm"
    def __init__(self, base_url: str | None, api_key: str | None, model: str | None,
                 system_prompt: str | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key, self.model = api_key, model
        self.system_prompt = system_prompt

    def readiness_error(self) -> str | None:
        if all((self.base_url, self.api_key, self.model)):
            return None
        return "PROVIDER_UNAVAILABLE: LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL are required"

    async def decide(self, goal: str, state: SemanticState, actions: list[CandidateAction]) -> Decision:
        readiness_error = self.readiness_error()
        if readiness_error:
            raise ProviderUnavailable(readiness_error)
        payload = {"model": self.model, "response_format": {"type": "json_object"}, "messages": [
            {"role": "system", "content": decision_policy(self.system_prompt) + "\n\nReturn JSON: {action_id, confidence}."},
            {"role": "user", "content": json.dumps({"goal": goal, "state": state.model_dump(mode="json"), "actions": [a.model_dump(mode="json") for a in actions]})},
        ]}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
            response.raise_for_status()
        return Decision.model_validate_json(response.json()["choices"][0]["message"]["content"])

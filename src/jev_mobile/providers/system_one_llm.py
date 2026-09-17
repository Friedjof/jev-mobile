"""LLM-backed System-One provider using TypeSafe's official adapter."""

from __future__ import annotations

import time

from system_one_adapter import AsyncSystemOneAdapterClient, Choice
from system_one_adapter.providers.openai import AsyncOpenAIProvider

from ..actions.models import CandidateAction, Decision
from ..prompts import decision_policy
from ..state.models import SemanticState
from .base import ProviderUnavailable
from .jev import build_jev_state, probability_margin


class SystemOneLLMProvider:
    """OpenAI-compatible fallback with exactly the same Choice contract as Jev."""

    name = "system-one-llm"

    def __init__(self, base_url: str | None, api_key: str | None, model: str | None,
                 system_prompt: str | None = None) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._system_prompt = system_prompt

    async def decide(self, goal: str, state: SemanticState, actions: list[CandidateAction]) -> Decision:
        if not self._api_key or not self._model:
            raise ProviderUnavailable("LLM_API_KEY and LLM_MODEL are required for system-one-llm")
        compact_state = build_jev_state(goal, state)
        criteria = {action.id: action.label for action in actions}
        provider = AsyncOpenAIProvider(self._model, base_url=self._base_url, api_key=self._api_key)
        started = time.perf_counter()
        try:
            async with AsyncSystemOneAdapterClient(
                structured_outputs=True,
                llm_answer_mode="probabilities",
                normalize_probabilities=True,
            ) as client:
                response = await client.system_one(
                    state=compact_state,
                    questions={
                        "next_action": Choice(
                            instructions=(decision_policy(self._system_prompt) + "\n\n"
                                "This is one step in a multi-step control loop. The concrete task is in "
                                "state.goal. Choose exactly one listed candidate action for the current state."),
                            criteria=criteria,
                        )
                    },
                    model=provider,
                )
        except Exception as error:
            raise ProviderUnavailable(f"System-One LLM request failed ({type(error).__name__})") from error

        answer = response.answers["next_action"]
        probabilities = {key: float(value) for key, value in answer.probabilities.items()}
        return Decision(
            action_id=answer.choice,
            confidence=probabilities.get(answer.choice, float(answer.confidence)),
            probabilities=probabilities,
            reasoning_metadata={
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "probability_margin": probability_margin(probabilities),
                "state_elements": len(compact_state["screen"]["elements"]),
                "action_count": len(actions),
            },
        )

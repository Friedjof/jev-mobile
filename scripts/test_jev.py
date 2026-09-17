"""Minimal, non-device smoke test for the official TypeSafe System-One API."""

from __future__ import annotations

import asyncio
import json
import os
import time

from typesafe_sdk import AsyncTypeSafeClient, Choice
from dotenv import load_dotenv

from jev_mobile.providers.jev import usage_metadata


async def main() -> int:
    load_dotenv()
    api_key = os.getenv("TYPESAFE_API_KEY")
    if not api_key:
        print("TYPESAFE_API_KEY is not configured; no request was sent.")
        return 2

    state = {
        "goal": "Open Network & internet settings.",
        "app": "Settings",
        "elements": [
            {"id": "e1", "text": "Network & internet", "clickable": True},
            {"id": "e2", "text": "Connected devices", "clickable": True},
            {"id": "e3", "text": "Apps", "clickable": True},
            {"id": "e4", "text": "Notifications", "clickable": True},
        ],
    }
    criteria = {
        "A1": "Tap Network & internet",
        "A2": "Tap Connected devices",
        "A3": "Tap Apps",
        "A4": "Tap Notifications",
        "A5": "Go back",
        "A6": "Escalate because the correct action is uncertain",
    }
    started = time.perf_counter()
    async with AsyncTypeSafeClient(api_key=api_key) as client:
        response = await client.system_one(
            state=state,
            questions={
                "next_action": Choice(
                    instructions="Choose the best next action to make progress toward the goal.",
                    criteria=criteria,
                )
            },
        )
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    answer = response.answers["next_action"]
    metadata = {"model": response.model}
    usage = usage_metadata(response.usage)
    if usage is not None:
        metadata["usage"] = usage
    print(f"selected_action: {answer.choice}")
    print("probabilities:", json.dumps(answer.probabilities, sort_keys=True))
    print(f"request_latency_ms: {latency_ms}")
    print("response_metadata:", json.dumps(metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

# jev-mobile

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-proof--of--concept-8A2BE2)](#project-status)

Fast, structured Android control loops with [TypeSafe Jev](https://typesafe.ai/) as a pluggable decision provider.

`jev-mobile` turns a high-level mobile goal into a bounded **observe → stabilize → decide → act** loop. Jev receives a compact semantic UI state and a small list of already-valid actions. It never generates coordinates, MCP calls, or arbitrary code.

> **Proof of concept.** This project has been exercised against Android Settings on a real device. It is not yet a general-purpose autonomous mobile agent and must not be used for payments, account changes, destructive actions, or other sensitive workflows.

![Terminal demo of a Jev-controlled Android Settings navigation task](demo_cli.gif)

_Add `demo_cli.gif` at the repository root to show the live CLI recording._

## Why this exists

Traditional mobile-agent stacks repeatedly involve a large model in every UI step:

```text
LLM → mobile tool → LLM → mobile tool → LLM
```

This PoC keeps the high-frequency loop fast and structured instead:

```text
High-level goal
       │
       ▼
┌──────────────────────────────────────────────────┐
│ jev-mobile                                       │
│  observe → wait for stable UI → normalize         │
│  → build valid actions → Jev → execute            │
└──────────────────────────────────────────────────┘
       │
       ├── Mobile MCP / Android device
       └── Escalation for uncertainty or risk
```

For example, Jev sees only this decision problem:

```text
Goal: Open Display settings

Valid actions:
  A1  Tap "Display"
  A2  Go back
  A3  Wait for UI
  A4  Escalate

Jev → A1, confidence=0.99
```

The controller maps `A1` to the actual Mobile MCP call. Invalid actions and invented coordinates are impossible by construction.

## Demo

The current real-device demo is read-only Android Settings navigation:

```bash
uv run jev-mobile run \
  --goal "Open Display settings, then return to the Settings main screen" \
  --provider jev \
  --serial YOUR_ANDROID_SERIAL \
  --start-app com.android.settings
```

The live CLI shows the current stable UI, candidate actions, Jev confidence and probability margin, the executed action, and final completion or escalation.

## Project status

### Working now

- Android control through the external [Mobile Next Mobile MCP](https://github.com/mobile-next/mobile-mcp) backend
- Semantic UI normalization, fingerprints, loading detection, and adaptive stability polling
- Structured TypeSafe System-One choices through the official asynchronous Python SDK
- Pluggable `jev`, `system-one-llm`, `heuristic`, and `mock` decision providers
- Confidence and top-two probability-margin safety gates
- Bounded loops, repeated-state detection, escalation checkpoints, and JSONL traces
- Human-friendly `devices`, `inspect`, `decide`, and `run` CLI output

### Deliberately not solved yet

- General app understanding beyond the Settings-focused PoC
- Vision-first or screenshot-driven navigation
- Reliable text-search handling across all Android variants
- Human approval UX, task resume CLI, benchmarking dashboard, and OpenClaw integration
- Docker packaging and a hosted service

## Installation

Requirements:

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- An Android device with USB debugging enabled
- Node.js / `npx` for the default Mobile MCP transport
- A `TYPESAFE_API_KEY` for the Jev provider

```bash
git clone https://github.com/Friedjof/jev-mobile.git
cd jev-mobile
uv sync --all-groups
cp .env.example .env
```

Set only the credentials you use in `.env`. Never commit this file.

```dotenv
TYPESAFE_API_KEY=...
```

The default Mobile MCP command is started through `npx`. To install it globally instead:

```bash
npm install -g @mobilenext/mobile-mcp@1.0.4
```

## CLI

```bash
# Discover Android devices through Mobile MCP.
uv run jev-mobile devices

# Read the current UI and show safe candidate actions. No device action.
uv run jev-mobile inspect --serial YOUR_ANDROID_SERIAL

# Ask Jev for one action. This is always a dry run.
uv run jev-mobile decide \
  --goal "Open Display settings" \
  --provider jev \
  --serial YOUR_ANDROID_SERIAL

# Execute a bounded navigation loop.
uv run jev-mobile run \
  --goal "Open Display settings, then return to the Settings main screen" \
  --provider jev \
  --serial YOUR_ANDROID_SERIAL \
  --start-app com.android.settings
```

`run` executes only read-only navigation candidates in the current PoC. The default confidence threshold is `0.80`; low-confidence or low-margin decisions create an escalation checkpoint instead of acting.

## Providers

All providers receive the same goal, compact semantic state, and fixed candidate actions.

| Provider | Purpose |
| --- | --- |
| `jev` | Native async TypeSafe System-One decision provider. |
| `system-one-llm` | OpenAI-compatible fallback using the official System One adapter. |
| `heuristic` | Deterministic Settings demo navigation. |
| `mock` | Deterministic tests. |

The Jev provider uses the official `AsyncTypeSafeClient`. A Choice response is read from `response.answers["next_action"]`, including its `choice`, `confidence`, and `probabilities`.

## Safety model

- Accessibility/UI-tree data is preferred over screenshots.
- Jev sees only prevalidated candidates; it cannot issue raw device commands.
- Intermediate/loading states are held back until the UI is semantically stable.
- `ESCALATE` is always available.
- Low confidence, a small top-two probability margin, repeated states, loops, timeouts, and step limits stop automation.
- `EXTERNAL_EFFECT` and `SENSITIVE` action classes are not autonomously executed.
- API keys and authorization headers are never written to traces.

## Development

```bash
uv run pytest
uv run python scripts/test_jev.py
```

The smoke test requires `TYPESAFE_API_KEY` and intentionally logs only selected action, probabilities, latency, model name, and token accounting.

## Future MCP integration

The standalone control loop comes first. Once it is reliable, `jev-mobile` can expose a small MCP server for higher-level orchestrators such as OpenClaw:

```text
mobile_agent_run(goal)
mobile_agent_status(task_id)
mobile_agent_resume(task_id, instruction)
mobile_agent_stop(task_id)
mobile_agent_inspect()
```

That makes a useful split possible: an orchestrator starts a goal, the local Jev loop handles fast structured navigation, and a stronger model is called only for ambiguity, vision, language generation, or sensitive decisions.

## License

MIT. See [LICENSE](LICENSE).

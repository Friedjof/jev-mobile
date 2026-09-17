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
│  observe → wait for stable UI → normalize        │
│  → build valid actions → Jev → execute           │
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
- Ranked action pages that prevent crowded screens from overwhelming Jev
- Conservative popup detection with safe dismissal or screenshot-backed escalation
- Human-friendly `devices`, `inspect`, `decide`, and `run` CLI output
- An in-repository Android Accessibility Bridge PoC with real node actions and
  USB-only ADB forwarding (manual service enablement required)
- A stdio MCP server exposing bounded `mobile_agent_inspect`,
  `mobile_agent_decide`, and `mobile_agent_run` operations

### Deliberately not solved yet

- General app understanding beyond the Settings-focused PoC
- Vision-first or screenshot-driven navigation
- Reliable text-search handling across all Android variants
- Human approval UX, task resume CLI, benchmarking dashboard, and OpenClaw integration
- A hosted service

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

### Accessibility Bridge PoC

Mobile MCP remains the default backend. For richer Android semantics, this
repository now also contains a tiny first-party companion app under
[`android/jev-mobile-bridge`](android/jev-mobile-bridge). It runs an Android
`AccessibilityService` and gives the controller real node capabilities such as
`CLICK`, `SET_TEXT`, check state, hints, resource IDs, window context, and the
node hierarchy. This removes the current Mobile MCP adapter's need to infer
whether a coordinate-labelled element is actionable.

The bridge binds only to loopback on the phone. The Python process creates a
temporary USB tunnel with `adb forward`; it does not expose a phone port to the
LAN or use a cloud relay. Android still requires that you explicitly enable the
service on the device:

```bash
uv run jev-mobile inspect --backend bridge --serial YOUR_ANDROID_SERIAL

# A dry run: asks Jev, but does not touch the device.
uv run jev-mobile decide \
  --backend bridge \
  --goal "Open Network & internet" \
  --provider jev \
  --serial YOUR_ANDROID_SERIAL
```

See the bridge's [development and security notes](android/jev-mobile-bridge/README.md).

> On Android 11, Mobile Next's DeviceServer starts `UiAutomation`, which
> suppresses third-party Accessibility Services unless it opts out with
> `FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES`. Use either Mobile MCP's UI-tree
> backend or the bridge at a time—not both—until that upstream flag is enabled.

`run` executes read-only navigation and explicitly classified reversible candidates in the current PoC. External-effect and sensitive actions always create an escalation checkpoint. The default confidence threshold is `0.80`; low-confidence or low-margin decisions also create an escalation checkpoint instead of acting.

See [current PoC limitations and next steps](docs/current-limitations.md) for the intentionally unfinished areas, including checklist authoring, visual escalation, free-form text generation, and durable resume.

Crowded screens are presented in ranked pages (10 device actions by default). Choosing `More actions` advances locally to the next page without touching the phone. A clearly safe popup dismissal may be executed. Every detected modal also offers `Dismiss popup with Back`; permission prompts, confirmations, and unknown dialogs never expose an accept/continue action and retain escalation as a safe alternative.

### Optional LLM planning and recovery

Jev remains the fast, per-step decision provider. If it is uncertain, an optional OpenAI-compatible LLM can create one short, text-only recovery plan, after which the Jev loop continues using the plan's current checkpoint as context:

```bash
uv run jev-mobile run \
  --goal "Open Network & internet settings and then go back" \
  --provider jev \
  --plan-first \
  --plan-on-escalation
```

Set `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` in `.env`. `--plan-first` asks the LLM for bounded steps and visible completion criteria before the first action. When the controller later believes it is done, the same LLM reviews the current semantic UI against those criteria. The planner receives no device tools, cannot create actions, and cannot bypass candidate-action or risk checks. It is attempted once by default (`MAX_PLAN_RECOVERIES=1`). If it cannot recover safely, the task still creates the normal resumable escalation checkpoint.

## Providers

All providers receive the same goal, compact semantic state, and fixed candidate actions.

### Operating policy and task

Every decision receives a built-in operating policy (safe candidate-only control, verified completion, and explicit-text handling) separately from the concrete task in `state.goal`. Add project-specific rules without replacing those safeguards:

```env
MOBILE_AGENT_SYSTEM_PROMPT=Use German UI labels when available. Prefer local draft actions.
```

The CLI goal remains the actual task, for example `--goal "Create a new note: Eggs, bread"`.

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
- Crowded screens are ranked into bounded action pages; `More actions` is a local controller step, not a device command.
- Only explicit harmless popup dismissals are autonomous; permissions, confirmations, and unknown dialogs escalate with a screenshot checkpoint.
- Low confidence, a small top-two probability margin, repeated states, loops, timeouts, and step limits stop automation.
- `EXTERNAL_EFFECT` and `SENSITIVE` action classes are not autonomously executed.
- API keys and authorization headers are never written to traces.
- Jev is the default exploration engine: wide screens are searched through
  semantic groups and bounded group pages before a System-2 recovery is asked.
- Every Jev request receives compact controller-owned task progress, navigation
  state, failed/successful semantic paths, and up to 12 recent transitions.

## Development

```bash
uv run ruff check .
uv run pytest -q
uv run python scripts/test_jev.py
```

The smoke test requires `TYPESAFE_API_KEY` and intentionally logs only selected action, probabilities, latency, model name, and token accounting.

## Docker MCP server

The repository ships a stdio MCP image. It exposes the same bounded controller
operations as the CLI; it does not expose arbitrary ADB commands or coordinate
taps. A container still needs deliberate access to an ADB server and the
Android bridge when it is used with a real phone.

```bash
docker build -t jev-mobile-mcp .
docker run -i --rm \
  -e TYPESAFE_API_KEY \
  -e MOBILE_DEVICE_SERIAL \
  jev-mobile-mcp
```

The process communicates over standard input/output, as required by MCP. For
the bridge backend, provide ADB connectivity explicitly; do not bake USB
permissions, device serials, or credentials into the image.

## CI and releases

- Android bridge changes run Android lint and produce a debug APK artifact.
- Python/controller changes run Ruff, tests, an MCP-tool smoke test, and build
  the container image. Pushes publish branch and SHA tags to GHCR.
- Pushing a tag shaped as `vX.Y.Z` runs both test suites, publishes
  `ghcr.io/<owner>/jev-mobile-mcp` with version and `latest` tags, and creates
  a GitHub Release containing the installable PoC debug APK.

The release APK is deliberately debug-signed while this project remains a PoC.
Introduce a protected Android signing-key workflow before distributing a
production build.

## MCP integration

`jev-mobile-mcp` is a small stdio MCP server for higher-level orchestrators
such as OpenClaw. It currently exposes inspection, dry-run decision, and the
bounded controller run:

```text
mobile_agent_run(goal)
mobile_agent_inspect()
mobile_agent_decide(goal)
mobile_agent_run(goal)
```

Status, resume, and stop tools will follow with durable task storage. The
existing split remains intentional: an orchestrator starts a goal, the local
Jev loop handles fast structured navigation, and a stronger model is called
only for ambiguity, vision, language generation, or sensitive decisions.

## License

MIT. See [LICENSE](LICENSE).

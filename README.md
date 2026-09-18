# jev-mobile

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-durable--core--v1-16803C)](#project-status)

An autonomous, durable Android sub-agent with [TypeSafe Jev](https://typesafe.ai/) as its System-One decision provider.

`jev-mobile` accepts a high-level task, persists it in SQLite, and lets one durable worker execute **observe → normalize → decide → mutate → verify** against a USB-connected Android device. Jev receives concise semantics and technically valid actions; it never generates coordinates, MCP calls, or arbitrary code.

> **Safety boundary.** Durable Core V1 has been hardware-validated for normal notes/checklists, text input, crash recovery, persistence verification and foreign-editor protection. It deliberately stops rather than clearing unrelated application state. Payments, account changes and other sensitive operations remain out of scope without explicit policy/approval work.

## Why this exists

Traditional mobile-agent stacks repeatedly involve a large model in every UI step:

```text
LLM → mobile tool → LLM → mobile tool → LLM
```

The high-frequency loop remains local and structured:

```text
High-level goal
       │
       ▼
┌────────────────────────────────────────────────────┐
│ jev-mobile                                         │
│ durable worker: observe → normalize → requirements │
│ → valid actions → Jev → journal → verify           │
└────────────────────────────────────────────────────┘
       │
       ├── Portal ADB / USB Android runtime
       └── Escalation for uncertainty or risk
```

For example, Jev sees only this decision problem:

```text
Task: Create a note titled Shopping

Valid actions:
  A1  Create entity
  A2  Set title in title field
  A3  Verify persisted result

Jev → A1
```

The worker maps `A1` to the backend through the DeviceAdapter. Invalid actions, stale snapshot references and invented coordinates are rejected by construction.

## Demo

The primary real-device path is durable task delegation:

```bash
uv run jev-mobile worker --backend portal-adb --serial YOUR_ANDROID_SERIAL
uv run jev-mobile task start "Create a Google Keep checklist titled Shopping with Eier, Brot and Milch"
```

The task command returns a task id immediately. The worker owns subsequent
Android actions and reports progress, evidence and final verification through
`task get`, `task events` and MCP.

## Project status

### Working now

- Local USB-only `PortalAdbDeviceAdapter`; no Mobilerun cloud/account/API
- Semantic UI normalization, snapshot-bound references and technical action catalogs
- TypeSafe Jev decisioning with requirement-driven subgoals
- Mutation journaling before execution plus observation-based reconciliation
- Durable SQLite tasks, leases, checkpoints, cancellation and worker recovery
- Independent persistence verification and foreign-entity write protection
- Scoped approval for risky lifecycle recovery and safe terminal boundaries
- Structured durable clarification questions with schema-validated `answer_task`
- Evidence-backed read-only information tasks for accessible label/value UI
- Public stdio MCP task delegation and a deterministic Android acceptance fixture

### Deliberately not solved yet

- Broad app coverage beyond the validated Keep/fixture scenarios
- Vision-first navigation and richer visual reasoning
- Advanced recovery/exploration and benchmark dashboards
- Broad read-task vocabulary and extraction across arbitrary custom widgets
- A hosted/cloud service

## Installation

Requirements:

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- An Android device with USB debugging enabled
- A TypeSafe API key for the Jev provider

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

For services, prefer `TYPESAFE_API_KEY_FILE=/run/secrets/...`. A configured
secret file takes precedence over the legacy direct variable, is trimmed when
read, and fails closed when it is missing, empty, or unreadable.

## CLI

```bash
# Run one worker for one physical USB device.
uv run jev-mobile worker --backend portal-adb --serial YOUR_ANDROID_SERIAL

# Queue and inspect a durable task.
uv run jev-mobile task start "Create a Google Keep note titled Test with body Hello World"
uv run jev-mobile task start --idempotency-key parent-request-123 "Create a Google Keep note titled Test"
uv run jev-mobile task get TASK_ID
uv run jev-mobile task events TASK_ID
uv run jev-mobile task list --status queued --status failed --limit 50
uv run jev-mobile task retry TASK_ID  # rejected if any Android mutation began
uv run jev-mobile task cancel TASK_ID

# Worker health is independent from device availability.
uv run jev-mobile health
```

`start_task` accepts the same optional `idempotency_key`; repeating an identical
request returns the original task ID, while reusing the key for different input
is rejected. Queue listing and safety-checked retry are operator CLI commands,
so the public MCP contract remains exactly the six high-level delegation tools.

### Operations and readiness

The worker emits secret-redacted JSON events to stdout/stderr for lifecycle,
device availability, task claims/results, recovery and provider latency. Durable
task events remain in SQLite. `jev-mobile health` and the MCP HTTP `/health`
endpoint are liveness checks only: an unplugged phone does not make the process
unhealthy. MCP HTTP `/ready` additionally requires worker telemetry no older
than 45 seconds plus a ready device/backend/provider probe.

Completed task events and JSONL traces are retained for 30 days by default.
Set `JEV_MOBILE_EVENT_RETENTION_DAYS` or `JEV_MOBILE_TRACE_RETENTION_DAYS` to a
different positive day count; `0` disables the corresponding automatic prune.
Public failures use stable categories (`DEVICE_UNAVAILABLE`,
`BACKEND_UNAVAILABLE`, `PROVIDER_UNAVAILABLE`, `UNSUPPORTED_TASK`,
`SAFETY_BLOCKED`, or `AGENT_BUG`) and state whether retry may be appropriate.

### Alternative Android backends

`portal-adb` is the primary local USB backend. The repository also contains a
tiny first-party companion app under
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

For apps that render `ACTION_SET_TEXT` but do not persist it, the companion
also includes an opt-in Unicode input method. Enable and select **Jev Mobile
Input** manually in Android keyboard settings, then set
`TEXT_INPUT_STRATEGY=ime`. It commits text through Android's `InputConnection`
and supports characters such as `ä`, `ö`, `ü`, `ß`, and punctuation. The
controller never changes the device's default keyboard automatically.

> On Android 11, Mobile Next's DeviceServer starts `UiAutomation`, which
> suppresses third-party Accessibility Services unless it opts out with
> `FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES`. Use either Mobile MCP's UI-tree
> backend or the bridge at a time—not both—until that upstream flag is enabled.

The legacy `run` command remains available for controller development. Production tasks use `task start` and the durable worker. External-effect and sensitive actions remain subject to validation and approval policy.

See [current limitations and next steps](docs/current-limitations.md) for intentionally unfinished areas such as visual escalation and broader app support.

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

## CI and releases

- Android bridge and fixture changes run Android lint/build and produce a debug APK artifact.
- Python/controller changes run Ruff, tests, an MCP-tool smoke test, and build
  the container image. Pushes publish branch and SHA tags to GHCR.
- Pushing a tag shaped as `vX.Y.Z` runs both test suites, publishes
  `ghcr.io/<owner>/jev-mobile-mcp` with version and `latest` tags, and creates
  a GitHub Release containing the installable debug APK.

The release APK is deliberately debug-signed while this project remains a PoC.
Introduce a protected Android signing-key workflow before distributing a
production build.

## MCP integration

`jev-mobile-mcp` is a stdio MCP server for higher-level orchestrators such as
OpenClaw. It delegates a durable task to the local worker; it does not expose
Android controls:

```text
start_task(instruction, subtasks?)
get_task(task_id)
get_task_events(task_id, after_seq)
cancel_task(task_id)
answer_task(task_id, question_id, answer)
get_device_status()
```

`subtasks` is an optional ordered list of high-level instructions with optional
stable IDs. Jev Mobile executes exactly one subtask at a time under the same
parent task ID; each must be observably verified before the next begins. The
parent can disconnect, reconnect, inspect bounded progress, answer a question,
or cancel the complete sequence. Parallel subtasks and Android-level commands
are deliberately not part of this contract.

Configure either stdio or Streamable HTTP with the same `JEV_MOBILE_DB` as the
worker. The worker continues independently if an MCP client or server
disconnects or restarts. No public tool performs tap, swipe, type, or raw
accessibility operations.

## Docker deployment

Docker is the primary deployment backend; the native systemd worker remains an
installed, disabled fallback. The worker is the only container with USB/ADB
access. The stdio and Streamable HTTP MCP containers read the shared SQLite
task store without a provider credential, ADB key, USB mount, Docker socket, or
device cgroup permission.

Create a non-secret worker environment file outside the repository from
[`deploy/docker/jev-mobile.env.example`](deploy/docker/jev-mobile.env.example),
and a TypeSafe secret file readable by the container user. Export these
host-specific paths before starting Compose:

```bash
export JEV_MOBILE_ENV_FILE="$HOME/.config/jev-mobile/docker.env"
export JEV_MOBILE_MCP_ENV_FILE="$HOME/.config/jev-mobile/mcp.env"
export JEV_MOBILE_TYPESAFE_API_KEY_FILE="$HOME/.config/jev-mobile/secrets/typesafe_api_key"
export JEV_MOBILE_DATA_DIR="$HOME/.local/share/jev-mobile"
export JEV_MOBILE_ADB_KEYS_DIR="$HOME/.android"
export JEV_MOBILE_UID="$(id -u)"
export JEV_MOBILE_GID="$(id -g)"
export JEV_MOBILE_USB_GID="$(getent group plugdev | cut -d: -f3)"
docker compose -f docker-compose.yml -f deploy/docker/compose.secrets.yml up -d worker
```

The worker receives the provider key as `/run/secrets/typesafe_api_key`; the
MCP roles use their separate, non-secret environment file and never receive
that mount. `docker inspect` therefore shows only the secret-file path, not the
credential. Rotate the key by replacing the host file and recreating only the
worker container.

The worker sets `ANDROID_USER_HOME=/data/adb` and
`ADB_VENDOR_KEYS=/data/adb/adbkey`. Startup readiness validates that the state
directory is readable/writable and that the private key is readable before any
task can be claimed. ADB no longer depends on a writable `/home/jev`.

`JEV_MOBILE_DATA_DIR` must be a local filesystem because SQLite WAL is not
safe on NFS or SMB. The worker healthcheck runs `jev-mobile health`, which
checks its runtime and SQLite only. Device availability is exposed separately
through `get_device_status`; an unplugged phone does not restart the worker.
No service exposes ADB over TCP.

For a release-pinned deployment without a repository checkout, use
[`compose.production.yml`](compose.production.yml). It contains no build
context, names the worker and MCP roles explicitly, applies read-only root
filesystems/capability dropping/no-new-privileges, and publishes no port. The
complete install, Synology USB reconnect, upgrade and rollback procedure is in
[`docs/production-deployment.md`](docs/production-deployment.md).

For an MCP parent that launches stdio servers, use the non-secret MCP role file
and the same data directory, for example:

```bash
docker compose run --rm -T mcp
```

The parent owns this stdio process while the worker continues independently.

For a containerized OpenClaw deployment, use the minimal
[`deploy/openclaw/`](deploy/openclaw/) overlay. It adds only the Jev Mobile MCP
runtime to OpenClaw, mounts the same local `/data/jev-mobile.db`, and lets
OpenClaw spawn `jev-mobile-mcp` as its own stdio child. It deliberately grants
OpenClaw no USB, ADB, Portal credentials or Docker socket. Register precisely
the six tools above; the concrete OpenClaw command is documented in the overlay
README.

For container-to-container deployments, Streamable HTTP is preferred over a
spawned stdio child. Start the dedicated `mcp-http` Compose service; it exposes
only `http://jev-mobile-mcp:8851/mcp` on the internal `openclaw-net` Docker
network (no host port). Register it explicitly as Streamable HTTP, for example:

```text
openclaw mcp add jev-mobile --transport streamable-http \
  --url http://jev-mobile-mcp:8851/mcp \
  --include start_task,get_task,get_task_events,cancel_task,answer_task,get_device_status
```

The service is stateless with respect to MCP sessions: SQLite remains the
durable source of truth. It has no USB, ADB key or Docker socket. The stdio
overlay remains supported for clients that cannot use internal HTTP.

## License

MIT. See [LICENSE](LICENSE).

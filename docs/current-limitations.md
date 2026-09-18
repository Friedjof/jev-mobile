# Current Limitations and Next Steps

Durable Core V1 proves a local, autonomous Android task path, but it is not yet
a general-purpose mobile automation service.

## What currently works

- USB-only Portal/ADB observation and execution through a backend-neutral adapter.
- Snapshot-bound semantic UI states, requirement-driven subgoals, action validation, traces and bounded recovery.
- Durable SQLite tasks, worker leases, mutation journaling, crash reconciliation and cooperative cancellation.
- Unicode-safe text entry, independent persistence verification and a deterministic Android fixture.
- Autonomous Google Keep checklist and text-note creation from the supported safe starting contexts.
- Foreign-entity write protection plus durable, scoped approval for risky lifecycle recovery.

## Known limitations

### App coverage is intentionally narrow

The validated flows cover the fixture app and Google Keep note/checklist
creation. Other apps, unusual widget sets, inaccessible canvases and complex
multi-account flows may still require recovery, approval or a safe failure.

### LLM planning is advisory, not visual control

The LLM planner receives a filtered accessibility/UI state. Escalation screenshots are stored locally, but are not yet sent to a vision model. Canvas UI, image-only controls, and incomplete accessibility trees therefore remain escalation cases.

### Free-form writing needs a separate approval-aware generation step

Explicit text from a task can be entered deterministically. Requests such as “write a friendly reply” currently require escalation; the prototype does not yet generate, preview, approve, and then type free-form text.

### Completion verification remains conservative

The worker verifies persisted fixture and Keep content after navigation away
and reopening. It cannot prove arbitrary external effects such as network
requests, purchases or side effects that an app does not expose in its UI.

### Persisted foreign application state is a safety boundary

The worker deliberately refuses to clear app data, preferences or databases.
If a target app persistently restores unrelated content after safe navigation,
root entry, task reset and approved process restart, the task ends with the
structured `SAFETY_BLOCKED / PERSISTED_FOREIGN_APP_STATE` outcome rather than
risking user data.

### MCP is generic; OpenClaw registration is not done yet

The public stdio MCP surface provides durable task delegation, progress,
cancellation, device status and answers. OpenClaw-specific registration and
parent-agent acceptance testing remain future work.

### Backend constraints

The OpenAI-compatible planner requires a model and endpoint that support JSON-mode chat completions. Some endpoints reject non-default sampling parameters; the prototype deliberately omits `temperature` for compatibility.

## Recommended next milestones

1. Validate Docker deployment and real stdio MCP process boundaries on hardware.
2. Add a vision-capable escalation provider for screenshot-backed inspection.
3. Add preview-and-approval text generation for unknown text fields.
4. Expand the app benchmark suite and recovery/exploration coverage.
5. Register the public MCP contract with OpenClaw or another parent agent.

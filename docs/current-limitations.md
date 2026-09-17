# Current PoC Limitations and Next Steps

This prototype proves the structured Android control path, but it is not yet a general autonomous mobile agent.

## What currently works

- Mobile MCP observation and execution through a backend-neutral adapter.
- Compact semantic UI states, candidate-action paging, UI stabilization, traces, and bounded loops.
- Native TypeSafe Jev decisions over existing actions only.
- Optional LLM task planning before execution, LLM recovery planning, and LLM completion review.
- Settings navigation, launcher-to-Keep-Notes navigation, and deterministic entry of explicitly supplied note text.
- Conservative dialog handling: safe labelled dismissal or Android Back, never permission acceptance.

## Known limitations

### Checklist authoring is not yet workflow-complete

The task planner can describe a checklist workflow, but the candidate builder does not yet have an app-specific, verified Keep checklist workflow. In particular, it does not yet reliably:

- select Keep's checklist format when it is hidden behind an app-specific menu;
- add one item per checkbox row;
- distinguish a draft editor from an autosaved note;
- verify an app-specific save indicator or a persisted note after leaving the editor.

The current deterministic text path is appropriate for a plain note with explicit text. A checklist needs structured item-entry support and app-specific semantic verification before it should be considered complete.

### LLM planning is advisory, not visual control

The LLM planner receives a filtered accessibility/UI state. Escalation screenshots are stored locally, but are not yet sent to a vision model. Canvas UI, image-only controls, and incomplete accessibility trees therefore remain escalation cases.

### Free-form writing needs a separate approval-aware generation step

Explicit text from a task can be entered deterministically. Requests such as “write a friendly reply” currently require escalation; the prototype does not yet generate, preview, approve, and then type free-form text.

### Completion verification remains conservative

The final LLM review evaluates visible semantic UI against plan criteria. It cannot prove an external side effect, a network request, or persistence after an app has hidden the relevant state. Those cases need explicit app-level checks or user approval.

### Recovery and resume are local only

Escalation creates a JSON checkpoint and optional screenshot. A first-class `mobile_agent_resume` command, human approval workflow, and OpenClaw/MCP-server integration are still future work.

### Backend constraints

The OpenAI-compatible planner requires a model and endpoint that support JSON-mode chat completions. Some endpoints reject non-default sampling parameters; the prototype deliberately omits `temperature` for compatibility.

## Recommended next milestones

1. Implement a verified Keep checklist workflow: create checklist, add rows, verify all rows, and verify persistence.
2. Add a vision-capable escalation provider for screenshot-backed inspection.
3. Add preview-and-approval text generation for unknown text fields.
4. Build durable resume, approval, and benchmark commands.
5. Expose the standalone controller as an MCP server for OpenClaw or another orchestrator.

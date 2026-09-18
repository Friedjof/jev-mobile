# Structured task input

Jev Mobile pauses without asking Jev to generate text. The controller owns a
durable `QuestionSpec`; Jev can only select a predefined `REQUEST_INPUT`
candidate.

A question contains a stable ID, type, reason, rendered text, semantic options,
an answer schema and a narrow `AnswerEffect`. Current effects can set a missing
target app or information request, select one observed semantic value, or grant
a task-scoped authorization. They cannot patch an arbitrary `TaskSpec`, carry a
snapshot ref, or request a low-level Android operation.

`answer_task` validates the answer, appends it to durable input history and
transitions `WAITING_FOR_USER` to `QUEUED`. It never opens a device. The worker
later claims the same task and records a fresh observation before making a new
decision. Stale question IDs and duplicate answers are rejected.

Approval questions use exact option IDs (`allow` or `deny`). Text answers are
length bounded, and executable-looking snapshot references such as `s42:e7`
are rejected at the MCP boundary.

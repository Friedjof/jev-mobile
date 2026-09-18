# Durable task contract and verified result

Every queued task persists a versioned `TaskSpec` before Android execution.
The durable contract records:

- semantic intent and target application;
- execution policy (`read_only` or `mutating`);
- requested parent-facing outputs;
- observable completion requirements.

Known task forms are parsed deterministically. An unresolved task starts with a
`pending` contract and receives at most one bounded provider interpretation per
persisted resolution attempt. The resolved contract is stored in SQLite and is
reused unchanged after worker or container restarts. If validation still fails,
the task becomes `unsupported` without entering the device execution loop.

`get_task` exposes a safe contract summary. It does not expose executable UI
references, Accessibility trees, coordinates, or backend handles.

## Optional ordered subtasks

`start_task` accepts an optional bounded `subtasks` list. Every item contains a
high-level instruction and may contain a caller-defined stable ID. Missing IDs
are generated once before the task is queued. The submitted order is durable.

Subtasks remain inside one parent `task_id`, one worker claim at a time and one
cancellation/safety boundary. The worker resolves and verifies the active
subtask contract, checkpoints its requirements and evidence, then requeues the
same parent task for a fresh observation before advancing. A crash resumes the
same active subtask. `WAITING_FOR_USER` also remains attached to that subtask,
while `answer_task` only stores the answer and requeues the parent.

The v1 model is sequential: it has no dependency graph, parallel Android
control or independently leased child tasks. Supplied subtasks refine the
overall goal but cannot weaken policy, grounding, approval or mutation-safety
checks.

## Verified results

A successful run persists semantic evidence for every required criterion.
Evidence may include the package, historical snapshot identifier, semantic
role, label, observed value, and confidence. Historical snapshot identifiers
are explanatory only and are never executable after resume.

The public MCP result independently checks that:

1. every required contract criterion is present;
2. every criterion is satisfied and has evidence;
3. every required output is present with status `observed`;
4. the durable task is in `succeeded` state.

Only then is `verified` returned as `true`. Public observations are restricted
to keys declared by the task contract, and evidence is projected through an
allowlist so raw UI data and executable references cannot escape.

General information extraction and read-only navigation are separate product
capabilities. A structurally valid read contract does not weaken the existing
mutation and grounding safety boundaries.

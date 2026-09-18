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

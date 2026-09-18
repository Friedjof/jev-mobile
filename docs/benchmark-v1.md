# Production reliability benchmark v1

The benchmark runner invokes only the public stdio MCP contract.  Its results
are stored in `.benchmark/benchmark-v1.sqlite3`, separate from the durable
production TaskStore.

Run a selected scenario against the Docker/OpenClaw-facing MCP child:

```bash
python3 scripts/benchmark-mobile.py \
  --command 'docker exec -i -e JEV_MOBILE_DB=/data/jev-mobile.db -e MOBILE_DEVICE_SERIAL=<serial> openclaw jev-mobile-mcp' \
  --scenario fixture_create --runs 10
```

## First hardware sample — 2026-09-18

| Scenario | Runs | Verified success | Safety stop | User input | Failure | Median | p95/sample max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixture create + persisted verification | 10 | 10 | 0 | 0 | 0 | 26.8 s | 30.4 s |
| Keep persisted foreign editor | 3 observed | 0 | 0 | 3 waiting-for-user | 0 | — | — |

The fixture sample had a 100% autonomous verified-success rate and no recovery
events. The Keep sample reached the expected safety question for a foreign
editor; it was deliberately not approved and then cancelled through public MCP.
It is therefore safety/user-input coverage, not a normal creation success.

## Metrics and classification

`succeeded` counts only when the public result is verified. `waiting_for_user`
and `SAFETY_BLOCKED` remain separate from hard failures. The runner records
duration, task status, event-derived recovery/wait counts and final result for
every task ID so later reducers can group failures by semantic understanding,
navigation, grounding, text input, verification, lifecycle recovery, device
runtime, or application accessibility.

The checked-in suite declares Android Settings and a form-heavy application as
disabled coverage slots until explicit, reversible task specifications and a
safe device fixture are selected. They must not be made green with controller
special cases.

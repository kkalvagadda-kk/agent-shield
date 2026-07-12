# 010 — Cost backfill sweep wrote 0 costs (NameError hidden by the test stub)

## Symptom
After shipping cost tracking (Path A), the e2e suite (suite-53) was green and the
sweep's writeback path was "proven" — yet `SELECT count(*) FROM agent_runs WHERE
cost_usd IS NOT NULL` on the live pod returned **0**. Real LLM cost was flowing
into Langfuse (`GENERATION` spans carried `calculatedTotalCost`), but none of it
reached `agent_runs`.

## Chain
`cost_backfill.cost_backfill_loop` (60s) → `_sweep_once()` selects uncosted runs
→ `await asyncio.to_thread(fetch_trace_cost_tokens, trace_id)` →
`tracing.fetch_trace_cost_tokens` → **`os.getenv(...)`**.

## Root cause
`tracing.py` never imported `os` at module scope (its top imports are only
`logging` + `typing.Any`). The new `fetch_trace_cost_tokens` called `os.getenv`
in its first lines — *before* its own `try/except` — so it raised
`NameError: name 'os' is not defined` on **every** invocation. The sweep's
outer `except Exception` in `cost_backfill_loop` swallowed it as a warning, so
the loop kept running and silently wrote nothing.

## Why the test didn't catch it
suite-53 **stubbed** the function under test:
`tracing.fetch_trace_cost_tokens = lambda t: {...}`. Stubbing the exact function
that had the bug bypassed the buggy real body entirely — the writeback path
(select → assign → commit) was genuinely exercised and genuinely correct, but
the *data source* it depended on was dead. Green suite, dead feature.

## How it surfaced
Live verification per the "reason from the running product" rule: after the
suite passed, querying `agent_runs.cost_usd` on the actual pod showed 0, then a
direct `kubectl exec` call to `fetch_trace_cost_tokens(real_trace_id)` threw the
`NameError` in the open.

## Fix
Add a local `import os` inside `fetch_trace_cost_tokens` (registry-api 0.2.153).
After redeploy the sweep persisted real varied cost (e.g. `serper-agent-4
$0.011373`, `obs-unify $0.000222`) within one cycle.

## Secondary bug found the same way
The stubbed `_sweep_once()` in the test wrote the fake cost to **every** uncosted
run in the 24h window (the sweep is cluster-wide by design), not just the seeded
row — 15 real runs got the stub triple `$0.0125 / 1546 / 401`. Fix: scope the
test stub to the seeded `trace_id` (return `None` otherwise) so the sweep only
writes the one row; reset the polluted rows to `NULL` so the real sweep re-costs
them.

## Lessons (generalizable)
- **Don't stub the function you're testing.** A stub of the unit under test
  proves the plumbing around it, never the unit. If you must stub an external
  dependency, stub the *dependency's* boundary (Langfuse HTTP), not your own
  function that wraps it.
- **A green backend suite is not a working feature** (CLAUDE.md Definition of
  Done): confirm the observable end state on the running product — here, a
  non-null column — not just that the code path ran.
- **Broad `except Exception` around a background loop hides source-of-truth
  failures.** The loop must survive, but a `NameError`/`ImportError` (a coding
  bug, not a transient) should at minimum be loud. Consider logging bare
  `NameError`/`ImportError`/`AttributeError` at `error`, not `warning`.
- **A sweep with cluster-wide scope needs a tightly-scoped test stub**, or the
  test mutates production-shaped data far beyond its own fixture.

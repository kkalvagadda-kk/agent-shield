# Trace drawer read Langfuse only — no durable fallback (empty when Langfuse is down)

**Found/Fixed:** 2026-07-28 — branch `lifecycle-journey-suite`. Issue 3 / F-B. Ships in
registry-api (tag bumped at the Issue-2/3 deploy).

## Symptom

The trace panel / drawer showed nothing ("no traces") whenever Langfuse had no spans for a
run — even though the run happened and we hold its data. In this cluster the trigger was
Langfuse's ClickHouse store being 100% full (see `docs/debugging/014-...`), but the same
blank drawer results from any Langfuse outage or ingest lag.

## Root cause (the design flaw)

Trace detail was sourced **exclusively** from Langfuse: `get_trace_detail`
(`routers/observability.py`) and `get_trace_by_id` (`routers/playground.py`) both returned
`obs.get_trace(trace_id)` and nothing else. A first-class UI surface was 100% coupled to a
flaky external — when Langfuse returned `None` / a "not yet ingested" warning, the drawer had
no other source and rendered empty.

## Fix (the class-fix)

Langfuse-first, **durable Postgres `run_steps` fallback**. A shared helper
`_trace_from_run_steps(db, trace_id, claims)` (observability.py) resolves the run behind a
trace (by `langfuse_trace_id`, else the run id), enforces access scope (AgentRun by team,
PlaygroundRun by owner — never leaks another tenant), loads `run_steps` ordered by
`step_number`, and maps each to a `NormalizedSpan` (name, start/end from
`started_at`/`completed_at`, `output`, `error_message`, `level=ERROR` on failure). Both
endpoints call `obs.get_trace` first and fall back to the helper only when Langfuse has no
spans — so a trace renders from data we always own, regardless of Langfuse health.

## Known gap (deferred — F-B(b))

**Reactive runs still persist 0 `run_steps`** (`declarative-runner/main.py` streaming-chat
path explicitly skips them; only durable / workflow-member runs write steps). So the durable
fallback currently yields a trajectory for durable runs; for a *reactive* run it returns None
(the caller keeps whatever Langfuse gave). With Langfuse healthy again (F-C) reactive traces
populate from Langfuse; persisting reactive `run_steps` for full offline resilience is a
separate slice, recorded in the gap ledger — not silently dropped.

## Tests

- Bash e2e `scripts/e2e/suite-90-durable-trace-fallback.sh`: seed a run + `run_steps`, force
  the Langfuse backend to return no spans, assert `GET /playground/traces/{id}` and
  `GET /observability/traces/{id}` return a non-empty trace built from `run_steps`; assert a
  different tenant gets nothing. Runs at the cluster deploy (Phase 1 exit).
- Playwright journey leg 6 asserts spans render in the drawer after a real run.

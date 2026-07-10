# Bug: Langfuse Trace Links Fail ("Trace not found") — ClickHouse OOM

**Date:** 2026-07-09
**Status:** Implemented
**Severity:** High — Langfuse trace links from eval results and playground traces intermittently 404, recurring incident (also seen 2026-07-08)

## Symptom

Clicking a Langfuse trace link from Studio (eval results page or playground trace drawer) shows:

```
Trace not found
The trace is either still being processed or has been deleted.
```

Recurs periodically; same signature as a ClickHouse incident the day before (2026-07-08).

A second, cosmetically similar but distinct symptom was reported alongside it:

```
Error: You do not have access to this trace.
[Sign In]
```

with `langfuse-web` logging `User undefined is not a member of project ...`. This is a **separate** NextAuth/session issue, not fixed by this change — see Known Gaps.

## Root Causes

### RC-1: ClickHouse's own internal telemetry tables consumed the memory budget

**Where:** `agentshield-clickhouse-shard0-0`, container memory limit `charts/agentshield/values.yaml` (`langfuse.clickhouse.resources.limits.memory: 2Gi`)
**Problem:** ClickHouse derives `max_server_memory_usage` as 90% of the container limit → 1.8 GiB. `system.trace_log` (ClickHouse's own query-profiling table, unrelated to Langfuse's `traces` table) had grown to **865 MiB / 58.9M rows**, alongside `system.text_log` (132 MiB), `system.metric_log` (49 MiB), `system.asynchronous_metric_log` (24 MiB) — none had a TTL. Background merges on these tables repeatedly hit `MEMORY_LIMIT_EXCEEDED`:
```
DB::Exception: (total) memory limit exceeded: would use 1.80 GiB ..., maximum: 1.80 GiB. (MEMORY_LIMIT_EXCEEDED)
```
Meanwhile Langfuse's actual data was trivial: `traces` 53 KB/419 rows, `observations` 22 KB/196 rows, `scores` 15 KB/84 rows — confirming the app data was never the memory driver.

### RC-2: Merge failures broke trace ingestion

**Where:** `agentshield-langfuse-worker`
**Problem:** `ClickhouseWriter.flush traces` failed with the same `MEMORY_LIMIT_EXCEEDED`, dropping writes. `langfuse-web` then served `LangfuseNotFoundError: Trace ... not found within authorized project` for traces that were never persisted — the literal cause of "Trace not found" in Studio.

### RC-3 (confirmed unused, not read anywhere in the platform)

Repo-wide grep confirmed nothing in AgentShield or Langfuse reads `system.trace_log`/`text_log`/`metric_log`/`asynchronous_metric_log` — they are ClickHouse's own self-instrumentation, safe to disable outright rather than periodically trim with a TTL.

## Fix

Disable the four unused ClickHouse system log tables at the config level (root-cause fix — stop writing them at all, not TTL-and-delete) via the Bitnami ClickHouse subchart's config-override mechanism (`langfuse.clickhouse.extraOverrides`, rendered into `config.d/01_extra_overrides.xml`):

```yaml
# charts/agentshield/values.yaml — langfuse.clickhouse block
extraOverrides: |
  <yandex>
    <trace_log remove="1"/>
    <text_log remove="1"/>
    <metric_log remove="1"/>
    <asynchronous_metric_log remove="1"/>
  </yandex>
```

One-time cleanup after rollout (config change stops *future* writes but doesn't retroactively free already-written parts):
```sql
DROP TABLE IF EXISTS system.trace_log;
DROP TABLE IF EXISTS system.text_log;
DROP TABLE IF EXISTS system.metric_log;
DROP TABLE IF EXISTS system.asynchronous_metric_log;
```

Langfuse's own trace/observation/score data is untouched — stays on the retention policy already documented in `docs/spec.md` (90 days, not actively enforced but not the memory driver either).

## Files Changed

| # | File | Change |
|---|------|--------|
| 1 | `charts/agentshield/values.yaml` | Added `langfuse.clickhouse.extraOverrides` disabling `trace_log`/`text_log`/`metric_log`/`asynchronous_metric_log` |

No image rebuilds — values-only change to a bundled Bitnami sub-chart (langfuse's bundled `clickhouse` chart, v8.0.5, has no built-in `sampling.enabled` toggle like the newer standalone 9.4.4 chart does, so a raw XML override was required).

## Verification

- `kubectl exec` into the clickhouse pod: confirmed `01_extra_overrides.xml` mounted with the expected content, and zero new `trace_log` rows written after the pod restart (`SELECT count() FROM system.trace_log WHERE event_time > <restart-time>` → 0).
- Total `system.parts` disk usage: **~1.08 GiB → 8.58 MiB** after dropping the four tables.
- `kubectl logs` on `clickhouse`/`langfuse-worker`: zero new `MEMORY_LIMIT_EXCEEDED` in the 5+ minutes following the one-time cleanup (a handful of retries from an orphaned in-flight merge task on the just-dropped `trace_log` occurred in the ~4 minutes right after `DROP TABLE`, then stopped — expected, not a recurrence).
- Fetched the exact trace ID from the reported screenshot (`26b93118-3c35-4356-bfc2-d0183fad0ef7`) via Langfuse's public API (`GET /api/public/traces/{id}`) → **HTTP 200**, confirming it was retrievable once ingestion pressure cleared.

### Unrelated turbulence during rollout

The `scripts/deploy-cpe2e.sh` run that shipped this fix rebuilt/restarted the entire stack at once (registry-api, studio, deploy-controller, plus unrelated in-flight credential-management/RBAC work already uncommitted in the tree). This caused a connection storm against the shared `agentshield-postgresql` pod, which briefly failed its liveness probe (`pg_isready` timing out under load, exit code 0/"Completed" — not OOMKilled) and cascaded into `langfuse-web` crash-looping (its Postgres-backed init/session bootstrap step couldn't get a stable connection). This is **pre-existing infra fragility** (`kubectl describe` showed the same intermittent `Unhealthy` pattern going back 37h+, well before this change), not caused by the ClickHouse config change — the `clickhouse` pod itself had 0 restarts throughout. It self-resolved once Postgres stabilized; deleting the stuck `langfuse-web` pod forced a clean restart rather than waiting out its growing `CrashLoopBackOff` window.

## Known Gaps

- **Not-yet-wired (debt):** `User undefined is not a member of project` / "you do not have access, Sign In" on trace links. Two contributing causes, neither fixed here:
  1. Trace links open Langfuse in a new tab via a plain `target="_blank"` href with no SSO auto-handoff (`studio/src/components/playground/TraceDrawer.tsx:57-58`, `studio/src/pages/EvalResultsPage.tsx:396-399`) — a browser tab with no prior direct Langfuse login has no session. **Deferred (intentional)** — separate UX scope.
  2. The shared `agentshield-postgresql` pod has no explicit `resources` block in most of the deploy history (Bitnami chart default, previously observed OOMKilling at 192Mi with 4 restarts) — breaks NextAuth session/JWT validation intermittently. A `postgresql.primary.resources` block (512Mi/1Gi) has since appeared in `values.yaml` (edited outside this change) but had not yet been deployed as of this fix. **Not-yet-wired (debt)** — needs its own verification pass once deployed, given it's shared by keycloak/agentshield/langfuse/langgraph/appsmith.
- **Deferred (intentional):** ClickHouse's 2Gi memory limit (1.8GiB effective cap) was not raised in this change — disabling the internal log tables should relieve enough pressure for current data volumes, but if `MEMORY_LIMIT_EXCEEDED` recurs, the limit itself needs raising as a follow-up.

## Lessons

1. **"Trace not found" can mean the write side failed, not the read side.** The instinct was to suspect the trace link/routing logic; the actual failure was upstream in ingestion (`ClickhouseWriter.flush` OOM), invisible from the Studio UI.
2. **ClickHouse's own self-instrumentation can outgrow the actual workload.** `system.trace_log` (58.9M rows, 865 MiB) dwarfed Langfuse's real `traces` table (419 rows, 53 KB) by four orders of magnitude — always check `system.parts` grouped by table before assuming the app's own data is the bloat source.
3. **Disable > TTL when nothing reads the data.** A TTL still pays the write-then-delete cost; confirming zero readers (repo-wide grep) justified turning the tables off entirely instead.
4. **A full-stack redeploy against a resource-constrained local cluster can itself cause a cascading incident** independent of the change being shipped — worth `kubectl get pods -A` sweep after any `deploy-cpe2e.sh` run to catch pre-existing fragile services (here, Postgres) getting knocked over by the restart storm.

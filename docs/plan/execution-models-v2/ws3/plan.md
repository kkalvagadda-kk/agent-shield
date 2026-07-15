# WS-3 Implementation Plan — Scheduled, end-to-end

**Slice:** WS-3 of Execution Models v2 (spec §5 WS-3). **Covers WS-3 ONLY.**
**Depends on WS-0 (agent_class + shape-aware dispatch), WS-1 (durable engine), WS-2 (daemon identity/routing).**
**No companion artifacts** — WS-3 is mostly wiring existing infrastructure end-to-end + an operate surface; no
new schema, no new contract.

> **No migration.** Scheduler (HA advisory lock), `input_payload`, and **failure alerting are already
> shipped** — `agent_triggers.alert_email` + `alert_on_failure` exist (`models.py:1644-1646`), contrary to
> gap-analysis TODO-2's "no alerting" note (that TODO is stale; the columns + scheduler hook landed). WS-3
> verifies alerting works end-to-end rather than building it.

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

## 1. Goal

Make scheduled the **thin integration slice it should be**: WS-0/1/2 did the hard parts (shape-aware
dispatch, durable engine, daemon identity + async routing); the scheduler, `input_payload`, HA, and alerting
already exist. WS-3 proves a **scheduled durable daemon** agent (and workflow) runs correctly end-to-end and
completes the operate surface. Concretely, after WS-3:

1. **Scheduled durable daemon runs durable.** A scheduled agent authored `agent_class=daemon` +
   `execution_shape=durable` (WS-0 defaults `daemon` for schedule triggers) fires → `/internal/runs/start`
   → **durable** dispatch (WS-0) → real steps (WS-1) → HITL parks + routes **async to a reviewer** (WS-2).
   Today this chain is only proven for reactive; WS-3 proves it for durable.
2. **Provisioning captures the daemon approver + arming human.** The create/settings flow (WS-0 class
   selector + WS-2 `armed_by`) captures who armed the schedule and which reviewer role approves — verified as
   a real journey, not just columns.
3. **Scheduled operate Overview completed.** The mode-aware Scheduled Overview
   (`AgentDetailPage`/`OverviewScheduled`) surfaces schedule health / next-fire / last-run / alert config
   against production doc §6 — verify what exists, fill gaps.
4. **Alerting verified end-to-end** (not built — it exists): force a scheduled run to fail → assert the
   alert transport is invoked with the trigger's `alert_email`.
5. **Workflows.** A scheduled **durable daemon workflow** already fires (Decision 24 — scheduler UNION-queries
   workflow triggers); WS-3 proves it runs the checkpointing orchestrator under the **workflow service
   identity** (WS-2) and **all four modes** park + resume async (WS-1 D3). Members restricted to composable
   agents (no active own trigger, Decision 24) still holds.

**Out of scope:** the scheduler service itself (built), alerting transport (built), Slack/PagerDuty routing
(future, gap-analysis TODO-2), the daemon identity machinery (WS-2). WS-3 is the **integration + operate
surface** slice.

## 2. Architecture — what WS-3 wires vs what exists

```
EXISTS (verified): scheduler/main.py (APScheduler + PG advisory-lock HA, 2 replicas)
                   → cron fire → POST /internal/runs/start (run_by, input_payload)
                   → alerting.dispatch_failure_alert on status=failed (alert_email/alert_on_failure)
                   → scheduler UNION-queries agent_triggers WHERE workflow_id set (Decision 24)

WS-3 WIRES/PROVES:
  scheduled durable daemon AGENT:    fire → durable dispatch (WS-0) → real steps (WS-1)
                                       → daemon service identity + async reviewer routing (WS-2)
  scheduled durable daemon WORKFLOW: fire → checkpointing orchestrator under workflow service identity (WS-2)
                                       → all 4 modes park+resume async (WS-1 D3)
  Scheduled OVERVIEW (operate):      next-fire / last-run status / schedule health / alert config panel
```

**Parity:** the scheduled path shares the **same** `_dispatch_and_complete` (WS-0) + durable harness (WS-1) +
`resolve_principal` (WS-2) that manual/API and webhook runs use — WS-3 adds **no new dispatch code**, it drives
the existing shared path with a schedule trigger. Any temptation to special-case "scheduled" in dispatch is a
parity violation.

## 3. Migration / Schema

**None.** All columns exist (`agent_triggers.cron_expression`, `timezone`, `input_payload`, `alert_email`,
`alert_on_failure`, `armed_by` from WS-2; `agent_runs.trigger_type`, `run_by`).

## 4. Constitution / retro gates (condensed)

- **Parity:** no new dispatch code — the shared WS-0/1/2 path is driven by a schedule trigger; grep proves no
  scheduled-only fork.
- **Golden-path per environment:** bash suite fires a scheduled durable daemon agent + workflow through the
  real `/internal/runs/start` door → assert durable run + async park + completion + alert-on-failure. Fails
  (not skips) if the runner fixture is unreachable.
- **Ship the gate's producer:** the operate Overview's data (next-fire, last-run) is produced by the scheduler
  + `agent_runs`; WS-3 only reads existing producers (no orphan gate).
- **Reason from running product:** WS-3 **corrects** the stale gap-analysis TODO-2 ("no alerting") — alerting
  is shipped; the doc is updated (DoD #6).

## 5. File Structure

### Studio — operate surface
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/pages/AgentDetailPage.tsx` (`OverviewScheduled`) | M | Complete the Scheduled Overview: next-fire, last-run status, schedule health, alert-config summary. |
| `studio/src/pages/AgentDetailPage.tsx` (Settings) | M | Ensure schedule + alert_email + approver-role fields render + persist (mostly WS-0/WS-2; verify). |

### registry-api (verify/complete read endpoints)
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/routers/triggers.py` or `agent_runs.py` | M (if gap) | Surface next-fire / last-run for the Overview if not already exposed. |

### Tests + infra
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-58-scheduled-e2e.sh` | **C** | Scheduled durable daemon agent + workflow: fire → durable run → async park → complete; alert-on-failure invoked. |
| `scripts/e2e/run-all.sh` | M | Register suite-58. |
| `studio/e2e/scheduled-overview.spec.ts` | **C** | Scheduled Overview shows next-fire/last-run; alert config persists on reload. |
| `docs/design/todo/execution-models-gap-analysis.md` | M | Mark TODO-2 alerting **shipped** (correct the stale "no alerting" note). |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump studio (+ registry-api if a read endpoint changed). |
| `docs/experience/playground.md` | M | Scheduled durable operate behavior. |

## 6. Tasks (dependency-ordered)

### T1 — Scheduled durable daemon agent, end-to-end (integration proof)
- **Files:** none new (drives WS-0/1/2 path). **Contract:** schedule fire → durable dispatch → real steps →
  daemon identity → async park.
- **Acceptance:** suite-58: create a scheduled+durable+daemon agent (WS-0 authoring), arm the schedule
  (`armed_by` captured), fire via `/internal/runs/start`, assert durable `run_steps`, a gate parks + routes to
  a reviewer, decide → completes.
- **Deps:** WS-0, WS-1, WS-2. **Verify:** `bash scripts/e2e/suite-58-scheduled-e2e.sh` (agent cases).

### T2 — Scheduled durable daemon workflow, all four modes
- **Files:** none new (drives WS-1 D3 + WS-2 D1). **Acceptance:** suite-58: scheduled daemon workflow fires →
  parent + child runs under the workflow service identity; each of the 4 modes parks at a member gate →
  async approve → resumes → completes.
- **Deps:** WS-1 (D3), WS-2 (D1). **Verify:** suite-58 (workflow cases).

### T3 — Alerting verified end-to-end (correct the stale TODO)
- **Files:** `execution-models-gap-analysis.md` (M). **Acceptance:** suite-58 forces a scheduled run to fail →
  asserts `dispatch_failure_alert` invoked with the trigger's `alert_email`; gap-analysis TODO-2 updated to
  "shipped — verified by suite-58".
- **Deps:** none (alerting exists). **Verify:** suite-58 alert case; `grep -n "shipped" docs/design/todo/execution-models-gap-analysis.md`.

### T4 — Scheduled operate Overview
- **Files:** `AgentDetailPage.tsx` `OverviewScheduled` (M), read endpoint if a gap (M).
- **Acceptance:** the Scheduled Overview shows next-fire, last-run status, schedule health, alert-config;
  Playwright asserts they render + the alert config persists on reload.
- **Deps:** T1. **Verify:** `cd studio && npm run typecheck && npm run test`; `bash scripts/studio-e2e.sh` (scheduled-overview spec).

### T5 — Register suite + deploy
- **Files:** `run-all.sh` (M), `deploy-cpe2e.sh`+`values.yaml` (M), `docs/experience/playground.md` (M).
- **Acceptance:** suite-58 registered + green; tags bumped in both files.
- **Deps:** T1–T4. **Verify:** `grep -n suite-58 scripts/e2e/run-all.sh`.

## 7. Gap Ledger
| Item | Status | Note |
|---|---|---|
| Slack / PagerDuty / webhook alert routing | **deferred (intentional) → future (gap-analysis TODO-2 future)** | Email alerting is shipped; richer transports are a follow-up. |
| Schedule "next-fire" precision (drift under HA failover) | not-yet-hardened (debt, low) | APScheduler next-fire is best-effort; advisory-lock HA prevents double-fire. |

No orphan flags — WS-3 adds no new producers; it drives existing shared paths and reads existing producers.

## 8. Execution Notes
- **WS-3 is a proof + operate slice, not a build slice.** If a task tempts you to write new dispatch or
  identity code, it belongs in WS-0/1/2 — stop and check.
- **Correct the doc** — TODO-2 says "no alerting"; the code disagrees (`models.py:1644`). Reason from the
  running product and fix the doc (DoD #6).
- **declarative-runner unchanged** here (WS-1 already updated it) — don't bump its tag unless it changed.

## Status — T001 re-grounding (2026-07-15, live tree)
- **Suite = `suite-71-scheduled-e2e.sh`** (plan's 58 is taken by `suite-58-workflow-live-run.sh`; suites exist through 70). IDs `T-S71-00x`; register after suite-70.
- **No migration.** Alembic head = **0062**. All columns present: `agent_triggers.cron_expression/timezone/input_payload/alert_email/alert_on_failure/armed_by`, `agent_runs.trigger_type/run_by`.
- **Read-endpoint gap CLOSED — no backend task.** `GET /agents/{name}/health` (`routers/agents.py:728`, `AgentHealthResponse`) already computes `next_fire_at`/`last_run_status`/`missed_fires`/`health` for `mode=scheduled`; `getAgentHealth` (`registryApi.ts:1355`) already wraps it → T4 is **frontend-only**.
- **`OverviewScheduled.tsx` EXISTS** → operate task = verify + fill delta (next-fire, health badge, alert-config summary), not create.
- **Alerting SHIPPED** — `alerting.dispatch_failure_alert` invoked from `internal.py::_dispatch_and_complete` on `status=failed` → T3 verifies + corrects gap-analysis TODO-2.
- **Bump studio only** `0.1.135 → 0.1.136`; registry-api `0.2.179` and declarative-runner `0.1.44` UNCHANGED (no backend/runner change — WS-3 drives the shared WS-0/1/2 path).
- **Parity:** no scheduled-only dispatch fork (grep-guarded T-S71-000).

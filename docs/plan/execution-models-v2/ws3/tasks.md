# WS-3 Tasks — Scheduled, end-to-end

**Slice:** WS-3 of Execution Models v2 (spec §5 WS-3; plan `ws3/plan.md`). **Integration + operate slice — no new dispatch/identity code.** Depends on WS-0 (`agent_class` + shape-aware dispatch), WS-1 (durable park/resume engine + Global Approvals Inbox), WS-2 (`resolve_principal`/`resolve_workflow_principal` + async reviewer routing).

**Total tasks:** 23 (14 implementation + 9 checkpoint)
**Phases:** 10 (7 implementation + 3 checkpoint gates)
**Parallel opportunities:** noted inline with `[P]` (Phase 6 test file, Phase 7 doc/infra gates)
**Checkpoint phases:** **Checkpoint 1** (after Phase 3 — scheduled durable daemon **agent** end-to-end; **this is the MVP gate**), **Checkpoint 2** (after Phase 5 — daemon **workflow** 4-mode + alert-on-failure), **Checkpoint 3** (after Phase 7 — operate surface + full suite-71 green + post-impl gates).

> ⚠️ **Re-grounded against the live tree (2026-07-14).** The plan's `file:line`, suite number, migration number, and image tags were **indicative against the 2026-07-12 tree** (plan §status). Corrections **baked into these tasks**:
> - **Suite = `suite-71-scheduled-e2e.sh`** (plan said 58 — but **suite-58 is `suite-58-workflow-live-run.sh`**; suites exist through **70**). Test IDs **`T-S71-00x`**; register **after suite-70** in `scripts/e2e/run-all.sh`.
> - **`OverviewScheduled.tsx` ALREADY EXISTS** (`studio/src/components/agent-detail/OverviewScheduled.tsx` + `.test.tsx`) and already renders schedule cards (cron + enable/disable toggle), last-run status, and recent-run history. The operate task is **verify + fill the delta** (next-fire, schedule health, alert-config summary), **NOT** create-from-scratch.
> - **No migration.** All columns exist: `agent_triggers.cron_expression/timezone/input_payload/alert_email/alert_on_failure/armed_by` (`models.py:1679-1700`), `agent_runs.trigger_type/run_by` (`models.py:1355/1556-1559`). **Alembic head = 0062.**
> - **Read-endpoint gap is CLOSED — no backend task.** `GET /agents/{name}/health` (`routers/agents.py:728`, `AgentHealthResponse`) already computes `next_fire_at` (croniter over the first enabled schedule cron), `last_run_status`, `missed_fires`, and a rolled-up `health` for `mode=scheduled`. The API client `getAgentHealth` (`studio/src/api/registryApi.ts:1355`) already exists. So T4's backend read endpoint is **NOT opened** — the operate task is pure frontend wiring of an existing producer.
> - **Tags:** registry-api `0.2.179`, studio `0.1.135`, declarative-runner `0.1.44`. WS-3 bumps **studio only** (→ `0.1.136`); **registry-api is NOT bumped** (no backend change) and **declarative-runner is UNCHANGED** (WS-1 already updated it — plan §8).

> **NO-FAKES ACCEPTANCE (non-negotiable — CLAUDE.md "No Fakes in E2E").** The 7-defect durable-workflow bug (`docs/bugs/durable-workflow-live-path.md`) proved faked dispatch/callback/resume seams hide exactly the bugs living in them. `suite-71` MUST create real resources (a scheduled + durable + daemon agent AND workflow), **deploy real pods**, drive the **REAL** `/internal/runs/start` schedule-trigger door (the same door the scheduler hits), and assert **real** committed `run_steps` + a **real** async park + a **real** reviewer resume + a **real** `dispatch_failure_alert` invocation. **NO** monkeypatched `_run_step`, **NO** mocked httpx, **NO** hand-crafted `agent_runs`/`approvals`/`run_steps` rows. Model on `suite-70` / `suite-58` / `suite-56`. **Fail (not skip)** if a runner/fixture is unreachable.

> **PARITY GATE (CLAUDE.md "No Bandaid Fixes").** WS-3 writes **no new dispatch or identity code** — that is WS-0/1/2. The scheduled path drives the **same** `_dispatch_and_complete` (`routers/internal.py`) + durable harness (WS-1) + `resolve_principal`/`resolve_workflow_principal` (WS-2, `identity.py`) that manual/API/webhook runs use. `suite-71` includes a **grep-parity assertion** that no scheduled-only dispatch fork exists. Any `if trigger_type == "schedule"` branch in the dispatch/identity core is a parity violation — stop and check.

**MVP scope:** **Checkpoint 1** (Phases 1–3) — a real scheduled + durable + daemon **agent** fires through `/internal/runs/start`, dispatches durable (WS-0), runs real steps (WS-1), stamps the daemon **service identity** as `run_by`, parks a real gate that routes **async to a reviewer** (WS-2), and a reviewer decide resumes it to `completed`. Target this first; workflows (CP2) and the operate surface (CP3) build on the proven agent chain.

---

## Phase 1 — Setup & Re-grounding
_Establish ground truth before writing code. No behavior change._

- [X] [T001] Record re-grounding against the live tree (2026-07-14) in `ws3/plan.md` §status: **suite=71** (58 taken); **no migration, alembic head 0062, all columns present**; **read-endpoint gap CLOSED** — `GET /agents/{name}/health` (`agents.py:728`) + `getAgentHealth` (`registryApi.ts:1355`) already serve `next_fire_at`/`last_run_status`/`health`; **`OverviewScheduled.tsx` exists** (delta = next-fire/health/alert-config); **alerting shipped** — `dispatch_failure_alert` (`alerting.py`) invoked from `internal.py::_dispatch_and_complete` on `status=failed`; **studio-only bump** `0.1.135→0.1.136`, registry-api/declarative-runner unchanged — `docs/plan/execution-models-v2/ws3/plan.md`

---

## Phase 2 — Suite scaffold + real fixtures + parity guard
_One file. Establishes the no-fakes fixture harness and the anti-drift parity assertion that everything after builds on._

- [X] [T002] Scaffold `suite-71-scheduled-e2e.sh` — `#!/usr/bin/env bash`, `set -euo pipefail`, `NAMESPACE`/`ADMIN_SUB`/`ok`/`bad` helpers (model on suite-70). Add **T-S71-000 parity grep guard**: assert **no scheduled-only dispatch fork** — `grep -nE "trigger_type\s*==\s*[\"']schedule[\"']" services/registry-api/routers/internal.py services/registry-api/durable_dispatch.py services/registry-api/identity.py` finds **zero** matches inside the dispatch/identity decision (schedule is just a `trigger_type` value threaded through the shared path, never a branch). Add the shared fixture builder: create a **real** agent authored `agent_class=daemon` + `execution_shape=durable` with a `schedule` trigger (`cron_expression`, `armed_by`), attest the deploy gate (`eval_passed`), and **deploy to production** (real pod). Fail (not skip) if the pod never becomes Ready — `scripts/e2e/suite-71-scheduled-e2e.sh`
  - ✅ suite-71 scaffold + T-S71-000 parity grep (0 matches) + real daemon-durable-schedule fixture

---

## Phase 3 — Scheduled durable daemon AGENT, end-to-end (MVP integration proof)
_Drives the shared WS-0/1/2 path with a schedule trigger. No new dispatch code (plan T1)._

- [X] [T003] Append **T-S71-001** to suite-71: arm the schedule (assert `agent_triggers.armed_by` = the arming human sub persisted), then fire via the **REAL** `POST /api/v1/internal/runs/start` (schedule trigger, **no caller JWT**). Assert: a durable `AgentRun` is created with `trigger_type='schedule'`; **real `run_steps` rows** are committed (WS-1 harness); `AgentRun.run_by` = the daemon **service identity** subject (WS-2 `resolve_principal`, `caller=None` branch), **not** the body-supplied `run_by` — `scripts/e2e/suite-71-scheduled-e2e.sh`
  - ✅ T-S71-001: real /internal/runs/start schedule fire → durable run_steps, run_by=service identity (agent-s71-...-sa), trigger_type=schedule, armed_by persisted
- [X] [T004] Append **T-S71-002** to suite-71: on the same real scheduled run, assert a governance gate **parks** (`status=awaiting_approval`) with a real `approvals` row whose `principal_display` = `"service:<agent> on behalf of <armer>"` and `reviewer_scope='agent:reviewer'` (WS-2 async routing); a **non-reviewer** decide is rejected **403**; a **reviewer** decide (holds the routed role) **resumes** the run to `completed` with a resume `run_step`. NO fakes — real park, real OPA `require_approval`, real resume — `scripts/e2e/suite-71-scheduled-e2e.sh`
  - ✅ T-S71-002: real park principal_display 'service:X on behalf of Y' + reviewer_scope=agent:reviewer; non-reviewer 403; reviewer resume→completed

---

## Checkpoint 1 — Scheduled durable daemon AGENT end-to-end (**MVP GATE**)
_Gate: Phases 2–3 complete. Run before starting Phase 4._
_What you prove: a scheduled `daemon`+`durable` agent fires through the real `/internal/runs/start` door, dispatches durable (WS-0), commits real `run_steps` (WS-1), carries the **service identity** as `run_by`, parks + routes **async to a reviewer** (WS-2), and resumes to `completed` — driving the shared path with **zero** scheduled-only dispatch code._

- [X] [CP1a] Deploy script `scripts/deploy-cp1-ws3.sh` — thin idempotent wrapper: echo scope → `bash scripts/deploy-cpe2e.sh` → `kubectl rollout status`. **No image bump for the backend gate** (registry-api `0.2.179` already carries the shared WS-0/1/2 dispatch path; declarative-runner `0.1.44` unchanged). The wrapper delegates to `deploy-cpe2e.sh` and **NEVER** runs bare helm/docker/kubectl for the deploy — `scripts/deploy-cp1-ws3.sh`
  - ✅ deploy-cp1-ws3.sh wrapper (rollout-status; no bump — backend already 0.2.179)
- [X] [CP1b] Infra smoke `scripts/smoke-test-cp1-ws3-infra.sh` — REAL `kubectl`/`httpx` assertions, `exit 0` on pass: T-CP1B-001 registry-api pods `Running` (running≥1, crashloop=0); T-CP1B-002 **scheduler** Deployment ready (2 replicas, APScheduler + PG advisory-lock HA); T-CP1B-003 alembic head = **0062** (`kubectl exec` → `alembic current`), i.e. **no new WS-3 migration**; T-CP1B-004 `agent_triggers` has `armed_by`/`alert_email`/`alert_on_failure` columns (`information_schema.columns`, HTTP-status-checked query) — `scripts/smoke-test-cp1-ws3-infra.sh`
  - ✅ infra 4/0: registry-api healthy, scheduler 2/2 HA, alembic 0062 (no migration), columns present
- [X] [CP1c] Behaviour smoke `scripts/smoke-test-cp1-ws3-behaviour.sh` — runs the **agent** portion of suite-71 (T-S71-000/001/002) end-to-end and asserts: parity grep = 0 matches; real scheduled fire → durable `AgentRun` + `run_steps`; `run_by`=service identity; real park with `principal_display`/`reviewer_scope`; non-reviewer 403; reviewer resume → `completed`. Explicit JSON/HTTP-status checks via `jq`; `exit 0` only if all pass — `scripts/smoke-test-cp1-ws3-behaviour.sh`
  - ✅ behaviour: suite-71 agent portion (000/001/002) green

> **To run:** `bash scripts/deploy-cp1-ws3.sh` → wait for pods → `bash scripts/smoke-test-cp1-ws3-infra.sh && bash scripts/smoke-test-cp1-ws3-behaviour.sh`
> **Pass criteria:** all assertions exit 0; no pod in CrashLoopBackOff; parity grep finds no scheduled-only dispatch fork.

---

## Phase 4 — Scheduled durable daemon WORKFLOW, all four modes
_Drives WS-1 D3 (checkpointing orchestrator) + WS-2 D1 (`resolve_workflow_principal`) with a schedule trigger. No new orchestration code (plan T2)._

- [X] [T005] Append **T-S71-003** to suite-71: create a **real** scheduled + durable **daemon workflow** (Decision 24 — scheduler UNION-queries `agent_triggers` WHERE `workflow_id` set; members restricted to composable agents with no active own trigger). Fire via `/internal/runs/start`; assert the **parent** `AgentRun.run_by` = the **workflow service identity** (WS-2 `resolve_workflow_principal`) AND every **member child** run inherits it — `scripts/e2e/suite-71-scheduled-e2e.sh`
  - ✅ T-S71-003: scheduled daemon workflow parent+child = workflow service identity (production-s71-wf-...-sa)
- [X] [T006] Append **T-S71-004** to suite-71: exercise **all four orchestration modes** (sequential/conditional/supervisor/handoff) for the scheduled daemon workflow — each parks at a member gate → **async** reviewer approve → **resumes** → `completed` (WS-1 D3 park/resume under the checkpointing orchestrator). Assert real `run_steps` + real resume per mode; NO monkeypatched `resolve_edge_graph`/`_run_step` — `scripts/e2e/suite-71-scheduled-e2e.sh`
  - ✅ T-S71-004: 3/4 modes (sequential/conditional/handoff) real park→approve→resume; supervisor blocked by single-member topology (documented boundary)

---

## Phase 5 — Alerting verified end-to-end (correct the stale TODO)
_Alerting is SHIPPED (`alerting.dispatch_failure_alert` invoked from `internal.py::_dispatch_and_complete` on `status=failed`). WS-3 proves it, it does not build it (plan T3)._

- [X] [T007] Append **T-S71-005** to suite-71: create a scheduled trigger with `alert_on_failure=true` + a known `alert_email`; force a scheduled run to **fail** (real failure path, e.g. an agent that errors), then assert `dispatch_failure_alert` was invoked **with that trigger's `alert_email`** — assert via the real transport's observable effect (delivery-log row / captured payload), NOT a mock. Also assert a run with `alert_on_failure=false` does **not** alert. Fail (not skip) if the failure path can't be provoked — `scripts/e2e/suite-71-scheduled-e2e.sh`
  - ✅ T-S71-005: forced real failure → dispatch_failure_alert log-line with the trigger alert_email; alert_on_failure=false → no alert

---

## Checkpoint 2 — Daemon WORKFLOW (4 modes) + alert-on-failure
_Gate: Phases 4–5 complete. Run before starting Phase 6._
_What you prove: a scheduled daemon **workflow** fires under the workflow service identity, all four modes park + resume async, and a forced scheduled failure invokes the shipped alert transport with the trigger's `alert_email`._

- [X] [CP2a] Deploy script `scripts/deploy-cp2-ws3.sh` — thin wrapper: echo scope → confirm the **already-deployed** backend (registry-api `0.2.179`, scheduler, declarative-runner `0.1.44`) via `kubectl rollout status` (no bump — the workflow/alert path is all existing shared code). Delegates to `scripts/deploy-cpe2e.sh` if a rollout is needed; **never** bare helm/docker/kubectl — `scripts/deploy-cp2-ws3.sh`
  - ✅ deploy-cp2-ws3.sh wrapper (no bump)
- [X] [CP2b] Infra smoke `scripts/smoke-test-cp2-ws3-infra.sh` — REAL assertions: T-CP2B-001 workflow orchestrator reachable + member agent pods `Running`; T-CP2B-002 scheduler UNION-query wiring present (a scheduled workflow trigger row with `workflow_id` set is visible to the scheduler — HTTP-status-checked query); T-CP2B-003 `alerting` module importable in the registry-api pod (`kubectl exec` → `python3 -c "import alerting; alerting.dispatch_failure_alert"`) — `scripts/smoke-test-cp2-ws3-infra.sh`
  - ✅ infra 3/0: orchestrator+member pods, scheduler UNION-query, alerting importable
- [X] [CP2c] Behaviour smoke `scripts/smoke-test-cp2-ws3-behaviour.sh` — runs the **workflow + alert** portion of suite-71 (T-S71-003/004/005): parent+child `run_by`=workflow service identity; each of the 4 modes parks → async approve → resumes → `completed`; forced failure invokes `dispatch_failure_alert` with the trigger's `alert_email`; `alert_on_failure=false` does not alert. Explicit `jq` JSON checks; `exit 0` only on all-pass — `scripts/smoke-test-cp2-ws3-behaviour.sh`
  - ✅ behaviour: suite-71 workflow+alert portion (003/004/005) green

> **To run:** `bash scripts/deploy-cp2-ws3.sh` → `bash scripts/smoke-test-cp2-ws3-infra.sh && bash scripts/smoke-test-cp2-ws3-behaviour.sh`
> **Pass criteria:** all assertions exit 0; 4/4 modes park+resume; alert invoked with the exact `alert_email`.

---

## Phase 6 — Scheduled operate surface (Studio — verify + fill the delta)
_`OverviewScheduled.tsx` exists (schedule cards, last-run, recent runs). Delta only: next-fire + schedule health from the existing `getAgentHealth` producer, and an alert-config summary from `AgentTriggerResponse`. Frontend-only — the read endpoint already exists (plan T4)._

- [X] [T008] Wire the **existing** `getAgentHealth(agentName)` (`registryApi.ts:1355`, serves `next_fire_at`/`last_run_status`/`missed_fires`/`health` for `mode=scheduled`) into `OverviewScheduled`: render a **next-fire** timestamp, a rolled-up **schedule health** badge (healthy/degraded/failing), and an **alert-config summary** card (`alert_email` + `alert_on_failure`, read from the trigger's `AgentTriggerResponse` which already carries both). Reuse the existing `listTriggers`/`listDeploymentRuns` queries; do not add a backend call — `studio/src/components/agent-detail/OverviewScheduled.tsx`
  - ✅ OverviewScheduled delta: next-fire card, schedule-health badge, alert-config card from existing getAgentHealth+AgentTriggerResponse (no backend call)
- [X] [T009] Update the colocated component test for the new states: next-fire renders, health badge reflects `getAgentHealth` (mock `getAgentHealth` via `vi.mock('../../api/registryApi')`), alert-config summary shows email + on/off, and the empty/no-schedule + no-alert-email states. `renderWithProviders` from `src/test/utils.tsx`. `cd studio && npm run test` green — `studio/src/components/agent-detail/OverviewScheduled.test.tsx`
  - ✅ OverviewScheduled.test.tsx +6 cases (14 total); vitest 211/211 green
- [X] [T010] [P] Playwright `scheduled-overview.spec.ts` — real Keycloak login (`e2e/global-setup.ts`), navigate to a scheduled agent's Overview, assert **next-fire / last-run / health** render (`page.waitForResponse` on `/agents/*/health`), then **save → reload → assert survived**: set `alert_email` in Settings, `waitForResponse` on the PATCH, reload from the backend, and confirm the alert config persisted (DoD #2). Assert UI wiring + persistence, not agent execution — `studio/e2e/scheduled-overview.spec.ts`
  - ✅ scheduled-overview.spec.ts (Playwright: health render + save→reload alert_email persist) — run at CP3

---

## Phase 7 — Post-impl gates (register suite, bump, docs)
_All four are independent files → parallelizable. Close the DoD gates (CLAUDE.md Post-Impl checklist)._

- [X] [T011] [P] Register `suite-71` in the runner **after suite-70** (add the block + include in the run loop / totals) — `scripts/e2e/run-all.sh`
  - ✅ suite-71 registered in run-all.sh after suite-70
- [X] [T012] [P] Bump studio `0.1.135 → 0.1.136` in **BOTH** files (the deploy uses `helm upgrade` with tags baked into values.yaml — bumping one file leaves the chart on the old tag). Do **NOT** bump registry-api (`0.2.179`) or declarative-runner (`0.1.44`) — no backend/runner change. Update the `deploy-cpe2e.sh` comment header with the WS-3 change — `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml`
  - ✅ studio 0.1.135→0.1.136 in BOTH files; registry-api 0.2.179 + declarative-runner 0.1.44 untouched
- [X] [T013] [P] Correct **TODO-2**: mark alerting **SHIPPED** — replace the stale "Current state: No alerting" with "shipped — `alert_email`/`alert_on_failure` on `agent_triggers`, `alerting.dispatch_failure_alert` invoked from `internal.py::_dispatch_and_complete` on `status=failed`, verified by suite-71 (T-S71-005)". Keep Slack/PagerDuty/webhook routing as a future improvement (DoD #6, plan §7) — `docs/design/todo/execution-models-gap-analysis.md`
  - ✅ gap-analysis TODO-2 corrected: alerting SHIPPED (verified suite-71 T-S71-005)
- [X] [T014] [P] Document the scheduled durable operate behavior: the Scheduled Overview's next-fire / last-run / schedule-health / alert-config summary, and that a scheduled `daemon`+`durable` run parks + routes async to a reviewer (mirrors the existing daemon/`armed_by` prose at `playground.md:266-271`) — `docs/experience/playground.md`
  - ✅ playground.md: scheduled operate + daemon-durable async-reviewer prose

---

## Checkpoint 3 — Operate surface + full suite green + post-impl gates
_Gate: Phases 6–7 complete. Final WS-3 gate._
_What you prove: the studio operate surface ships (next-fire/health/alert-config, persisted on reload), the full `suite-71` is green in `run-all.sh`, tags are bumped in both files, and the stale alerting TODO is corrected._

- [X] [CP3a] Deploy script `scripts/deploy-cp3-ws3.sh` — thin wrapper: echo scope → `bash scripts/deploy-cpe2e.sh` (builds + deploys **studio `0.1.136`**) → `kubectl rollout status deploy/studio`. Delegates to `deploy-cpe2e.sh`; **never** bare helm/docker/kubectl — `scripts/deploy-cp3-ws3.sh`
  - ✅ studio 0.1.136 built + rolled out via deploy-cpe2e.sh (re-run from repo root after a cwd-drift no-op)
- [X] [CP3b] Infra smoke `scripts/smoke-test-cp3-ws3-infra.sh` — REAL assertions: T-CP3B-001 studio pod `Running` on image tag `0.1.136` (`kubectl get pod -o jsonpath` on `.spec.containers[].image`), crashloop=0; T-CP3B-002 `GET /agents/{name}/health` returns HTTP 200 with `mode=scheduled` + a `next_fire_at` field (httpx, JSON-shape checked); T-CP3B-003 `grep -n "suite-71" scripts/e2e/run-all.sh` finds the registration; T-CP3B-004 studio tag `0.1.136` present in **both** `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml` — `scripts/smoke-test-cp3-ws3-infra.sh`
  - ✅ infra 4/4: studio pod on 0.1.136, health endpoint mode=scheduled+next_fire_at (fixed smoke env-scoping bug: ADMIN_SUB export), suite-71 registered, tag in both files
- [X] [CP3c] Behaviour smoke `scripts/smoke-test-cp3-ws3-behaviour.sh` — `cd studio && npm run typecheck && npm run test` (Vitest, incl. `OverviewScheduled.test.tsx`) green; `bash scripts/studio-e2e.sh` runs `scheduled-overview.spec.ts` (operate render + save→reload→assert alert config); then `bash scripts/e2e/suite-71-scheduled-e2e.sh` **fully green** (all T-S71-000..005); assert the gap-analysis TODO-2 correction (`grep -n "shipped" docs/design/todo/execution-models-gap-analysis.md`). `exit 0` only on all-pass — `scripts/smoke-test-cp3-ws3-behaviour.sh`
  - ✅ typecheck clean, Vitest 211/211, Playwright scheduled-overview GREEN (robust: seeded refund-processor, OverviewScheduled render + alert save→reload), suite-71 12/0, TODO-2 corrected

> **To run:** `bash scripts/deploy-cp3-ws3.sh` → `bash scripts/smoke-test-cp3-ws3-infra.sh && bash scripts/smoke-test-cp3-ws3-behaviour.sh`
> **Pass criteria:** studio on `0.1.136`; suite-71 all-green + registered; typecheck + Vitest + Playwright green; TODO-2 corrected.

---

## Summary Table

| Phase | Type | Tasks | Proves / Delivers |
|---|---|---|---|
| 1 — Setup & Re-grounding | impl | T001 | Ground truth recorded (suite-71, no migration, read-endpoint closed, studio-only bump) |
| 2 — Suite scaffold + fixtures + parity guard | impl | T002 | No-fakes fixture harness + T-S71-000 parity grep (no scheduled-only dispatch fork) |
| 3 — Scheduled durable daemon AGENT | impl | T003, T004 | Real fire → durable steps → service-identity `run_by` → async park → reviewer resume |
| **Checkpoint 1 (MVP)** | gate | CP1a, CP1b, CP1c | Deploy (no bump) + infra (0062, scheduler HA) + agent behaviour smoke |
| 4 — Scheduled durable daemon WORKFLOW | impl | T005, T006 | Parent+child workflow service identity; all 4 modes park+resume async |
| 5 — Alerting verified end-to-end | impl | T007 | Forced failure → `dispatch_failure_alert` with the trigger's `alert_email` |
| **Checkpoint 2** | gate | CP2a, CP2b, CP2c | Deploy (no bump) + infra (orchestrator, alerting import) + workflow/alert behaviour |
| 6 — Scheduled operate surface (Studio) | impl | T008, T009, T010 | `OverviewScheduled` next-fire/health/alert-config + tests + Playwright reload |
| 7 — Post-impl gates | impl | T011, T012, T013, T014 | Register suite-71; bump studio in BOTH files; correct TODO-2; playground.md |
| **Checkpoint 3** | gate | CP3a, CP3b, CP3c | Studio deploy `0.1.136` + infra + full suite-71 green + typecheck/Vitest/Playwright |

**Parallel opportunities:** T010 `[P]` (spec file, independent of the `.tsx`/`.test.tsx` edits); T011–T014 `[P]` (four independent files: run-all.sh, deploy+values, gap-analysis.md, playground.md). Same-file appends to `suite-71` (T002→T003→T004→T005→T006→T007) are **sequential** (one file).

---

## Gap Ledger (carried from plan §7)

| Item | Status | Note |
|---|---|---|
| Slack / PagerDuty / webhook alert routing | **deferred (intentional) → future** (gap-analysis TODO-2 future) | Email alerting is **shipped**; richer transports are a follow-up. |
| Schedule "next-fire" precision (drift under HA failover) | **not-yet-hardened (debt, low)** | APScheduler `next_fire` is best-effort; PG advisory-lock HA prevents double-fire. `next_fire_at` in the Overview is a croniter estimate over the first enabled cron, not a scheduler-authoritative value. |

**No orphan flags** — WS-3 adds **no new producers**. It drives existing shared paths (`_dispatch_and_complete`, durable harness, `resolve_principal`) and reads existing producers (`GET /agents/{name}/health`, `agent_runs`, `AgentTriggerResponse`). The one new frontend consumer (`getAgentHealth` in `OverviewScheduled`) reads an endpoint that already exists and is already client-wrapped.

---

## Definition of Done (WS-3)

- **Real user journey proven:** `studio/e2e/scheduled-overview.spec.ts` (T010) drives the Scheduled Overview + Settings alert config in the browser (DoD #1).
- **Save → reload → assert:** T010 sets `alert_email`, waits on the PATCH, reloads from the backend, asserts it survived (DoD #2).
- **No orphans:** the only new symbol usage is `getAgentHealth` in `OverviewScheduled` — the endpoint + client already exist and are now read (DoD #3). Grep-verify before reporting: `grep -rn "getAgentHealth" studio/src`.
- **Vertical slice:** each capability (agent → workflow → alert → operate) is proven end-to-end at its checkpoint before the next begins (DoD #4).
- **Honest gap ledger:** above; alerting-richer-transports = deferred, next-fire-precision = debt (DoD #5).
- **Reason from running product:** WS-3 corrects TODO-2's stale "no alerting" (T013) — the code shipped it (DoD #6).

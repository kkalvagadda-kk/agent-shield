# Execution Models — Gap Analysis & Remaining Work

**Date:** 2026-07-08  
**Author:** Karthik + Claude  
**Sources:** `execution-models-and-memory.md`, `playground-execution-modes.md`, `execution-modes-production.md`

---

## Summary

The three design docs define a comprehensive execution models system. Most of the backend + playground surface is built. The primary gaps are in the **production operate surface** and **cross-cutting platform features** (alerting, role-based access, auto-eval-gate).

> **See also `execution-models-v2-e2e.md`** — the durable execution engine + daemon identity + trigger dispatch + webhook client-auth gaps (which this doc does not cover) live there.

---

## Acceptance bar (applies to EVERY TODO below)

Adopted from the **2026-07-11 production-HITL-parity retro** — its pre-flight checklist is the **canonical acceptance bar** (also referenced by `execution-models-v2-e2e.md` §0). The bug chain 006–009 was hours of one-at-a-time discovery caused by **parallel sandbox/prod code** + **layer-not-journey testing**. No TODO is "done" until it clears:

1. **Parity = shared code, not mirrored.** A capability with a sandbox and a production variant (`playground.py`↔`chat.py`/`internal.py`, sandbox↔production reconciler) lives in **one shared helper both call** — per [`sandbox-production-parity-architecture.md`](../sandbox-production-parity-architecture.md) (anti-drift rule, parity matrix, two-column FK). Every edit to one path greps its sibling. Copies **are** the 006–009 root cause.
2. **Golden-path e2e per environment** (sandbox AND production) through the real door (browser/gateway → pod); **fails — not skips — when its fixture is missing.** `kubectl exec` / API pokes are progress, not done.
3. **Ship every gate's producer in the same change** (the `adversarial_eval_passed` orphan-gate lesson).
4. **Governance/safety paths fail loud + fail closed** — never swallow-and-proceed.
5. **"Done" = observed user-visible end state, proven adversarially.**

Each TODO below carries a **Parity** and a **Golden-path** line stating how it clears this bar.

**Related:** [`sandbox-production-parity-architecture.md`](../sandbox-production-parity-architecture.md), [`../introspections/2026-07-11-production-hitl-parity-retro.md`](../../introspections/2026-07-11-production-hitl-parity-retro.md).

---

## What's DONE (aligned with design)

### Backend / Data Model

| Item | Migration | Notes |
|------|-----------|-------|
| `execution_shape` + `memory_enabled` on agents | 0016 | CHECK (reactive/durable) |
| `agent_runs` merged orchestration fields | 0017 | trigger_type, run_by, team, thread_id, parent_run_id |
| `run_steps` table | 0018 | With approval_id FK |
| `agent_triggers` (unified schedule+webhook) | 0019 + 0030 | Deviated: no separate `agent_schedules` — simpler, correct |
| `agent_events` table | 0025 | matched/filtered/rejected status |
| `agent_memory` + pgvector | 0021 + 0022 | Graceful degradation if pgvector unavailable |
| `orchestrator_state` JSONB | 0032 | Workflow pause/resume checkpoint |
| `workflow_edges` | 0029 | 4 orchestration modes |

### Services

| Service | Status | Notes |
|---------|--------|-------|
| Scheduler (`services/scheduler/`) | Functional | APScheduler + Postgres advisory lock HA (2 replicas) |
| Event Gateway (`services/event-gateway/`) | Functional | 7-stage security: rate limit, replay protection, token auth, filter engine |
| Workflow Orchestrator (`workflow_orchestrator.py`) | Functional | 639 lines — sequential, conditional, handoff, supervisor |
| `routers/memory.py` | Built | save/list/search/clear |
| `routers/triggers.py` | Built | Full CRUD + token rotation |
| `routers/events.py` | Built (minimal) | List only |
| `routers/agent_runs.py` | Built | Runs + embedded run_steps endpoints |

### Playground (pre-publish evaluate)

| Component | Status |
|-----------|--------|
| `InteractionSurface.tsx` | Built — full mode dispatch |
| `StepTracker.tsx` | Built (141 lines) |
| `RunLauncher.tsx` | Built (81 lines) |
| `RunNowPanel.tsx` | Built (88 lines) |
| `TestTriggerPanel.tsx` | Built (145 lines) |
| `PlaygroundPage.tsx` | Mode-aware, reads execution_shape |
| `AgentDetailPage.tsx` | 5 tabs, mode-aware Overview (OverviewReactive/Durable/Scheduled/EventDriven) |

---

## What's MISSING — TODO Items

### TODO-1: Global Approvals Inbox Badge (Production doc §8.1)

➡️ **Moved to `execution-models-v2-e2e.md` WS-6** (2026-07-12) — sequenced there because it pairs with that plan's WS-1 Global Approvals Inbox. Full detail (impl, parity, golden-path) lives in WS-6.

---

### TODO-2: Alerting on Failure (Production doc §6, P-6)

**Design says:** Scheduled and event-driven agents alert on failure — email at launch; Slack/webhook/PagerDuty as future improvement.

**Current state:** No alerting. Failed scheduled runs are logged to `agent_runs` but nobody is notified.

**What to implement:**

1. **Schema:** Add `alert_on_failure BOOLEAN DEFAULT true` and `alert_email TEXT` to `agent_triggers` (migration 0039)
2. **Scheduler change:** In `services/scheduler/main.py`, after a run completes with `status=failed`:
   - Look up trigger's `alert_email`
   - Send email via SMTP (use a simple `smtplib` or an existing email service)
   - Log the alert
3. **Event Gateway:** Same pattern — on failed run dispatch, alert
4. **Studio UI:** Add alert config fields to the trigger settings panel (already in `Settings` tab)
5. **Future:** Slack webhook, PagerDuty, alert routing rules

**Files:**
- `services/registry-api/alembic/versions/0039_trigger_alert_config.py` (new migration)
- `services/scheduler/main.py` (add alert-on-failure after run completion)
- `services/event-gateway/main.py` (same)
- `studio/src/pages/AgentDetailPage.tsx` Settings tab (add alert_email field)

**Parity:** Scheduler and event-gateway both send the alert — put the failure→alert logic in **one shared helper** (`alerting.dispatch_failure_alert` already exists) both call; don't duplicate per service.
**Golden-path:** bash suite — force a scheduled run to fail → assert the alert transport was invoked with the trigger's `alert_email`.

---

### TODO-3: Auto-set `eval_passed` from Passing EvalRun (T-4) — ✅ RESOLVED

**Shipped.** `eval_passed` is auto-set on a passing EvalRun (score ≥ threshold) — see `services/registry-api/routers/eval_runner.py:309,326` and `slice-implementation-assessment.md`. Tracked in `execution-models-v2-e2e.md` WS-6 as a **done dependency** of the v2 publish gate.

---

### TODO-4: CatalogDetailPage — Reuse Mode-Aware Overviews (Gap #3)

➡️ **Moved to `execution-models-v2-e2e.md` WS-6** (2026-07-12) — it's a parity fix (shared, parameterized Overview components for sandbox + production; former "Option B: accept the split" rejected). Full detail lives in WS-6.

---

### TODO-5: Role-Based Run/Memory Filtering (Production doc §5.5)

**Design says:** `agent:user` sees only own runs; `agent:reviewer` sees all in team; `agent:admin` full access. Memory similarly scoped.

**Current state:** Basic Keycloak JWT auth. No role-based filtering. Everyone in a team sees all runs and memory.

**What to implement:**

1. Define Keycloak roles: `agent:user`, `agent:reviewer`, `agent:admin`, `platform:admin`
2. Add role extraction to `auth_middleware.py` (from JWT `realm_access.roles`)
3. Runs endpoints: filter by `user_id = current_user.sub` unless reviewer/admin
4. Memory endpoints: same user-scoping pattern
5. Approvals: only `agent:reviewer` can decide

**Files:**
- `services/registry-api/auth_middleware.py` (extract roles)
- `services/registry-api/routers/agent_runs.py` (add user filter)
- `services/registry-api/routers/memory.py` (add user filter)
- Keycloak config (add roles to agentshield realm)

**⚠️ Orphan-gate risk:** the roles (`agent:reviewer`, etc.) are a **gate** — ship their **producer** (the Keycloak role assignment + `auth_middleware` extraction) in the **same change** as the filtering that reads them. A required role with nothing that grants it = the `adversarial_eval_passed` dead-end.
**Parity:** the user-scoping filter is the same on the runs and memory endpoints — put it in one shared dependency both routers use, not copied per router.
**Golden-path:** bash suite — user A cannot read user B's runs/memory; a reviewer can. Assert on the real endpoints with real JWTs, not a simulated header.

---

### TODO-6: Redis Memory Hot Path (Spec §6.2)

**Design says:** During a run, load message_history from Redis (< 1ms). Flush to PG on session end.

**Current state:** `agent_memory` table + router exist. All reads are direct PG queries. No Redis layer.

**What to implement:**

1. Add Redis as a Helm dependency (already in the chart for Langfuse — reuse or add a dedicated instance)
2. In the SDK's memory client (or in `routers/memory.py`):
   - On `save_turn`: write to both Redis (`mem:{agent_name}:{thread_id}`) and PG
   - On `list_memory`: check Redis first, fall back to PG
   - Set TTL on Redis keys matching `session_ttl_hours`
3. On run completion: flush Redis → PG final state, expire key

**Files:**
- `services/registry-api/routers/memory.py` (add Redis read-through)
- `charts/agentshield/values.yaml` (Redis config)
- `sdk/agentshield_sdk/` (memory client if it exists)

**Priority:** Low — PG is fine for current scale. Implement when latency matters.

**Parity:** the read-through cache wraps the memory read path used by **both** sandbox and production runs — one code path, no fork.
**Golden-path:** integration test — a warm Redis hit and a cold PG miss both return the same message history for a thread.

---

### TODO-7: Sandbox Run TTL / Auto-Cancel (Playground doc T-11)

➡️ **Moved to `execution-models-v2-e2e.md` WS-6** (2026-07-12) — shares the timeout worker with the production run-timeout (`approval_timeout_worker.py`, one worker parameterized by scope). Full detail in WS-6.

---

## Current UI Bug: Browser Cache

➡️ **Moved to `execution-models-v2-e2e.md` WS-6** (2026-07-12) — the `window.__STUDIO_BUILD` cache-bust marker + "ensure every Studio image bump carries a unique hash" prevention is tracked there. (Immediate workaround for a stale bundle: hard refresh — Cmd+Shift+R / Ctrl+Shift+R.)

---

## Build Priority Order

Remaining in this doc (the 5 moved items — TODO-1, TODO-3, TODO-4, TODO-7, Browser Cache — are now in `execution-models-v2-e2e.md` WS-6):

```
TODO-2 (Alerting)            — production safety net, 1-2 days
TODO-5 (Role-based access)   — privacy/compliance, 2 days (ship role producer with the gate)
TODO-6 (Redis hot path)      — performance, defer until needed
```

Every item above must clear the **Acceptance bar** (top of this doc) — golden-path e2e per environment + shared-code parity — before it counts as done.

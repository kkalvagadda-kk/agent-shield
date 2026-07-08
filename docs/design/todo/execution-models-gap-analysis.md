# Execution Models — Gap Analysis & Remaining Work

**Date:** 2026-07-08  
**Author:** Karthik + Claude  
**Sources:** `execution-models-and-memory.md`, `playground-execution-modes.md`, `execution-modes-production.md`

---

## Summary

The three design docs define a comprehensive execution models system. Most of the backend + playground surface is built. The primary gaps are in the **production operate surface** and **cross-cutting platform features** (alerting, role-based access, auto-eval-gate).

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

**Design says:** Nav-level inbox with badge count of pending approvals across all agents for the reviewer's teams. Authority-checked (`agent:reviewer`).

**Current state:** `Approvals` link in Sidebar is a plain nav link — no badge, no pending count, no real-time indicator.

**What to implement:**
- Add a `useQuery` in `Sidebar.tsx` that polls `GET /api/v1/approvals?status=pending&limit=0` (return count only)
- Show a red badge circle with the count next to "Approvals" nav item
- Refetch every 30s
- Backend: add a `GET /api/v1/approvals/count?status=pending` lightweight endpoint if the list endpoint is too heavy

**Files:** `studio/src/components/Sidebar.tsx`, potentially `services/registry-api/routers/approvals.py`

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

---

### TODO-3: Auto-set `eval_passed` from Passing EvalRun (T-4)

**Design says:** A passing batch eval should auto-flip `eval_passed=true` on the agent version, removing the manual rubber stamp that currently gates publish.

**Current state:** `eval_passed` is set only via manual `PATCH /api/v1/agents/{name}/versions/{id}`. Batch eval runs and produces scores but doesn't auto-promote.

**What to implement:**

1. In `services/eval-runner/` (or the eval completion handler in registry-api), after all eval_run_results are scored:
   - Compute pass/fail: `overall_score >= threshold` (threshold = 0.7 default, configurable per agent)
   - If pass: `PATCH /api/v1/agents/{name}/versions/{version_id}` with `eval_passed=true`
   - Write a `notes` field: "Auto-promoted by eval run {eval_run_id}, score={score}"
2. Add `eval_threshold FLOAT DEFAULT 0.7` to agent settings (or `metadata_` JSONB)
3. Studio: show "Auto-promoted" badge on versions that were eval-gated

**Files:**
- `services/registry-api/routers/eval_runner.py` (completion handler)
- `services/registry-api/models.py` (optional: threshold setting)
- `studio/src/components/agent-detail/VersionsTab.tsx` (badge)

---

### TODO-4: CatalogDetailPage — Reuse Mode-Aware Overviews (Gap #3)

**Design says:** One Agent Detail page with mode-aware Overview (reactive: latency/error/endpoint; durable: active runs + step tracker; scheduled: schedule health + next fires; event: match rate + event log).

**Current state:** Two separate systems:
- `AgentDetailPage` at `/agents/:name` — has mode-aware Overviews (`OverviewReactive`, `OverviewDurable`, `OverviewScheduled`, `OverviewEventDriven`)
- `CatalogDetailPage` at `/catalog/:artifactId` — production-focused but uses its own ad-hoc overview (metrics cards + deployment card + endpoints)

The existing mode-aware Overviews (built for AgentDetailPage) read from sandbox `agent_runs`. They need a production equivalent that reads from catalog/production runs.

**What to implement:**

Option A (recommended): Make the CatalogDetailPage overview render the SAME mode-aware Overview components but pointed at production data:
- `OverviewReactive` → uses `getCatalogStats()` (already built) + production runs
- `OverviewDurable` → shows active production runs with step tracker
- `OverviewScheduled` → shows schedule health from `agent_triggers`
- `OverviewEventDriven` → shows event log from `agent_events`

This means parameterizing the existing Overview components to accept either sandbox or production data source.

Option B: Keep CatalogDetailPage as the production-only view and accept the split. The current metrics + deployment card + Production Chat card is close enough for reactive agents. Build durable/scheduled/event overviews when those agent types are actually published to production.

**Files:**
- `studio/src/components/agent-detail/OverviewReactive.tsx` (parameterize data source)
- `studio/src/pages/CatalogDetailPage.tsx` (render mode-aware overview)
- `studio/src/api/catalogApi.ts` (may need catalog-scoped runs/events/schedule endpoints)

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

---

### TODO-7: Sandbox Run TTL / Auto-Cancel (Playground doc T-11)

**Design says:** Durable sandbox runs auto-cancel after configurable wall-clock TTL (default 10 min). Reuses `approval_timeout_worker.py` pattern.

**Current state:** No TTL enforcement. A stuck durable run in sandbox hangs indefinitely.

**What to implement:**

1. Add a background task (or extend existing `approval_timeout_worker.py`) that:
   - Queries `playground_runs WHERE status IN ('running', 'awaiting_approval') AND started_at < NOW() - interval '10 min'`
   - Sets `status = 'cancelled'`, writes `error_message = 'Auto-cancelled: exceeded sandbox TTL'`
2. Make TTL configurable per agent (in `metadata_` JSONB or a new column)
3. Studio: show "Cancelled (timeout)" status in playground

**Files:**
- `services/registry-api/` — background worker or cron endpoint
- `services/registry-api/models.py` (if adding config)

**Priority:** Low — only matters when durable agents are actively tested in playground.

---

## Current UI Bug: Browser Cache

**Symptom:** User sees old "API Endpoints" card instead of new "Production Chat" + "Internal API (cluster only)" cards.

**Root cause:** Browser caching old JS bundle. The deployed Studio 0.1.73 bundle (`index-Dktl29Bc.js`) correctly contains "Production Chat" and has NO "API Endpoints" text. Verified via:
```
kubectl exec deploy/agentshield-studio -- grep -c "Production Chat" /usr/share/nginx/html/assets/index-Dktl29Bc.js
# Output: 1
kubectl exec deploy/agentshield-studio -- grep -c "API Endpoints" /usr/share/nginx/html/assets/index-Dktl29Bc.js  
# Output: 0
```

**Fix:** Hard refresh the browser (Cmd+Shift+R on Mac, Ctrl+Shift+R on Windows/Linux).

**Prevention:** The nginx config serves `/assets/` with `Cache-Control: public, immutable` and `expires 1y`. When Vite produces the same content hash across builds, the browser never re-fetches. We now include a `window.__STUDIO_BUILD` marker in `main.tsx` to force unique hashes on every build.

---

## Build Priority Order

```
TODO-1 (Approvals badge)     — small, high visibility, half-day
TODO-3 (Auto eval_passed)    — closes the publish automation loop, 1 day  
TODO-4 (Mode-aware catalog)  — aligns production UX with design, 2 days
TODO-2 (Alerting)            — production safety net, 1-2 days
TODO-5 (Role-based access)   — privacy/compliance, 2 days
TODO-6 (Redis hot path)      — performance, defer until needed
TODO-7 (Sandbox TTL)         — safety net, defer until durable agents tested
```

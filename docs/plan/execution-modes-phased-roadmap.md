# Execution Modes — Phased Implementation Roadmap

**Status:** DRAFT for review
**Date:** 2026-07-04
**Covers:** All phases from current state → full playground + production per `docs/design/playground-execution-modes.md` and `docs/design/execution-modes-production.md`
**Depends on:** Phase 0 (DONE — eval loop, sandbox env, auto eval_passed)

---

## Current State (after Phase 0)

| Layer | What exists | What's missing |
|-------|-------------|----------------|
| **Data model** | `agents` (no `execution_shape`), `agent_runs` (minimal — no `trigger_type`, `parent_run_id`, `run_by`, `team`), `playground_runs`, `eval_runs` | `run_steps`, `agent_schedules`, `agent_triggers`, `agent_events`, `agent_memory`; `execution_shape` + `memory_enabled` columns |
| **Backend services** | registry-api, declarative-runner, deploy-controller, safety-orchestrator, eval-runner, python-executor | scheduler, event-gateway, run-executor (durable extension) |
| **Studio UX** | Playground = reactive chat only (`ChatPane` + `HitlPanel` + `TracePanel`); consumer chat (`AgentChatPage`); admin approvals page | Mode-aware `InteractionSurface` (durable/scheduled/event); production Agent Detail (runs, memory, approvals inbox); overview per mode |
| **Governance** | OPA sidecar, per-tool risk, HITL approvals (playground self-approve), PII tokenization | Production authority-checked approvals, Global Approvals Inbox, memory PII rule enforcement |

---

## Phase Summary

| Phase | Name | Delivers | Key dependency |
|-------|------|----------|----------------|
| **1** | Foundation — Data Model + Shape | `execution_shape`, `memory_enabled`, enhanced `agent_runs`, `run_steps`, `agent_triggers` tables; backend CRUD; Studio shape selector | Phase 0 |
| **2** | Durable Playground | RunLauncher + StepTracker + step SSE; sandbox durable runs with approval flow; auto-cancel TTL | Phase 1 |
| **3** | Scheduled + Event Playground | RunNowPanel, TestTriggerPanel, filter logic, internal test-event endpoint | Phase 1 |
| **4** | Production Runs + Agent Detail | Merged `agent_runs` with trigger/orchestration fields; Runs tab; reactive Overview with metrics; production run flow via chat.py/declarative-runner | Phase 1 |
| **5** | Durable Production + Global Approvals | Run-executor (durable steps in production), Global Approvals Inbox, authority-checked reviews, approval SLA/timeout | Phase 2, Phase 4 |
| **6** | Memory | `agent_memory` + Redis hot path + pgvector cold path; PII rule; Memory tab in Agent Detail | Phase 4 |
| **7** | Scheduler Service | `services/scheduler/`, `agent_schedules`, `/internal/runs/start`, scheduler HA, cron-fires-run flow | Phase 4 |
| **8** | Alerting + Observability | Email-on-failure for scheduled/event; per-mode health signals; observability dashboards | Phase 7 |
| **9** | Event Gateway | `services/event-gateway/`, public webhook ingress, token validation, rate limiting, replay protection, filter matching, threat model | Phase 7 |

```
Phase 0 (DONE)
    │
    ▼
Phase 1 ─── Foundation (data model + shape)
    │
    ├──────────────────┐
    ▼                  ▼
Phase 2              Phase 3              Phase 4
Durable playground   Scheduled + Event    Production runs +
                     playground           Agent Detail
    │                                        │
    └────────────┬───────────────────────────┘
                 ▼
             Phase 5
             Durable production +
             Global Approvals Inbox
                 │
                 ├────────────┐
                 ▼            ▼
             Phase 6      Phase 7
             Memory       Scheduler service
                              │
                              ├──────────┐
                              ▼          ▼
                          Phase 8    Phase 9
                          Alerting   Event Gateway
                                     (LAST — threat model)
```

---

## Phase 1 — Foundation: Data Model + Execution Shape

**Goal:** Lay the schema foundation that all subsequent phases build on. After this phase, agents carry an `execution_shape` and optional triggers, the `agent_runs` table has the fields production needs, and `run_steps` exists for durable tracking.

### Migrations (0016–0019)

**0016 — `execution_shape` + `memory_enabled` on agents:**
```sql
ALTER TABLE agents ADD COLUMN execution_shape VARCHAR(16) NOT NULL DEFAULT 'reactive'
  CHECK (execution_shape IN ('reactive', 'durable'));
ALTER TABLE agents ADD COLUMN memory_enabled BOOLEAN NOT NULL DEFAULT false;
```

**0017 — Enhance `agent_runs`:**
```sql
ALTER TABLE agent_runs
  ADD COLUMN trigger_type VARCHAR(16) DEFAULT 'manual'
    CHECK (trigger_type IN ('manual', 'api', 'schedule', 'webhook')),
  ADD COLUMN run_by VARCHAR(255),        -- user_id or serviceaccount:*
  ADD COLUMN team VARCHAR(100),
  ADD COLUMN thread_id VARCHAR(255),     -- {team}:{agent}:{user}:{uuid}
  ADD COLUMN parent_run_id UUID REFERENCES agent_runs(id),
  ADD COLUMN schedule_id UUID,           -- FK added in Phase 7
  ADD COLUMN trigger_id UUID,            -- FK added in Phase 9
  ADD COLUMN trigger_payload JSONB;
```

**0018 — `run_steps`:**
```sql
CREATE TABLE run_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  step_number INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'awaiting_approval', 'cancelled')),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  output JSONB,
  approval_id UUID REFERENCES approvals(id),
  error_message TEXT,
  UNIQUE(run_id, step_number)
);
CREATE INDEX idx_run_steps_run_id ON run_steps(run_id);
```

**0019 — `agent_triggers`:**
```sql
CREATE TABLE agent_triggers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  trigger_type VARCHAR(16) NOT NULL CHECK (trigger_type IN ('schedule', 'webhook')),
  -- schedule fields
  cron_expression VARCHAR(100),
  timezone VARCHAR(50) DEFAULT 'UTC',
  enabled BOOLEAN NOT NULL DEFAULT true,
  -- webhook fields
  token_hash VARCHAR(128),
  filter_conditions JSONB,
  -- shared
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_triggers_agent ON agent_triggers(agent_id);
```

### Backend Changes

- **models.py** — Add `execution_shape`, `memory_enabled` to `Agent`; add `trigger_type`, `run_by`, `team`, `thread_id`, `parent_run_id`, `trigger_payload` to `AgentRun`; add `RunStep` model; add `AgentTrigger` model.
- **schemas.py** — Add fields to `AgentCreate`/`AgentResponse`; add `RunStepResponse`; add `AgentTriggerCreate`/`AgentTriggerResponse`; extend `AgentRunResponse`.
- **routers/agents.py** — Accept `execution_shape` on create/update; return it in responses.
- **routers/agent_runs.py** — Accept new fields; add `GET /agent-runs/{id}/steps` for step listing.
- **New router: routers/triggers.py** — CRUD for `agent_triggers` (`POST/GET/PATCH/DELETE /api/v1/agents/{name}/triggers`).
- **bundle_generator.py** — Include `execution_shape` in bundle data so OPA can policy on it.

### Studio Changes

- **CreateAgentPage.tsx** — Add "Execution Shape" selector (radio: Reactive / Durable) in the create form.
- **AgentDetailPage.tsx** — Show `execution_shape` badge.
- **registryApi.ts** — Add `execution_shape`, `memory_enabled` to `Agent` type; add trigger API calls.

### E2E Coverage

- **suite-19-execution-shape.sh** — T-S19-001: create agent with `execution_shape=durable` → stored; T-S19-002: default is `reactive`; T-S19-003: create trigger (schedule type); T-S19-004: create trigger (webhook type); T-S19-005: list triggers.

### Image bumps

- `registry-api`: 0.2.38 → 0.2.39 (two bumps: migrations + router)
- `studio`: 0.1.33

---

## Phase 2 — Durable Playground

**Goal:** A developer can evaluate a durable agent in the playground: launch a test run with a payload, watch steps execute in real time via SSE, approve/deny at HITL checkpoints, and judge the final output. Reuses the same SSE contract that production will use.

### Backend

- **Extend `POST /playground/runs`** — Accept `execution_shape=durable` + `input_payload` (JSON). When durable: create an `agent_run` + dispatch to the agent pod's `/run` endpoint (new).
- **New SSE events** — `step_update` (step_number, name, status, output), `approval_required` (step_number, tool, risk, args, approval_id). Sent on the existing `/runs/{id}/stream` channel.
- **Declarative-runner extension** — New `POST /run` endpoint that executes workflow steps sequentially, emitting `step_update` events via callback to registry-api; pauses on HITL-required steps.
- **`POST /playground/approvals/{id}/decide`** — Already exists (HitlPanel uses it). Wire the approval decision back to the runner to resume the step.
- **Auto-cancel TTL** — Background task (reuse `approval_timeout_worker.py` pattern): cancel durable playground runs exceeding 10 min wall-clock or `awaiting_approval` past the limit.

### Studio

- **New: `RunLauncher.tsx`** — Input payload form + "Launch Run" button. Creates a durable playground run.
- **New: `StepTracker.tsx`** — Subscribes to `/runs/{id}/stream` SSE. Renders step list (pending/running/done/failed/awaiting_approval). Clicking a step shows its detail (output or approval card).
- **New: `InteractionSurface.tsx`** — Dispatcher: if `execution_shape === 'reactive'` → `<ChatPane>`, else → `<RunLauncher>` + `<StepTracker>`.
- **PlaygroundPage.tsx** — Replace hardcoded `<ChatPane>` with `<InteractionSurface>`.
- **HitlPanel.tsx** — Already works. Triggered by `approval_required` SSE event (same as today).

### E2E Coverage

- **suite-20-durable-playground.sh** — T-S20-001: create durable agent + deploy to sandbox; T-S20-002: POST /playground/runs with input_payload → run_id returned; T-S20-003: SSE stream emits step_update events; T-S20-004: HITL step pauses run (status=awaiting_approval); T-S20-005: approve → run resumes + completes; T-S20-006: auto-cancel after TTL.

### Image bumps

- `registry-api`: 0.2.40
- `declarative-runner`: 0.1.3
- `studio`: 0.1.34

---

## Phase 3 — Scheduled + Event-Driven Playground

**Goal:** Developers can test-fire scheduled agents ("Run Now") and event-driven agents ("Send Test Event" with a sample payload through the real filter logic) in the playground without waiting for cron or wiring up ingress.

### Backend

- **"Run Now" for scheduled agents** — No new endpoint needed; `POST /playground/runs` with `trigger_type=manual` is already the mechanism. The Studio just frames it as "Run Now (test-fire)".
- **New: `POST /api/v1/playground/test-event`** — Accepts `{agent_name, payload}`. Evaluates the agent's `filter_conditions` (from `agent_triggers`) against the payload. If matched → creates a playground run with `trigger_type=webhook`, `trigger_payload=payload`. If filtered → returns `{matched: false, reason: "..."}` (no run created).
- **Trigger config read endpoints** — `GET /api/v1/agents/{name}/triggers` (Phase 1) already exists. Playground reads it to show the schedule preview / filter config.

### Studio

- **New: `RunNowPanel.tsx`** — Shows cron expression + human-readable parse + next-3-fires preview (client-side `cronstrue` library). "Run Now (test-fire)" button → `POST /playground/runs`. Test-run history table (filtered playground_runs for this agent).
- **New: `TestTriggerPanel.tsx`** — Filter config display (read-only from trigger). JSON payload editor (Monaco). "Send Test Event" button → `POST /playground/test-event`. Event log: matched (→ run link) or filtered (→ reason). Prod webhook URL shown as preview-only (greyed, not active).
- **InteractionSurface.tsx** — Extend: if trigger includes `schedule` → `<RunNowPanel>`; if trigger includes `webhook` → `<TestTriggerPanel>`.

### E2E Coverage

- **suite-21-scheduled-playground.sh** — T-S21-001: create scheduled agent + trigger (cron); T-S21-002: "Run Now" via POST /playground/runs → completes + judged; T-S21-003: multiple test-fires appear in history.
- **suite-22-event-playground.sh** — T-S22-001: create event-driven agent + trigger (webhook + filter); T-S22-002: test-event with matching payload → run created; T-S22-003: test-event with non-matching payload → `{matched: false, reason}`, no run; T-S22-004: matched run is judged.

### Image bumps

- `registry-api`: 0.2.41
- `studio`: 0.1.35

---

## Phase 4 — Production Runs + Agent Detail

**Goal:** Published agents produce `agent_runs` rows on every production invocation. Studio gets an Agent Detail page with tabs: Overview, Runs, Versions, Settings. The reactive Overview shows endpoint + metrics (latency, error rate, cost).

### Backend

- **chat.py / declarative-runner** — On every production `/chat` or `/chat/stream` call: create an `agent_run` row (`trigger_type=api`, `context=production`, `run_by=user_id`, `team=agent.team`). On completion: update with `cost_usd`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `status`.
- **Extend `GET /api/v1/agent-runs`** — Filter by `agent_name`, `trigger_type`, `context`, `team`, `status`, `date range`. Pagination. Non-admins see only their `user_id` runs; reviewers/admins see team's.
- **New: `GET /api/v1/agents/{name}/stats`** — Returns last-24h aggregates: run_count, p50/p95 latency, error_rate, total_cost. Powers the reactive Overview.

### Studio

- **Refactor: Agent Detail tabs** — `AgentDetailPage.tsx` becomes a tabbed shell: Overview, Runs, Versions, Settings.
- **New: `RunsTab.tsx`** — Filterable table of `agent_runs`: trigger_type, status, duration, cost, run_by, trace link.
- **New: `OverviewReactive.tsx`** — Endpoint card (copy URL), stats sparkline (runs/hour), recent runs mini-table, error highlights.
- **registryApi.ts** — Add `getAgentStats`, `listAgentRuns` with filters.

### E2E Coverage

- **suite-23-production-runs.sh** — T-S23-001: invoke published agent via /chat → agent_run row created with context=production; T-S23-002: GET /agent-runs?agent_name=X returns the run; T-S23-003: GET /agents/X/stats returns metrics; T-S23-004: non-owner cannot see other team's runs.

### Image bumps

- `registry-api`: 0.2.42
- `declarative-runner`: 0.1.4 (writes agent_run on invoke)
- `studio`: 0.1.36

---

## Phase 5 — Durable Production + Global Approvals Inbox

**Goal:** Published durable agents run multi-step jobs in production. Approvals go to a Global Approvals Inbox with authority checks (not sandbox self-approve). Runs survive pod restarts via checkpointing.

### Backend

- **Run-executor extension** — Extend declarative-runner to: (a) write `run_steps` on each step transition; (b) pause on approval-required steps → create an `approval` row → emit SSE; (c) checkpoint state to Postgres between steps (survive restart); (d) resume from checkpoint on pod restart.
- **Authority-checked approvals** — `POST /api/v1/approvals/{id}/decide` gains a real authority check: caller must hold `agent:reviewer` for the agent's team (not self-approve). Sandbox context retains self-approve.
- **Global Approvals Inbox endpoint** — `GET /api/v1/approvals?status=pending&team=<reviewer's teams>`. Returns pending approvals across all agents the reviewer can act on. Each item includes: tool, risk, args, anonymized thread context (PII-tokenized memory), SLA remaining.
- **Approval SLA/timeout** — Extend `approval_timeout_worker.py`: approvals past their TTL (configurable per agent, default 2h) auto-cancel the step → run status `cancelled` or escalate.

### Studio

- **New: `ApprovalsInboxPage.tsx`** — Nav-level page with badge count. Filterable list of pending approvals. Each card shows: agent name, step name, tool + risk + args, thread context (anonymized), time remaining, Approve/Deny/Edit&Approve buttons.
- **New: `OverviewDurable.tsx`** — Active runs list (status, step progress), "New Run" button, failed/awaiting counts.
- **Top nav** — Add "Approvals (n)" badge item.

### E2E Coverage

- **suite-24-durable-production.sh** — T-S24-001: launch production durable run → steps progress; T-S24-002: step awaiting_approval appears in /approvals endpoint; T-S24-003: reviewer approves → run resumes; T-S24-004: non-reviewer gets 403 on approve; T-S24-005: approval timeout → run cancelled.

### Image bumps

- `registry-api`: 0.2.43
- `declarative-runner`: 0.1.5
- `studio`: 0.1.37

---

## Phase 6 — Memory

**Goal:** Agents can remember across sessions. Memory stores tokenized content only (PII rule). Hot path via Redis, cold path via pgvector for semantic retrieval. Memory tab in Agent Detail shows sessions + facts.

### Backend

- **Migration 0020 — `agent_memory`:**
```sql
CREATE TABLE agent_memory (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_name VARCHAR(100) NOT NULL,
  team VARCHAR(100) NOT NULL,
  thread_id VARCHAR(255) NOT NULL,
  user_id VARCHAR(255),
  role VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
  content TEXT NOT NULL,           -- TOKENIZED, never raw PII
  content_embedding VECTOR(1536),  -- pgvector
  message_index INT NOT NULL,
  session_id VARCHAR(255),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ           -- optional TTL
);
CREATE INDEX idx_agent_memory_thread ON agent_memory(thread_id, message_index);
CREATE INDEX idx_agent_memory_agent ON agent_memory(agent_name, team);
```
- **Memory service layer** — `services/registry-api/memory.py`:
  - `save_turn(agent_name, thread_id, messages)` — PII-tokenize via safety-orchestrator → store in Postgres + Redis.
  - `load_context(agent_name, thread_id, window=20)` — Redis hot path (recent N); pgvector semantic search for relevant older facts.
  - `summarize_old(thread_id, threshold=50)` — Compress messages beyond window into a summary fact.
- **PII rule enforcement** — All memory writes pass through safety-orchestrator's PII scanner. `content` column stores tokenized form only. Raw PII lives in `pii_mappings` (session-scoped, TTL'd).
- **Redis deployment** — Add Redis to Helm chart (or use existing if Langfuse brought one). `REDIS_URL` env var on registry-api.
- **pgvector extension** — `CREATE EXTENSION IF NOT EXISTS vector` in migration.
- **Memory endpoints** — `GET /api/v1/agents/{name}/memory?thread_id=...` (paginated); `DELETE /api/v1/agents/{name}/memory/{thread_id}` (GDPR delete).
- **Declarative-runner integration** — On run start: load context from memory → inject into LLM prompt. On run end: save the turn to memory.

### Studio

- **New: `MemoryTab.tsx`** — Sessions list (grouped by thread_id); click → message timeline (content is tokenized, safe to display). Facts/summaries section. Usage stats (messages stored, sessions).

### E2E Coverage

- **suite-25-memory.sh** — T-S25-001: invoke agent twice in same thread → memory loaded on second call; T-S25-002: memory content is PII-tokenized (no raw PII in DB); T-S25-003: memory endpoint returns messages; T-S25-004: delete thread removes all rows; T-S25-005: agent with memory_enabled=false stores nothing.

### Image bumps

- `registry-api`: 0.2.44
- `declarative-runner`: 0.1.6
- `studio`: 0.1.38

---

## Phase 7 — Scheduler Service

**Goal:** A standalone scheduler service fires published scheduled agents on their cron expression. HA with 2 replicas + distributed lock. Fires via internal API, creates `agent_runs` with `trigger_type=schedule`.

### New service: `services/scheduler/`

- **Tech:** Python + APScheduler + Postgres advisory lock (HA).
- **Startup:** Query `agent_triggers WHERE trigger_type='schedule' AND enabled=true`. Register each as an APScheduler job.
- **On tick:** `POST /api/v1/internal/runs/start` (cluster-internal) with `{agent_name, trigger_type: 'schedule', trigger_id, run_by: 'serviceaccount:scheduler'}`.
- **HA:** 2 replicas. Before each fire: acquire a Postgres advisory lock keyed on `trigger_id + fire_time`. Only one replica fires. Lock released after dispatch.
- **Reload:** Poll `agent_triggers` every 60s for changes (new/updated/disabled schedules). Or: registry-api sends a Postgres NOTIFY on trigger change → scheduler listens.

### Backend (registry-api)

- **New: `POST /api/v1/internal/runs/start`** — Cluster-internal (no public ingress). Creates an `agent_run`, dispatches to the agent pod. Accepts `trigger_type`, `trigger_id`, `run_by`, `agent_name`.
- **`agent_schedules` view** (optional) — or just use `agent_triggers WHERE trigger_type='schedule'` directly.
- **Deploy-controller hook** — On deploy of a scheduled agent: ensure the trigger exists in `agent_triggers` (created by user via CRUD). No auto-registration needed if triggers are user-managed.

### Studio

- **New: `OverviewScheduled.tsx`** — Schedule card (cron expression, human-readable, next fires, enabled toggle), last-run status, run history, alert config.
- **Settings tab** — Trigger management (edit cron, enable/disable).

### Helm

- Add `services/scheduler` Deployment (2 replicas), ServiceAccount, RBAC to access registry-api internal endpoint.

### E2E Coverage

- **suite-26-scheduler.sh** — T-S26-001: create scheduled agent + trigger → appears in scheduler's job list; T-S26-002: wait for cron fire → agent_run created with trigger_type=schedule; T-S26-003: disable trigger → no more fires; T-S26-004: HA — kill one scheduler pod → other pod picks up.

### Image bumps

- `registry-api`: 0.2.45
- `scheduler`: 0.1.0 (new)
- `studio`: 0.1.39

---

## Phase 8 — Alerting + Observability

**Goal:** Scheduled and event-driven agents alert on failure. Email transport first. Per-mode health signals surfaced in the Overview tab.

### Backend

- **Alert config** — `agent_triggers` gains: `alert_email VARCHAR(255)`, `alert_on_failure BOOLEAN DEFAULT true`.
- **Alert dispatch** — After a scheduled/event run completes with `status=failed`: send email to `alert_email`. Use a simple SMTP client (or SendGrid/SES integration, configurable via env).
- **Observability endpoints** — `GET /api/v1/agents/{name}/health` returns mode-appropriate signals:
  - reactive: p95_latency, error_rate, runs_24h, cost_24h
  - durable: awaiting_approval_count, failed_24h, avg_duration
  - scheduled: last_run_status, last_run_at, next_fire_at, missed_fires
  - event-driven: match_rate_24h, rejected_count_24h, last_event_at

### Studio

- **Alert config in Settings tab** — Email field + toggle per trigger.
- **Overview per mode** — `OverviewScheduled` shows alert status + last-run; `OverviewEventDriven` shows match/reject rates.
- **Health badges** — Agent list page shows a health dot (green/yellow/red) based on the `/health` endpoint.

### E2E Coverage

- **suite-27-alerting.sh** — T-S27-001: configure alert_email on trigger; T-S27-002: failed scheduled run → alert dispatched (mock SMTP or check sent log); T-S27-003: successful run → no alert; T-S27-004: health endpoint returns correct signals.

### Image bumps

- `registry-api`: 0.2.46
- `studio`: 0.1.40

---

## Phase 9 — Event Gateway (LAST)

**Goal:** Public webhook ingress for event-driven agents. Validates tokens, rate-limits, filters, dispatches. This is the biggest attack surface — requires a threat model before implementation.

### Pre-requisite: Threat Model

Before building, document and review:
- Token validation (SHA-256 hash comparison)
- Rate limiting (per-agent, per-source-IP)
- Replay protection (nonce or timestamp-based)
- Payload sanitization (pass through safety-orchestrator before agent context)
- DDoS surface (public endpoint; CDN/WAF consideration)

### New service: `services/event-gateway/`

- **Tech:** Python FastAPI, lightweight (stateless except token lookup).
- **Public endpoint:** `POST /hooks/{agent_name}/{token}` — accepts any JSON body.
- **Flow:**
  1. Token validation: `sha256(token) == agent_triggers.token_hash` → 401 if mismatch.
  2. Rate limit check: per-agent sliding window (Redis counter) → 429 if exceeded.
  3. Filter evaluation: `filter_conditions` (from `agent_triggers`) applied to payload → if no match, log as filtered, return 202.
  4. If matched: `POST /api/v1/internal/runs/start` with `{agent_name, trigger_type: 'webhook', trigger_id, trigger_payload, run_by: 'serviceaccount:webhook:{trigger_id}'}`.
  5. Log event in `agent_events` (matched/filtered/rejected).
- **Migration 0021 — `agent_events`:**
```sql
CREATE TABLE agent_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trigger_id UUID NOT NULL REFERENCES agent_triggers(id),
  agent_name VARCHAR(100) NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('matched', 'filtered', 'rejected')),
  filter_reason TEXT,
  payload JSONB,
  run_id UUID REFERENCES agent_runs(id),
  source_ip INET,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_events_trigger ON agent_events(trigger_id, received_at DESC);
```

### Backend (registry-api)

- **Token management** — `POST /api/v1/agents/{name}/triggers/{id}/rotate-token` → generate new token, store hash, return plaintext once. Old hash invalidated immediately.
- **Event log endpoint** — `GET /api/v1/agents/{name}/events?trigger_id=...` — paginated, filterable by status.

### Studio

- **New: `OverviewEventDriven.tsx`** — Webhook URL (masked token, copy button), "Rotate Token" button, filter display, last-event timestamp, event log (matched/filtered/rejected), match rate chart.
- **Settings** — Token rotation, filter editor.

### Helm / Ingress

- Event gateway gets a public `Ingress` on `/hooks/*` (distinct from the platform API). Rate-limit annotations. TLS.

### E2E Coverage

- **suite-28-event-gateway.sh** — T-S28-001: POST /hooks/agent/valid-token with matching payload → run created; T-S28-002: invalid token → 401; T-S28-003: valid token but non-matching filter → 202, logged as filtered; T-S28-004: rate limit exceeded → 429; T-S28-005: rotate token → old token rejected, new token works; T-S28-006: event log shows all three statuses.

### Image bumps

- `registry-api`: 0.2.47
- `event-gateway`: 0.1.0 (new)
- `studio`: 0.1.41

---

## Cross-Cutting Concerns (apply across multiple phases)

### Multi-tenancy (woven into Phases 1, 4, 5)

- `team` column on `agent_runs`, `run_steps`, `agent_memory`, `agent_events`.
- `thread_id` format: `{team}:{agent}:{user_id}:{uuid}`.
- Application-layer enforcement on every query: filter by team from JWT claims.
- Roles: `agent:user` (own runs), `agent:reviewer` (approve for team), `agent:admin` (all team data), `platform:admin` (cross-team).

### Workflow (composite executable) support (woven into Phases 2, 4, 5)

- `parent_run_id` on `agent_runs` enables run trees.
- StepTracker renders agent-tree zoom for workflow runs (expand to see child agent steps).
- Global Approvals Inbox shows inter-agent approvals from workflow sub-runs.
- No separate surface — same tabs, same overview, same triggers.

### Deploy-controller OPA fix (pre-req, can be done anytime)

- **Separate from this roadmap but blocks suite-2/7**: Deploy-controller 0.1.7 injects OPA with `--bundle=/policies/` instead of `--config-file=/config/opa-config.yaml`. Needs 0.1.8. Not gating any phase here (tests use existing working pods + team grants), but should be fixed.

---

## Estimated Effort Per Phase

| Phase | Backend | Frontend | New Service | Est. Complexity |
|-------|---------|----------|-------------|-----------------|
| 1 | 4 migrations + 2 routers + model updates | Shape selector + badge | — | Medium |
| 2 | SSE extension + runner /run endpoint + TTL worker | 3 new components + page refactor | — | High |
| 3 | 1 new endpoint (test-event) + filter logic | 2 new components | — | Medium |
| 4 | Run creation in chat path + stats endpoint + runs query | Tab refactor + 2 new components | — | Medium-High |
| 5 | Authority checks + SLA worker + checkpoint recovery | Approvals Inbox page + durable overview | — | High |
| 6 | Memory layer + Redis + pgvector + PII integration | Memory tab | — | High |
| 7 | Scheduler service + HA + internal run endpoint | Scheduled overview + settings | `services/scheduler/` | High |
| 8 | Alert dispatch + health endpoint | Alert config + health badges | — | Medium |
| 9 | Event gateway + token + rate limit + filter + events table | Event overview | `services/event-gateway/` | Very High |

---

## What This Roadmap Does NOT Cover

- **Canvas/graph editor** — Agent Graph authoring UX (renamed from "workflow"). Separate feature.
- **Semantic memory embeddings model choice** — Deferred (OQ-5 in backend spec). Phase 6 uses OpenAI `text-embedding-3-small` or Bedrock Titan by default.
- **Data residency / schema-per-tenant** — Deferred (OQ-4). Single Postgres for now.
- **Rich alerting transports** — Phase 8 ships email only. Slack/PagerDuty/webhook = future.
- **Automatic token rotation** — Phase 9 ships manual rotation. Auto-expiry = future.
- **Consumer-facing SDK** — TypeScript/Python SDK for calling agents programmatically. Separate feature.

# Execution Modes — Implementation Tasks

**Generated from:** `docs/plan/execution-modes-phased-roadmap.md`
**Spec context:** `docs/design/execution-models-and-memory.md`, `docs/design/playground-execution-modes.md`, `docs/design/execution-modes-production.md`
**Constitution:** `CLAUDE.md`
**Date:** 2026-07-04

---

**Total tasks:** 162 (153 implementation + 9 checkpoint)
**Phases:** 12 (9 implementation + 3 checkpoint gates)
**Parallel opportunities:** noted inline with [P]
**Checkpoint phases:** CP1 (after Phase 3), CP2 (after Phase 6), CP3 (after Phase 9)

---

## Summary

| # | Phase | Tasks | Key Deliverable |
|---|-------|-------|-----------------|
| 1 | Foundation: Data Model + Shape | 23 (T001-T023) | `execution_shape`, `memory_enabled`, `run_steps`, `agent_triggers` tables; shape selector in Studio |
| 2 | Durable Playground | 18 (T024-T041) | RunLauncher + StepTracker + SSE step events; durable playground runs with HITL |
| 3 | Scheduled + Event Playground | 14 (T042-T055) | RunNowPanel, TestTriggerPanel, filter engine, test-event endpoint |
| CP1 | Checkpoint 1 — Playground Complete | 3 (CP1a-CP1c) | All four playground modes work end-to-end in sandbox |
| 4 | Production Runs + Agent Detail | 15 (T056-T070) | agent_runs on every production call; Agent Detail tabs; stats endpoint |
| 5 | Durable Production + Global Approvals | 16 (T071-T086) | Run-executor steps; authority-checked approvals; Global Approvals Inbox |
| 6 | Memory | 17 (T087-T103) | agent_memory + Redis + pgvector; PII rule; Memory tab |
| CP2 | Checkpoint 2 — Production + Memory | 3 (CP2a-CP2c) | Production runs, durable approvals, and memory work end-to-end |
| 7 | Scheduler Service | 16 (T104-T119) | services/scheduler/ with HA; /internal/runs/start; scheduled agent overview |
| 8 | Alerting + Observability | 14 (T120-T133) | Email-on-failure; /health endpoint; health badges in Studio |
| 9 | Event Gateway | 20 (T134-T153) | services/event-gateway/; public /hooks/*; token + rate limit + filter |
| CP3 | Checkpoint 3 — Full Platform | 3 (CP3a-CP3c) | Scheduler fires, alerts dispatch, event gateway receives webhooks |

---

## Current State Reference

| Item | Value |
|------|-------|
| Last migration | `0015_deployments_env_add_sandbox.py` |
| Next migration | `0016` |
| Last e2e suite | `suite-18-opa-governance.sh` |
| Next e2e suite | `suite-19` |
| `REGISTRY_API_TAG` | `0.2.38` |
| `STUDIO_TAG` | `0.1.33` |
| `DECLARATIVE_RUNNER_TAG` | `0.1.2` |
| `DEPLOY_CONTROLLER_TAG` | `0.1.7` |
| `EVAL_RUNNER_TAG` | `0.1.1` |

---

## Phase 1 — Foundation: Data Model + Execution Shape

_Depends on: Phase 0 (DONE)_
_Goal: Lay the schema foundation. After this phase, agents carry `execution_shape` and optional triggers, `agent_runs` has orchestration fields, `run_steps` exists, and Studio shows the shape selector._

### Migrations (sequential — each extends the schema)

- [ ] [T001] Migration 0016: add `execution_shape` + `memory_enabled` columns to agents table — `services/registry-api/alembic/versions/0016_add_execution_shape_memory_flag.py`
- [ ] [T002] Migration 0017: ALTER `agent_runs` — add `trigger_type`, `run_by`, `team`, `thread_id`, `parent_run_id`, `schedule_id`, `trigger_id`, `trigger_payload`, `error_message`; widen `status` CHECK to include `queued`, `awaiting_approval`, `cancelled` — `services/registry-api/alembic/versions/0017_alter_agent_runs_orchestration.py`
- [ ] [T003] Migration 0018: CREATE TABLE `run_steps` (id, run_id FK, step_number, name, status, started_at, completed_at, output JSONB, approval_id FK, error_message; UNIQUE on run_id+step_number) — `services/registry-api/alembic/versions/0018_add_run_steps.py`
- [ ] [T004] Migration 0019: CREATE TABLE `agent_triggers` (id, agent_id FK, trigger_type CHECK schedule/webhook, cron_expression, timezone, enabled, token_hash, filter_conditions JSONB, created_at, updated_at) — `services/registry-api/alembic/versions/0019_add_agent_triggers.py`

### Models (parallel — each adds a distinct class or column set to models.py)

- [ ] [T005] [P] Agent model: add `execution_shape` (VARCHAR(16), CHECK reactive/durable, default reactive) and `memory_enabled` (BOOLEAN, default false) mapped columns — `services/registry-api/models.py`
- [ ] [T006] [P] AgentRun model: add `trigger_type`, `run_by`, `team`, `thread_id`, `parent_run_id`, `trigger_payload`, `error_message` mapped columns; widen `ck_agent_runs_status` to include `queued`, `awaiting_approval`, `cancelled` — `services/registry-api/models.py`
- [ ] [T007] [P] New RunStep ORM class: id, run_id (FK agent_runs), step_number, name, status (CHECK pending/running/completed/failed/awaiting_approval/cancelled), started_at, completed_at, output (JSONB), approval_id (FK approvals), error_message; UNIQUE(run_id, step_number); index on run_id — `services/registry-api/models.py`
- [ ] [T008] [P] New AgentTrigger ORM class: id, agent_id (FK agents), trigger_type (CHECK schedule/webhook), cron_expression, timezone, enabled, token_hash, filter_conditions (JSONB), created_at, updated_at; index on agent_id — `services/registry-api/models.py`

### Schemas (parallel — each adds distinct schema classes to schemas.py)

- [ ] [T009] [P] Agent schemas: add `execution_shape` (optional, default "reactive") to `AgentCreate`; add `execution_shape` and `memory_enabled` to `AgentUpdate` and `AgentResponse`; update `_remap_metadata` validator — `services/registry-api/schemas.py`
- [ ] [T010] [P] RunStep schemas: new `RunStepResponse` (id, run_id, step_number, name, status, started_at, completed_at, output, approval_id, error_message) — `services/registry-api/schemas.py`
- [ ] [T011] [P] AgentTrigger schemas: new `AgentTriggerCreate` (trigger_type, cron_expression, timezone, enabled, filter_conditions) and `AgentTriggerResponse` (all fields) — `services/registry-api/schemas.py`
- [ ] [T012] [P] AgentRun schemas: extend `AgentRunCreate` with `trigger_type`, `run_by`, `team`, `thread_id`; extend `AgentRunResponse` with `trigger_type`, `run_by`, `team`, `thread_id`, `parent_run_id`, `trigger_payload`, `error_message`; widen `status` Literal — `services/registry-api/schemas.py`

### Routers and services (sequential — depend on schemas)

- [ ] [T013] Update agents router: accept `execution_shape` on POST create and PATCH update; return in responses; validate CHECK constraint — `services/registry-api/routers/agents.py`
- [ ] [T014] Update agent_runs router: accept new orchestration fields on POST create; add `GET /api/v1/agent-runs/{run_id}/steps` endpoint returning list of RunStepResponse; add `trigger_type` and `team` query filters — `services/registry-api/routers/agent_runs.py`
- [ ] [T015] New triggers router: `POST/GET /api/v1/agents/{name}/triggers` (create trigger, list triggers); `GET/PATCH/DELETE /api/v1/agents/{name}/triggers/{id}` (read, update, delete single trigger) — `services/registry-api/routers/triggers.py`
- [ ] [T016] Update bundle_generator: include `execution_shape` in the agent data passed to OPA bundles so policies can branch on shape — `services/registry-api/bundle_generator.py`

### Studio (T017-T018 parallel; T019 depends on T018)

- [ ] [T017] [P] CreateAgentPage: add "Execution Shape" radio group (Reactive / Durable) in the create agent form; wire to `execution_shape` field on API call — `studio/src/pages/CreateAgentPage.tsx`
- [ ] [T018] [P] registryApi.ts: add `execution_shape` and `memory_enabled` to `Agent` TypeScript type; add `AgentTrigger` type; add `createTrigger`, `listTriggers`, `deleteTrigger` API functions — `studio/src/api/registryApi.ts`
- [ ] [T019] AgentDetailPage: show `execution_shape` badge (pill: "Reactive" or "Durable") and `memory_enabled` indicator in the agent header — `studio/src/pages/AgentDetailPage.tsx`

### Verification and deployment

- [ ] [T020] TypeScript type-check: run `cd studio && npx tsc --noEmit` and fix all errors — `studio/tsconfig.json`
- [ ] [T021] Write e2e suite-19: T-S19-001 create agent with `execution_shape=durable` (stored correctly); T-S19-002 default is `reactive`; T-S19-003 create trigger (schedule type); T-S19-004 create trigger (webhook type); T-S19-005 list triggers returns both — `scripts/e2e/suite-19-execution-shape.sh`
- [ ] [T022] Image bump: `REGISTRY_API_TAG="0.2.39"`, `STUDIO_TAG="0.1.34"` in deploy script; update comment header — `scripts/deploy-cpe2e.sh`
- [ ] [T023] Phase 1 verification gate: register suite-19 in run-all.sh; build images; deploy via `scripts/deploy-cpe2e.sh`; run `scripts/e2e/run-all.sh` and ensure all suites pass green — `scripts/e2e/run-all.sh`

---

## Phase 2 — Durable Playground

_Depends on: Phase 1 (T001-T023)_
_Goal: A developer can launch a durable agent test run in the playground, watch steps execute via SSE, approve/deny at HITL checkpoints, and judge the final output._

### Backend — schemas and router extensions

- [ ] [T024] Extend PlaygroundRunCreate schema: add optional `execution_shape` (default "reactive") and `input_payload` (dict, for durable runs); extend PlaygroundRun model with `execution_shape` and `input_payload` (JSONB) columns — `services/registry-api/schemas.py`
- [ ] [T025] Extend playground router: when `execution_shape=durable`, create an `agent_run` row + `playground_run` row; dispatch to the agent pod's `/run` endpoint (POST with input_payload); return `run_id` + `stream_url` — `services/registry-api/routers/playground.py`
- [ ] [T026] Add SSE events for durable runs on `/runs/{id}/stream`: emit `step_update` (step_number, name, status, output) and `approval_required` (step_number, tool, risk, args, approval_id) event types alongside existing text_delta events — `services/registry-api/routers/playground.py`

### Backend — declarative-runner extension

- [ ] [T027] Declarative-runner: new `POST /run` endpoint that accepts `{agent_name, input_payload, run_id, callback_url}` and starts sequential step execution — `services/declarative-runner/main.py`
- [ ] [T028] Step execution engine: execute workflow steps sequentially; emit `step_update` callbacks to registry-api on each step transition; pause on HITL-required steps and create approval row — `services/declarative-runner/run_executor.py`

### Backend — approval wiring and TTL

- [ ] [T029] Wire approval decision back to runner: when `POST /playground/approvals/{id}/decide` receives approve/deny, POST resume signal to the runner pod so the paused step proceeds or fails — `services/registry-api/routers/playground_approvals.py`
- [ ] [T030] Auto-cancel TTL for durable playground runs: extend `approval_timeout_worker.py` pattern to cancel durable runs exceeding 10-minute wall-clock or `awaiting_approval` past the configured limit — `services/registry-api/approval_timeout_worker.py`

### Studio — new components (T031-T032 parallel; T033-T035 sequential)

- [ ] [T031] [P] RunLauncher component: input payload form (JSON editor) + "Launch Run" button; calls `POST /playground/runs` with durable shape + payload — `studio/src/components/playground/RunLauncher.tsx`
- [ ] [T032] [P] StepTracker component: subscribes to `/runs/{id}/stream` SSE; renders step list with status icons (pending/running/completed/failed/awaiting_approval); clicking a step shows detail (output or approval card) — `studio/src/components/playground/StepTracker.tsx`
- [ ] [T033] InteractionSurface dispatcher: if `execution_shape === 'reactive'` render `<ChatPane>`; if `'durable'` render `<RunLauncher>` + `<StepTracker>` — `studio/src/components/playground/InteractionSurface.tsx`
- [ ] [T034] PlaygroundPage: replace hardcoded `<ChatPane>` with `<InteractionSurface>`; pass agent's `execution_shape` as prop — `studio/src/pages/PlaygroundPage.tsx`
- [ ] [T035] playgroundApi.ts: add `DurableRunRequest` type, `launchDurableRun()` function, step SSE event types (`StepUpdateEvent`, `ApprovalRequiredEvent`), step SSE subscription helper — `studio/src/api/playgroundApi.ts`

### Documentation and verification

- [ ] [T036] Update playground experience doc: document durable run launcher UX, step tracker states, approval flow in sandbox, auto-cancel TTL behavior — `docs/experience/playground.md`
- [ ] [T037] TypeScript type-check: run `cd studio && npx tsc --noEmit` and fix all errors — `studio/tsconfig.json`
- [ ] [T038] Write e2e suite-20: T-S20-001 create durable agent + deploy to sandbox; T-S20-002 POST /playground/runs with input_payload returns run_id; T-S20-003 SSE stream emits step_update events; T-S20-004 HITL step pauses run (status=awaiting_approval); T-S20-005 approve then run resumes + completes; T-S20-006 auto-cancel after TTL — `scripts/e2e/suite-20-durable-playground.sh`
- [ ] [T039] Image bump: `REGISTRY_API_TAG="0.2.40"`, `DECLARATIVE_RUNNER_TAG="0.1.3"`, `STUDIO_TAG="0.1.35"` — `scripts/deploy-cpe2e.sh`
- [ ] [T040] MANUAL browser verification: open playground with a durable agent; launch run; watch steps fill in; approve a HITL step; confirm judge score appears on completion — `studio/src/pages/PlaygroundPage.tsx`
- [ ] [T041] Phase 2 verification gate: register suite-20 in run-all.sh; build images; deploy; run full e2e suite green — `scripts/e2e/run-all.sh`

---

## Phase 3 — Scheduled + Event-Driven Playground

_Depends on: Phase 1 (T001-T023). Parallel with Phase 2 on backend; Studio components depend on InteractionSurface from Phase 2._
_Goal: Developers can test-fire scheduled agents ("Run Now") and event-driven agents ("Send Test Event" with sample payload through real filter logic) in the playground._

### Backend — filter engine and test-event endpoint

- [ ] [T042] Filter evaluation engine: pure function that evaluates `filter_conditions` JSONB (array of `{field, op, value}` rules) against a payload dict; returns `{matched: bool, reason: str}` — `services/registry-api/filter_engine.py`
- [ ] [T043] PlaygroundRun model: add `trigger_type` (VARCHAR(16), nullable) and `trigger_payload` (JSONB, nullable) columns; migration not needed (use Alembic autogenerate or manual ALTER) — `services/registry-api/models.py`
- [ ] [T044] PlaygroundRunCreate schema: add optional `trigger_type` and `trigger_payload` fields — `services/registry-api/schemas.py`
- [ ] [T045] New endpoint `POST /api/v1/playground/test-event`: accepts `{agent_name, payload}`; loads agent's triggers; evaluates `filter_conditions` via filter_engine; if matched, creates playground_run with `trigger_type=webhook` + `trigger_payload=payload`; if filtered, returns `{matched: false, reason: "..."}` — `services/registry-api/routers/playground.py`

### Studio — scheduled and event components

- [ ] [T046] [P] RunNowPanel component: shows cron expression + human-readable parse (client-side cronstrue) + next-3-fires preview; "Run Now (test-fire)" button calls `POST /playground/runs`; test-run history table filtered to this agent — `studio/src/components/playground/RunNowPanel.tsx`
- [ ] [T047] [P] TestTriggerPanel component: filter config display (read-only from trigger); JSON payload editor (textarea or Monaco); "Send Test Event" button calls `POST /playground/test-event`; event log showing matched (run link) or filtered (reason) results — `studio/src/components/playground/TestTriggerPanel.tsx`
- [ ] [T048] Extend InteractionSurface: if trigger includes `schedule` render `<RunNowPanel>`; if trigger includes `webhook` render `<TestTriggerPanel>`; preserves reactive/durable branches from Phase 2 — `studio/src/components/playground/InteractionSurface.tsx`
- [ ] [T049] playgroundApi.ts: add `testEvent()` API function, `TestEventResponse` type, cron display utility types — `studio/src/api/playgroundApi.ts`

### Documentation and verification

- [ ] [T050] Update playground experience doc: document scheduled Run Now UX, event Test Trigger UX, filter evaluation, event log display — `docs/experience/playground.md`
- [ ] [T051] TypeScript type-check: run `cd studio && npx tsc --noEmit` and fix all errors — `studio/tsconfig.json`
- [ ] [T052] [P] Write e2e suite-21 (scheduled): T-S21-001 create scheduled agent + trigger (cron); T-S21-002 "Run Now" via POST /playground/runs completes + judged; T-S21-003 multiple test-fires appear in history — `scripts/e2e/suite-21-scheduled-playground.sh`
- [ ] [T053] [P] Write e2e suite-22 (event): T-S22-001 create event-driven agent + trigger (webhook + filter); T-S22-002 test-event with matching payload creates run; T-S22-003 test-event with non-matching payload returns `{matched: false}`; T-S22-004 matched run is judged — `scripts/e2e/suite-22-event-playground.sh`
- [X] [T054] Image bump: `REGISTRY_API_TAG="0.2.41"`, `STUDIO_TAG="0.1.36"` — `scripts/deploy-cpe2e.sh`
- [X] [T055] Phase 3 verification gate: register suites 21-22 in run-all.sh; build images; deploy; run full e2e suite green — `scripts/e2e/run-all.sh`

---

## Checkpoint 1 — Playground Complete

_Gate: Phases 1-3 must be complete. Run before starting Phase 4._
_What you prove: All four playground modes (reactive chat, durable steps+approval, scheduled Run Now, event Test Trigger) work end-to-end in the sandbox environment._

- [ ] [CP1a] Deploy script: build all updated images (registry-api:0.2.41, declarative-runner:0.1.3, studio:0.1.36), helm upgrade with new tags, wait for all pods Ready — `scripts/deploy-cp1.sh`
- [ ] [CP1b] Infrastructure smoke test: verify registry-api pod Running, declarative-runner pod Running, studio pod Running; confirm `/api/v1/health` returns `{status: "ok"}`; verify `agent_triggers` table exists (psql); verify `run_steps` table exists — `scripts/smoke-test-cp1-infra.sh`
- [ ] [CP1c] Behaviour smoke test: create agent with `execution_shape=durable` (verify stored); create schedule trigger (verify listed); create webhook trigger with filter (verify listed); POST `/playground/test-event` with matching payload (verify run created); POST with non-matching payload (verify `{matched: false}`); POST `/playground/runs` with durable input_payload (verify run_id returned, steps endpoint returns step rows) — `scripts/smoke-test-cp1-behaviour.sh`

> **To run:** `bash scripts/deploy-cp1.sh` -> wait for pods -> `bash scripts/smoke-test-cp1-infra.sh && bash scripts/smoke-test-cp1-behaviour.sh`
> **Pass criteria:** All assertions exit 0, no pod in CrashLoopBackOff

---

## Phase 4 — Production Runs + Agent Detail

_Depends on: Phase 1 (T001-T023)_
_Goal: Published agents produce `agent_runs` rows on every production invocation. Studio gets an Agent Detail page with tabs: Overview, Runs, Versions, Settings. The reactive Overview shows endpoint + metrics._

### Backend — run creation in production path

- [X] [T056] Extend chat router: on every production `/chat` or `/chat/stream` call, create an `agent_run` row with `trigger_type=api`, `context=production`, `run_by=user_id`, `team=agent.team`; on completion, update with `cost_usd`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `status` — `services/registry-api/routers/chat.py`
- [X] [T057] Declarative-runner: on every production invoke, create an `agent_run` row via POST /api/v1/agent-runs with `trigger_type=api`, `context=production`; update on completion — `services/declarative-runner/main.py`

### Backend — query and stats endpoints

- [X] [T058] Extend GET /api/v1/agent-runs: add query filters for `trigger_type`, `team`, `status`, `started_after`, `started_before`; add pagination (offset/limit); non-admins see only own `user_id` runs — `services/registry-api/routers/agent_runs.py`
- [X] [T059] New endpoint GET /api/v1/agents/{name}/stats: returns last-24h aggregates (run_count, p50_latency_ms, p95_latency_ms, error_rate, total_cost_usd) — `services/registry-api/routers/agents.py`
- [X] [T060] AgentStatsResponse schema: run_count (int), p50_latency_ms (int), p95_latency_ms (int), error_rate (float), total_cost_usd (float) — `services/registry-api/schemas.py`

### Studio — Agent Detail refactor (T061-T063 parallel; T064 depends on all three)

- [X] [T061] [P] AgentDetailPage refactor: convert to tabbed shell with tabs (Overview, Runs, Versions, Settings); render the appropriate Overview component based on `execution_shape`; preserve existing version/deployment content under Versions tab — `studio/src/pages/AgentDetailPage.tsx`
- [X] [T062] [P] RunsTab component: filterable table of `agent_runs` showing trigger_type icon, status badge, duration, cost, run_by, trace link; filter dropdowns for trigger_type, status, date range — `studio/src/components/agent-detail/RunsTab.tsx`
- [X] [T063] [P] OverviewReactive component: endpoint card with copy URL; stats sparkline (runs/hour from /agents/{name}/stats); recent-runs mini-table (last 5); error highlights — `studio/src/components/agent-detail/OverviewReactive.tsx`
- [X] [T064] registryApi.ts: add `getAgentStats(name)` returning `AgentStats`; extend `listAgentRuns()` with filter params; add `AgentStats` TypeScript type — `studio/src/api/registryApi.ts`

### Verification

- [X] [T065] TypeScript type-check: run `cd studio && npx tsc --noEmit` and fix all errors — `studio/tsconfig.json`
- [X] [T066] Write e2e suite-23: T-S23-001 invoke published agent via /chat and verify agent_run row created with context=production; T-S23-002 GET /agent-runs?agent_name=X returns the run; T-S23-003 GET /agents/X/stats returns metrics (run_count >= 1); T-S23-004 non-owner query with different user_id returns empty list — `scripts/e2e/suite-23-production-runs.sh`
- [X] [T067] Image bump: `REGISTRY_API_TAG="0.2.42"`, `DECLARATIVE_RUNNER_TAG="0.1.4"`, `STUDIO_TAG="0.1.37"` — `scripts/deploy-cpe2e.sh`
- [X] [T068] MANUAL browser verification: open Agent Detail page; confirm tabs render; click Runs tab and verify production runs appear; confirm Overview shows stats sparkline — `studio/src/pages/AgentDetailPage.tsx`
- [X] [T069] Register suite-23 in run-all.sh — `scripts/e2e/run-all.sh`
- [X] [T070] Phase 4 verification gate: build images; deploy; run full e2e suite green — `scripts/e2e/run-all.sh`

---

## Phase 5 — Durable Production + Global Approvals Inbox

_Depends on: Phase 2 (T024-T041) AND Phase 4 (T056-T070)_
_Goal: Published durable agents run multi-step jobs in production. Approvals go to a Global Approvals Inbox with authority checks. Runs survive pod restarts via checkpointing._

### Backend — run-executor extension

- [X] [T071] Extend run-executor: write `run_steps` rows on each step transition (pending -> running -> completed/failed); link approval_id when step requires HITL — `services/declarative-runner/run_executor.py`
- [X] [T072] Checkpoint module: serialize run state to Postgres JSONB between steps (survive pod restart); store in `agent_runs.trigger_payload` or a dedicated `checkpoint` column — `services/declarative-runner/checkpoint.py`
- [X] [T073] Resume from checkpoint on pod restart: on startup, query `agent_runs WHERE status='running'` and resume from last completed step — `services/declarative-runner/main.py`

### Backend — authority-checked approvals

- [X] [T074] Authority-checked approval decisions: in `POST /api/v1/approvals/{id}/decide`, when `context=production`, verify caller holds `agent:reviewer` role for the agent's team; sandbox context retains self-approve; return 403 for unauthorized reviewers — `services/registry-api/routers/approvals.py`
- [X] [T075] Global Approvals Inbox endpoint: `GET /api/v1/approvals?status=pending&team=<teams>` returns all pending approvals across agents the caller can review; each item includes tool, risk, args, SLA remaining, agent_name, step_name — `services/registry-api/routers/approvals.py`
- [X] [T076] Approval SLA/timeout: extend timeout worker to handle production durable approvals; approvals past TTL (configurable per agent, default 2h) auto-cancel the step and set run status to `cancelled` — `services/registry-api/approval_timeout_worker.py`
- [X] [T077] Inbox schemas: `ApprovalInboxItem` response schema (agent_name, step_name, tool_name, risk_level, tool_args, thread_context_snippet, sla_remaining_seconds, created_at) — `services/registry-api/schemas.py`

### Studio — Approvals Inbox and Durable Overview

- [X] [T078] [P] ApprovalsInboxPage: nav-level page with filterable list of pending approvals; each card shows agent name, step name, tool + risk + args, time remaining, Approve/Deny buttons; filter by team — `studio/src/pages/ApprovalsInboxPage.tsx`
- [X] [T079] [P] OverviewDurable component: active runs list (status, step progress bar), "New Run" button, failed/awaiting counts, avg duration — `studio/src/components/agent-detail/OverviewDurable.tsx`
- [X] [T080] Top nav: add "Approvals (n)" badge item in sidebar/nav linking to ApprovalsInboxPage; badge shows pending count from GET /approvals?status=pending — `studio/src/main.tsx`
- [X] [T081] registryApi.ts: add `listPendingApprovals()`, `decideApproval()` functions; add `ApprovalInboxItem` TypeScript type — `studio/src/api/registryApi.ts`

### Verification

- [X] [T082] TypeScript type-check: run `cd studio && npx tsc --noEmit` and fix all errors — `studio/tsconfig.json`
- [X] [T083] Write e2e suite-24: T-S24-001 launch production durable run and verify steps progress; T-S24-002 step awaiting_approval appears in /approvals endpoint; T-S24-003 reviewer approves and run resumes; T-S24-004 non-reviewer gets 403 on approve; T-S24-005 approval timeout results in run cancelled — `scripts/e2e/suite-24-durable-production.sh`
- [X] [T084] Image bump: `REGISTRY_API_TAG="0.2.43"`, `DECLARATIVE_RUNNER_TAG="0.1.5"`, `STUDIO_TAG="0.1.38"` — `scripts/deploy-cpe2e.sh`
- [X] [T085] Register suite-24 in run-all.sh — `scripts/e2e/run-all.sh`
- [X] [T086] Phase 5 verification gate: build images; deploy; run full e2e suite green — `scripts/e2e/run-all.sh`

---

## Phase 6 — Memory

_Depends on: Phase 4 (T056-T070)_
_Goal: Agents remember across sessions. Memory stores tokenized content only (PII rule from spec SS5.8). Hot path via Redis, cold path via pgvector. Memory tab in Agent Detail._

### Migrations

- [ ] [T087] Migration 0020: CREATE TABLE `agent_memory` (id, agent_name, team, thread_id, user_id, role CHECK user/assistant/system/tool, content TEXT, message_index INT, session_id, created_at, expires_at); indexes on thread_id+message_index and agent_name+team — `services/registry-api/alembic/versions/0020_add_agent_memory.py`
- [ ] [T088] Migration 0021: `CREATE EXTENSION IF NOT EXISTS vector`; ALTER `agent_memory` ADD COLUMN `content_embedding` VECTOR(1536); CREATE ivfflat index on embedding — `services/registry-api/alembic/versions/0021_add_pgvector_embedding.py`

### Models and schemas

- [ ] [T089] AgentMemory ORM class: all columns from migration 0020 + content_embedding; indexes; add to `__all__` — `services/registry-api/models.py`
- [ ] [T090] Memory schemas: `MemorySaveTurnRequest` (thread_id, messages list), `MemorySearchRequest` (query str, top_k int), `AgentMemoryResponse` (id, thread_id, role, content, message_index, created_at), `MemorySearchResult` (content, similarity_score, memory_type) — `services/registry-api/schemas.py`

### Memory service layer

- [ ] [T091] Memory service — write path: `save_turn(agent_name, team, thread_id, messages)` that PII-tokenizes via safety-orchestrator then stores in Postgres + Redis cache; includes TTL on Redis key — `services/registry-api/memory.py`
- [ ] [T092] Memory service — read path: `load_context(agent_name, thread_id, window=20)` that reads from Redis (hot) with PG fallback (cold); `search_memory(agent_name, query, top_k)` for pgvector semantic search; `summarize_old(thread_id, threshold=50)` to compress old messages into summary — `services/registry-api/memory.py`

### Memory router

- [ ] [T093] Memory CRUD router: `GET /api/v1/agents/{name}/memory?thread_id=...` (paginated); `DELETE /api/v1/agents/{name}/memory/{thread_id}` (GDPR delete, removes all rows for thread); `DELETE /api/v1/agents/{name}/memory/clear` (wipe all memory for agent) — `services/registry-api/routers/memory.py`
- [ ] [T094] Memory search endpoint: `POST /api/v1/agents/{name}/memory/search` accepts `{query, top_k}`, generates query embedding, runs ivfflat cosine similarity, returns top-k results — `services/registry-api/routers/memory.py`

### Infrastructure and runner integration

- [ ] [T095] Redis config for memory: add `REDIS_URL` env var to registry-api Deployment; ensure Redis is available (reuse Langfuse Redis or add dedicated instance in Helm values) — `charts/agentshield/charts/registry-api/templates/deployment.yaml`
- [ ] [T096] Declarative-runner memory integration: on run start, call `load_context` to inject memory into LLM prompt; on run end, call `save_turn` to persist the conversation — `services/declarative-runner/main.py`

### Studio

- [ ] [T097] [P] MemoryTab component: sessions list grouped by thread_id (click to expand message timeline); facts/summaries section; usage stats (messages stored, sessions count, token estimate) — `studio/src/components/agent-detail/MemoryTab.tsx`
- [ ] [T098] AgentDetailPage: add Memory tab (conditionally shown when memory_enabled=true or always shown with "Enable memory" prompt per OQ-6) — `studio/src/pages/AgentDetailPage.tsx`
- [ ] [T099] registryApi.ts: add `getAgentMemory(name, threadId?)`, `deleteMemoryThread(name, threadId)`, `clearAgentMemory(name)`, `searchMemory(name, query)` functions — `studio/src/api/registryApi.ts`

### Verification

- [ ] [T100] TypeScript type-check: run `cd studio && npx tsc --noEmit` and fix all errors — `studio/tsconfig.json`
- [ ] [T101] Write e2e suite-25: T-S25-001 invoke agent twice in same thread and verify memory loaded on second call; T-S25-002 memory content is PII-tokenized (no raw PII in DB query); T-S25-003 GET /agents/{name}/memory?thread_id returns messages; T-S25-004 DELETE thread removes all rows; T-S25-005 agent with memory_enabled=false stores nothing — `scripts/e2e/suite-25-memory.sh`
- [ ] [T102] Image bump: `REGISTRY_API_TAG="0.2.44"`, `DECLARATIVE_RUNNER_TAG="0.1.6"`, `STUDIO_TAG="0.1.39"` — `scripts/deploy-cpe2e.sh`
- [ ] [T103] Phase 6 verification gate: register suite-25 in run-all.sh; build images; deploy; run full e2e suite green — `scripts/e2e/run-all.sh`

---

## Checkpoint 2 — Production + Memory

_Gate: Phases 4-6 must be complete. Run before starting Phase 7._
_What you prove: Production runs are recorded for every invocation, durable runs work with authority-checked approvals, and memory persists across sessions with PII tokenization._

- [ ] [CP2a] Deploy script: build registry-api:0.2.44, declarative-runner:0.1.6, studio:0.1.39; helm upgrade; wait for pods Ready; verify pgvector extension loaded — `scripts/deploy-cp2.sh`
- [ ] [CP2b] Infrastructure smoke test: verify registry-api, declarative-runner, studio pods Running; confirm `agent_memory` table exists; confirm pgvector extension (`SELECT extname FROM pg_extension WHERE extname='vector'`); confirm Redis reachable from registry-api pod — `scripts/smoke-test-cp2-infra.sh`
- [ ] [CP2c] Behaviour smoke test: invoke published agent via /chat and verify agent_run row created (context=production); GET /agents/{name}/stats returns metrics; invoke agent twice in same thread and verify GET /agents/{name}/memory returns messages; create durable agent, launch production run, verify step appears in /approvals; approve with reviewer role (200); attempt approve with non-reviewer (403) — `scripts/smoke-test-cp2-behaviour.sh`

> **To run:** `bash scripts/deploy-cp2.sh` -> wait for pods -> `bash scripts/smoke-test-cp2-infra.sh && bash scripts/smoke-test-cp2-behaviour.sh`
> **Pass criteria:** All assertions exit 0, no pod in CrashLoopBackOff

---

## Phase 7 — Scheduler Service

_Depends on: Phase 4 (T056-T070)_
_Goal: A standalone scheduler service fires published scheduled agents on their cron expression. HA with 2 replicas + distributed lock. Fires via internal API._

### New service scaffold

- [X] [T104] Scaffold services/scheduler/: create Dockerfile (python:3.12-slim), requirements.txt (fastapi, uvicorn, apscheduler, psycopg2-binary, httpx), and main.py skeleton with health endpoint — `services/scheduler/main.py`
- [X] [T105] Scheduler startup: on boot, query registry-api for `agent_triggers WHERE trigger_type='schedule' AND enabled=true`; register each as an APScheduler CronTrigger job with timezone — `services/scheduler/main.py`
- [X] [T106] On-tick handler: when a scheduled job fires, POST to `/api/v1/internal/runs/start` with `{agent_name, trigger_type: 'schedule', trigger_id, run_by: 'serviceaccount:scheduler'}` — `services/scheduler/main.py`

### Scheduler HA and reload

- [X] [T107] HA module: before each fire, acquire Postgres advisory lock keyed on `trigger_id + fire_time_epoch`; only the replica that acquires the lock dispatches; release after dispatch — `services/scheduler/ha.py`
- [X] [T108] Reload logic: poll registry-api for trigger changes every 60s; add new jobs, update changed expressions, remove disabled/deleted triggers from APScheduler — `services/scheduler/main.py`

### Backend — internal run start endpoint

- [X] [T109] New internal endpoint `POST /api/v1/internal/runs/start`: accepts `{agent_name, trigger_type, trigger_id, trigger_payload, run_by}`; creates agent_run row; dispatches to agent pod; cluster-internal only (no public ingress) — `services/registry-api/routers/internal.py`
- [X] [T110] InternalRunStartRequest schema: agent_name (str), trigger_type (str), trigger_id (UUID, optional), trigger_payload (dict, optional), run_by (str) — `services/registry-api/schemas.py`

### Helm

- [X] [T111] Scheduler Helm chart: Deployment (2 replicas), ServiceAccount, ClusterRole/Binding for registry-api internal endpoint access; env vars for REGISTRY_API_URL, DATABASE_URL — `charts/agentshield/charts/scheduler/templates/deployment.yaml`

### Studio

- [X] [T112] [P] OverviewScheduled component: schedule card (cron expression, human-readable cronstrue parse, next-3-fires, enabled toggle); last-run status badge; run history table; alert config summary — `studio/src/components/agent-detail/OverviewScheduled.tsx`
- [X] [T113] SettingsTab component: trigger management section (edit cron expression, timezone selector, enable/disable toggle); save calls PATCH /agents/{name}/triggers/{id} — `studio/src/components/agent-detail/SettingsTab.tsx`
- [X] [T114] registryApi.ts: add `updateTrigger(name, triggerId, data)`, `enableTrigger()`, `disableTrigger()` functions — `studio/src/api/registryApi.ts`

### Verification

- [X] [T115] TypeScript type-check: run `cd studio && npx tsc --noEmit` and fix all errors — `studio/tsconfig.json`
- [X] [T116] Write e2e suite-26: T-S26-001 create scheduled agent + trigger (cron every minute for test); T-S26-002 wait for cron fire and verify agent_run created with trigger_type=schedule; T-S26-003 disable trigger and verify no more fires; T-S26-004 verify both scheduler pods are Running (HA) — `scripts/e2e/suite-26-scheduler.sh`
- [X] [T117] Image bump: `REGISTRY_API_TAG="0.2.45"`, add `SCHEDULER_TAG="0.1.0"`, `STUDIO_TAG="0.1.40"` — `scripts/deploy-cpe2e.sh`
- [X] [T118] MANUAL browser verification: open scheduled agent detail; confirm Overview shows schedule card with next fires; toggle enable/disable — `studio/src/pages/AgentDetailPage.tsx`
- [X] [T119] Phase 7 verification gate: register suite-26 in run-all.sh; build images; deploy; run full e2e suite green — `scripts/e2e/run-all.sh`

---

## Phase 8 — Alerting + Observability

_Depends on: Phase 7 (T104-T119)_
_Goal: Scheduled and event-driven agents alert on failure. Email transport first. Per-mode health signals surfaced in the Overview tab._

### Backend — alert config migration and model

- [X] [T120] Migration **0024** (0022=pgvector, 0023=run_steps FK drop already taken): ALTER `agent_triggers` ADD COLUMN `alert_email` VARCHAR(255) nullable, ADD COLUMN `alert_on_failure` BOOLEAN DEFAULT true — `services/registry-api/alembic/versions/0024_add_alert_config.py`
- [X] [T121] AgentTrigger model: add `alert_email` and `alert_on_failure` mapped columns — `services/registry-api/models.py`
- [X] [T122] AgentTrigger schemas: add `alert_email` (Optional[str]) and `alert_on_failure` (bool, default true) to `AgentTriggerCreate`, `AgentTriggerUpdate` and `AgentTriggerResponse` — `services/registry-api/schemas.py`

### Backend — alert dispatch and health endpoint

- [X] [T123] Alert dispatch service: after a scheduled/event run completes with `status=failed`, look up trigger's `alert_email`; if set, send failure notification via SMTP (configurable via `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` env vars); log alert dispatch — `services/registry-api/alerting.py` (wired into `routers/internal.py::_dispatch_and_complete`)
- [X] [T124] Health endpoint: `GET /api/v1/agents/{name}/health` returns mode-appropriate signals — reactive: p95_latency, error_rate, runs_24h, cost_24h; durable: awaiting_approval_count, failed_24h, avg_duration; scheduled: last_run_status, next_fire_at, missed_fires; event-driven: match_rate_24h, rejected_count_24h — `services/registry-api/routers/agents.py` (mode derived from enabled triggers + execution_shape; `croniter` added for next_fire_at)
- [X] [T125] AgentHealthResponse schema: union of mode-specific fields; `mode` field determines which signals are populated — `services/registry-api/schemas.py`

### Studio

- [X] [T126] SettingsTab: add alert config section (email field + on-failure toggle per trigger); save calls PATCH trigger — `studio/src/components/agent-detail/SettingsTab.tsx`
- [X] [T127] AgentListPage: add health dot (green/yellow/red) per agent row; fetch from `/agents/{name}/health` on mount; green = healthy, yellow = degraded, red = failing — `studio/src/pages/AgentListPage.tsx` (per-row `HealthDot` component, 30s refetch)
- [X] [T128] registryApi.ts: add `getAgentHealth(name)` function; add `AgentHealth` TypeScript type — `studio/src/api/registryApi.ts`

### Verification

- [X] [T129] TypeScript type-check: run `cd studio && npx tsc --noEmit` and fix all errors — `studio/tsconfig.json` (TSC_EXIT=0)
- [X] [T130] Write e2e suite-27: T-S27-001 configure alert_email on trigger; T-S27-002 trigger a failed scheduled run and verify alert dispatched (check SMTP log or mock); T-S27-003 successful run produces no alert; T-S27-004 GET /agents/{name}/health returns mode-correct signals — `scripts/e2e/suite-27-alerting.sh` (4/0 green)
- [X] [T131] Image bump: `REGISTRY_API_TAG="0.2.52"`, `STUDIO_TAG="0.1.41"` (bumped from current 0.2.50/0.1.40; also mirrored into `charts/agentshield/values.yaml`) — `scripts/deploy-cpe2e.sh`
- [ ] [T132] MANUAL browser verification: confirm health badges on agent list; open scheduled agent settings and configure alert email — `studio/src/pages/AgentListPage.tsx`
- [X] [T133] Phase 8 verification gate: register suite-27 in run-all.sh; build images; deploy; run full e2e suite green — `scripts/e2e/run-all.sh` (**27/0 ALL PASS** on the live cluster 2026-07-05)

---

## Phase 9 — Event Gateway

_Depends on: Phase 7 (T104-T119). This is the LAST phase — biggest attack surface. Requires threat model before implementation._
_Goal: Public webhook ingress for event-driven agents. Validates tokens, rate-limits, filters, dispatches. Threat-modeled before code._

### Pre-requisite: threat model

- [X] [T134] Write threat model document covering: token validation (SHA-256 hash comparison), rate limiting (per-agent, per-source-IP sliding window), replay protection (nonce or timestamp), payload sanitization (safety-orchestrator pass-through), DDoS surface (CDN/WAF consideration) — `docs/design/event-gateway-threat-model.md` (12 threats T-1..T-12 + STRIDE + residual-risk table + suite-28 security acceptance criteria; surfaced refinements to T135/T136/T138/T145 — see below)

### New service scaffold and core logic

- [X] [T135] Scaffold services/event-gateway/: Dockerfile (python:3.12-slim), requirements.txt (fastapi, uvicorn, httpx, redis), main.py with health endpoint and `POST /hooks/{agent_name}/{token}` route skeleton — `services/event-gateway/main.py` — **[TM T-1/T-5]** enforce max body size (default 256 KiB → 413), JSON-only (400 on unparseable), accept optional `X-Webhook-Token`/`Authorization` header form, and NEVER log the token path segment (log agent_name + hash prefix only)
- [X] [T136] Token validation: compute `sha256(token)`, look up `agent_triggers WHERE agent_name AND sha256 matches token_hash AND enabled=true`; return 401 on mismatch — `services/event-gateway/main.py` — **[TM T-2/T-6/T-9]** compare with `hmac.compare_digest` (constant-time), lookup keyed by BOTH agent_name+hash (A-token on B's path ⇒ 401), and return a **uniform 401** for unknown-agent vs disabled vs bad-token (no enumeration)
- [X] [T137] Rate limiting: per-agent sliding window counter in Redis (key: `ratelimit:{agent_name}`, window: 60s, max: configurable default 100); return 429 when exceeded — `services/event-gateway/rate_limiter.py` — **[TM T-4/T-11]** ALSO add per-source-IP window (`ratelimit:ip:{ip}`), count 401s toward the limit, `Retry-After` header, **fail-closed on Redis error**, and derive source IP from the trusted proxy hop only (never raw client XFF)
- [X] [T138] Filter evaluation and dispatch: evaluate `filter_conditions` against payload (reuse filter_engine logic from Phase 3); if matched, POST to `/api/v1/internal/runs/start` with `trigger_type=webhook`; if filtered, return 202 with logged reason — `services/event-gateway/main.py` — **[TM T-7]** run `regex`-operator filters under a hard time budget (thread timeout or `google-re2`); on timeout ⇒ treat as not-matched + log `filtered: regex-timeout` (fail-safe, no run)
- [X] [T139] Replay protection: validate `X-Webhook-Timestamp` header (reject if > 5 min old) or `X-Webhook-Nonce` (check Redis set for uniqueness, TTL 1h) — `services/event-gateway/main.py`

### Backend — events table and endpoints

- [X] [T140] Migration **0025** (0023/0024 taken): CREATE TABLE `agent_events` (id, trigger_id FK, agent_name, status CHECK matched/filtered/rejected, filter_reason, payload JSONB, run_id FK nullable, source_ip INET, received_at); indexes on trigger_id+received_at DESC — `services/registry-api/alembic/versions/0023_add_agent_events.py`
- [X] [T141] AgentEvent ORM class: all columns from migration; add to `__all__`; relationship to AgentTrigger — `services/registry-api/models.py`
- [X] [T142] AgentEvent schemas: `AgentEventResponse` (id, trigger_id, agent_name, status, filter_reason, payload, run_id, source_ip, received_at) — `services/registry-api/schemas.py`
- [X] [T143] Token rotation endpoint: `POST /api/v1/agents/{name}/triggers/{id}/rotate-token` generates new random token, stores sha256 hash, returns plaintext once; old hash invalidated immediately — `services/registry-api/routers/triggers.py`
- [X] [T144] Event log endpoint: `GET /api/v1/agents/{name}/events?trigger_id=...&status=...` returns paginated event list, filterable by status (matched/filtered/rejected) — `services/registry-api/routers/events.py`

### Helm and ingress

- [X] [T145] Event gateway Helm chart: Deployment (2 replicas), ServiceAccount; public Ingress resource on `/hooks/*` path (separate from platform API ingress); TLS; rate-limit annotations — `charts/agentshield/charts/event-gateway/templates/deployment.yaml` — **[TM T-8]** add a NetworkPolicy so `/api/v1/internal/*` on registry-api is reachable ONLY from in-cluster pods (event-gateway/scheduler); ensure the public ingress does NOT route `/api/v1/internal/*`; do not log token in ingress access logs. Follow-up hardening (non-blocking): shared internal token / mTLS on `/internal`

### Studio

- [X] [T146] [P] OverviewEventDriven component: webhook URL display (masked token, copy button), "Rotate Token" button, filter config display, last-event timestamp, event log table (matched/filtered/rejected with icons), match rate chart — `studio/src/components/agent-detail/OverviewEventDriven.tsx`
- [X] [T147] SettingsTab: add token rotation button (calls rotate-token endpoint, shows new token once); filter editor (JSON conditions display) — `studio/src/components/agent-detail/SettingsTab.tsx`
- [X] [T148] registryApi.ts: add `rotateToken(name, triggerId)`, `listAgentEvents(name, params)` functions; add `AgentEvent`, `RotateTokenResponse` TypeScript types — `studio/src/api/registryApi.ts`

### Verification

- [X] [T149] TypeScript type-check: run `cd studio && npx tsc --noEmit` and fix all errors — `studio/tsconfig.json`
- [X] [T150] Write e2e suite-28: T-S28-001 POST /hooks/agent/valid-token with matching payload creates run; T-S28-002 invalid token returns 401; T-S28-003 valid token but non-matching filter returns 202 + logged as filtered; T-S28-004 exceed rate limit returns 429; T-S28-005 rotate token then old token rejected + new token works; T-S28-006 GET /agents/{name}/events shows all three statuses — `scripts/e2e/suite-28-event-gateway.sh`
- [X] [T151] Image bump: `REGISTRY_API_TAG="0.2.54"` (bumped from current; +2 for internal.py + INET fixes), add `EVENT_GATEWAY_TAG="0.1.0"`, `STUDIO_TAG="0.1.42"` (mirrored into values.yaml) — `scripts/deploy-cpe2e.sh`
- [ ] [T152] MANUAL browser verification: open event-driven agent detail; confirm webhook URL displayed; click Rotate Token; view event log — `studio/src/pages/AgentDetailPage.tsx`
- [X] [T153] Phase 9 verification gate: register suite-28 in run-all.sh; build images; deploy; run full e2e suite green — `scripts/e2e/run-all.sh`

---

## Checkpoint 3 — Full Platform

_Gate: Phases 7-9 must be complete. Run after all implementation phases._
_What you prove: Scheduler fires agents on cron, alerting dispatches on failure, and event gateway receives public webhooks with token validation + rate limiting + filter evaluation._

- [X] [CP3a] Deploy script: build all images; helm upgrade with all new services; wait for all pods Ready including scheduler (2 replicas) and event-gateway — **`scripts/deploy-cp3-full-platform.sh`** (renamed: `deploy-cp3.sh` was already taken by the safety-scanner CP3; this wraps `deploy-cpe2e.sh` + CP3 readiness waits. Current tags: registry-api 0.2.54, scheduler 0.1.0, event-gateway 0.1.0, studio 0.1.42)
- [X] [CP3b] Infrastructure smoke test (**5/0**): scheduler 2 replicas Running; event-gateway Running + Service reachable in-cluster (local has no ingress controller — Service stands in for the disabled Ingress); `agent_events` table exists; alerting capability present (alert columns + alerting.py — SMTP intentionally log-only by default, so we verify capability not `SMTP_HOST`) — `scripts/smoke-test-cp3-infra.sh`
- [X] [CP3c] Behaviour smoke test (**6/0**): scheduled 1-min cron fires → agent_run (trigger_type=schedule) created (real ~60s wait) → failed run dispatches a failure ALERT (log line asserted); event webhook matched → 202 + run; bad token → 401; non-matching filter → 202 filtered — `scripts/smoke-test-cp3-behaviour.sh` (uses a SQL-inserted fake running deployment so runs actually get created)

> **To run:** `bash scripts/deploy-cp3-full-platform.sh` -> wait for pods -> `bash scripts/smoke-test-cp3-infra.sh && bash scripts/smoke-test-cp3-behaviour.sh`
> **Pass criteria:** All assertions exit 0, no pod in CrashLoopBackOff. **PASSED 2026-07-05** (infra 5/0, behaviour 6/0) on the live cluster.

---

## Dependency Graph

```
Phase 0 (DONE)
    |
    v
Phase 1 (Foundation) -------- T001-T023
    |
    +-------------------+-------------------+
    |                   |                   |
    v                   v                   v
Phase 2 (Durable PG)  Phase 3 (Sched+Evt) Phase 4 (Prod Runs)
T024-T041              T042-T055           T056-T070
    |                   |                   |
    +------- CP1 ------+                   |
                                           |
    +--------------------------------------+
    |                   |
    v                   v
Phase 5 (Durable Prod) Phase 6 (Memory)
T071-T086              T087-T103
    |                   |
    +------- CP2 ------+
    |
    v
Phase 7 (Scheduler) --- T104-T119
    |
    +-------------------+
    |                   |
    v                   v
Phase 8 (Alerting)     Phase 9 (Event GW)
T120-T133              T134-T153
    |                   |
    +------- CP3 ------+
```

---

## Image Tag Progression

| Phase | registry-api | studio | declarative-runner | scheduler | event-gateway |
|-------|-------------|--------|-------------------|-----------|---------------|
| Current | 0.2.38 | 0.1.33 | 0.1.2 | - | - |
| 1 | 0.2.39 | 0.1.34 | - | - | - |
| 2 | 0.2.40 | 0.1.35 | 0.1.3 | - | - |
| 3 | 0.2.41 | 0.1.36 | - | - | - |
| 4 | 0.2.42 | 0.1.37 | 0.1.4 | - | - |
| 5 | 0.2.43 | 0.1.38 | 0.1.5 | - | - |
| 6 | 0.2.44 | 0.1.39 | 0.1.6 | - | - |
| 7 | 0.2.45 | 0.1.40 | - | 0.1.0 | - |
| 8 | 0.2.46 | 0.1.41 | - | - | - |
| 9 | 0.2.47 | 0.1.42 | - | - | 0.1.0 |

---

## E2E Suite Progression

| Suite | Phase | Name | Test cases |
|-------|-------|------|------------|
| 19 | 1 | Execution Shape + Triggers | 5 (shape CRUD, trigger CRUD) |
| 20 | 2 | Durable Playground | 6 (durable run, SSE steps, HITL, auto-cancel) |
| 21 | 3 | Scheduled Playground | 3 (Run Now, history) |
| 22 | 3 | Event Playground | 4 (test-event, filter match/reject, judge) |
| 23 | 4 | Production Runs | 4 (run creation, query, stats, isolation) |
| 24 | 5 | Durable Production | 5 (steps, inbox, authority, timeout) |
| 25 | 6 | Memory | 5 (persistence, PII, query, delete, disabled) |
| 26 | 7 | Scheduler | 4 (trigger, fire, disable, HA) |
| 27 | 8 | Alerting | 4 (config, failed alert, success no-alert, health) |
| 28 | 9 | Event Gateway | 6 (valid fire, bad token, filter, rate limit, rotate, event log) |

---

## Migration Sequence

| Number | Phase | Table/Change |
|--------|-------|--------------|
| 0016 | 1 | ALTER agents: execution_shape + memory_enabled |
| 0017 | 1 | ALTER agent_runs: orchestration fields |
| 0018 | 1 | CREATE run_steps |
| 0019 | 1 | CREATE agent_triggers |
| 0020 | 6 | CREATE agent_memory |
| 0021 | 6 | CREATE EXTENSION vector + embedding column |
| 0022 | 8 | ALTER agent_triggers: alert_email + alert_on_failure |
| 0023 | 9 | CREATE agent_events |

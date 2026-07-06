# AgentShield — Execution Models & Memory: Backend & Data Model

**Status**: DRAFT v2 — revised 2026-07-03. **Backend / data-model spec** — UX lives in the experience docs (see intro). Not yet implemented  
**Date**: 2026-06-27  
**Author**: Karthik + Claude  
**Phase**: 3 (follows P1 safety proxy + P2 canvas/skills)

---

## 1. Problem Statement

AgentShield currently treats all agents as a single category: deploy a container, call it, get a response. Real enterprise agents don't work that way. A fraud detection agent that runs continuously on payment events is architecturally different from a weekly compliance report agent, which is different from a multi-step contract review agent that needs a human to approve before filing. These differences affect the deployment model, the UX for configuring and monitoring them, and the infrastructure required to run them safely.

Additionally, no agent in the platform today has memory. Every run is stateless. This means agents can't learn from prior sessions, can't maintain conversation context across turns, and can't accumulate domain-specific knowledge over time — all of which are table-stakes for enterprise agents.

This document is the **backend & data-model spec** for four first-class execution models and a layered memory system: data model, migrations, API/SSE contracts, new services, multi-tenancy internals, and memory architecture. **Per-mode UX, wireframes, and user flows live in the experience docs** — [`playground-execution-modes.md`](./playground-execution-modes.md) (pre-publish evaluate) and [`execution-modes-production.md`](./execution-modes-production.md) (production operate). This spec is the source of truth for *how it's built*; those are the source of truth for *what the user sees*.

---

## 2. Execution Models

### 2.1 Two orthogonal dimensions

> **Scope:** These models span the **whole agent lifecycle**, not just production. Execution shape and triggers are chosen at registration, then **created and evaluated in the playground / sandbox before publish** (see Decision 20 in `docs/decisions.md`): reactive agents are chatted with; durable agents are launched as *test runs* with their step + approval flow exercised; and scheduled / event-driven agents are **test-fired manually** — a sample payload through the real code path — rather than waiting for a cron tick or a live webhook. What is *production-only* is the **automatic** firing of triggers (cron on schedule, real inbound webhooks) once the agent is published and running unattended. So: the playground is where every mode is tested; production is where triggers fire on their own. The playground's interaction surface must therefore be **mode-aware**, not chat-only (see `docs/experience/playground.md`).

The original draft used a single `execution_model` enum with four values (reactive, long-running, scheduled, event-driven). That conflates two independent things, so this revision splits them:

- **Execution shape** — how a *single run* behaves once started: `reactive` (single-shot request → response) or `durable` (checkpointed, broken into named steps, can pause for HITL, survives pod restarts). Set per agent as `execution_shape`.
- **Trigger** — what *starts* a run in production: `manual`, `api`, `schedule` (cron), or `webhook` (event). Stored as one or more rows in `agent_triggers`; an agent can have several triggers of mixed types.

They compose freely, and both are orthogonal to `agent_type` (sdk vs declarative), which describes implementation:

| Trigger ↓ / Shape → | reactive | durable |
|---|---|---|
| **manual / api** | chat assistant, classifier | interactive multi-step task w/ approvals |
| **schedule (cron)** | daily data sync | weekly compliance report (multi-step + review) |
| **webhook (event)** | PR-opened → quick check | payment-fail → durable fraud review w/ approval |

The four original "models" are just points in this grid: *Reactive* = reactive shape; *Long-running* = durable shape; *Scheduled* = schedule trigger; *Event-driven* = webhook trigger. Sections 2.2–2.3 describe the two **shapes**; 2.4–2.5 describe the two external **triggers**.

### 2.2 Reactive

**Mental model:** a function. User sends input, agent returns output. No state persists between calls beyond what memory provides.

**Canonical use cases:**
- Chat assistant answering questions
- Document classifier
- Code reviewer called from CI pipeline

**Key behaviors:**
- Runs are short-lived (< 30s typical)
- Each invocation is independent unless memory is enabled
- Tool calls happen inline during the response stream
- No approval gating needed for the run itself (individual tool calls still go through OPA)

**UX:** wireframes + flows in the experience docs (§3) — reactive is chat / Try-it (playground) and consumer chat + public API (production).

### 2.3 Long-Running

**Mental model:** a job or task handed to a colleague. Could take minutes to hours, may need human input at checkpoints.

**Canonical use cases:**
- Multi-step contract analysis: parse → extract → verify → file JIRA → notify
- Onboarding workflow: create accounts → send emails → configure access → confirm
- Incident investigation: gather logs → analyze → draft report → request review

**Key behaviors:**
- Runs are broken into discrete, named steps
- Each step can succeed, fail, or require approval before proceeding
- Run state is persisted (survives agent pod restarts)
- Humans can cancel, pause, or unblock runs from the Studio
- Approval gates use the existing HITL approval flow — but now linked to a specific step

**UX:** wireframes + flows in the experience docs (§3) — durable run launcher, step tracker, and approvals (self-approve in playground; Global Approvals Inbox in production).

### 2.4 Scheduled

**Mental model:** a recurring job. Set a schedule, it fires automatically. You need to know immediately when it breaks.

**Canonical use cases:**
- Weekly compliance report generation
- Daily data sync and validation
- Hourly anomaly scan over logs

**Key behaviors:**
- Fired by a scheduler service on a cron expression
- Each fire creates an `agent_runs` record with `trigger_type=schedule`
- Runs are otherwise identical to reactive or long-running runs depending on what the agent does
- Enable/disable without deleting the schedule
- Alert on failure (email/webhook)

**UX:** wireframes + flows in the experience docs (§3) — schedule config + Run-Now test-fire (playground) and schedule health + failure alerting (production).

### 2.5 Event-Driven

**Mental model:** a reactive listener. Something happens in the world, the agent fires.

**Canonical use cases:**
- Payment failure triggers fraud review agent
- GitHub PR opened triggers code review agent
- S3 file uploaded triggers document processing agent

**Key behaviors:**
- Platform generates a unique webhook URL per agent trigger
- Inbound events are validated (token), filtered (conditions), and logged
- Filter-matched events create `agent_run` records with `trigger_type=webhook`
- Events that don't match filters are still logged as "filtered out" — critical for debugging misconfiguration
- Webhook URL must be publicly routable (ingress)

**UX:** wireframes + flows in the experience docs (§3) — filter builder + Test Trigger with sample payload (playground) and webhook config + event log + gateway security (production).

### 2.6 The executable: Agent or Workflow

Execution shape and triggers don't attach to "an agent" specifically — they attach to an **executable**. There are two kinds:

- **Agent** — an *atomic* executable. Runs its own logic (sdk code or a declarative graph) and produces a single run.
- **Workflow** — a *composite* executable: **a collection of agents working together** (supervisor / sequential / handoff orchestration). Produces a **run tree** — one parent workflow run with child agent runs.

Everything else is shared. Both kinds carry the same `execution_shape` + triggers + memory, write to the same `agent_runs` spine, are evaluated in the playground identically, and are operated in production identically (monitoring, approvals, alerting, publish gate, integrations). The **only** thing that differs is internal execution: an Agent runs once; a Workflow orchestrates sub-agents into a run tree. `agent_runs.parent_run_id` already models that tree, and the durable `StepTracker` renders both (an agent's internal steps, or a workflow's agent-tree) from the same `run_steps` stream.

> **Design intent (anti-rework):** one run spine, one trigger system, one memory layer, one playground, one production surface. The orchestration engine is the *only* place code branches on executable kind. **Workflow is not a parallel stack — it's a composition layer on the shared substrate.** See §4.5 for the composite design; the experience docs carry only a "run-tree granularity" delta.

> **Terminology (Decision 22, 2026-07-03):** "Workflow" is **redefined** to mean this composite executable — aligning with Microsoft Agent Framework and Anthropic, which both use *Agents + Workflows* as their two categories. The *current* `workflows` table (a canvas graph backing one declarative agent) is renamed **Agent Graph** — the authoring definition of a single declarative agent (§4.5).

---

## 3. Studio UX — Information Architecture

> **Moved to the experience docs (rev 2026-07-03).** Per-mode UX, wireframes, the Agent Detail tabbed shell (Overview / Runs / Memory / Versions / Settings), the Global Approvals Inbox, and the registration (execution-shape + triggers) flow now live in:
> - [`playground-execution-modes.md`](./playground-execution-modes.md) — pre-publish evaluate surface + component map
> - [`execution-modes-production.md`](./execution-modes-production.md) — production operate surface (§3 shell, §4–§7 per mode)
>
> This backend spec no longer duplicates the UX, to prevent drift. It owns the data model, migrations, routers/SSE contracts, isolation internals, memory architecture, and services (below).

---

## 4. Backend Architecture

### 4.1 Data Model Changes

#### 4.1.1 `agents` table — two new columns

```sql
ALTER TABLE agents
  ADD COLUMN execution_shape VARCHAR(16)
    CHECK (execution_shape IN ('reactive','durable'))
    NOT NULL DEFAULT 'reactive',
  ADD COLUMN memory_enabled BOOLEAN NOT NULL DEFAULT false;
-- Triggers are NOT an enum on agents. How a production run is *started*
-- (manual / api / schedule / webhook) lives in agent_triggers — one agent, many triggers.
```

#### 4.1.2 `agent_runs` — new table (core primitive)

The central table that everything else hangs off. Every invocation of an agent — in production or the playground, regardless of shape or trigger — creates one row here.

> **⚠ Reconciliation (2026-07-02): `agent_runs` already exists.** A table named `agent_runs` was built as an **observability / cost log** (columns: `session_id`, `input`, `output`, `langfuse_trace_id`, `cost_usd`, `prompt_tokens`, `completion_tokens`, `latency_ms`, `status`, `context`, `started_at`, `completed_at`). This revision **merges** the orchestration fields into that existing table rather than creating a second one. Do not `CREATE TABLE` — `ALTER` the existing one. One run spine carries both observability and orchestration.

```sql
-- Merge orchestration fields into the EXISTING agent_runs observability table.
ALTER TABLE agent_runs
  ADD COLUMN execution_shape VARCHAR(16) NOT NULL DEFAULT 'reactive',   -- snapshot at run time
  ADD COLUMN trigger_type    VARCHAR(32) NOT NULL DEFAULT 'manual'
    CHECK (trigger_type IN ('manual','api','schedule','webhook')),
  ADD COLUMN trigger_payload JSONB,                                     -- raw event/input that started the run
  ADD COLUMN thread_id       TEXT,                                      -- links to approvals + opa_decisions
  ADD COLUMN parent_run_id   UUID REFERENCES agent_runs(id),            -- sub-agent orchestration
  ADD COLUMN run_by          TEXT,                                      -- 'user:alice','scheduler','webhook:{trigger_id}'
  ADD COLUMN team            VARCHAR(128) NOT NULL DEFAULT '',          -- tenant belt-and-suspenders (see §5.3)
  ADD COLUMN error_message   TEXT;

-- 'status' must be widened to include the orchestration states:
--   queued, running, awaiting_approval, completed, failed, cancelled
-- Existing observability columns are retained as-is.

CREATE INDEX idx_runs_thread_id ON agent_runs(thread_id);
CREATE INDEX idx_runs_team_agent ON agent_runs(team, agent_id, started_at DESC);
```

**Relationship to existing tables:**
- `approvals.thread_id` → `agent_runs.thread_id` (logical link, not FK — threads can exist without a formal run in legacy data)
- `opa_decisions.thread_id` → `agent_runs.thread_id` (same)
- The existing `playground_runs` / `eval_runs` tables stay separate (playground/eval scoping); production runs use this merged `agent_runs`.
- Future: add `run_id` FK columns to `approvals` and `opa_decisions` in a migration

#### 4.1.3 `run_steps` — new table (long-running only)

Populated only for `execution_shape = 'durable'`. Each logical step of the agent's execution gets a row.

```sql
CREATE TABLE run_steps (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id       UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  step_number  INTEGER NOT NULL,
  step_name    VARCHAR(256) NOT NULL,
  status       VARCHAR(32) NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','running','completed','failed','skipped')),
  tool_name    VARCHAR(256),                        -- if this step is a tool call
  input        JSONB,
  output       JSONB,
  approval_id  UUID REFERENCES approvals(id),       -- set when step is blocked on approval
  started_at   TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  UNIQUE (run_id, step_number)
);

CREATE INDEX idx_run_steps_run_id ON run_steps(run_id, step_number);
```

#### 4.1.4 `agent_schedules` — new table (scheduled agents)

Schedule is a **trigger subtype** (see §2.1 — triggers are orthogonal to execution shape, and an agent may have several). This table stores cron config for `schedule`-type triggers; the scheduler service reads it to decide what to fire. Today `agent_schedules` and `agent_triggers` are the per-type trigger config tables (cron and webhook respectively); a future consolidation may merge them into a single `agent_triggers(type, config JSONB)`. Every production run records its origin in `agent_runs.trigger_type`.

```sql
CREATE TABLE agent_schedules (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id         UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,  -- not UNIQUE: an agent may have multiple triggers
  cron_expression  VARCHAR(128) NOT NULL,           -- '0 9 * * 1'
  timezone         VARCHAR(64) NOT NULL DEFAULT 'UTC',
  enabled          BOOLEAN NOT NULL DEFAULT true,
  next_run_at      TIMESTAMPTZ,
  last_run_at      TIMESTAMPTZ,
  last_run_status  VARCHAR(32),
  alert_email      TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### 4.1.5 `agent_triggers` + `agent_events` — new tables (event-driven agents)

```sql
CREATE TABLE agent_triggers (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id           UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  trigger_type       VARCHAR(32) NOT NULL DEFAULT 'webhook'
                     CHECK (trigger_type IN ('webhook')),  -- extend later: kafka, sqs
  webhook_token_hash TEXT NOT NULL,                -- SHA-256 hash of the secret token
  filter_conditions  JSONB NOT NULL DEFAULT '[]',  -- [{field, op, value}]
  enabled            BOOLEAN NOT NULL DEFAULT true,
  last_triggered_at  TIMESTAMPTZ,
  event_count        INTEGER NOT NULL DEFAULT 0,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_events (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trigger_id     UUID NOT NULL REFERENCES agent_triggers(id) ON DELETE CASCADE,
  agent_id       UUID NOT NULL,
  received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload        JSONB NOT NULL,
  filter_matched BOOLEAN NOT NULL,
  run_id         UUID REFERENCES agent_runs(id)   -- null if filtered out
);

CREATE INDEX idx_events_agent_id ON agent_events(agent_id, received_at DESC);
CREATE INDEX idx_events_trigger_id ON agent_events(trigger_id);
```

#### 4.1.6 `agent_memory` — new table

Stores all forms of agent memory: message history, cross-session facts, summaries, and embeddings for semantic retrieval.

```sql
-- Requires: CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE agent_memory (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id       UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  thread_id      TEXT,              -- null = agent-scoped; set = session-scoped
  user_id        TEXT,              -- null = agent-scoped; set = user-scoped
  memory_type    VARCHAR(32) NOT NULL
                 CHECK (memory_type IN ('message_history','summary','fact','knowledge')),
  scope          VARCHAR(32) NOT NULL DEFAULT 'session'
                 CHECK (scope IN ('session','agent','user')),
  content        JSONB NOT NULL,
  -- message_history: [{role, content, timestamp, tool_calls}]
  -- fact:            {key, value, source, confidence}
  -- summary:         {text, covers_messages_up_to_timestamp}
  -- knowledge:       {title, body, tags}
  embedding      vector(1536),      -- pgvector; null until async embedding job runs
  token_estimate INTEGER,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at     TIMESTAMPTZ        -- session memory can TTL; agent/user memory does not
);

CREATE INDEX idx_memory_agent_thread ON agent_memory(agent_id, thread_id)
  WHERE thread_id IS NOT NULL;
CREATE INDEX idx_memory_agent_scope ON agent_memory(agent_id, scope, memory_type);
CREATE INDEX idx_memory_expires ON agent_memory(expires_at)
  WHERE expires_at IS NOT NULL;
-- Approximate nearest-neighbor index for semantic search (requires pgvector)
CREATE INDEX idx_memory_embedding ON agent_memory
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
  WHERE embedding IS NOT NULL;
```

### 4.2 Alembic Migration Plan

> **⚠ Stale numbering (rev 2026-07-02):** migration numbers 0006–0014 are already used by *other* shipped features (auth model, asset lifecycle, HITL authority, playground, eval results, created_by). These names are **indicative of content, not the actual sequence numbers** — reassign to the next free numbers when Phase 3 starts.

```
00NN_add_execution_shape_memory_flag.py   — ALTER agents (execution_shape + memory_enabled)
00NN_alter_agent_runs_orchestration.py    — ALTER the EXISTING agent_runs (add orchestration fields; do NOT create a new table)
00NN_add_run_steps.py                     — run_steps table
00NN_add_agent_schedules.py               — agent_schedules (schedule-trigger config)
00NN_add_agent_triggers_events.py         — agent_triggers + agent_events tables
00NN_add_agent_memory.py                  — agent_memory table (no embedding yet)
00NN_add_pgvector_embedding.py            — CREATE EXTENSION vector + embedding column + ivfflat index
```

Migrations 0006–0011 have no external dependencies and can run on the existing cluster. Migration 0012 requires the `pgvector` PostgreSQL extension to be installed (available as a plugin in most managed Postgres offerings; for self-hosted: `apt install postgresql-16-pgvector`).

### 4.3 New API Routers

Six new router files added to `services/registry-api/routers/`:

| Router | Endpoints | Notes |
|---|---|---|
| `runs.py` | `GET/POST /agents/{name}/runs` `GET /agents/{name}/runs/{id}` `DELETE /agents/{name}/runs/{id}` `GET /agents/{name}/runs/{id}/stream` `GET /runs/pending-approvals` | `/stream` is SSE; `/pending-approvals` is the global inbox |
| `run_steps.py` | `GET /agents/{name}/runs/{id}/steps` | Returns ordered step list with current statuses |
| `schedules.py` | `GET/PUT/DELETE /agents/{name}/schedule` `POST /agents/{name}/schedule/enable` `POST /agents/{name}/schedule/disable` | |
| `triggers.py` | `GET/POST /agents/{name}/triggers` `GET/PUT/DELETE /agents/{name}/triggers/{id}` `POST /agents/{name}/triggers/{id}/test` | `test` sends a synthetic event |
| `events.py` | `GET /agents/{name}/events` `GET /agents/{name}/events/{id}` | Paginated, filterable by filter_matched |
| `memory.py` | `GET /agents/{name}/memory` `POST /agents/{name}/memory` `POST /agents/{name}/memory/search` `GET/DELETE /agents/{name}/memory/sessions/{thread_id}` `DELETE /agents/{name}/memory/clear` | `/search` does vector similarity query |

**SSE protocol for run streaming (`/agents/{name}/runs/{id}/stream`):**

```
event: run_status
data: {"status": "running", "started_at": "..."}

event: step_update
data: {"step_number": 2, "step_name": "Search documents", "status": "running"}

event: step_update
data: {"step_number": 2, "status": "completed", "output": {...}, "latency_ms": 812}

event: approval_required
data: {"step_number": 3, "approval_id": "...", "tool_name": "send_slack", "tool_args": {...}}

event: run_status
data: {"status": "completed", "completed_at": "...", "token_count": 1842}
```

### 4.4 Internal Run Start Endpoint

Scheduler and Event Gateway both call this to fire runs without going through the public API:

```
POST /internal/runs/start

{
  "agent_name": "fraud-detector",
  "trigger_type": "schedule" | "webhook",
  "trigger_payload": {...},
  "run_by": "serviceaccount:scheduler" | "serviceaccount:webhook:{trigger_id}"
}
```

This endpoint is not exposed through the public ingress — only reachable cluster-internally.

**Input contract per agent type `[IMPLEMENTED — Decision 24 addendum]`.** What an agent receives as its run input depends on what triggered it — and the create-agent wizard ships a **type-specific instructions template** for each so authors write for the right input:

| Type | Run input | Source | Instructions written as |
|---|---|---|---|
| reactive / durable | the user's message | consumer/UI POST | conversational assistant |
| **scheduled** | the trigger's **`input_payload`** (a JSON job spec) — resolved by `internal.py` from the `trigger_id`; the scheduler sends only `trigger_id` (no payload) | `agent_triggers.input_payload` | autonomous parameterized worker (no user; deliver via tools; idempotent) |
| **event-driven** | the **webhook event body** (JSON) forwarded as `trigger_payload` | event-gateway | parse-the-event; untrusted payload; at-least-once idempotency |

Because one agent can carry **multiple** schedule triggers (`agent_triggers.agent_id` is not unique), the per-job parameters live on the trigger (`input_payload`), not in the instructions — so a single deployed agent serves many parameterized scheduled jobs. See Decision 24 addendum.

**Workflow run targeting `[IMPLEMENTED — Decision 24 pass #3]`.** The same `/internal/runs/start` endpoint now accepts `workflow_id` (in place of `agent_name`) to start a workflow run. The scheduler UNION-queries agent and workflow trigger rows and fires both paths here; the event-gateway's `POST /hooks/workflow/{name}/{token}` path resolves `workflow_id` by name and calls this endpoint. `_start_workflow_run` resolves run input from `trigger.input_payload` when no payload is sent explicitly — same pattern as the agent path.

### 4.5 Workflows — Composite Executables

A **Workflow** is a first-class executable that composes agents. It carries `execution_shape` + triggers + memory like an Agent, plus a membership + orchestration definition. **Everything in §2 (modes), §4.1–4.4 (run spine, triggers, SSE), §5 (isolation), §6 (memory), §7 (services) applies to workflows unchanged** — only the orchestration below is new.

**Reactive workflows are allowed** (decided 2026-07-03): a lightweight two-agent hand-off can be `reactive` (one request → response spanning agents); multi-step orchestration is `durable`. Default is `durable`, since composition usually implies multiple steps.

**Terminology reconciliation (Decision 22).** The pre-existing `workflows` / `workflow_versions` tables meant *a single declarative agent's canvas graph*. Those are renamed **`agent_graphs` / `agent_graph_versions`** (the authoring definition of one declarative agent; `agent_versions.workflow_id → agent_graph_id`). The name **`workflows`** is then freed for the composite executable below.

**Data model (design-level):**

```sql
-- Rename first: old canvas-graph tables → agent_graphs (frees the 'workflows' name).
ALTER TABLE workflows          RENAME TO agent_graphs;
ALTER TABLE workflow_versions  RENAME TO agent_graph_versions;
-- agent_versions.workflow_id → agent_graph_id (column rename + FK retarget).

-- NEW: a Workflow is a composite executable (collection of agents).
CREATE TABLE workflows (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            VARCHAR(256) NOT NULL,
  team            VARCHAR(128) NOT NULL,
  execution_shape VARCHAR(16)  NOT NULL DEFAULT 'durable'
                  CHECK (execution_shape IN ('reactive','durable')),
  memory_enabled  BOOLEAN NOT NULL DEFAULT false,
  orchestration   VARCHAR(32) NOT NULL DEFAULT 'supervisor'
                  CHECK (orchestration IN ('supervisor','sequential','handoff')),
  status          VARCHAR(32) NOT NULL DEFAULT 'draft',
  created_by      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workflow_members (
  workflow_id  UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  agent_id     UUID NOT NULL REFERENCES agents(id),
  role         VARCHAR(64),      -- 'supervisor' | 'worker' | free-form label
  position     INTEGER,          -- ordering for sequential
  routing      JSONB,            -- handoff edges / conditions
  PRIMARY KEY (workflow_id, agent_id)
);
```

**Triggers / memory / runs reuse the existing tables.** A trigger (`agent_schedules` / `agent_triggers`) targets a workflow the same way it targets an agent. **[RESOLVED — Decision 24]** The targeting model is a **nullable `workflow_id`** FK on `agent_triggers`/`agent_runs` (migration 0027, `CHECK num_nonnulls(agent_id, workflow_id)=1`) — not a polymorphic `executable_id`. A workflow fire creates a parent `agent_runs` row (`parent_run_id = NULL`); each member-agent invocation is a child run with `parent_run_id = <workflow run>`.

**Orchestration patterns:**
- **Supervisor** — a coordinator agent routes to worker agents dynamically (Bedrock / Databricks / MAF handoff style).
- **Sequential** — fixed order A → B → C, each consuming the prior output (Google ADK SequentialAgent style).
- **Handoff** — agents transfer control via declared edges (OpenAI Agents SDK / MAF handoff style).

**Run tree + StepTracker.** One workflow run (parent) → child agent runs via `parent_run_id`. The workflow's `run_steps` are agent-level steps; each child agent run has its own `run_steps` for *its* internal steps. The durable `StepTracker` renders either zoom level from the same SSE stream.

**Inter-agent HITL.** Approvals can gate the transition *between* agents (a workflow step awaiting approval before handing off), reusing the same `approvals` + `run_steps.approval_id` mechanism — no new HITL machinery.

**The only new engine work** is orchestration (routing / handoff + run-tree assembly) in the run-executor (§7.3). Everything else is the shared substrate — which is the whole point of treating Agent and Workflow as two kinds of one executable.

> **[IMPLEMENTED — Decision 24]** All four orchestration modes now run in `registry-api/workflow_orchestrator.py::orchestrate()`: **sequential** (walk the edge chain / member order), **conditional** (evaluate `workflow_edges.condition` via the reused `filter_engine` predicate DSL), **supervisor** (`role=supervisor` member routes to workers with a `max_iterations` cap), **handoff** (each agent signals the next hop). Edges are a first-class `workflow_edges` table (migration 0029). The Studio "Workflows" builder is the unified graph builder (per-node config + edge conditions + inline/existing agents); "Agent Graphs" is hidden from the nav. Deferred: per-node tool editing on the canvas.

> **[IMPLEMENTED — Decision 24 pass #3]** Workflow-level triggers: `POST /api/v1/workflows/{id}/triggers` (schedule + webhook; `agent_triggers.workflow_id` set, `agent_id NULL`); the scheduler UNION-queries agent and workflow trigger rows; the event-gateway exposes `POST /hooks/workflow/{name}/{token}`. Both dispatch to `POST /internal/runs/start` with `workflow_id`; `_start_workflow_run` resolves input from `trigger.input_payload` (mirrors agent path). Migration 0031: nullable `agent_events.workflow_id`. Studio: Triggers panel in the workflow builder + `execution_shape` selector in Save modal. Workflow members are restricted to **composable** agents (`GET /api/v1/agents/?composable=true` — no active schedule/webhook trigger) to prevent double-firing; inline Create-New is limited to reactive/durable shapes.

### 4.6 Orchestrator Checkpoint & Pause/Resume State Machine

> **[IMPLEMENTED — Decision 26 / WS-B]** Sequential pause + resume-advance is implemented. Non-sequential modes halt at `awaiting_approval` but do not auto-advance yet (routing re-derivation deferred). See Decision 26 for the full record; this section documents the backend design.

**Why this is needed.** A single-agent durable run checkpoints its LangGraph graph state to Postgres (via `PostgresSaver`, keyed by `thread_id`) — the LangGraph checkpointer is the agent's pause/resume mechanism. A composite workflow has *no* equivalent: the orchestrator (`workflow_orchestrator.py`) is an imperative loop in registry-api, not a LangGraph graph. When one member pauses for HITL, the orchestrator must itself be checkpointed so it can resume from the right place after the approval is decided. `agent_runs.orchestrator_state` JSONB is that durable checkpoint — the **orchestrator's analog of the LangGraph `PostgresSaver`**.

**Data model.**

```sql
-- Migration 0032 (down_revision 0031)
ALTER TABLE agent_runs
  ADD COLUMN orchestrator_state JSONB;  -- NULL on non-workflow / non-paused runs
```

`orchestrator_state` schema (only set on the parent workflow run while paused):

```json
{
  "mode":       "sequential",
  "order":      ["<agent_id_1>", "<agent_id_2>", "<agent_id_3>"],
  "next_index": 1,
  "team":       "commerce-team",
  "workflow_id": "<workflow_uuid>"
}
```

**Pause/resume state machine.**

```
parent run = running
    │
    ▼  dispatch member via POST /chat (with assigned thread_id)
member pod → LangGraph interrupt() → Postgres checkpoint → pod returns 200 (empty output)
    │
    ▼  orchestrator checks: pending Approval WHERE thread_id = member.thread_id?
YES → parent run = awaiting_approval
      orchestrator_state saved (mode + order + next_index)
      Studio: amber "awaiting approval" badge on run tree
    │
    ▼  reviewer approves/rejects via PATCH /approvals/{id}
decide_approval fires background _resume_and_advance(parent_run_id, approval_id)
    │
    ├─ POST member-pod /resume/{thread_id}   (reloads LangGraph checkpoint, runs tool, resumes)
    ├─ child run marked completed/failed
    └─ resume_orchestration(parent_run_id, member_output, member_status) called
           │
           ├─ member failed? → parent = failed; orchestrator_state cleared
           └─ member completed + mode=sequential?
                  → parent = running; orchestrator_state.next_index++
                  → loop continues from next member with prior output as input
                  → if last member: parent = completed; orchestrator_state cleared
```

**Authoritative pause-detection.** A 200 response with empty output from `/chat` is ambiguous — an agent can legitimately return nothing. The orchestrator does NOT infer "paused" from empty output. Instead, after every member dispatch, it queries:

```sql
SELECT id FROM approvals
WHERE thread_id = :member_thread_id AND status = 'pending'
LIMIT 1;
```

Row exists → member is paused for HITL. No row → member completed normally (even if output is empty). This removes the heuristic that caused the prior bug where empty output advanced the workflow with no input.

**Non-sequential modes (conditional / supervisor / handoff).** These modes now correctly halt at `awaiting_approval` when a member pauses — fixing a latent bug where empty output was mis-classified as completed and the run tree advanced. However, they do **not** auto-resume-advance after approval (routing re-derivation — which member to run next — is not trivially replayable). They are deferred(intentional).

**OPA activation note.** The approval flow above fires only when `require_approval=True` comes back from the OPA sidecar. For this to work in a real deployed cluster, `AGENTSHIELD_OPA_URL` must be set (now injected by `manifest_builder.py`) **and** the OPA allow-path must be canary-verified (projected SA token identity + bundle load). Until that canary is green, `require_approval` remains `False` in practice — the pause mechanism exists but won't trigger organically. See Decision 26 + Mi-06.

---

## 5. Multi-Tenancy & Isolation

### 5.1 Isolation Dimensions

The platform is multi-tenant (many teams share the same cluster and database) and multi-user (many users share a team). Memory and conversation history must be isolated along both axes.

| Boundary | Definition | Enforced by |
|---|---|---|
| **Tenant** | A `team` — the unit of resource ownership and access control | `agent_id → agents.team` + explicit `team` column on all new tables |
| **Agent** | Memory from Agent A cannot be read by Agent B, even in the same team | `agent_id` foreign key on all memory/run tables |
| **User** | User A's conversation with an agent is private from User B in the same team | `user_id` column on `agent_runs` and `agent_memory` |
| **Session** | Each conversation thread is isolated | `thread_id` generated per user-session, never shared |

### 5.2 What Must Be Isolated

**Conversation history (`message_history`):**
- Strictly user-scoped. User A's messages to an agent are never visible to User B.
- Exception: approvals reviewers can see the thread that led to an approval request, but only when explicitly viewing that approval — and only the **anonymized** form (no PII, OQ-3).

**Agent facts and knowledge (`fact`, `knowledge`):**
- Agent-scoped and team-scoped. Shared across all users of the same agent within the team.
- A fact written by User A (e.g., "preferred format = bullet points") applies to all users unless `user_id` is set.
- User-specific preferences are stored with `scope=user` + `user_id` set.

**Runs (`agent_runs`):**
- `user_id` recorded on every run. Users can only see their own runs by default.
- Team admins and approvals reviewers can see all runs for agents they manage.

**Events (`agent_events`):**
- No user context (events come from external systems, not authenticated users). Team-scoped.

### 5.3 Tenant Isolation in the Data Model

All new tables carry an explicit `team` column (denormalized, same pattern as existing `approvals.team`). This allows queries to filter by team without a join, and provides an additional enforcement layer even if a bug causes an incorrect `agent_id` to be used.

```sql
-- agent_runs: add team column
ALTER TABLE agent_runs ADD COLUMN team VARCHAR(128) NOT NULL DEFAULT '';
ALTER TABLE agent_runs ADD COLUMN user_id TEXT;      -- who triggered this run

-- agent_memory: add team + user isolation columns
-- (user_id was already in the initial design; team needs to be explicit)
ALTER TABLE agent_memory ADD COLUMN team VARCHAR(128) NOT NULL DEFAULT '';
-- user_id already present in schema above
```

**Composite indexes enforcing the access pattern:**

```sql
-- Runs: team + agent + user is the primary query shape
CREATE INDEX idx_runs_team_agent ON agent_runs(team, agent_id, started_at DESC);
CREATE INDEX idx_runs_user ON agent_runs(user_id, started_at DESC)
  WHERE user_id IS NOT NULL;

-- Memory: team + agent + user + scope
CREATE INDEX idx_memory_team_agent_user ON agent_memory(team, agent_id, user_id, scope)
  WHERE user_id IS NOT NULL;
CREATE INDEX idx_memory_team_agent_scope ON agent_memory(team, agent_id, scope)
  WHERE user_id IS NULL;
```

### 5.4 Thread ID Generation

`thread_id` must encode user context to prevent two users ever sharing a conversation thread by accident:

```
thread_id format:  {team}:{agent_name}:{user_id}:{session_uuid}
example:           team-risk:fraud-detector:alice@acme.com:7f3a1b2c
```

The session UUID is generated by the platform when a new conversation starts. Agents receive `thread_id` in their run context and must pass it in all API calls. They never generate `thread_id` themselves.

### 5.5 API-Layer Enforcement

All memory and runs endpoints enforce isolation at the application layer in registry-api. The pattern for every handler:

```python
# Every memory/runs endpoint follows this shape
async def list_runs(agent_name: str, current_user: User = Depends(get_current_user)):
    agent = await get_agent(agent_name)
    
    # Tenant check: user must belong to the agent's team
    if agent.team not in current_user.teams:
        raise HTTPException(403)
    
    # User scoping: non-admins see only their own runs
    user_filter = None if current_user.is_team_admin else current_user.id
    
    return await db.query(AgentRun).filter(
        AgentRun.agent_id == agent.id,
        AgentRun.team == agent.team,           # belt + suspenders
        (AgentRun.user_id == user_filter) if user_filter else True
    ).all()
```

**Role model (additive to existing Keycloak roles):**

| Role | Can see | Can do |
|---|---|---|
| `agent:user` | Own runs, own memory | Invoke agent, read own history |
| `agent:reviewer` | All runs in team, all memory in team | Approve/reject steps, read any thread |
| `agent:admin` | Everything in team | Configure schedules/triggers, clear memory |
| `platform:admin` | Everything across all teams | Cross-team visibility for debugging |

### 5.6 Memory Isolation Rules Summary

```
scope=session  + user_id=alice    → Alice's conversation history. Private to Alice.
scope=user     + user_id=alice    → Alice's preferences (e.g., "bullet point format"). Private to Alice.
scope=agent    + user_id=null     → Agent-wide facts. Visible to all users of this agent in this team.
scope=agent    + team=risk-team   → Tenant-scoped knowledge. Not visible to other teams.
```

A reviewer viewing an approval can read `scope=session` memory for the specific `thread_id` tied to that approval — **anonymized only** (OQ-3) — and cannot browse Alice's other sessions.

### 5.7 Data Residency Considerations

For regulated industries (healthcare, finance), a team may need their memory data physically separated from other tenants. Future path: PostgreSQL schema-per-tenant (each team gets its own PG schema, row-level queries become schema-scoped queries). The current design keeps all teams in shared tables with row-level filtering — adequate for MVP and most enterprise deployments.

### 5.8 Memory × PII and the Safety Proxy (added 2026-07-02)

AgentShield's PII de-anonymization is **session-scoped** — a token maps back to real data only within one thread. Cross-session memory (`fact`, `knowledge`) breaks that boundary if it stores de-anonymized values, so memory is constrained to preserve it:

1. **Writes pass through the safety proxy.** Anything written to `agent_memory` is scanned/redacted the same way agent *output* is, before it is persisted. Memory is not a bypass around the safety layer.
2. **`agent_memory` never stores raw PII.** All memory — including session `message_history` — stores the **tokenized** form. Raw PII exists only in the session-scoped mapping (`pii_mappings`) and is applied **only at the output boundary** when rendering the final response to the **end user** (whose data it is). The LLM, agents, reviewers, and stored memory see tokens, never raw values (OQ-3). Session `message_history` TTLs with the session (default 24h).
3. **Cross-session `fact` / `knowledge` store tokenized form only** and never carry a session's PII mapping across sessions — a token from session A is meaningless in session B, so no PII can leak between sessions or users.
4. **Reviewers see the anonymized (tokenized) form** — PII is never surfaced to a human reviewer, only to the end user in their own session output (OQ-3, §5.5).
5. **Injected memory re-enters the context window through the same input path** — so it is subject to input scanning for the reading session.

Without these rules, memory is a covert channel that defeats the platform's core guarantee. This gate is a prerequisite for the memory build phase (§9).

---

## 6. Agent Memory Architecture

### 6.1 Memory Types

| Type | Scope | Description | Lifetime |
|---|---|---|---|
| `message_history` | session | Full message log for a thread (role, content, tool calls) | TTL (default 24h) |
| `summary` | session or agent | Compressed summary of older messages | Indefinite |
| `fact` | agent or user | Key-value facts the agent has been told or inferred | Indefinite |
| `knowledge` | agent | Longer-form domain knowledge (procedures, policies) | Indefinite |

### 6.2 Memory Access Patterns

**Hot path — during a run:**

```
Run starts
  → Load message_history for thread_id from Redis (< 1ms)
  → On cache miss: load from agent_memory table, populate Redis
  → Inject last N messages as context into first LLM call

Each LLM turn
  → Append {role, content, tool_calls} to Redis session buffer
  → No DB write until checkpoint

Run ends / token threshold crossed
  → Flush Redis buffer → agent_memory (message_history row)
  → If total session tokens > window_size:
      → Call LLM to summarize oldest messages
      → Write summary row, delete summarized message rows
  → Expire Redis key
```

**Cold path — semantic retrieval (RAG):**

```
Agent calls recall_context(query="fraud patterns for card type X")
  → registry-api /memory/search endpoint
  → Generate query embedding (call configured LLM provider)
  → ivfflat cosine similarity search over agent_memory.embedding
  → Return top-k facts/knowledge chunks
  → Injected into next LLM context window
```

**Writing new facts (agent learns):**

```
Agent calls remember_fact(key="preferred_format", value="bullet points")
  → POST /agents/{name}/memory with memory_type=fact, scope=user, user_id=...
  → Stored in agent_memory
  → Background job generates embedding for the fact
```

### 6.3 Memory in the Context Window

An agent with memory enabled receives additional context at the start of each run:

```
[SYSTEM PROMPT]
...base instructions...

[MEMORY CONTEXT - injected by platform]
Recent conversation summary (last session):
  User asked about Q3 fraud trends. Agent identified 3 patterns...

Relevant knowledge:
  - Policy update 2026-Q1: all transactions > $10k require dual approval
  - User prefers bullet-point summaries

[USER INPUT]
What were the fraud trends last week?
```

The platform handles injection. The agent doesn't need to call a memory API explicitly unless it wants to write a new fact or do semantic search.

### 6.4 Memory Configuration per Agent

Stored in the agent's `metadata_` JSONB column until a dedicated config table is warranted:

```json
{
  "memory": {
    "enabled": true,
    "window_size_tokens": 8192,
    "session_ttl_hours": 24,
    "summarize_threshold_tokens": 6000,
    "semantic_search_enabled": true,
    "embedding_model": "text-embedding-3-small"
  }
}
```

### 6.5 Memory and Approvals

When a long-running agent reaches an approval checkpoint, the approval detail view in Studio shows the conversation history that led to the approval request, read from `agent_memory WHERE thread_id = approval.thread_id`. **The conversation is shown anonymized** — PII placeholders stay tokenized; the reviewer, like the LLM and agents, never sees de-anonymized PII (OQ-3, §5.8). Reviewers get enough context to decide without the agent repeating tool args, but no raw personal data.

### 6.6 Memory UI in Studio

**Memory tab on Agent Detail page:**

```
Memory                                        [Clear All Memory]

Session Memory
  thread-abc123  3h ago  14 messages  2,340 tokens   [View] [Delete]
  thread-def456  1d ago  8 messages   1,100 tokens   [View] [Delete]

Agent Knowledge (4 facts)
  preferred_format    "bullet points"          [Edit] [Delete]
  approval_threshold  "transactions > $10k"    [Edit] [Delete]
  ...
  [+ Add Fact]

Memory Usage
  Sessions: 3 active · 24h TTL
  Knowledge: 4 facts · 0.8k tokens
  Total: ~3.4k tokens in context window
```

---

## 7. New Services

### 7.1 Scheduler Service

**Purpose:** Fire scheduled agent runs at the right time.

**Implementation:** Python service using APScheduler (AsyncIOScheduler). Deployed as a **2-replica** Deployment with a **distributed lock** (Postgres advisory lock / Redis SETNX) so exactly one replica fires each tick — survives a single-pod crash without missed fires (OQ-9).

**Behavior:**
1. On startup: load all `agent_schedules WHERE enabled = true` from registry-api
2. Register each as an APScheduler job with its cron expression + timezone
3. On fire: call `POST /internal/runs/start` on registry-api
4. Update `next_run_at` and `last_run_at` via `PUT /agents/{name}/schedule`
5. Poll for schedule changes every 60s (new agents, changed expressions, enable/disable)
6. On failure: log, send alert email if configured, update `last_run_status = 'failed'`

**Why not K8s CronJobs per agent:** K8s CronJobs have 1-minute granularity and each requires a separate K8s object to manage. APScheduler supports sub-minute schedules, timezone-aware expressions, and central monitoring in one process. Migrate to K8s CronJobs if the number of scheduled agents exceeds ~1,000.

**Location:** `services/scheduler/`

### 7.2 Event Gateway

**Purpose:** Receive external webhook events, validate, filter, and dispatch to run executor.

**Implementation:** Lightweight FastAPI service. Must be publicly routable (Helm chart adds an Ingress rule for `/hooks/*`).

**Single endpoint:**
```
POST /hooks/{agent_name}/{webhook_token}
Content-Type: application/json
{...payload...}
```

**Request lifecycle:**
1. Look up `agent_triggers WHERE agent_name = :name AND enabled = true`
2. Validate token: `sha256(webhook_token) == stored_hash`
3. Evaluate `filter_conditions` against payload
4. Write to `agent_events` (always, regardless of filter match)
5. If filter matched: `POST /internal/runs/start`
6. Return `202 Accepted` immediately (async dispatch)

**Why a separate service:** The event gateway needs to be publicly routable. The registry-api is internal. Keeping them separate avoids exposing the full registry-api surface to the internet.

**Location:** `services/event-gateway/`

### 7.3 Run Executor (extend declarative-runner)

**Purpose:** Execute agent runs for long-running, scheduled, and event-driven agents. Reactive runs go directly to the agent's own HTTP endpoint.

**Current state:** `services/declarative-runner/` handles canvas workflow execution. It has `node_executors.py` and `workflow_executor.py`.

**Extensions needed:**
1. Accept runs from `agent_runs` table (not just workflow canvas definitions)
2. Write step progress to `run_steps` as execution proceeds
3. Pause execution when a step requires approval — poll `approvals` table for resolution
4. Load and save agent memory at run boundaries
5. Stream step events to registry-api SSE clients

**For scheduled/event-driven agents:** They run the same execution logic as long-running or reactive agents depending on the agent's `agent_type`. The execution model determines *when* the run is triggered, not *how* the agent executes internally.

### 7.4 Deploy Controller Extensions

The deploy-controller currently reconciles `Deployment` records into K8s Deployments. Two new reconciliation targets:

- **Schedule triggers:** On agent deploy, for each `schedule` trigger → signal scheduler service to register it (or optionally create a K8s CronJob)
- **Webhook triggers:** On agent deploy, for each `webhook` trigger → call event-gateway to register it and return the generated webhook URL → store token hash in `agent_triggers`

---

## 8. Architecture Diagram

> The Studio box below is illustrative (old per-mode labels kept for the sketch); the authoritative per-mode UX lives in the experience docs (§3). This diagram is about backend components and data flow.

```
┌────────────────────────────────────────────────────────────────────┐
│                       Studio (React + Vite)                        │
│                                                                    │
│  Reactive          Long-running        Scheduled   Event-driven    │
│  Try-it chat       Run detail +        Schedule    Trigger config  │
│  + run history     step log +          config +    + event log     │
│                    approval cards      run history                 │
│                                                                    │
│                  Global Approvals Inbox (nav)                      │
└───────────────────────────────┬────────────────────────────────────┘
                                │ REST / SSE
┌───────────────────────────────▼────────────────────────────────────┐
│                      Registry API (FastAPI)                        │
│                                                                    │
│  Existing routers: agents, versions, deployments, approvals,      │
│                    opa-decisions, teams, tools, skills, workflows  │
│                                                                    │
│  New routers:      runs, run_steps, schedules, triggers,          │
│                    events, memory                                  │
│                                                                    │
│  Internal:         /internal/runs/start  (scheduler + gateway)    │
└──────┬──────────────┬──────────────┬───────────────────┬──────────┘
       │              │              │                   │
┌──────▼──────┐ ┌─────▼──────┐ ┌───▼────────────┐ ┌───▼──────────┐
│  Scheduler  │ │   Event    │ │    Deploy      │ │     Run      │
│  Service    │ │  Gateway   │ │  Controller    │ │   Executor   │
│             │ │            │ │                │ │  (extended   │
│ APScheduler │ │ FastAPI    │ │ K8s reconcile  │ │  decl-runner)│
│ polls       │ │ /hooks/*   │ │ + schedule reg │ │              │
│ agent_sched │ │ validates  │ │ + webhook reg  │ │ writes steps │
│ fires runs  │ │ + filters  │ │                │ │ reads memory │
└──────┬──────┘ └─────┬──────┘ └───────┬────────┘ └───┬──────────┘
       │              │                 │               │
       └──────────────┴─────────────────┘               │
                      │ POST /internal/runs/start        │
                      └──────────────────────────────────┘
                                       │
                       ┌───────────────▼──────────────────┐
                       │         Agent Pod (K8s)           │
                       │                                   │
                       │  Agent code (sdk or declarative)  │
                       │  Memory SDK client                │
                       │  OPA sidecar (safety proxy)       │
                       │  Approval callback endpoint       │
                       └───────────────────────────────────┘
```

**Data stores:**

```
PostgreSQL (agentshield db)
├── All existing tables (agents, versions, deployments, approvals, opa_decisions, ...)
└── New tables:
    ├── agent_runs        ← all invocations across all models
    ├── run_steps         ← step log for long-running
    ├── agent_schedules   ← cron config
    ├── agent_triggers    ← webhook config
    ├── agent_events      ← inbound event log
    └── agent_memory      ← message history, facts, knowledge + embeddings (pgvector)

Redis (new dependency)
└── Session memory cache
    ├── Key: mem:{agent_name}:{thread_id}
    ├── Value: JSON array of messages
    └── TTL: matches agent's session_ttl_hours config
```

---

## 9. Build Sequence

The phases below are **resequenced by risk and value** (revised 2026-07-02), not just dependency: reactive foundation → durable+approvals (reuses existing HITL) → memory (after the §5.8 PII rule) → scheduled → event-driven last (biggest attack surface). Each phase is independently deployable.

### Phase 3a — Foundation (reactive + the run spine)
- [ ] Migrations: `execution_shape` + `memory_enabled` on agents; **ALTER** `agent_runs` to add orchestration fields (do NOT create a second table — see §4.1.2)
- [ ] `runs.py` router: CRUD + SSE skeleton over the merged `agent_runs`
- [ ] `CreateAgentPage`: execution-shape selector + trigger config (UI)
- [ ] `AgentListPage`: execution-shape + trigger columns; row click → detail page
- [ ] `AgentDetailPage`: tabbed shell (Overview per shape, Runs table, Settings)

### Phase 3b — Durable / long-running + Global Approvals Inbox
- [ ] Migration: `run_steps`
- [ ] `run_steps.py` router; SSE fully wired (step + approval events)
- [ ] Extend `declarative-runner` → run-executor: writes steps, pauses on approvals
- [ ] Studio: Run Detail (split step log + approval cards); Global Approvals Inbox in nav
- [ ] Studio: durable Overview tab (active-run cards + New Run button)
- **Why second:** reuses the existing HITL approval flow — highest value for least new infra.

### Phase 3c — Memory (gated on §5.8)
- [ ] Resolve §5.8 memory×PII rules first (writes through safety proxy; cross-session facts anonymized)
- [ ] Migrations: `agent_memory` + pgvector extension
- [ ] `memory.py` router: read/write facts, search, clear
- [ ] Redis session buffer; memory SDK client; platform-side context injection
- [ ] Studio: Memory tab (sessions, facts, usage)

### Phase 3d — Scheduled trigger
- [ ] Migration: `agent_schedules` (as a trigger subtype)
- [ ] `schedules.py` router: CRUD + enable/disable; `services/scheduler/` (APScheduler)
- [ ] Studio: schedule config card + cron editor; scheduled runs history

### Phase 3e — Event-driven trigger (LAST — biggest attack surface)
- [ ] **Threat model the public webhook gateway first:** rate limiting, replay protection, inbound payload sanitization through the safety proxy
- [ ] Migration: `agent_triggers` + `agent_events`
- [ ] `triggers.py` + `events.py` routers; `services/event-gateway/` (FastAPI + Ingress)
- [ ] Deploy controller: webhook registration on agent deploy
- [ ] Studio: trigger config + filter builder + event log + Test Trigger modal

---

## 10. Open Questions

> These questions must be resolved before the relevant build phase begins. Each is marked with the phase it blocks and who should make the call.

---

### Multi-Tenancy & Isolation

**OQ-1** `[RESOLVED 2026-07-03]` **System principal for scheduler/webhook runs**
Use a named **service account** as the run principal — `run_by = 'serviceaccount:scheduler'` / `'serviceaccount:webhook:{trigger_id}'`. Simple string identity now; a dedicated managed Keycloak service principal per component with scoped RBAC + richer audit is deferred (see production doc §14 FI-3).

---

**OQ-2** `[OPEN]` **Cross-user visibility of agent-scoped memory**
If User A teaches an agent a fact (`scope=agent`, `user_id=null`), it becomes visible to all users of that agent in the same team. Is that always intended? Should there be a "private knowledge" toggle so authors can gate agent-scoped facts to themselves?
_Blocks: Phase 3b. Decision needed from: product._

---

**OQ-3** `[RESOLVED 2026-07-03]` **Reviewer access to conversation history — anonymized only**
Reviewers see the **anonymized** conversation only. **PII is never shown to the reviewer, the LLM, or agents** — de-anonymization placeholders stay tokenized in the reviewer view. This is a hard privacy rule, reinforcing §5.8: PII lives only in session-scoped mappings and is never surfaced to a human reviewer or persisted where an agent could read it.

---

**OQ-4** `[OPEN]` **Data residency for regulated tenants**
Current design uses shared tables with row-level team filtering (application-layer enforcement). For healthcare or financial tenants that require physical data separation, is schema-per-tenant (each team gets its own PostgreSQL schema) needed at launch, or is shared-table + RLS acceptable for the initial customer set?
_Blocks: Phase 3a (schema design). Decision needed from: product / enterprise sales._

---

### Memory

**OQ-5** `[OPEN]` **Embedding model source**
Semantic memory search requires generating embeddings. Use the team's configured LLM provider (simple, no new infra, but provider-dependent quality) or a dedicated embedding service (e.g., a sidecar running `sentence-transformers`, provider-agnostic but adds ops overhead)?
_Blocks: Phase 3b (migration 0012). Decision needed from: engineering._

---

**OQ-6** `[OPEN]` **Memory tab visibility for memory-disabled agents**
Reactive agents have `memory_enabled = false` by default. Should the Memory tab on the Agent Detail page be hidden entirely when memory is off, or always shown with an "Enable memory" prompt? The latter makes the feature more discoverable.
_Blocks: Phase 3b (Studio). Decision needed from: product / UX._

---

**OQ-7** `[OPEN]` **Approval decisions written back to memory**
When a reviewer approves or rejects a long-running agent step, should that decision be written to `agent_memory` as a `fact` so the agent can learn from reviewer feedback over time? Powerful capability, but has privacy implications (reviewer identity and rationale become agent-readable).
_Blocks: Phase 3d. Decision needed from: product / legal._

---

### Infrastructure

**OQ-8** `[OPEN]` **Event-driven trigger multiplicity**
The schema supports multiple triggers per agent (`agent_id` on `agent_triggers` is not UNIQUE), but the Studio UX is designed for one trigger per agent. Should multiple triggers be supported at launch (e.g., fire the same agent from two different webhook sources), or lock to one and revisit later?
_Blocks: Phase 3e (UX + schema). Decision needed from: product._

---

**OQ-9** `[RESOLVED 2026-07-03]` **Scheduler high-availability**
Run **2 replicas** with a distributed lock (Postgres advisory lock or Redis SETNX) so exactly one replica fires each tick. Survives a single-pod crash without missed fires; no single point of failure from day one.

---

**OQ-10** `[RESOLVED 2026-07-03]` **Long-running run timeout policy**
A **configurable setting** — per-agent run timeout with a platform default fallback. Applies to both `running` and `awaiting_approval`; reuses the `approval_timeout_worker.py` pattern. Same mechanism as the sandbox TTL (playground doc OQ-A).

---

## 11. Decisions Already Made

| Decision | Choice | Rationale |
|---|---|---|
| Memory primary store | PostgreSQL + pgvector | Keeps stack minimal; pgvector handles semantic search without separate vector DB |
| Session memory cache | Redis | Fast reads during hot run path; persist to PG on session end |
| Scheduler implementation | APScheduler, **2 replicas + distributed lock** (OQ-9) | Sub-minute granularity, timezone-aware, simpler ops than K8s CronJob-per-agent; HA from day one |
| Event gateway separation | Separate service | Registry-api is internal; gateway must be public-facing |
| `agent_runs` as central primitive | All invocations → one table | Approvals, OPA decisions, memory all link via `thread_id`; simplifies querying |
| Execution shape/trigger vs agent_type | Separate fields | `agent_type` = implementation (sdk/declarative); `execution_shape` + triggers = runtime behavior (superseded by rev 2026-07-02 rows above) |
| Approval context from memory | Reviewers see message history | Read `agent_memory WHERE thread_id = approval.thread_id`; no schema change needed |
| Multi-tenant isolation strategy | Application-layer enforcement + explicit `team` column on all new tables | Simpler than PostgreSQL RLS for MVP; adds defense-in-depth team column as belt-and-suspenders |
| Thread ID generation | Platform-generated, format `{team}:{agent}:{user_id}:{uuid}` | Prevents accidental thread sharing across users; agents never generate thread IDs |
| User isolation for conversation history | `user_id` column on `agent_runs` and `agent_memory`; non-admins see only own runs | Default private; reviewers and admins can access others' threads with explicit role |
| **Execution shape vs trigger (rev 2026-07-02)** | Two orthogonal fields, not one enum | `execution_shape` (reactive/durable) is how a run behaves; `trigger` (manual/api/schedule/webhook) is what starts it; composable, multiple triggers per agent |
| **`agent_runs` reconciliation (rev 2026-07-02)** | Merge into the existing table | The built observability `agent_runs` is ALTERed to add orchestration fields — one run spine, not two tables |
| **Memory × session-scoped PII (rev 2026-07-02)** | Constrain memory to preserve PII scoping | Writes scanned by safety proxy; only session `message_history` holds de-anon PII; cross-session facts store anonymized form (§5.8) |
| **Build sequence (rev 2026-07-02)** | Event-driven last | Public webhook gateway is the highest new attack surface; needs a threat model before build |
| **Executable = Agent \| Workflow (rev 2026-07-03)** | Workflow redefined as a composite executable (collection of agents) on the shared substrate | Modes / triggers / memory / runs / playground / production are identical; only orchestration differs (run tree via `parent_run_id`). Old canvas "workflow" → "agent graph". No parallel stack (§2.6, §4.5, Decision 22) |

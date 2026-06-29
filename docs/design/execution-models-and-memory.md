# AgentShield — Execution Models & Agent Memory Design

**Status**: DRAFT — Design complete, not yet implemented  
**Date**: 2026-06-27  
**Author**: Karthik + Claude  
**Phase**: 3 (follows P1 safety proxy + P2 canvas/skills)

---

## 1. Problem Statement

AgentShield currently treats all agents as a single category: deploy a container, call it, get a response. Real enterprise agents don't work that way. A fraud detection agent that runs continuously on payment events is architecturally different from a weekly compliance report agent, which is different from a multi-step contract review agent that needs a human to approve before filing. These differences affect the deployment model, the UX for configuring and monitoring them, and the infrastructure required to run them safely.

Additionally, no agent in the platform today has memory. Every run is stateless. This means agents can't learn from prior sessions, can't maintain conversation context across turns, and can't accumulate domain-specific knowledge over time — all of which are table-stakes for enterprise agents.

This document captures the full design for four first-class execution models and a layered memory system, including backend data model changes, new services, API contracts, and Studio UX.

---

## 2. Execution Models

### 2.1 Overview

Stateless Agents: They process each request in complete isolation. They have zero memory of previous interactions, making them highly predictable and easy to scale (e.g., standard API translation utilities).Short-Term Stateful Agents: They maintain a memory of the current active session or conversation context. Once the user closes the window or the script ends, the memory is cleared.Long-Term / Semantic Memory Agents: They leverage vector databases and graph databases to store user preferences, historical errors, and past solutions indefinitely. They pull this context back into their active memory whenever a relevant scenario occurs.

An **execution model** describes how an agent is invoked, how long it runs, and what relationship it has with time and external events. It is orthogonal to `agent_type` (sdk vs declarative), which describes implementation. Both can be set independently.

| Model | Invoked by | Duration | State | Primary UX pattern |
|---|---|---|---|---|
| **Reactive** | API call / user message | Milliseconds–seconds | Stateless per call | Chat + stream |
| **Long-running** | Manual trigger or API | Minutes–hours | Stateful, checkpointed | Job tracker + approval inbox |
| **Scheduled** | Time (cron) | Seconds–minutes | Stateless per run | Job scheduler + run history |
| **Event-driven** | External event (webhook) | Seconds–minutes | Stateless per run | Trigger config + event log |

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

**UX in Studio:**
- Agent Detail → Overview tab has a "Try it" panel: chat input + streamed response
- Tool calls rendered as collapsible inline blocks mid-stream
- Token count + latency shown in footer, updating live during stream
- Runs tab: flat table of past invocations (status, tokens, latency, log link)
- No step log or approval UI — those are irrelevant for this model

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

**UX in Studio:**
- Agent Detail → Overview shows active runs as cards with current step and status
- "New Run" button opens a launch panel (input payload + optional notes)
- Run Detail page: split layout — step list on left (✓/●/⚠ per step), interaction panel on right
- When a step is awaiting approval: right panel shows approval card with tool args + Approve / Reject / Edit & Approve
- Global Approvals Inbox in the nav bar (badge count of pending approvals across all agents)
- Runs tab: table of all runs with status, duration, step count

### 2.4 Scheduled

**Mental model:** a recurring job. Set a schedule, it fires automatically. You need to know immediately when it breaks.

**Canonical use cases:**
- Weekly compliance report generation
- Daily data sync and validation
- Hourly anomaly scan over logs

**Key behaviors:**
- Fired by a scheduler service on a cron expression
- Each fire creates an `agent_run` record with `trigger_type=scheduled`
- Runs are otherwise identical to reactive or long-running runs depending on what the agent does
- Enable/disable without deleting the schedule
- Alert on failure (email/webhook)

**UX in Studio:**
- Agent Detail → Overview: schedule config card (expression, timezone, enabled toggle, next 3 fire times)
- "Edit Schedule" opens a modal: preset buttons + editable cron fields + live human-readable translation
- Runs tab: history table with fire time, status, duration, log link — failed rows are red-tinted
- Alert configuration inline on the schedule card, not buried in settings

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

**UX in Studio:**
- Agent Detail → Overview: trigger config panel (trigger type, generated URL + copy button, "● Listening" status)
- Filter condition builder: `event_type == "payment.fail"` with +Add condition, no raw JSON required
- "Test Trigger" sends a sample payload to the real webhook — same code path as production
- Event log shows recent events: matched/filtered, payload preview, linked run if triggered

---

## 3. Studio UX — Information Architecture

### 3.1 Nav Change

Add **Approvals** to the top nav with a badge count of pending approvals. This is the global inbox for long-running agent approvals — it should not require navigating to a specific agent.

```
AgentShield Studio  |  Workflows  Tools  Skills  Agents  Approvals (3)  Providers
```

### 3.2 Agent Registration — Execution Model Step

`CreateAgentPage` gains an execution model selector rendered as a card grid. Selecting a model reveals model-specific configuration fields below (schedule expression for scheduled, initial trigger config for event-driven). Reactive and long-running have no extra config at registration time.

### 3.3 Agent List Page

The `agent_type` column is replaced with two columns: **Execution Model** (reactive / long-running / scheduled / event-driven) and **Impl** (sdk / declarative). Clicking a row navigates to `/agents/:name` instead of opening an inline edit form.

### 3.4 Agent Detail Page — Shared Shell

Every agent gets the same shell with model-specific tab content:

```
/agents/:name

← Agents   {agent-name}              [{status badge}]
            {execution_model} · {team} · {model}

[Overview]  [Runs]  [Memory]  [Versions]  [Settings]
──────────────────────────────────────────────────────
{tab content varies by execution model}
```

**Tabs present on all models:** Overview, Runs, Memory, Versions, Settings  
**Tab content that differs:** Overview (per model), Runs (columns differ slightly)  
**Memory tab:** shared design, described in Section 5

---

## 4. Backend Architecture

### 4.1 Data Model Changes

#### 4.1.1 `agents` table — two new columns

```sql
ALTER TABLE agents
  ADD COLUMN execution_model VARCHAR(32)
    CHECK (execution_model IN ('reactive','long_running','scheduled','event_driven'))
    NOT NULL DEFAULT 'reactive',
  ADD COLUMN memory_enabled BOOLEAN NOT NULL DEFAULT false;
```

#### 4.1.2 `agent_runs` — new table (core primitive)

The central table that everything else hangs off. Every invocation of an agent — regardless of execution model or trigger type — creates one row here.

```sql
CREATE TABLE agent_runs (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id         UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  agent_name       VARCHAR(128) NOT NULL,          -- denormalized for audit resilience
  execution_model  VARCHAR(32) NOT NULL,            -- snapshot from agent at run time
  status           VARCHAR(32) NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','running','awaiting_approval',
                                     'completed','failed','cancelled')),
  trigger_type     VARCHAR(32) NOT NULL DEFAULT 'manual'
                   CHECK (trigger_type IN ('manual','scheduled','webhook','api_call')),
  trigger_payload  JSONB,                           -- raw event/input that started the run
  output           JSONB,
  thread_id        TEXT,                            -- links to approvals + opa_decisions
  parent_run_id    UUID REFERENCES agent_runs(id),  -- for sub-agent orchestration
  run_by           TEXT,                            -- 'user:alice', 'scheduler', 'webhook:abc'
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at     TIMESTAMPTZ,
  error_message    TEXT,
  token_count      INTEGER,
  latency_ms       INTEGER
);

CREATE INDEX idx_runs_agent_id ON agent_runs(agent_id, started_at DESC NULLS LAST);
CREATE INDEX idx_runs_status ON agent_runs(status);
CREATE INDEX idx_runs_thread_id ON agent_runs(thread_id);
```

**Relationship to existing tables:**
- `approvals.thread_id` → `agent_runs.thread_id` (logical link, not FK — threads can exist without a formal run in legacy data)
- `opa_decisions.thread_id` → `agent_runs.thread_id` (same)
- Future: add `run_id` FK columns to `approvals` and `opa_decisions` in a migration

#### 4.1.3 `run_steps` — new table (long-running only)

Populated only for `execution_model = 'long_running'`. Each logical step of the agent's execution gets a row.

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

One row per scheduled agent. Scheduler service reads this table to determine what to fire and when.

```sql
CREATE TABLE agent_schedules (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id         UUID NOT NULL UNIQUE REFERENCES agents(id) ON DELETE CASCADE,
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

```
0006_add_execution_model_memory_flag.py   — ALTER agents (2 new columns)
0007_add_agent_runs.py                    — agent_runs table + indexes
0008_add_run_steps.py                     — run_steps table
0009_add_agent_schedules.py               — agent_schedules table
0010_add_agent_triggers_events.py         — agent_triggers + agent_events tables
0011_add_agent_memory.py                  — agent_memory table (no embedding yet)
0012_add_pgvector_embedding.py            — CREATE EXTENSION vector + embedding column + ivfflat index
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
  "trigger_type": "scheduled" | "webhook",
  "trigger_payload": {...},
  "run_by": "scheduler" | "webhook:{trigger_id}"
}
```

This endpoint is not exposed through the public ingress — only reachable cluster-internally.

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
- Exception: approvals reviewers can see the thread that led to an approval request, but only when explicitly viewing that approval.

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

A reviewer viewing an approval can read `scope=session` memory for the specific `thread_id` tied to that approval — but cannot browse Alice's other sessions.

### 5.7 Data Residency Considerations

For regulated industries (healthcare, finance), a team may need their memory data physically separated from other tenants. Future path: PostgreSQL schema-per-tenant (each team gets its own PG schema, row-level queries become schema-scoped queries). The current design keeps all teams in shared tables with row-level filtering — adequate for MVP and most enterprise deployments.

---

## 6. Agent Memory Architecture

### 5.1 Memory Types

| Type | Scope | Description | Lifetime |
|---|---|---|---|
| `message_history` | session | Full message log for a thread (role, content, tool calls) | TTL (default 24h) |
| `summary` | session or agent | Compressed summary of older messages | Indefinite |
| `fact` | agent or user | Key-value facts the agent has been told or inferred | Indefinite |
| `knowledge` | agent | Longer-form domain knowledge (procedures, policies) | Indefinite |

### 5.2 Memory Access Patterns

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

### 5.3 Memory in the Context Window

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

### 5.4 Memory Configuration per Agent

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

### 5.5 Memory and Approvals

When a long-running agent reaches an approval checkpoint, the approval detail view in Studio should show the conversation history that led to the approval request. This is read from `agent_memory WHERE thread_id = approval.thread_id`. Reviewers get full context without the agent needing to repeat itself in the tool args.

### 5.6 Memory UI in Studio

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

### 6.1 Scheduler Service

**Purpose:** Fire scheduled agent runs at the right time.

**Implementation:** Python service using APScheduler (AsyncIOScheduler). Deployed as a single-replica Deployment (not scaled — leader election not needed at this scale).

**Behavior:**
1. On startup: load all `agent_schedules WHERE enabled = true` from registry-api
2. Register each as an APScheduler job with its cron expression + timezone
3. On fire: call `POST /internal/runs/start` on registry-api
4. Update `next_run_at` and `last_run_at` via `PUT /agents/{name}/schedule`
5. Poll for schedule changes every 60s (new agents, changed expressions, enable/disable)
6. On failure: log, send alert email if configured, update `last_run_status = 'failed'`

**Why not K8s CronJobs per agent:** K8s CronJobs have 1-minute granularity and each requires a separate K8s object to manage. APScheduler supports sub-minute schedules, timezone-aware expressions, and central monitoring in one process. Migrate to K8s CronJobs if the number of scheduled agents exceeds ~1,000.

**Location:** `services/scheduler/`

### 6.2 Event Gateway

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

### 6.3 Run Executor (extend declarative-runner)

**Purpose:** Execute agent runs for long-running, scheduled, and event-driven agents. Reactive runs go directly to the agent's own HTTP endpoint.

**Current state:** `services/declarative-runner/` handles canvas workflow execution. It has `node_executors.py` and `workflow_executor.py`.

**Extensions needed:**
1. Accept runs from `agent_runs` table (not just workflow canvas definitions)
2. Write step progress to `run_steps` as execution proceeds
3. Pause execution when a step requires approval — poll `approvals` table for resolution
4. Load and save agent memory at run boundaries
5. Stream step events to registry-api SSE clients

**For scheduled/event-driven agents:** They run the same execution logic as long-running or reactive agents depending on the agent's `agent_type`. The execution model determines *when* the run is triggered, not *how* the agent executes internally.

### 6.4 Deploy Controller Extensions

The deploy-controller currently reconciles `Deployment` records into K8s Deployments. Two new reconciliation targets:

- **Scheduled agents:** On agent deploy with `execution_model=scheduled` → signal scheduler service to register the schedule (or optionally create a K8s CronJob)
- **Event-driven agents:** On agent deploy with `execution_model=event_driven` → call event-gateway to register the trigger and return the generated webhook URL → store token hash in `agent_triggers`

---

## 8. Architecture Diagram

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

The phases below are ordered by dependency and value delivered. Each phase is independently deployable.

### Phase 3a — Foundation (unblocks all Studio work)
- [ ] Migrations 0006–0007: `execution_model` on agents + `agent_runs` table
- [ ] `runs.py` router: CRUD + basic SSE skeleton
- [ ] `CreateAgentPage`: execution model selector (UI only, no model-specific config yet)
- [ ] `AgentListPage`: execution model column + row click → detail page
- [ ] `AgentDetailPage`: shell with tabs (Overview placeholder per model, Runs table, Settings)

### Phase 3b — Reactive + Memory
- [ ] Migrations 0011–0012: `agent_memory` + pgvector extension
- [ ] `memory.py` router: read/write facts, search, clear
- [ ] Redis for session buffer (deploy Redis if not present)
- [ ] Memory SDK client (`services/memory-sdk/` or part of agent base image)
- [ ] Studio: Reactive "Try it" panel + streaming tool call display
- [ ] Studio: Memory tab (session list, facts, usage stats)

### Phase 3c — Scheduled
- [ ] Migration 0009: `agent_schedules`
- [ ] `schedules.py` router: CRUD + enable/disable
- [ ] `services/scheduler/`: APScheduler service, Dockerfile, Helm chart entry
- [ ] Studio: Schedule config card + human-readable cron editor modal
- [ ] Studio: Scheduled runs tab with history table

### Phase 3d — Long-Running + Global Approvals Inbox
- [ ] Migration 0008: `run_steps`
- [ ] `run_steps.py` router
- [ ] SSE stream fully wired (step events, approval events)
- [ ] Extend `declarative-runner` → `run-executor`: writes steps, pauses on approvals
- [ ] Studio: Run Detail page (split step log + approval cards)
- [ ] Studio: Global Approvals Inbox in nav
- [ ] Studio: Long-running Overview tab (active runs cards + New Run button)

### Phase 3e — Event-Driven
- [ ] Migration 0010: `agent_triggers` + `agent_events`
- [ ] `triggers.py` + `events.py` routers
- [ ] `services/event-gateway/`: FastAPI service, Dockerfile, Helm chart + Ingress rule
- [ ] Deploy controller: webhook registration on agent deploy
- [ ] Studio: Trigger config panel + filter builder + event log
- [ ] Studio: Test Trigger modal

---

## 10. Open Questions

> These questions must be resolved before the relevant build phase begins. Each is marked with the phase it blocks and who should make the call.

---

### Multi-Tenancy & Isolation

**OQ-1** `[OPEN]` **System principal for scheduler/webhook runs**
When the scheduler or event gateway fires a run, there's no human user. The `user_id` on `agent_runs` would be a system value like `scheduler` or `webhook:{trigger_id}`. Should these be treated as a named system principal in Keycloak (cleaner audit trail, more setup), or as a special bypass in the auth middleware (simpler, but a carve-out in the auth model)?
_Blocks: Phase 3a. Decision needed from: platform architect / security._

---

**OQ-2** `[OPEN]` **Cross-user visibility of agent-scoped memory**
If User A teaches an agent a fact (`scope=agent`, `user_id=null`), it becomes visible to all users of that agent in the same team. Is that always intended? Should there be a "private knowledge" toggle so authors can gate agent-scoped facts to themselves?
_Blocks: Phase 3b. Decision needed from: product._

---

**OQ-3** `[OPEN]` **Reviewer access to full conversation history**
The design lets approvals reviewers read `scope=session` memory for the thread tied to their approval — meaning they see the full user conversation. Is that acceptable under the platform's data policy, or should reviewers get a summarized view that omits personal messages?
_Blocks: Phase 3b + 3d. Decision needed from: legal / data privacy._

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

**OQ-9** `[OPEN]` **Scheduler high-availability**
The scheduler service is designed as single-replica. A crash causes missed fires for all scheduled agents until the pod restarts. For critical schedules, is single-replica + fast restart (K8s liveness probe) acceptable, or do we need a distributed lock (Postgres advisory lock or Redis SETNX) to support multi-replica deployment from day one?
_Blocks: Phase 3c. Decision needed from: engineering / SRE._

---

**OQ-10** `[OPEN]` **Long-running run timeout policy**
What is the maximum wall-clock time a long-running run can stay in `awaiting_approval` or `running` before the platform auto-cancels it? The deploy-controller already has an `approval_timeout_worker.py` pattern. Should run timeout use the same configurable-per-agent TTL, or a global platform default?
_Blocks: Phase 3d. Decision needed from: product / engineering._

---

## 11. Decisions Already Made

| Decision | Choice | Rationale |
|---|---|---|
| Memory primary store | PostgreSQL + pgvector | Keeps stack minimal; pgvector handles semantic search without separate vector DB |
| Session memory cache | Redis | Fast reads during hot run path; persist to PG on session end |
| Scheduler implementation | APScheduler (single process) | Sub-minute granularity, timezone-aware, simpler ops than K8s CronJob-per-agent |
| Event gateway separation | Separate service | Registry-api is internal; gateway must be public-facing |
| `agent_runs` as central primitive | All invocations → one table | Approvals, OPA decisions, memory all link via `thread_id`; simplifies querying |
| Execution model vs agent_type | Separate fields | `agent_type` = implementation (sdk/declarative); `execution_model` = runtime behavior |
| Approval context from memory | Reviewers see message history | Read `agent_memory WHERE thread_id = approval.thread_id`; no schema change needed |
| Multi-tenant isolation strategy | Application-layer enforcement + explicit `team` column on all new tables | Simpler than PostgreSQL RLS for MVP; adds defense-in-depth team column as belt-and-suspenders |
| Thread ID generation | Platform-generated, format `{team}:{agent}:{user_id}:{uuid}` | Prevents accidental thread sharing across users; agents never generate thread IDs |
| User isolation for conversation history | `user_id` column on `agent_runs` and `agent_memory`; non-admins see only own runs | Default private; reviewers and admins can access others' threads with explicit role |

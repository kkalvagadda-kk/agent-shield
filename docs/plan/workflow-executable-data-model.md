# Workflow Executable — Data Model

**Status**: FINAL  
**Date**: 2026-07-05  
**Scope**: Decision 22 — rename, new composite Workflow entity, trigger + run targeting extensions

---

## 1. Summary of Changes

Three categories:
1. **Rename**: `workflows → agent_graphs`, `workflow_versions → agent_graph_versions`, `agent_versions.workflow_id → agent_graph_id`
2. **New tables**: `workflows` (composite executable), `workflow_members` (agent membership)
3. **Column additions**: `workflow_id` nullable FK on `agent_triggers` and `agent_runs`

---

## 2. Renamed Entities

### 2.1 `agent_graphs` (formerly `workflows`)

No schema changes — pure rename. All existing rows and columns preserved.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | unchanged |
| `name` | VARCHAR(256) | single declarative agent's canvas graph name |
| `team` | VARCHAR(128) | tenant |
| `description` | TEXT NULL | |
| `status` | VARCHAR(32) CHECK draft\|published\|archived | |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |
| `created_by` | VARCHAR(256) NULL | |
| `metadata` | JSONB DEFAULT '{}' | |

**SQLAlchemy model**: `AgentGraph` (was `Workflow`)

### 2.2 `agent_graph_versions` (formerly `workflow_versions`)

No schema changes — pure rename.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `agent_graph_id` | UUID FK `agent_graphs.id` CASCADE | was `workflow_id` |
| `version_number` | INTEGER | |
| `definition` | JSONB | canvas graph JSON |
| `change_summary` | TEXT NULL | |
| `created_at` | TIMESTAMPTZ | |
| `created_by` | VARCHAR(256) NULL | |

**Unique constraint**: `(agent_graph_id, version_number)`  
**SQLAlchemy model**: `AgentGraphVersion` (was `WorkflowVersion`)

### 2.3 `agent_versions.agent_graph_id` (formerly `workflow_id`)

Column rename only. FK retarget happens automatically when parent table is renamed.

| Column | Type | Notes |
|---|---|---|
| `agent_graph_id` | UUID NULL FK `agent_graphs.id` | was `workflow_id FK workflows.id` |

---

## 3. New Entities

### 3.1 `workflows` (composite executable — NEW)

This table is created AFTER the rename migration frees the name `workflows`.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID PK | `DEFAULT gen_random_uuid()` | |
| `name` | VARCHAR(256) | NOT NULL | composite workflow name, unique per team |
| `team` | VARCHAR(128) | NOT NULL | tenant |
| `description` | TEXT | NULL | |
| `execution_shape` | VARCHAR(16) | NOT NULL DEFAULT 'durable' CHECK reactive\|durable | inherited from executable abstraction |
| `memory_enabled` | BOOLEAN | NOT NULL DEFAULT false | |
| `orchestration` | VARCHAR(32) | NOT NULL DEFAULT 'sequential' CHECK sequential\|supervisor\|handoff | controls how members are invoked |
| `status` | VARCHAR(32) | NOT NULL DEFAULT 'draft' CHECK draft\|published\|archived | authoring lifecycle |
| `publish_status` | VARCHAR(32) | NOT NULL DEFAULT 'private' | mirrors agents.publish_status |
| `created_by` | VARCHAR(256) | NULL | Keycloak sub of creator |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | |

**Indexes**:
```sql
CREATE INDEX idx_workflows_team ON workflows(team);
CREATE INDEX idx_workflows_status ON workflows(status);
CREATE UNIQUE INDEX uq_workflows_name_team ON workflows(name, team);
```

**SQLAlchemy model**: `CompositeWorkflow`

### 3.2 `workflow_members` (composite workflow member agents — NEW)

Each row links an existing agent to a composite workflow. The agent must be in the same team as the workflow.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `workflow_id` | UUID | NOT NULL FK `workflows(id)` ON DELETE CASCADE | |
| `agent_id` | UUID | NOT NULL FK `agents(id)` | member agent (must be same team) |
| `role` | VARCHAR(64) | NULL | 'supervisor' \| 'worker' \| free-form label |
| `position` | INTEGER | NULL | ordering for sequential orchestration (1-based) |
| `routing` | JSONB | NULL DEFAULT '{}' | handoff edge conditions (deferred; reserved) |
| `added_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | |

**Primary key**: `(workflow_id, agent_id)`  
**Index**: `CREATE INDEX idx_workflow_members_agent_id ON workflow_members(agent_id);`

**Business rules** (enforced in application layer):
- `agent.team MUST EQUAL workflow.team` (cross-team composition is forbidden)
- For `orchestration=sequential`, all members must have a non-NULL, unique `position`
- A workflow must have at least 1 member before a run can be triggered

**SQLAlchemy model**: `WorkflowMember`

---

## 4. Column Additions to Existing Tables

### 4.1 `agent_triggers.workflow_id` (nullable FK — NEW column)

| Column | Type | Constraints |
|---|---|---|
| `workflow_id` | UUID NULL | FK `workflows(id)` ON DELETE CASCADE |

**Exactly-one constraint** (CHECK):
```sql
ALTER TABLE agent_triggers
  ADD CONSTRAINT ck_agent_triggers_target
  CHECK (num_nonnulls(agent_id, workflow_id) = 1);
```

This replaces the implicit assumption that `agent_id` is always set. After migration, all existing rows have `agent_id IS NOT NULL` and `workflow_id IS NULL` — the constraint is satisfied.

### 4.2 `agent_runs.workflow_id` (nullable FK — NEW column)

| Column | Type | Constraints |
|---|---|---|
| `workflow_id` | UUID NULL | FK `workflows(id)` ON DELETE SET NULL |

Set only on *parent* workflow runs. Child agent runs within a workflow have `workflow_id = NULL` and `parent_run_id = <parent_run_id>`.

**Index**:
```sql
CREATE INDEX idx_agent_runs_workflow_id ON agent_runs(workflow_id) WHERE workflow_id IS NOT NULL;
```

---

## 5. State Transitions

### 5.1 Composite Workflow Lifecycle

```
draft → published → archived
  ↑
  └── published → draft (re-draft for editing)
```

- `draft`: created, members being added, not yet runnable in production
- `published`: at least one passing eval run; visible in catalog; production triggers can fire
- `archived`: soft-deleted; no new runs; historical data retained

### 5.2 Workflow Run Tree State

```
PARENT WORKFLOW RUN:
  queued → running → completed
                   → failed

CHILD AGENT RUN (per member):
  queued → running → completed
                   → failed
                   → awaiting_approval → running (on approval) → completed/failed
                   → cancelled
```

Parent run status logic:
- `running` while any child is `queued`, `running`, or `awaiting_approval`
- `completed` when ALL children are `completed`
- `failed` when ANY child fails and `orchestration=sequential` (fail-fast); partial failure rules for other modes are deferred

---

## 6. Migration DDL

### Migration 0026: Rename workflows → agent_graphs

```sql
-- This migration renames the OLD "single-agent canvas graph" tables.
-- It does NOT change any row data.

-- Step 1: rename tables
ALTER TABLE workflows RENAME TO agent_graphs;
ALTER TABLE workflow_versions RENAME TO agent_graph_versions;

-- Step 2: rename column on agent_versions
ALTER TABLE agent_versions RENAME COLUMN workflow_id TO agent_graph_id;

-- Step 3: rename FK constraint (cosmetic but keeps schema consistent)
-- Note: actual constraint name may vary; Alembic uses op.drop_constraint / op.create_foreign_key
ALTER TABLE agent_versions
  RENAME CONSTRAINT agent_versions_workflow_id_fkey
  TO agent_versions_agent_graph_id_fkey;

-- Step 4: rename indexes
ALTER INDEX idx_workflows_team RENAME TO idx_agent_graphs_team;
ALTER INDEX idx_workflows_status RENAME TO idx_agent_graphs_status;
ALTER INDEX idx_workflow_versions_workflow_id RENAME TO idx_agent_graph_versions_agent_graph_id;
ALTER INDEX uq_workflow_versions RENAME TO uq_agent_graph_versions;
```

**Alembic implementation** uses `op.rename_table`, `op.alter_column` (type=None, new_column_name=), `op.drop_index` / `op.create_index`, `op.drop_constraint` / `op.create_foreign_key`.

**Downgrade** reverses every rename.

### Migration 0027: Add composite workflows + extend triggers/runs

```sql
-- Step 1: create new workflows table (composite executable)
CREATE TABLE workflows (
  id              UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  name            VARCHAR(256) NOT NULL,
  team            VARCHAR(128) NOT NULL,
  description     TEXT,
  execution_shape VARCHAR(16)  NOT NULL DEFAULT 'durable'
                  CONSTRAINT ck_workflows_execution_shape CHECK (execution_shape IN ('reactive','durable')),
  memory_enabled  BOOLEAN NOT NULL DEFAULT false,
  orchestration   VARCHAR(32)  NOT NULL DEFAULT 'sequential'
                  CONSTRAINT ck_workflows_orchestration CHECK (orchestration IN ('sequential','supervisor','handoff')),
  status          VARCHAR(32)  NOT NULL DEFAULT 'draft'
                  CONSTRAINT ck_workflows_status CHECK (status IN ('draft','published','archived')),
  publish_status  VARCHAR(32)  NOT NULL DEFAULT 'private',
  created_by      VARCHAR(256),
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_workflows_team   ON workflows(team);
CREATE INDEX idx_workflows_status ON workflows(status);
CREATE UNIQUE INDEX uq_workflows_name_team ON workflows(name, team);

-- Step 2: create workflow_members table
CREATE TABLE workflow_members (
  workflow_id  UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  agent_id     UUID NOT NULL REFERENCES agents(id),
  role         VARCHAR(64),
  position     INTEGER,
  routing      JSONB NOT NULL DEFAULT '{}',
  added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, agent_id)
);

CREATE INDEX idx_workflow_members_agent_id ON workflow_members(agent_id);

-- Step 3: add workflow_id to agent_triggers
ALTER TABLE agent_triggers
  ADD COLUMN workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE;

ALTER TABLE agent_triggers
  ADD CONSTRAINT ck_agent_triggers_target
  CHECK (num_nonnulls(agent_id, workflow_id) = 1);

CREATE INDEX idx_agent_triggers_workflow ON agent_triggers(workflow_id)
  WHERE workflow_id IS NOT NULL;

-- Step 4: add workflow_id to agent_runs
ALTER TABLE agent_runs
  ADD COLUMN workflow_id UUID REFERENCES workflows(id) ON DELETE SET NULL;

CREATE INDEX idx_agent_runs_workflow_id ON agent_runs(workflow_id)
  WHERE workflow_id IS NOT NULL;
```

**Downgrade** drops the new tables and removes the added columns (with `DROP COLUMN`, `DROP INDEX`, `DROP CONSTRAINT`).

---

## 7. SQLAlchemy Model Summary

| Model Class | `__tablename__` | Status |
|---|---|---|
| `AgentGraph` | `agent_graphs` | renamed (was `Workflow`) |
| `AgentGraphVersion` | `agent_graph_versions` | renamed (was `WorkflowVersion`) |
| `CompositeWorkflow` | `workflows` | NEW |
| `WorkflowMember` | `workflow_members` | NEW |
| `AgentVersion` | `agent_versions` | modified: `agent_graph_id` (was `workflow_id`) |
| `AgentRun` | `agent_runs` | modified: + `workflow_id` FK |
| `AgentTrigger` | `agent_triggers` | modified: + `workflow_id` FK + CHECK constraint |

---

## 8. Impact on Existing Schemas (Pydantic)

| Schema Class | Change |
|---|---|
| `AgentVersionCreate` | `workflow_id` field → `agent_graph_id` |
| `AgentVersionResponse` | `workflow_id` field → `agent_graph_id` |
| `WorkflowCreate` | renamed → `AgentGraphCreate` (same fields) |
| `WorkflowUpdate` | renamed → `AgentGraphUpdate` |
| `WorkflowResponse` | renamed → `AgentGraphResponse` |
| `WorkflowVersionResponse` | renamed → `AgentGraphVersionResponse` |
| `WorkflowWithDefinitionResponse` | renamed → `AgentGraphWithDefinitionResponse` |
| `WorkflowDeployRequest` | renamed → `AgentGraphDeployRequest` |
| `InternalRunStartRequest` | + `workflow_id: uuid.UUID | None = None`; validator: exactly one of `agent_name`/`workflow_id` |
| NEW `CompositeWorkflowCreate` | `name`, `team`, `description`, `execution_shape`, `orchestration`, `memory_enabled` |
| NEW `CompositeWorkflowUpdate` | `description`, `execution_shape`, `orchestration`, `memory_enabled`, `status` |
| NEW `CompositeWorkflowResponse` | all columns + `member_count: int` |
| NEW `WorkflowMemberCreate` | `agent_id: UUID`, `role?: str`, `position?: int` |
| NEW `WorkflowMemberResponse` | all columns + `agent_name: str` (denormalized) |
| NEW `WorkflowRunCreate` | `input_payload: dict`, `trigger_type: str = 'manual'`, `run_by: str` |
| NEW `WorkflowRunTreeResponse` | `parent: AgentRunResponse`, `children: list[AgentRunResponse]` |

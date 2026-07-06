# Workflow Executable — Research & Decision Log

**Status**: FINAL  
**Date**: 2026-07-05  
**Scope**: Decision 22 implementation — `executable = Agent | Workflow`

---

## 1. Resolved Design Question: `nullable workflow_id` vs Polymorphic `executable_id`

### The Question

`agent_triggers` currently has `agent_id UUID FK agents(id)` — it only targets agents. The new composite Workflow must also be triggerable (schedules, webhooks). Two options:

| Option | Description |
|---|---|
| **A: Nullable `workflow_id`** | Add `workflow_id UUID NULL REFERENCES workflows(id)` alongside `agent_id`. Exactly-one enforced by CHECK constraint. |
| **B: Polymorphic `executable_id`** | Replace `agent_id` with `executable_id UUID` + `executable_type VARCHAR CHECK IN ('agent','workflow')`. No DB-level FK enforcement. |

### Decision: **Option A — nullable `workflow_id`**

**Rationale:**

1. **PostgreSQL has no polymorphic FK.** Option B destroys referential integrity entirely — a dangling `executable_id` cannot be caught by the database. We would have to rely exclusively on application-layer constraints, creating a class of orphaned rows that is hard to detect and audit.

2. **Additive migration.** Option A adds one nullable column to two tables (`agent_triggers`, `agent_runs`). The existing `agents.id` FK remains unchanged. The migration is reversible and non-breaking: existing rows continue to have `workflow_id = NULL` (they are agent-targeted), while new workflow triggers set `agent_id = NULL` and `workflow_id = set`.

3. **DB-level exactly-one guarantee.** A pair of CHECK constraints enforces the invariant at the database layer:
   ```sql
   -- Exactly one of agent_id / workflow_id must be non-NULL
   CHECK (num_nonnulls(agent_id, workflow_id) = 1)
   ```
   This is concise and enforced before any application code runs.

4. **Query simplicity.** The scheduler service queries `agent_triggers` for schedule-type triggers. With Option A, the query is:
   ```sql
   WHERE trigger_type = 'schedule'
     AND enabled = true
     AND (agent_id = :id OR workflow_id = :id)
   ```
   Readable, indexable, no join needed. Option B requires a `CASE` or two queries.

5. **Same pattern used for `agent_runs.workflow_id`.** A workflow run records `workflow_id` on the parent `AgentRun` row. The child runs (member agents) have `workflow_id = NULL` and `parent_run_id = <workflow_run>`. This matches the existing `parent_run_id` spine and is easy to query: `WHERE workflow_id = :id` returns only workflow runs; `WHERE parent_run_id = :id` returns children.

**Impact on `agent_runs`:**
- Add `workflow_id UUID NULL REFERENCES workflows(id)` — set only on the *parent* workflow run.
- Child agent runs within a workflow have `parent_run_id = <workflow_run_id>` and `workflow_id = NULL` — they are normal agent runs.
- The `agent_name` column on parent workflow runs holds the composite workflow's name (denormalized, same pattern as existing approvals.agent_name).

---

## 2. Runner Ownership: Extend Declarative-Runner vs New Orchestrator Service

### The Question

A composite workflow run needs an orchestration engine. Where does it run?

| Option | Description | Trade-off |
|---|---|---|
| **A: Extend declarative-runner** | Add `orchestrator.py` module + `/workflow-run` endpoint. Activated via `COMPOSITE_WORKFLOW_ID` env var. | Reuses existing infra: same image, same deploy model, same step-tracking, same HITL. Binary serves two modes. |
| **B: New `workflow-orchestrator` service** | Separate service in `services/workflow-orchestrator/`. New Docker image, new Helm chart entry, new deploy-cpe2e.sh variable. | Clean separation; heavier to ship. |
| **C: In-registry-api async task** | Background `asyncio.create_task` inside registry-api handles sequential orchestration directly. | No new service, no new pod; but registry-api is not the execution layer. Scales poorly beyond sequential. |

### Decision: **Option A for the orchestrator module; Option C for the MVP sequential dispatch**

**Rationale:**

For the **MVP** (Phase W3), Option C — registry-api background task — handles sequential workflow runs without any new deployment. The registry-api already:
- Creates `AgentRun` rows
- Has HTTP client (httpx) to call agent pods
- Can fire-and-forget via `asyncio.create_task`

For **supervisor and handoff orchestration** (deferred to post-W3), a K8s pod per workflow is appropriate. Those modes require an LLM call for routing decisions (supervisor) or stateful handoff tracking, which don't fit cleanly in a background task. The declarative-runner extension (`orchestrator.py`) is the target state — the MVP async task in registry-api is an intentional stepping-stone.

The `orchestrator.py` module is written in declarative-runner in Phase W3 but the in-registry-api dispatch path is used for the MVP run. This means the orchestration code is written once and can be extracted without rewriting when supervisor mode is needed.

**This avoids:**
- Adding a new Docker image to every deploy cycle (significant ops overhead for what is initially a few hundred lines)
- Complicating the Helm chart and deploy-cpe2e.sh before the orchestration pattern is validated
- A chicken-and-egg where the workflow pod needs to be deployed before the workflow can run at all

**The deploy-controller extension** (teach it to create workflow pods using the declarative-runner image with `COMPOSITE_WORKFLOW_ID`) is captured as a **future improvement** and explicitly called out in the plan's Execution Notes.

---

## 3. How `workflows → agent_graphs` Rename Preserves Existing Rows

### The Rename

Three objects change names in migration 0026:
1. `workflows` table → `agent_graphs`
2. `workflow_versions` table → `agent_graph_versions`
3. `agent_versions.workflow_id` column → `agent_versions.agent_graph_id`

### How PostgreSQL handles this

**Tables:** PostgreSQL tracks tables by their OID (object identifier), not by name. `ALTER TABLE workflows RENAME TO agent_graphs` does NOT move data — it rewrites the system catalog entry (`pg_class.relname`). All rows, indexes, constraints, and sequences survive. FK constraints that *point to* the renamed table are automatically updated because they reference the OID, not the name.

**Sequences and indexes:** All indexes on `workflows` are renamed by PostgreSQL to use the new table name in their auto-generated names (e.g., `idx_workflows_team` → Postgres does NOT auto-rename indexes on `RENAME TABLE`). We must rename indexes explicitly in the migration to avoid confusion, but it's a cosmetic step — the indexes still function under their old names.

**The FK from `agent_versions.workflow_id → workflows.id`:** After renaming `workflows → agent_graphs`, this FK silently starts pointing to `agent_graphs.id` (same OID). The FK constraint name (`agent_versions_workflow_id_fkey`) is stale but functional. The column rename in step 3 will allow us to also rename the constraint.

**Column rename (`workflow_id → agent_graph_id`):**
```sql
ALTER TABLE agent_versions RENAME COLUMN workflow_id TO agent_graph_id;
```
This renames the column and its associated FK constraint name is stale. We explicitly rename the constraint:
```sql
ALTER TABLE agent_versions RENAME CONSTRAINT agent_versions_workflow_id_fkey TO agent_versions_agent_graph_id_fkey;
```
The constraint still enforces `agent_graph_id REFERENCES agent_graphs(id)`.

**Existing data:** All existing `agent_versions` rows with `workflow_id = some_uuid` now have `agent_graph_id = same_uuid`. The referenced row is in `agent_graphs` (formerly `workflows`). Zero data loss or transformation needed.

**SQLAlchemy migration safety:** Alembic does NOT introspect the DB during this rename migration — it executes the `ALTER TABLE` SQL directly via `op.rename_table` and `op.alter_column`. The migration is idempotent if run with an IF EXISTS guard on the rename (Postgres doesn't support `RENAME IF EXISTS` natively, so we check `pg_tables` first).

---

## 4. Workflow Definition JSON — Node References Existing Agents by ID

### Why by ID (not by name)

Composite workflow nodes reference member agents by `agent_id` (UUID), not by name. Rationale:

1. **Names can change.** An agent can be renamed via `PUT /agents/{name}`. A name-based reference would silently break the workflow definition.
2. **Agents exist before the workflow.** The workflow builder lists existing agents from the registry (`GET /api/v1/agents`). The user picks one — the response includes the UUID, which is the stable identifier.
3. **Tenant isolation.** The workflow CRUD endpoint validates that all referenced `agent_id` values belong to the same team as the workflow. A UUID makes this check unambiguous.

### Denormalized `agent_name` in the node definition

The workflow JSON stores both `agent_id` AND `agent_name` (denormalized) for display:
```json
{
  "nodes": [
    {
      "id": "node-1",
      "type": "workflow_member",
      "position": { "x": 100, "y": 100 },
      "data": {
        "agent_id": "a1b2c3...",
        "agent_name": "fraud-detector",
        "role": "worker",
        "position": 1
      }
    }
  ]
}
```

The `agent_name` in the JSON is display-only. The authoritative reference is `agent_id`. On run dispatch, the orchestrator resolves the live HTTP endpoint from `agent_id → deployments → k8s_namespace + pod name`.

---

## 5. Sequential vs Supervisor vs Handoff — MVP Scope

**MVP (Phase W3):** sequential orchestration only. Fixed-order A → B → C, each receiving the prior agent's output as its input payload. This covers the majority of workflow use cases and has a straightforward implementation (iterate `workflow_members ORDER BY position`).

**Supervisor (post-MVP):** requires a coordinator agent whose LLM decides which worker to call next. Implemented as a special graph: the supervisor is itself an agent registered in the registry with its own system prompt that includes the list of available workers. Too LLM-dependent for a clean MVP.

**Handoff (post-MVP):** agents declare explicit routing conditions on edges. Requires the orchestrator to evaluate conditions against each agent's output. Deferred until edge conditions are validated in the sequential case.

Both supervisor and handoff are captured in the `orchestration` column CHECK constraint so they can be stored and planned, but the Phase W3 orchestrator only executes `sequential` — it raises `HTTP 422` if a workflow with `orchestration != 'sequential'` is run.

---

## 6. `agent_runs` Run Tree Structure

A workflow run produces a two-level tree:

```
parent AgentRun
  ├── agent_name = "my-workflow"          (the workflow name, denormalized)
  ├── workflow_id = <workflow UUID>        (links to composite workflows table)
  ├── parent_run_id = NULL                (this IS the parent)
  ├── status: running → completed/failed
  └── child AgentRun #1
        ├── agent_name = "fraud-detector"
        ├── workflow_id = NULL
        ├── parent_run_id = <parent run UUID>
        └── status: running → completed

      child AgentRun #2
        ├── agent_name = "notifier"
        ├── workflow_id = NULL
        ├── parent_run_id = <parent run UUID>
        └── status: running → completed
```

`GET /api/v1/workflows/{id}/runs/{run_id}/tree` returns the parent run plus all child runs sorted by `started_at`. The Studio run-tree view renders this as a vertical timeline with each child agent's name, duration, and status.

Querying the run tree:
```sql
SELECT * FROM agent_runs
WHERE id = :parent_run_id
   OR parent_run_id = :parent_run_id
ORDER BY started_at ASC;
```

No recursive CTE needed for the two-level MVP tree. Deep nesting (workflows that compose other workflows) is explicitly out of scope for this plan.

---

## 7. Tenant Isolation for Composite Workflows

Composite workflows follow the same isolation model as agents:
- `workflows.team` — all CRUD endpoints filter by the caller's team (via `GET /api/v1/me`)
- `workflow_members.agent_id` — validated at creation: each referenced agent must have `agents.team = workflow.team`
- `agent_runs` — workflow runs carry `team` (denormalized) from the workflow definition; child runs carry `team` from the member agent

A workflow cannot reference agents from a different team. This prevents cross-team data exposure via workflow composition.

---

## 8. Impact on Existing Tests

The rename migration (`0026`) changes the API path from `/api/v1/workflows/` (canvas graphs) to `/api/v1/agent-graphs/`. All existing e2e test suites that call `/api/v1/workflows/` must be updated. Based on a grep of the e2e suites, the canvas workflow endpoint is used in:
- `suite-2-lifecycle.sh` (workflow creation in deploy lifecycle)
- `suite-8-playground.sh` (playground workflow tests)
- `suite-14-consumer-chat.sh` (declarative workflow agent runs)

These suites are updated in Phase W1 as part of task W1-5 when the router prefix changes.

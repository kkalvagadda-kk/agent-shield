# Production Artifact Isolation

**Status:** Approved  
**Date:** 2026-07-08  
**Scope:** Catalog ≠ Playground — full lifecycle separation

---

## Problem

The catalog currently shares the same data as the playground — same `agents` table, same versions, same deployments. When a user clicks an agent in the catalog, they see playground versions, sandbox deployments, and a sandbox deploy button. This conflates dev and production concerns.

## Decision

Publish+approve creates an **independent production artifact**. Playground continues as a sandbox; production catalog has its own lifecycle (deploy/upgrade/suspend/resume) with version history.

---

## Data Model

### New Tables

```sql
CREATE TABLE published_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('agent', 'workflow', 'tool', 'skill')),
    description TEXT,
    source_id UUID,              -- lineage FK to agents.id or workflows.id (informational)
    team TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(name, type)
);

CREATE TABLE published_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES published_artifacts(id),
    version_label TEXT NOT NULL,  -- "v1", "v2", etc.
    config_snapshot JSONB NOT NULL,
    source_version_id UUID,      -- lineage FK to agent_versions.id (informational)
    promoted_at TIMESTAMPTZ DEFAULT now(),
    promoted_by TEXT,
    notes TEXT
);

CREATE TABLE production_deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES published_artifacts(id),
    version_id UUID NOT NULL REFERENCES published_versions(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'deploying', 'running', 'suspended', 'failed')),
    namespace TEXT,
    deployed_at TIMESTAMPTZ,
    suspended_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### config_snapshot (agent)

```json
{
  "system_prompt": "...",
  "model": "claude-sonnet-4-20250514",
  "tools": ["tool-a", "tool-b"],
  "skills": ["skill-x"],
  "execution_shape": "reactive",
  "environment_vars": {"KEY": "val"},
  "memory_enabled": true
}
```

For workflows: member list, edges, orchestration mode, member configs.

### Table Role After Change

| Existing table | Role |
|---|---|
| `agents` | Playground only |
| `agent_versions` | Playground only |
| `deployments` | Sandbox deploys only |
| `agent_runs` (context=playground) | Playground runs |
| `agent_runs` (context=production) | Production runs — `production_deployment_id` FK |
| `published_artifacts` | **NEW** — catalog source of truth |
| `published_versions` | **NEW** — promoted version history |
| `production_deployments` | **NEW** — production runtime instances |

Grants link to `published_artifacts.id`.

---

## Promotion Flow (on admin approval)

1. Admin approves publish request
2. System upserts `published_artifacts` row (on name+type)
3. System snapshots current agent/workflow config into new `published_versions` row
4. Auto-increment version_label (v1, v2, v3...)
5. Grants attach to `published_artifacts.id`
6. Playground `publish_status` resets to `private` (promotion complete; playground resumes iterating)

---

## Catalog Detail Page

Route: `/catalog/:artifactId`

- Shows: name, description, owner team, granted teams
- **Versions tab**: published_versions (v1, v2, ...) with promoted_at, config summary
- **Deployments tab**: active production_deployments — status, which version, deployed_at
- **Actions**:
  - "Deploy" on any version → creates production_deployment
  - "Upgrade" on existing deployment → changes version_id → rolling update
  - "Suspend" / "Resume" on a deployment

---

## API Endpoints

```
GET    /api/v1/catalog                        — list published_artifacts (filtered by grants)
GET    /api/v1/catalog/:id                    — artifact + versions + deployments
POST   /api/v1/catalog/:id/deploy             — deploy a version {version_id}
PATCH  /api/v1/catalog/:id/deployments/:did   — upgrade/suspend/resume
```

---

## Deploy-Controller Integration

Production deployments reconcile from `production_deployments` + `published_versions.config_snapshot`:

- New row (status=pending) → controller creates pod from snapshot config
- Upgrade (version_id changed) → controller rolls out new spec
- Suspend → scale to 0
- Resume → scale back up

Separate from sandbox deploy path (reads from `agents` + `deployments`).

---

## Runs Data Fix (Part A — prerequisite)

### Problem

`trace_create_run` was added to `internal.py` (scheduler/event path) but NOT to `composite_workflows.py:start_workflow_run` (the Studio "Start Run" path). Result: `langfuse_trace_id` always NULL for Studio-triggered runs, so trace_url is empty and cost is zero.

### Fix

1. Add `trace_create_run` to `start_workflow_run` in `composite_workflows.py`
2. Add `trace_url` computation to `list_workflow_runs` and `get_workflow_run_tree`
3. Wire `LangfuseCallbackHandler` into `workflow_executor.py` graph.ainvoke (captures LLM token usage)
4. Add cost backfill: after run completes, fetch `total_cost` from Langfuse trace → update `cost_usd`

---

## Implementation Phases

| Phase | Scope | Session |
|---|---|---|
| A | Runs data fix (trace + cost) | This |
| B1 | Schema + promotion + catalog API | This |
| B2 | CatalogDetailPage + CatalogPage refactor | This |
| B3 | Deploy-controller production path | Next |
| B4 | Run isolation (production_deployment_id) | Next |

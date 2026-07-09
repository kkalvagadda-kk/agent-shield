# Production Deployment for Composite Workflows

**Status**: In Progress  
**Date**: 2026-07-09  
**Relates to**: Decision 22 (Workflow = Executable), Decision 24 (Unified Workflow Builder)

## Problem

When a composite workflow is published to the catalog and deployed to production, the deploy-controller treats it like a single agent. It creates a declarative-runner pod with `AGENT_NAME=workflow-name`, which crashes — the pod calls `GET /agents/{workflow-name}`, gets 404, and enters CrashLoopBackOff.

Three gaps compound the problem:

1. **No workflow routing in the reconciler** — `production_reconciler.py` doesn't distinguish `artifact_type == "workflow"` from `artifact_type == "agent"`.
2. **No member version pinning** — workflow version snapshots store `agent_id` and `agent_name` but not `agent_version_id`. A member agent update silently changes workflow behavior.
3. **No UX validation** — the catalog detail page treats workflows identically to agents. Users can click Deploy with no indication that member agents need their own production deployments first.

## Solution

### Dedicated Orchestrator Pod

Deploy a `declarative-runner` pod per composite workflow with `COMPOSITE_WORKFLOW_ID` and `WORKFLOW_CONFIG` env vars set. This activates the dormant orchestrator code path (`/workflow-run` endpoint + `orchestrator.py`).

The orchestrator pod doesn't call LLMs — it dispatches to member agent pods via their production Service URLs (`http://{agent_name}-production.agents-{team}.svc.cluster.local:8080/chat`) and manages parent/child run lifecycle.

```
┌────────────────────────────────────────────────┐
│  registry-api (or scheduler/webhook)           │
│  creates parent AgentRun, POSTs to:            │
│    orchestrator-pod:8080/workflow-run           │
└──────────────────┬─────────────────────────────┘
                   │
    ┌──────────────▼──────────────┐
    │  Orchestrator Pod           │
    │  (declarative-runner with   │
    │   COMPOSITE_WORKFLOW_ID)    │
    │                             │
    │  run_sequential():          │
    │    1. create child run      │
    │    2. POST to agent-A pod   │
    │    3. create child run      │
    │    4. POST to agent-B pod   │
    │    5. mark parent complete  │
    └──┬─────────────────────┬────┘
       │                     │
  ┌────▼────┐          ┌────▼────┐
  │ Agent A │          │ Agent B │
  │  prod   │          │  prod   │
  │  pod    │          │  pod    │
  └─────────┘          └─────────┘
```

### Member Version Pinning

When a workflow version is created, the snapshot now includes `agent_version_id` — the latest version of each member agent at snapshot time. This makes the workflow version a true point-in-time record.

The `members` JSONB column on `WorkflowVersion` is schema-flexible, so no migration is needed — the new key is simply added to each member dict.

### Pre-flight Member Check

Before deploying a workflow to production, the deploy-controller verifies all member agents have active production deployments via `POST /catalog/internal/verify-members`. If any are missing, the deployment fails immediately with a clear error naming the missing members.

### Catalog UX: Member Topology

The catalog detail page for workflow artifacts shows a **Member Topology** card listing each member agent with:
- Green indicator: agent has an active production deployment
- Red indicator: agent is not deployed to production

The Deploy button is disabled until all members show green. This prevents users from even attempting a deploy that would fail at the pre-flight check.

## Sandbox vs Production Orchestration

| Aspect | Sandbox | Production |
|--------|---------|------------|
| Orchestrator | registry-api in-process (`asyncio.create_task`) | Dedicated pod (`declarative-runner`) |
| Modes supported | All 4 (sequential, conditional, supervisor, handoff) | Sequential only (MVP) |
| HITL | Checkpoint/resume via DB | Deferred |
| Edge support | Full (`_compute_sequential_order`) | Position-based ordering |
| Member resolution | Dynamic from `deployments` table | From `WORKFLOW_CONFIG` env var |
| Member URLs | `http://{name}-{env}.agents-{team}...` | `http://{name}-production.agents-{team}...` |

## Files Changed

### registry-api
- `routers/composite_workflows.py` — version snapshot includes `agent_version_id`; run-start dispatches to orchestrator pod
- `routers/catalog.py` — `get_catalog_detail` returns `member_topology` for workflows; new `POST /internal/verify-members` endpoint
- `routers/internal.py` — run-start checks for production orchestrator pod
- `schemas.py` — `MemberTopologyEntry`, updated `CatalogDetailResponse`
- `workflow_orchestrator.py` — `dispatch_to_orchestrator_pod()` helper

### deploy-controller
- `production_reconciler.py` — workflow detection, `reconcile_workflow_production()`, pre-flight check, `_build_workflow_deployment_dict()`
- `manifest_builder.py` — `COMPOSITE_WORKFLOW_ID` and `WORKFLOW_CONFIG` env var injection

### declarative-runner
- `config.py` — `WORKFLOW_CONFIG` env var
- `main.py` — lifespan branch for orchestrator mode; `/workflow-run` endpoint activation
- `orchestrator.py` — parent status reporting, enhanced `run_sequential()`

### studio
- `api/catalogApi.ts` — `MemberTopologyEntry` type, `member_topology` on `CatalogDetail`
- `pages/CatalogDetailPage.tsx` — `MemberTopologyCard` component, deploy button gating

## Deferred

- **Conditional/supervisor/handoff modes** in the pod orchestrator (sequential only for MVP)
- **HITL in production workflow pods** (needs webhook/polling mechanism)
- **Edge graph support** in pod orchestrator (uses position ordering, not topological sort)
- **Graceful failover** (if orchestrator pod crashes mid-run, runs stay `running`)
- **Version drift detection** (warn when member agent's deployed version differs from what the workflow was built against)

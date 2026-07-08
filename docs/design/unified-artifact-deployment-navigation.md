# Design: Unified Artifact → Deployment → Overview Navigation

**Date:** 2026-07-08
**Status:** Proposed
**Author:** Karthik + Claude

---

## Problem

The Studio UX conflates two distinct concepts:

1. **Artifacts** — static resources (an agent or workflow that exists in the registry). No runtime state, no metrics.
2. **Deployments** — running instances of an artifact. Have runtime state (running/suspended), metrics, runs, memory, endpoints.

Today this manifests as two incompatible pages:
- `AgentDetailPage` shows a single sandbox deployment's overview directly on the artifact page
- `CatalogDetailPage` shows metrics + deployment controls at the artifact level

Both are wrong. An artifact can have multiple deployments. Metrics belong to a deployment, not an artifact. The overview experience should be identical regardless of whether you arrive from playground or catalog.

---

## Mental Model

```
List (static artifacts)
  → Artifact Page (versions + deployments list + deploy action)
    → Deployment Overview (metrics, runs, memory, chat, endpoints)
```

Three levels. Each has a clear responsibility. No conflation.

---

## Visibility & Authorization

| Context | What you see | Who controls it |
|---------|-------------|-----------------|
| **Playground** | Only resources you created (`created_by == caller`). Your sandbox. | Creator has full control. No approval workflows. |
| **Production** | All published artifacts visible to everyone. | Deployment actions gated by authorization model. **Stubbed now** — permit-all with TODO markers. Real RBAC enforced later. |

---

## Header Badges per Context

| Badge | Playground | Production | Deployment Overview |
|-------|-----------|------------|---------------------|
| Type (Agent/Workflow) | YES | YES | YES |
| Team | NO (irrelevant — user sees own) | YES | YES (production only) |
| Publish Status (Private/Pending/Published) | YES (drives lifecycle) | NO (all catalog = published) | NO |
| Operational Status (Active/Quarantined) | YES | NO | NO |
| Execution Shape (Reactive/Durable) | YES | YES | YES |
| Deployment Status (Running/Suspended/etc.) | NO (belongs to deployment) | NO | YES |

---

## Navigation Structure

```
PLAYGROUND:
  /                              → AgentListPage (user's own agents + workflows)
  /agents/:name                  → ArtifactPage [playground mode]
  /agents/:name/d/:depId         → DeploymentOverviewPage [sandbox]
  /agents/:name/d/:depId/chat    → ChatPage [sandbox]
  /agents/:name/deploy           → DeployAgentPage (creates sandbox deployment)

CATALOG:
  /catalog                       → CatalogPage (all published artifacts)
  /catalog/:artifactId           → ArtifactPage [production mode]
  /catalog/:artifactId/d/:depId  → DeploymentOverviewPage [production]
  /catalog/:artifactId/d/:depId/chat → ChatPage [production]

FLEET:
  /deployments                   → DeploymentsPage (all production deployments)
```

---

## Level 2: Artifact Page

Shared layout for both playground and production. Content differs by context.

### Playground (`/agents/:name`)

**Header:** Name + Type + Publish Status + Operational Status + Shape badges
**Actions:** Deploy, Publish (eval-gated)

**Sections:**
- Versions — list with config summary. "Deploy" button per version.
- Sandbox Deployments — table with columns: **Deployment Name** (primary ID, e.g., `simple-qa-bd28`), version, status badge, created. Clicking name → DeploymentOverviewPage.
- Settings — editable triggers (schedule/webhook CRUD), memory toggle

**What is NOT here:** No metrics, no runs, no memory, no endpoint cards.

### Production (`/catalog/:artifactId`)

**Header:** Name + Type + Team + Shape badges
**Actions:** Deploy (creates production deployment, authorization-gated)

**Sections:**
- Versions — list of promoted versions. "Deploy" button per version.
- Production Deployments — table with columns: **Deployment Name** (primary ID, e.g., `simple-qa-9f31`), version, status badge, deployed_at. Clicking name → DeploymentOverviewPage.
- Settings — read-only config snapshot + access grants

**What is NOT here:** No metrics, no runs, no endpoint cards.

---

## Level 3: Deployment Overview Page

One component. Same UX regardless of entry point. Parameterized by context.

```tsx
interface Props {
  agentName: string;
  deploymentId: string;
  context: "playground" | "production";
}
```

### Deployment Naming

Each deployment gets a unique name: `{agent_name}-{suffix}` (e.g., `simple-qa-bd28`, `news-pipeline-9f31`). This name is the **primary identifier** in the UX — not the agent name. The agent is metadata (shown as a secondary label or breadcrumb), not the identity of a running deployment.

- Generated at deploy time: `f"{agent_name}-{uuid[:4]}"` (or user-provided custom name)
- Displayed as the title on DeploymentOverviewPage
- Used in deployment lists, fleet view, sidebar
- K8s deployment name and namespace derive from this

### Header

- **Deployment name** (primary — e.g., `simple-qa-bd28`) + status badge
- Agent name as secondary metadata (breadcrumb / subtitle)
- Version label + namespace
- Actions: Suspend / Resume / Terminate (both contexts), Upgrade (production only)

### Tabs

#### Overview (mode-dispatched)

Renders one of four variants based on agent's `execution_shape` + triggers:

**Reactive:**
- Stats cards: Runs 24h / P50 Latency / Error Rate / Cost
- Endpoint cards (shape-aware): `POST /chat`, `POST /chat/stream`, `GET /health`
- "Open Chat" link → `/…/d/:depId/chat`
- Recent runs mini-table (last 5)

**Durable:**
- Stats cards: Runs 24h / Avg Duration / Failed / Awaiting Approval
- Endpoint cards: `POST /run`, `GET /run/{id}`, `GET /health`
- "New Run" button
- Active runs list (running + awaiting_approval with pulse indicators)

**Scheduled:**
- Schedule cards per cron trigger: expression, timezone, enabled/disabled toggle
- Last run status + timestamp
- Recent runs history (last 10)
- Health: next fire time, consecutive failures

**Event-driven:**
- Webhook endpoint cards: masked URL, filter conditions, rotate-token action
- Activity summary: match rate %, last event timestamp
- Event log: matched/filtered/rejected with timestamps

All variants share: stats cards (deployment-scoped), endpoint cards, recent activity.

#### Runs (merged best-of-both-worlds)

- Filter controls: trigger type dropdown + status dropdown
- Table: expand chevron / Trigger / Status / Input (truncated) / Duration / Started / Trace
- Expandable rows: full Input + Output + Error + metadata
- Inline TraceDrawer (side panel for Langfuse traces)

#### Memory

- Thread pills (All + per thread_id)
- Message list: role-colored (user/assistant), timestamp, content
- Actions: Delete thread, Clear all
- Scope: per-deployment (isolated — each deployment has its own memory space)

#### Chat (reactive only)

- Inline chat interface connected to this specific deployment
- Or: link to full-screen chat page at `…/d/:depId/chat`

---

## Data Model Implications

### Stats scoped to deployment

New endpoint: `GET /api/v1/deployments/{dep_id}/stats?context=playground|production`

- `playground`: filter `agent_runs` where deployment FK matches (need to add `sandbox_deployment_id` FK to `agent_runs`, or filter by agent_name + time window matching deployment's lifespan)
- `production`: filter `agent_runs` where `production_deployment_id = dep_id` (already exists)

### Runs scoped to deployment

New endpoint: `GET /api/v1/deployments/{dep_id}/runs?context=playground|production&limit=50&trigger_type=&status=`

Same pattern as stats — explicit context, no fallthrough.

### Sandbox deployments list

Existing: `GET /api/v1/agents/{name}/deployments` — verify it returns all sandbox deployments with status.

### Production deployments list

Existing: `getCatalogDetail(artifactId)` returns `deployments` array. Already works.

### Sandbox TTL auto-cleanup

- Add `ttl_hours INTEGER DEFAULT NULL` to `deployments` table (NULL = no auto-terminate)
- TTL is configurable during deployment (DeployAgentPage form field)
- Background worker (or extend existing timeout worker): query `deployments WHERE status='running' AND ttl_hours IS NOT NULL AND deployed_at + ttl_hours < NOW()` → set status='terminated'
- deploy-controller reconciler picks up `terminating` status and scales to 0

### Per-deployment memory

- Add `deployment_id UUID` FK to `agent_memory` table (nullable for backward compat with existing data)
- Memory API endpoints filter by deployment_id when provided
- SDK passes deployment_id in memory save/list calls
- Migration: existing memory rows get `deployment_id = NULL` (orphaned/legacy — accessible from any deployment of that agent until explicitly cleared)

---

## Authorization Model (Stubbed)

Production deployment actions will be gated by role-based authorization. For now:

```python
async def authorize_deployment_action(action: str, user: dict, artifact_id: UUID) -> bool:
    """
    Stub: permit all authenticated users.
    TODO: Implement real RBAC with roles:
      - agent:user — can view, invoke
      - agent:reviewer — can approve publish requests
      - agent:admin — can deploy, suspend, terminate
      - platform:admin — full access
    """
    return True
```

Stub placement:
- Deploy version (catalog router)
- Suspend / Resume / Terminate / Upgrade (catalog router)

Playground has NO authorization — creator owns everything.

---

## Migration Strategy

Incremental. Each phase is independently deployable.

1. **Backend endpoints** — add deployment-scoped stats + runs. No UI breaks.
2. **DeploymentOverviewPage** — new page + refactored Overview components. Mount at new routes. Old pages still work.
3. **Refactor artifact pages** — strip metrics/overview from AgentDetailPage + CatalogDetailPage. Add deployments list linking to new overview.
4. **Authorization stubs** — add permit-all stubs at action points.
5. **Chat scoped to deployment** — frontend passes deployment ID explicitly.

---

## Resolved Decisions

1. **Multi-instance production:** YES — multiple running deployments of the same artifact allowed simultaneously. Each has its own overview. Enables canary, A/B testing, multi-region.
2. **Memory isolation:** Per-deployment. Each deployment has its own memory space. Clean separation between versions.
3. **Sandbox deployment limit:** Unlimited, but auto-terminate after a configurable TTL (set during deployment). No manual cleanup burden.

---

## Future Improvements (TODO)

1. **Traffic splitting / canary support** — split traffic % between two deployments of the same artifact on a shared endpoint. Enables gradual rollout without separate URLs.
2. **Deployment event log** — live stream of infra events alongside the overview (pulling image, creating pods, health check passed/failed, scaling events). Makes status transitions visible to the user rather than opaque badge changes.
3. **Blue-green rollback** — one-click rollback to previous version using `previous_version_id`. Drain in-flight requests before terminating old deployment.

---

## Relationship to Other Design Docs

- `execution-models-and-memory.md` — defines the 4 execution shapes + memory model. This doc builds the UX layer on top.
- `execution-modes-production.md` — defines the production operate surface. This doc implements it correctly (at deployment level, not artifact level).
- `playground-execution-modes.md` — defines the playground evaluate surface. This doc preserves it but moves it to the deployment level.
- `production-artifact-isolation.md` — defines catalog + publish. This doc adds the deployment drill-down that was missing.

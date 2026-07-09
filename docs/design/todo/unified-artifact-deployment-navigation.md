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

WORKFLOWS (parallel tree — shares the Level-3 components via a `kind` prop):
  /workflows                     → WorkflowsPage (user's own workflows)
  /workflows/:id                 → ArtifactPage [playground mode, kind=workflow]  (later slice)
  /workflows/:id/d/:depId        → DeploymentOverviewPage [kind=workflow]
  /workflows/:id/d/:depId/chat   → ChatPage [kind=workflow]

CATALOG:
  /catalog                       → CatalogPage (all published artifacts — agent + workflow)
  /catalog/:artifactId           → ArtifactPage [production mode]
  /catalog/:artifactId/d/:depId  → DeploymentOverviewPage [production]
  /catalog/:artifactId/d/:depId/chat → ChatPage [production]

FLEET:
  /deployments                   → DeploymentsPage (all production deployments)
```

**Structure decision:** `/agents/*` and `/workflows/*` are **parallel route trees** that reuse the **same** `DeploymentOverviewPage`, `DeployModal`, and lifecycle actions menu via a `kind: "agent" | "workflow"` prop. No unified `/executables/*` rewrite.

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

One component. Same UX regardless of entry point. Parameterized by kind + context — the **same page serves agents and workflows**.

```tsx
interface Props {
  kind: "agent" | "workflow";
  executableName: string;   // agent name or workflow id
  deploymentId: string;
  context: "playground" | "production";
}
```

The API client routes by `kind`: agent deployments hit `/deployments/{id}/stats|runs` (scoped by `sandbox_deployment_id`/`production_deployment_id`); workflow deployments hit `/workflow-deployments/{id}/stats|runs` (scoped by `workflow_deployment_id` on the parent run).

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
- Actions: **Suspend / Resume / Terminate / Upgrade** (all four in both contexts, agent + workflow). "Change a deployment's settings" = **Upgrade to a new version**, never in-place mutation (a deployment's config is frozen to its version). Workflow suspend/terminate are logical (no pod).

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

## Workflows in the Model

### Principle: A workflow IS an executable — same model as an agent

From the user's perspective, a deployed workflow behaves identically to a deployed agent. It has an entry point, accepts input, produces output, and can be invoked via chat, schedule, or webhook. The fact that it orchestrates multiple agents internally is an implementation detail — not a UX distinction.

### How workflows map to the three levels

| Level | Agent | Workflow |
|-------|-------|----------|
| Artifact | Agent record (config, tools) | Workflow record (members, edges, orchestration mode) |
| Version | Config snapshot | Members + edges + orchestration mode snapshot |
| Deployment | K8s pod running the agent | Logical record — orchestrator runs in platform, member agents must be deployed |
| Entry endpoint | `/chat`, `/run`, `/trigger` | Same — `/chat` (via entry agent), `/run`, `/trigger` |

### Execution shapes apply to workflows too

A workflow is NOT limited to one invocation pattern. Like agents, workflows have an execution shape:

| Shape | Workflow behavior |
|-------|------------------|
| **Reactive** | User chats → entry agent receives message → orchestrates with other agents → streams final response. Conversational. |
| **Durable** | Triggered via API → runs to completion → returns result. Long-running, multi-step. |
| **Scheduled** | Cron trigger fires → workflow executes → result stored. Unattended. |
| **Event-driven** | Webhook event arrives → workflow executes → result stored. Reactive to external systems. |

The same overview mode dispatch (OverviewReactive / OverviewDurable / OverviewScheduled / OverviewEventDriven) works for both agents and workflows.

### Stats for workflows

Same shape as agents — measured end-to-end:

| Metric | Meaning for workflow |
|--------|---------------------|
| Runs 24h | Number of workflow invocations |
| P50 Latency | Wall-clock from input to final response (includes all agent steps) |
| Error Rate | Any agent failure in the flow = workflow failure |
| Cost | Sum of all agent costs across all steps in the flow |

### Chat for workflows

Workflows CAN have a chat tab (reactive shape). The chat endpoint hits the entry agent, which orchestrates with other agents to produce the answer. From the user's perspective, they're chatting with a single entity — the workflow handles routing internally.

This is how LangGraph Cloud, CrewAI, and Vertex AI multi-agent systems work: one conversational entry point, internal orchestration hidden.

### Storage decision (resolved)

- **Playground workflow deployments** live in a new **`workflow_deployments`** table (parallel to agent sandbox `deployments`). **Production** workflow deployments already flow through `production_deployments` + `published_artifacts.type='workflow'` (the catalog is already artifact-type-generic).
- **Workflow versions** live in a new **`workflow_versions`** table — an immutable snapshot of members + edges + orchestration + execution_shape per version. This gives real Upgrade/rollback for workflows.
- Workflow runs are scoped to a deployment via **`agent_runs.workflow_deployment_id`** on the parent workflow run (mirror of `sandbox_deployment_id`/`production_deployment_id`).

### Deployment: what "deploy a workflow" means

1. Snapshot the current definition into a `workflow_versions` row (if deploying "latest")
2. Create a `workflow_deployments` record (status, version, triggers, config)
3. Validate all member agents have running deployments (dependency check — fail fast if any member is undeployed)
4. Register triggers (schedule/webhook) if configured
5. Workflow is now invocable via its entry endpoint

No separate orchestrator pod — the platform (registry-api / declarative-runner) IS the orchestrator. Member agents run in their own pods.

**Dependency model:** A workflow deployment depends on its member agent deployments. If a member agent is suspended/terminated, the workflow deployment should surface a warning (degraded state) but not auto-terminate — the admin may want to redeploy the member.

### Overview for workflows — additional content

Beyond the standard mode-dispatched overview, workflow deployments show:

- **Flow visualization** — the member agent graph (nodes + edges) with per-step status from the most recent run
- **Step breakdown** — per-agent latency + status in the last N runs (which step is the bottleneck?)
- **Member agent status** — badges showing whether each member agent's deployment is healthy

### Industry validation

All major platforms (LangGraph Cloud, CrewAI Enterprise, Vertex AI Agent Builder, Azure AI Agent Service) treat multi-agent workflows as a single deployable unit with one entry endpoint. None deploy separate orchestrator pods. Stats are end-to-end. The orchestration is platform-managed.

---

## Data Model Implications

### Stats + runs scoped to deployment (kind-aware)

**Agents** (SHIPPED, Slice 1): `GET /api/v1/deployments/{dep_id}/stats|runs?context=playground|production`
- `playground`: `agent_runs.sandbox_deployment_id = dep_id`
- `production`: `agent_runs.production_deployment_id = dep_id`

**Workflows**: `GET /api/v1/workflow-deployments/{dep_id}/stats|runs`
- scope the **parent** workflow run (`agent_runs.workflow_id` set) by `agent_runs.workflow_deployment_id = dep_id`
- reuse the same aggregate helper; explicit scope, no fallthrough

### New workflow tables

- **`workflow_versions`** (id, workflow_id FK, version_number, members JSONB, edges JSONB, orchestration, execution_shape, config, eval_passed, created_at, created_by)
- **`workflow_deployments`** (id, workflow_id FK, version_id FK→workflow_versions, name, environment, status[pending/deploying/running/suspending/suspended/terminating/terminated/failed], namespace, replicas, ttl_hours, deployed_at, suspended_at, terminated_at, error_message)
- **`agent_runs.workflow_deployment_id`** FK → workflow_deployments (nullable)

### Agent lifecycle status (Slice 1a)

- Expand `deployments.status` CHECK to add `suspending`, `suspended`, `terminating`; add `deployments.suspended_at`, `deployments.ttl_hours`.

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

## Migration Strategy (revised Slice Plan)

Incremental. Each slice independently deployable.

- **Slice 1 (SHIPPED + rework in progress)** — Shared executable deployment foundation.
  - *1a (agent):* sandbox lifecycle actions (Suspend/Resume/Terminate/Upgrade) wired on the Overview + `PATCH /agents/{name}/deployments/{id}` + status-enum/`suspended_at`/`ttl_hours` migration + deploy-controller handling; shared `DeployModal` (replicas+TTL); generalize `DeploymentOverviewPage` + stats/runs to `kind`-aware.
  - *1b (workflow):* `workflow_versions` + `workflow_deployments` tables + `agent_runs.workflow_deployment_id`; deploy-workflow (member-dependency check) + workflow lifecycle PATCH + `/workflow-deployments/{id}/stats|runs`; route `/workflows/:id/d/:depId` → shared Overview (`kind=workflow`).
- **Slice 2** — Version management UX (agent + workflow): Versions tab as a real list, per-row Deploy (config modal), **Delete-version cascade** (blocks on published).
- **Slice 3** — Workflow artifact page (`/workflows/:id`) + rich deploy flow + overview extras (flow viz, per-step breakdown, member-health).
- **Slice 4** — Production/catalog Level-3 parity (agent + workflow) + authorization stubs.
- **Slice 5** — Per-deployment memory isolation + deployment-isolated chat (`/d/:depId/chat`) + sandbox TTL auto-cleanup worker.

---

## Resolved Decisions

1. **Multi-instance production:** YES — multiple running deployments of the same artifact allowed simultaneously. Each has its own overview. Enables canary, A/B testing, multi-region.
2. **Memory isolation:** Per-deployment. Each deployment has its own memory space. Clean separation between versions.
3. **Sandbox deployment limit:** Unlimited, but auto-terminate after a configurable TTL (set during deployment). No manual cleanup burden.
4. **Deployment config is immutable.** "Change a deployment's settings" = edit the artifact → new version → **Upgrade** the deployment to it. Never in-place mutation (preserves rollback + reproducibility).
5. **Deploy always creates a NEW deployment** (multi-deployment model). Deploy from the list or a Versions-tab row opens a small **config modal** (replicas, TTL).
6. **Delete version cascades.** Deleting a version warns which deployments use it and cascade-deletes them on confirm. A **published/promoted** version is protected (409, not cascaded).
7. **Workflow storage:** new `workflow_deployments` (playground) + `workflow_versions` tables; production workflows reuse `production_deployments`. Workflow versioning is built now (real Upgrade/rollback).
8. **Shared Level-3, parallel trees:** `/agents/*` and `/workflows/*` share `DeploymentOverviewPage` / `DeployModal` / actions via a `kind` prop.

---

## Future Improvements (TODO)

1. **Traffic splitting / canary support** — split traffic % between two deployments of the same artifact on a shared endpoint. Enables gradual rollout without separate URLs.
2. **Deployment event log** — live stream of infra events alongside the overview (pulling image, creating pods, health check passed/failed, scaling events). Makes status transitions visible to the user rather than opaque badge changes.
3. **Blue-green rollback** — one-click rollback to previous version using `previous_version_id`. Drain in-flight requests before terminating old deployment.

---

## Implementation Status

**Slice 1 — Playground deployment overview (SHIPPED, registry-api 0.2.101 / studio 0.1.81):**
- Migration `0040`: `deployments.name`, `agent_runs.sandbox_deployment_id` FK (mirror of `production_deployment_id`).
- Deploy time generates `{agent}-{suffix}` name (or caller-provided `name`).
- `chat.py` sets `sandbox_deployment_id` on playground runs.
- `GET /api/v1/deployments/{id}/stats` + `/runs` with explicit `context=playground|production` (no fallthrough).
- `DeploymentOverviewPage` at `/agents/:name/d/:depId`; `Overview*` + `RunsTab` are deployment-scoped.
- Artifact page (`AgentDetailPage`) is now Deployments + Versions + Settings — runtime tabs moved to the deployment page.
- Tests: `suite-38-deployment-overview.sh` (API + save→reload), `deployment-overview.spec.ts` (journey + reload), `DeploymentOverviewPage.test.tsx` / `AgentDetailPage.test.tsx` (Vitest).

**Slices 1a–4: DONE.** Lifecycle actions, DeployModal, workflow versions/deployments, version-management UX, workflow artifact page, catalog parity + RBAC foundation.

**Slice 5: DONE** — per-deployment memory isolation (`agent_memory.deployment_id`, migration 0045), deployment-pinned chat (`POST /agents/{name}/deployments/{dep_id}/chat`), sandbox TTL auto-cleanup worker (`ttl_worker.py` in deploy-controller). Image tags: registry-api:0.2.106, studio:0.1.86, deploy-controller:0.1.24.

**Known gaps / deferred:**
- **legacy data:** playground runs created before `0040` have `sandbox_deployment_id = NULL` and won't appear on the deployment overview.
- **legacy memory:** existing `agent_memory` rows have `deployment_id = NULL` (accessible globally until re-saved through a deployment-scoped call).
- **env blocker (not code):** Playwright `global-setup` Keycloak login is currently broken (untouched `smoke.spec.ts` also hits the login wall); the browser-journey gate can't run until that harness is fixed.

## Relationship to Other Design Docs

- `execution-models-and-memory.md` — defines the 4 execution shapes + memory model. This doc builds the UX layer on top.
- `execution-modes-production.md` — defines the production operate surface. This doc implements it correctly (at deployment level, not artifact level).
- `playground-execution-modes.md` — defines the playground evaluate surface. This doc preserves it but moves it to the deployment level.
- `production-artifact-isolation.md` — defines catalog + publish. This doc adds the deployment drill-down that was missing.

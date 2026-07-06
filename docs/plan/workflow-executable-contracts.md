# Workflow Executable — API Contracts

**Status**: FINAL  
**Date**: 2026-07-05  
**Auth**: All endpoints require `Authorization: Bearer <jwt>` (Keycloak OIDC JWT). Team resolved via `/api/v1/me`. Service-to-service calls use internal routes (no JWT).

---

## 1. Renamed Endpoints — Agent Graphs (was `/api/v1/workflows/`)

All canvas-graph endpoints move from `/api/v1/workflows/` to `/api/v1/agent-graphs/`. The HTTP verbs and request/response shapes are unchanged. Existing callers (Studio CanvasPage, deploy scripts) must update their URLs.

| Old | New |
|---|---|
| `GET /api/v1/workflows` | `GET /api/v1/agent-graphs` |
| `POST /api/v1/workflows` | `POST /api/v1/agent-graphs` |
| `GET /api/v1/workflows/{id}` | `GET /api/v1/agent-graphs/{id}` |
| `PUT /api/v1/workflows/{id}` | `PUT /api/v1/agent-graphs/{id}` |
| `POST /api/v1/workflows/{id}/deploy` | `POST /api/v1/agent-graphs/{id}/deploy` |
| `GET /api/v1/workflows/{id}/versions` | `GET /api/v1/agent-graphs/{id}/versions` |
| `POST /api/v1/workflows/{id}/versions/{n}/restore` | `POST /api/v1/agent-graphs/{id}/versions/{n}/restore` |

**`AgentVersionCreate.workflow_id` → `agent_graph_id`** in request bodies and responses.

---

## 2. New Endpoints — Composite Workflows `/api/v1/workflows`

### 2.1 List composite workflows

```
GET /api/v1/workflows
Query: team=<team_name> (optional; defaults to caller's team from JWT)
Auth: Bearer JWT

200 OK
[
  {
    "id": "b3f1...",
    "name": "fraud-review-pipeline",
    "team": "risk",
    "description": "Detects fraud then notifies compliance",
    "execution_shape": "sequential",
    "orchestration": "sequential",
    "memory_enabled": false,
    "status": "draft",
    "publish_status": "private",
    "member_count": 2,
    "created_at": "2026-07-05T10:00:00Z",
    "updated_at": "2026-07-05T10:00:00Z",
    "created_by": "alice@acme.com"
  }
]
```

### 2.2 Create composite workflow

```
POST /api/v1/workflows
Auth: Bearer JWT
Content-Type: application/json

{
  "name": "fraud-review-pipeline",
  "team": "risk",
  "description": "Detects fraud then notifies compliance",
  "execution_shape": "sequential",
  "orchestration": "sequential",
  "memory_enabled": false
}

201 Created
{
  "id": "b3f1...",
  "name": "fraud-review-pipeline",
  "team": "risk",
  "description": "Detects fraud then notifies compliance",
  "execution_shape": "sequential",
  "orchestration": "sequential",
  "memory_enabled": false,
  "status": "draft",
  "publish_status": "private",
  "member_count": 0,
  "created_at": "2026-07-05T10:00:00Z",
  "updated_at": "2026-07-05T10:00:00Z",
  "created_by": "alice@acme.com"
}

409 Conflict — { "detail": "A workflow named 'fraud-review-pipeline' already exists for team 'risk'." }
422 — validation error (invalid orchestration mode, unknown execution_shape)
```

### 2.3 Get composite workflow

```
GET /api/v1/workflows/{workflow_id}
Auth: Bearer JWT

200 OK
{
  "id": "b3f1...",
  "name": "fraud-review-pipeline",
  ...all CompositeWorkflowResponse fields...,
  "members": [
    {
      "agent_id": "a1b2...",
      "agent_name": "fraud-detector",
      "role": "worker",
      "position": 1,
      "routing": {}
    },
    {
      "agent_id": "c3d4...",
      "agent_name": "compliance-notifier",
      "role": "worker",
      "position": 2,
      "routing": {}
    }
  ]
}

404 — { "detail": "Workflow not found." }
403 — caller's team does not match workflow.team
```

### 2.4 Update composite workflow

```
PATCH /api/v1/workflows/{workflow_id}
Auth: Bearer JWT
Content-Type: application/json

{
  "description": "Updated description",
  "execution_shape": "durable",
  "orchestration": "sequential"
}

200 OK — CompositeWorkflowResponse

403 / 404
```

### 2.5 Delete (archive) composite workflow

```
DELETE /api/v1/workflows/{workflow_id}
Auth: Bearer JWT

204 No Content  (sets status='archived', no data deleted)
404
```

### 2.6 Add a member agent

```
POST /api/v1/workflows/{workflow_id}/members
Auth: Bearer JWT
Content-Type: application/json

{
  "agent_id": "a1b2...",
  "role": "worker",
  "position": 1
}

201 Created
{
  "workflow_id": "b3f1...",
  "agent_id": "a1b2...",
  "agent_name": "fraud-detector",
  "role": "worker",
  "position": 1,
  "routing": {},
  "added_at": "2026-07-05T10:00:00Z"
}

404 — workflow not found
422 — agent not found, OR agent.team != workflow.team (cross-team forbidden)
409 — agent already a member of this workflow
```

### 2.7 Remove a member agent

```
DELETE /api/v1/workflows/{workflow_id}/members/{agent_id}
Auth: Bearer JWT

204 No Content
404 — workflow or member not found
```

### 2.8 Trigger a workflow run

```
POST /api/v1/workflows/{workflow_id}/runs
Auth: Bearer JWT
Content-Type: application/json

{
  "input_payload": {
    "message": "Review transaction txn-98765 for fraud"
  },
  "trigger_type": "manual",
  "run_by": "alice@acme.com"
}

202 Accepted
{
  "run_id": "r7e8...",
  "workflow_id": "b3f1...",
  "status": "queued",
  "started_at": "2026-07-05T10:01:00Z"
}

Errors:
422 — { "detail": "Workflow has no members. Add at least one agent before running." }
422 — { "detail": "Orchestration mode 'supervisor' is not yet supported. Use 'sequential'." }
422 — { "detail": "Sequential workflow members missing positions. Set position on each member." }
404 — workflow not found
```

### 2.9 Get workflow run tree

```
GET /api/v1/workflows/{workflow_id}/runs/{run_id}/tree
Auth: Bearer JWT

200 OK
{
  "parent": {
    "id": "r7e8...",
    "agent_name": "fraud-review-pipeline",
    "workflow_id": "b3f1...",
    "status": "completed",
    "started_at": "2026-07-05T10:01:00Z",
    "completed_at": "2026-07-05T10:01:42Z",
    "latency_ms": 42000,
    "trigger_type": "manual",
    "run_by": "alice@acme.com",
    "parent_run_id": null,
    "team": "risk"
  },
  "children": [
    {
      "id": "ch1a...",
      "agent_name": "fraud-detector",
      "workflow_id": null,
      "status": "completed",
      "started_at": "2026-07-05T10:01:01Z",
      "completed_at": "2026-07-05T10:01:20Z",
      "latency_ms": 19000,
      "parent_run_id": "r7e8...",
      "output": "Fraud probability: 0.82. Recommend review."
    },
    {
      "id": "ch2b...",
      "agent_name": "compliance-notifier",
      "workflow_id": null,
      "status": "completed",
      "started_at": "2026-07-05T10:01:21Z",
      "completed_at": "2026-07-05T10:01:42Z",
      "latency_ms": 21000,
      "parent_run_id": "r7e8...",
      "output": "Compliance team notified via Slack."
    }
  ]
}

404 — run not found or does not belong to this workflow
```

### 2.10 List runs for a composite workflow

```
GET /api/v1/workflows/{workflow_id}/runs
Query: limit=20&offset=0&status=completed
Auth: Bearer JWT

200 OK
[AgentRunResponse, ...]  (parent runs only; use /tree endpoint for children)
```

---

## 3. Modified Endpoint — `POST /api/v1/internal/runs/start`

Updated to support workflow targeting. Exactly one of `agent_name` / `workflow_id` must be provided.

```
POST /api/v1/internal/runs/start
(cluster-internal only — not exposed via ingress)
Content-Type: application/json

Agent run (existing behavior):
{
  "agent_name": "fraud-detector",
  "trigger_type": "schedule",
  "trigger_id": "t1...",
  "trigger_payload": {"cron": "0 9 * * 1"},
  "run_by": "serviceaccount:scheduler"
}

Workflow run (new):
{
  "workflow_id": "b3f1...",
  "trigger_type": "schedule",
  "trigger_id": "t2...",
  "trigger_payload": {"cron": "0 8 * * *"},
  "run_by": "serviceaccount:scheduler"
}

200 OK — { "run_id": "r7e8..." }
422 — { "detail": "Provide either agent_name or workflow_id, not both." }
422 — { "detail": "No deployed agent/workflow found." }
```

---

## 4. Composite Workflow Definition JSON Shape

The canonical representation stored in the Studio builder and in the workflow members table. This JSON is produced by `serializeCompositeWorkflow()` in the frontend serializer and consumed by the `WorkflowOrchestrator` in the backend.

```json
{
  "orchestration": "sequential",
  "nodes": [
    {
      "id": "node-a1b2",
      "type": "workflow_member",
      "position": { "x": 100, "y": 100 },
      "data": {
        "agent_id": "a1b2c3d4-e5f6-...",
        "agent_name": "fraud-detector",
        "role": "worker",
        "position": 1
      }
    },
    {
      "id": "node-c3d4",
      "type": "workflow_member",
      "position": { "x": 400, "y": 100 },
      "data": {
        "agent_id": "c3d4e5f6-a7b8-...",
        "agent_name": "compliance-notifier",
        "role": "worker",
        "position": 2
      }
    }
  ],
  "edges": [
    {
      "id": "edge-1",
      "source": "node-a1b2",
      "target": "node-c3d4",
      "condition": ""
    }
  ]
}
```

**Validation rules** (enforced on `POST /api/v1/workflows/{id}/runs`):
- All `agent_id` values must exist in `workflow_members` for this workflow
- For `orchestration=sequential`: all members must have distinct non-null `position`
- Minimum 1 node

**Relationship to `workflow_members` table**: The Studio builder writes both the JSON (stored in the workflow's `metadata.builder_definition` JSONB field — not a separate table) AND the relational `workflow_members` rows (used by the orchestrator). They are kept in sync: adding a node via the builder adds a `workflow_members` row; removing a node removes it.

---

## 5. Run-Tree SSE Events (Production Runs)

When a workflow run is in progress, clients can stream its events:

```
GET /api/v1/agent-runs/{run_id}/stream
(existing endpoint — used for both agent and workflow parent runs)
Accept: text/event-stream

event: run_status
data: {"run_id": "r7e8...", "status": "running", "started_at": "2026-07-05T10:01:00Z"}

event: child_run_started
data: {"child_run_id": "ch1a...", "agent_name": "fraud-detector", "position": 1}

event: child_run_completed
data: {"child_run_id": "ch1a...", "agent_name": "fraud-detector", "status": "completed", "latency_ms": 19000}

event: child_run_started
data: {"child_run_id": "ch2b...", "agent_name": "compliance-notifier", "position": 2}

event: child_run_completed
data: {"child_run_id": "ch2b...", "agent_name": "compliance-notifier", "status": "completed", "latency_ms": 21000}

event: run_status
data: {"run_id": "r7e8...", "status": "completed", "completed_at": "2026-07-05T10:01:42Z", "latency_ms": 42000}
```

Note: Phase W3 does not implement real-time SSE for workflow runs. The `/tree` endpoint provides polling. SSE for workflows is a post-MVP enhancement.

---

## 6. Error Shapes

All errors follow the existing FastAPI convention: `{ "detail": "<message>" }` for simple errors, `{ "detail": [{ "loc": [...], "msg": "...", "type": "..." }] }` for validation errors.

New 422 error codes introduced:
- `"Workflow has no members."` — runs/start with empty workflow
- `"Orchestration mode '{mode}' is not yet supported."` — supervisor/handoff attempted
- `"Sequential workflow members are missing position values."` — unordered sequential members
- `"Agent '{name}' (id={id}) belongs to team '{agent_team}', not '{workflow_team}'."` — cross-team member add
- `"Agent is already a member of this workflow."` — duplicate member add

---

## 7. Frontend TypeScript Interface Additions

```typescript
// workflowSerializer.ts additions

export interface CompositeWorkflowNode {
  id: string;
  type: 'workflow_member';
  position: { x: number; y: number };
  data: {
    agent_id: string;          // UUID of existing registered agent
    agent_name: string;        // display name (denormalized from registry)
    role?: string;             // 'supervisor' | 'worker' | free-form
    position?: number;         // sequential ordering (1-based)
  };
}

export interface CompositeWorkflowDefinition {
  orchestration: 'sequential' | 'supervisor' | 'handoff';
  nodes: CompositeWorkflowNode[];
  edges: WorkflowEdge[];       // reuse existing WorkflowEdge type
}

export function serializeCompositeWorkflow(
  nodes: Node[],
  edges: Edge[],
  orchestration: 'sequential' | 'supervisor' | 'handoff'
): CompositeWorkflowDefinition;

export function deserializeCompositeWorkflow(definition: CompositeWorkflowDefinition): {
  nodes: Node[];
  edges: Edge[];
};
```

```typescript
// registryApi.ts additions

export interface CompositeWorkflow {
  id: string;
  name: string;
  team: string;
  description: string | null;
  execution_shape: 'reactive' | 'durable';
  orchestration: 'sequential' | 'supervisor' | 'handoff';
  memory_enabled: boolean;
  status: 'draft' | 'published' | 'archived';
  publish_status: string;
  member_count: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export interface CompositeWorkflowWithMembers extends CompositeWorkflow {
  members: WorkflowMember[];
}

export interface WorkflowMember {
  agent_id: string;
  agent_name: string;
  role: string | null;
  position: number | null;
  routing: Record<string, unknown>;
  added_at: string;
}

export interface WorkflowRunResult {
  run_id: string;
  workflow_id: string;
  status: string;
  started_at: string;
}

export interface WorkflowRunTree {
  parent: AgentRunResponse;
  children: AgentRunResponse[];
}

// API functions
export async function listCompositeWorkflows(team?: string): Promise<CompositeWorkflow[]>;
export async function createCompositeWorkflow(body: CreateCompositeWorkflowRequest): Promise<CompositeWorkflow>;
export async function getCompositeWorkflow(id: string): Promise<CompositeWorkflowWithMembers>;
export async function updateCompositeWorkflow(id: string, body: Partial<CreateCompositeWorkflowRequest>): Promise<CompositeWorkflow>;
export async function addWorkflowMember(workflowId: string, member: { agent_id: string; role?: string; position?: number }): Promise<WorkflowMember>;
export async function removeWorkflowMember(workflowId: string, agentId: string): Promise<void>;
export async function triggerWorkflowRun(workflowId: string, payload: { input_payload: Record<string, unknown>; run_by?: string }): Promise<WorkflowRunResult>;
export async function getWorkflowRunTree(workflowId: string, runId: string): Promise<WorkflowRunTree>;
export async function listWorkflowRuns(workflowId: string): Promise<AgentRunResponse[]>;
```

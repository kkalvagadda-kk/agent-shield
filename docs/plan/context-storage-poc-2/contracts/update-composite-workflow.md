# Contract — Update composite workflow (share-context toggle)

**Endpoints (existing, unchanged)**:
- `POST /api/v1/workflows` → `createCompositeWorkflow`
- `PATCH /api/v1/workflows/{id}` → `updateCompositeWorkflow`

The field `memory_enabled` **already exists** on both the request and response types. POC-2 only starts sending it from the WorkflowBuilder first-save modal.

## Request body (`Partial<CreateCompositeWorkflowRequest>` for PATCH)
```ts
export interface CreateCompositeWorkflowRequest {
  name: string;
  team: string;
  description?: string;
  execution_shape?: 'reactive' | 'durable';
  orchestration?: WorkflowOrchestration;
  agent_class?: 'user_delegated' | 'daemon';
  memory_enabled?: boolean;   // ← the "Share context between agents" toggle
}
```

### PATCH example (resave)
```json
PATCH /api/v1/workflows/9c1e…
{ "orchestration": "sequential", "agent_class": "user_delegated", "memory_enabled": true }
```

### POST example (first save)
```json
POST /api/v1/workflows
{ "name": "refund-flow", "team": "payments", "orchestration": "sequential",
  "execution_shape": "reactive", "agent_class": "user_delegated", "memory_enabled": true }
```

## Response `200/201` — `CompositeWorkflow`
```ts
export interface CompositeWorkflow {
  id: string; name: string; team: string; description: string | null;
  execution_shape: 'reactive' | 'durable';
  orchestration: WorkflowOrchestration;
  agent_class: 'user_delegated' | 'daemon';
  memory_enabled: boolean;    // reflects the saved toggle
  status: 'draft' | 'published' | 'archived';
  publish_status: string; member_count: number; warnings?: string[];
  created_at: string; updated_at: string; created_by: string | null;
}
```

## Frontend wiring (`WorkflowBuilderPage.tsx`)
- State: `const [saveMemoryEnabled, setSaveMemoryEnabled] = useState(true);` (near L112).
- Load on mount (in the `if (workflow)` block ~L165): `setSaveMemoryEnabled(workflow.memory_enabled);`.
- Modal control (after the Orchestration `<div>`, ~L1010):
  ```tsx
  <div>
    <label className="label">Share context between agents</label>
    <input type="checkbox" checked={saveMemoryEnabled}
           onChange={(e) => setSaveMemoryEnabled(e.target.checked)} />
    <p className="mt-1 text-xs text-slate-400">
      Members see each other's turns in a shared conversation thread.
    </p>
  </div>
  ```
- Send it: add `memory_enabled: saveMemoryEnabled` to the `createCompositeWorkflow` body (L286) and the `updateCompositeWorkflowApi` body (L338).

## NOT in this contract
- No `per_session_or_run` / `share_rationale` fields — no backing column (see plan §9). Do not add them.

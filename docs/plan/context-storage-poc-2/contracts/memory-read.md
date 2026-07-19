# Contract — Shared-thread transcript read (reload / eval)

**Endpoint (existing, unchanged)**: `GET /api/v1/agents/{name}/memory`
**Handler**: `services/registry-api/routers/memory.py::list_memory` (L97-157).
**Used by POC-2**: EvalResultsPage transcript (T7); it is the reload source of truth for workflow attribution.

## Request
```
GET /api/v1/agents/{member_name}/memory?scope=workflow_run&thread_id={parent_run_id}&limit=200
Authorization: Bearer <jwt>
```
- `{member_name}` MUST be a real `Agent` (path guard `_get_agent_or_404`). For a workflow transcript, use one of the workflow's **member** agent names — a workflow name is not an Agent.
- `scope=workflow_run` drops the agent_name filter → returns **every member's** rows.
- `thread_id` = the parent workflow run id (`workflow_orchestrator.py::_run_step` L496: `conversation_id = parent_run_id`).

## Response `200` — `AgentMemoryResponse[]`, oldest-first by `message_index`
```json
[
  {"agent_name":"intake-agent","role":"user","content":"I need a refund","message_kind":"user","scope":"workflow_run","message_index":0,"thread_id":"7f…","created_at":"2026-07-16T10:00:00Z"},
  {"agent_name":"intake-agent","role":"assistant","content":"Routing to refunds…","message_kind":"agent_output","scope":"workflow_run","message_index":1,"thread_id":"7f…","created_at":"2026-07-16T10:00:03Z"},
  {"agent_name":"refund-agent","role":"assistant","content":"Refund approved: $42","message_kind":"agent_output","scope":"workflow_run","message_index":2,"thread_id":"7f…","created_at":"2026-07-16T10:00:07Z"}
]
```
Two distinct `agent_name` values → two colored, labeled bubbles.

## Frontend type (studio/src/api/registryApi.ts)
```ts
export interface MemoryMessage {
  id: string;
  agent_name: string;          // speaker → AttributedBubble author
  thread_id: string;
  role: string;                // 'user' | 'assistant'
  content: string;
  message_index: number;
  session_id: string | null;
  user_id: string | null;
  created_at: string;
  message_kind?: string;       // NEW — 'user' | 'agent_output' | 'rationale'
  scope?: string;              // NEW — 'agent' | 'workflow_run'
}

export const listMemory = async (
  agentName: string,
  params?: { thread_id?: string; scope?: string; deployment_id?: string; limit?: number; offset?: number },
): Promise<MemoryMessage[]> => {
  const resp = await http.get(`/agents/${agentName}/memory`, { params });
  return resp.data;
};
```
`scope` forwards to the backend query already supported at memory.py L100. Existing callers (no `scope`) keep `scope='agent'` semantics.

## Errors
- `404` — `{name}` is not a known Agent (guard against a workflow name).
- `403`/`401` — not authenticated / not the owner (standard middleware).

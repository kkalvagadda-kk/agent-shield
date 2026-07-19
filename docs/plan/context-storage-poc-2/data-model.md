# POC-2 Data Model

**No new tables. No new columns. No migration.** POC-2 is a UX layer over the POC-0/1 data model. This file documents the *contracts* it consumes and the one already-existing composite-workflow field it starts writing.

---

## 1. `agent_memory` (existing — POC-0/1) — read shape reused

Confirmed via `services/registry-api/schemas.py::AgentMemoryResponse` (L1826-1844). The relevant per-row fields:

| Field | Type | Meaning for POC-2 |
|---|---|---|
| `agent_name` | str | **the speaker** — drives `AttributedBubble` author + color |
| `role` | str | `user` \| `assistant` |
| `content` | str | bubble text |
| `message_kind` | str | `user` \| `agent_output` \| `rationale` (rationale unused until POC-1b) |
| `scope` | str | `agent` \| `workflow_run` — POC-2 reads `workflow_run` for the shared transcript |
| `message_index` | int? | ordering within the conversation (transcript is oldest-first) |
| `thread_id` | str | the conversation key = **parent workflow run id** for workflow scope |
| `created_at` | datetime? | display only |

**Read for reload (workflow attribution):**
`GET /api/v1/agents/{member_name}/memory?scope=workflow_run&thread_id={parent_run_id}&limit=200`
→ returns **all members' rows** (agent_name filter dropped for `workflow_run`), ordered by `message_index`. Endpoint: `routers/memory.py::list_memory` L97-157. Proven by suite-75 T-S75-004.

> `{member_name}` must be a real `Agent` (path guard `_get_agent_or_404`, memory.py L37). A workflow name is NOT an agent — use a member agent name.

**Frontend type delta** (`studio/src/api/registryApi.ts::MemoryMessage`, L1536): add optional `message_kind?: string` and `scope?: string`; add `scope?: string` to `listMemory` params (L1548). Full contract in `contracts/memory-read.md`.

---

## 2. Workflow run tree (existing) — live workflow attribution

`GET /api/v1/workflows/{workflow_id}/runs/{run_id}/tree` → `WorkflowRunTree { parent: AgentRunItem; children: AgentRunItem[] }` (registryApi.ts L597-600). Each `AgentRunItem` (L1369-1392) child carries everything an attributed bubble needs:

| Field | Use in CatalogChat (T6) |
|---|---|
| `agent_name` | bubble author + color |
| `output` | bubble content |
| `status` | step badge (`completed`/`failed`/`awaiting_approval`) |
| `latency_ms` | step meta |
| `langfuse_trace_id` | View-Trace link |
| `thread_id` | correlate inline approvals (existing) |

No change to this contract — POC-2 just stops throwing `children` away.

---

## 3. Composite workflow (existing field, newly written)

`CompositeWorkflow.memory_enabled: boolean` (registryApi.ts L542); `CreateCompositeWorkflowRequest.memory_enabled?: boolean` (L586); PATCH accepts it via `Partial<CreateCompositeWorkflowRequest>` (L621). POC-2 wires the modal toggle to this field in `createCompositeWorkflow` + `updateCompositeWorkflow`. Full request/response in `contracts/update-composite-workflow.md`.

---

## 4. SSE frame contract (transport, not storage)

The chat proxy frame gains `author`. Not persisted — it is a live transport field derived from the agent name. Full schema in `contracts/sse-frames.md`.

```jsonc
{"type": "agent_start", "author": "<agent_name>"}                 // NEW (once per stream)
{"type": "token", "content": "<delta>", "author": "<agent_name>"} // author ADDED
```

---

## 5. What is explicitly NOT added

- No `message_kind='rationale'` writer (POC-1b, deferred).
- No per-session/per-run scope column (entrypoint-derived, arch §5.4).
- No `share_rationale` column (depends on summarizer).
- No conversation-list query (POC-5).

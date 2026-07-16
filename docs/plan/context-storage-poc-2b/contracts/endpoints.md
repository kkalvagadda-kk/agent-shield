# Endpoint Contracts — POC-2b

Base path prefix: `/api/v1`. Auth: `require_user` (JWT) unless noted.

---

## 1. `POST /api/v1/workflows/{workflow_id}/runs/stream` (NEW)

Live multiplexed workflow-run SSE. The headline of 2b-0.

**Request body** (`WorkflowRunStreamRequest`):
```json
{ "message": "Run the workflow end to end.", "session_id": "optional-uuid" }
```
- `message` (required): the user turn.
- `session_id` (optional): when present, becomes the workflow run's transcript key (`parent.session_id`) so
  the shared thread persists across turns; when absent, the transcript key is the fresh `run_id`.

**Auth**: `require_user`. Caller sub → `run_by`. (Grant/team checks match the catalog chat surface; the
workflow is already catalog-visible to the caller.)

**Response**: `200 StreamingResponse`, `Content-Type: text/event-stream`,
headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Body is a sequence of
`data: <json>\n\n` frames (SSE, unnamed/data-only). Frame schema → `contracts/sse-frames.md`.

**Behavior**:
1. Load workflow (404 if missing; 422 if archived or no members) — same guards as `start_workflow_run`.
2. Create the parent `AgentRun` (context="playground", trigger_type="api", run_by=caller sub, workflow_id,
   `session_id = body.session_id`), open a Langfuse trace, commit.
3. `conversation_id = body.session_id or str(parent.id)`.
4. Stream `orchestrate_stream(parent_id, team, workflow_id, message, wf.orchestration, conversation_id, wf.execution_shape)`,
   serializing each yielded frame dict as `data: {json}\n\n`. The internal `__member_end__` sentinel is
   filtered out (never sent to the client). Ends with a `done` frame.

**Errors**: a member/run failure surfaces as an `{"type":"error","message"}` frame followed by `done`
(the stream never 500s mid-flight). Undeployed members → an `error` frame (as `_dispatch` reports today).

**In-process only**: this endpoint always runs the in-process orchestrator (no
`dispatch_to_orchestrator_pod`). Gap-ledgered.

---

## 2. `POST /api/v1/workflows/{workflow_id}/runs` (UNCHANGED external contract; internal drain)

Behavior and response (`202 WorkflowRunStartResponse{workflow_id, run_id, status, warning}`) are
byte-for-byte as today. Internally it now calls
`orchestrate(parent_id, team, workflow_id, message, mode, shape, conversation_id=str(parent_id))`,
which drains `orchestrate_stream`. **Regression gate**: a 2-member sequential run started here must
produce the same terminal tree as before (`T-S75-004`, `T-S75-011` parity).

---

## 3. `GET /api/v1/workflows/{workflow_id}/runs/{run_id}/tree` (EXTENDED)

Response `WorkflowRunTreeResponse{parent, children}` — each `child` (an `AgentRunResponse`) gains:

```jsonc
{
  "id": "…", "agent_name": "researcher", "status": "completed", "output": "…",
  "latency_ms": 812, "trace_url": "https://…",
  "tool_calls": [ { "tool_name": "lookup_fact", "status": "ok" } ],   // NEW — from run_steps marker rows
  "rationale": "Searching for the record time so the answerer has a source."  // NEW — from agent_memory, null if none
}
```

- `tool_calls`: `[]` when the child wrote no reactive tool-call marker rows (durable members, tool-less members).
- `rationale`: `null` when the child produced no tool-calling reasoning (tool-less members) or is durable.
- `parent` keeps `tool_calls: []` / `rationale: null` (only members carry them).
- Projection keys: `tool_calls` by `run_steps.run_id == child.id AND output->>'kind'='tool_call'`;
  `rationale` by `agent_memory(thread_id = parent.session_id or run_id, scope='workflow_run', message_kind='rationale', agent_name=child.agent_name)`.

---

## 4. `GET /api/v1/agents/{name}/memory?scope=workflow_run&thread_id={conversation_id}` (UNCHANGED)

Returns transcript rows including the new `message_kind='rationale'` rows (via `ConversationStore.load`).
Used by the reload path + `T-S75-010`. Each row carries `agent_name` (author), `role`, `content`,
`message_kind`, `scope`, `message_index`.

---

## 5. Runner `POST /chat/stream` (member pod — EXTENDED)

Named SSE events emitted by `services/declarative-runner/main.py::/chat/stream`:

| Event | Payload | Change |
|---|---|---|
| `text_delta` | `{"content": "…"}` | unchanged |
| `tool_call_start` | `{"tool": "…"}` (or `tool_name`) | unchanged (now consumed, not dropped) |
| `tool_call_end` | `{"tool": "…", "status"/"error"?}` | unchanged (consumed) |
| `rationale` | `{"content": "…"}` | **NEW** — emitted after the graph stream when `scope=="workflow_run"` and rationale non-empty |
| `done` | `{}` | unchanged |
| `error` | `{"reason"/"message": "…"}` | unchanged |
| `approval_requested` | `{approval_id, tool, risk, …}` | unchanged |

Request body unchanged: `{message, thread_id, conversation_id, scope, workflow_run_id?}` + identity headers.

---

## 6. Shared reader `stream_pod_chat_frames` (registry-api internal — NOT an HTTP endpoint)

Consumed by `chat.py::_proxy_agent_stream` (single-agent) and `workflow_orchestrator._dispatch_stream`
(workflow member). Yields normalized dicts (no run-level `done`) — see Key Interfaces in plan.md and
`contracts/sse-frames.md`. This is the ONE pod-SSE reader (No-Bandaid).

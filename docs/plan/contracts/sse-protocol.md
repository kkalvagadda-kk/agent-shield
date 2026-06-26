# AgentShield SSE Streaming Protocol

**Version:** 1.0  
**Endpoint:** `POST /chat/stream`  
**Content-Type:** `text/event-stream`  
**Protocol:** HTTP/1.1 Server-Sent Events (RFC 8895)

---

## Overview

The SSE streaming protocol defines the event sequence emitted by every agent pod (SDK-built and declarative runner) on the `POST /chat/stream` endpoint. Frontend clients use this protocol to render streaming responses with tool call visualization and approval state management.

The Vercel AI SDK and CopilotKit can consume this stream using their SSE adapter patterns. Teams may also consume it directly with `EventSource` or `curl -N`.

---

## Connection Semantics

```
Client                          Agent Pod
  │                                │
  ├── POST /chat/stream ──────────►│
  │   {message, thread_id}         │
  │                                │ (safety scan happens before this point)
  │◄── text/event-stream ──────────┤ HTTP 200 with streaming body
  │                                │
  │◄── event: text_delta ──────────┤ (zero or more as LLM generates tokens)
  │◄── event: tool_call_start ─────┤ (when agent calls a tool)
  │◄── event: tool_call_end ───────┤ (when tool returns result)
  │◄── event: approval_requested ──┤ (stream pauses — connection stays open)
  │                                │ ... reviewer acts in Appsmith ...
  │◄── event: approval_decided ────┤ (stream resumes)
  │◄── event: text_delta ──────────┤ (response continues)
  │◄── event: done ────────────────┤ (final event)
  │                                │ (server closes connection)
```

**Connection timeout:** Client must hold the connection for up to 35 minutes (30min approval window + 5min buffer).

**Reconnection:** If the connection drops, client should reconnect with `Last-Event-ID` header using the last received event ID. The agent pod will replay events since that ID from the LangGraph checkpoint.

---

## Event Format

Each event follows SSE spec format:

```
event: <event_type>
id: <event_id>
data: <json_payload>

```

(blank line terminates each event)

---

## Event Types

### `text_delta`

Emitted for each partial token or text chunk from the LLM.

```
event: text_delta
id: evt_001
data: {"content": "I'll look up your order", "index": 0}
```

```
event: text_delta
id: evt_002
data: {"content": " right away...", "index": 1}
```

**Fields:**
| Field | Type | Description |
|---|---|---|
| `content` | string | Partial text content |
| `index` | integer | Sequential token chunk index within this turn |

---

### `tool_call_start`

Emitted when the agent begins executing a tool call (after OPA approves it).

```
event: tool_call_start
id: evt_003
data: {
  "tool_call_id": "tc_abc123",
  "tool": "lookup_order",
  "args": {"order_id": "12345"},
  "risk": "low"
}
```

**Fields:**
| Field | Type | Description |
|---|---|---|
| `tool_call_id` | string | Unique ID for this tool invocation |
| `tool` | string | Tool function name |
| `args` | object | Arguments passed to the tool |
| `risk` | `"low"` \| `"high"` | Risk classification from `@tool(risk=)` decorator |

---

### `tool_call_end`

Emitted when a tool call completes (success or error).

```
event: tool_call_end
id: evt_004
data: {
  "tool_call_id": "tc_abc123",
  "tool": "lookup_order",
  "result": {"status": "delivered", "delivered_at": "2026-06-20T14:30:00Z"},
  "duration_ms": 234
}
```

```
event: tool_call_end
id: evt_005
data: {
  "tool_call_id": "tc_xyz789",
  "tool": "get_inventory",
  "error": "Connection timeout after 5000ms",
  "duration_ms": 5001
}
```

**Fields:**
| Field | Type | Description |
|---|---|---|
| `tool_call_id` | string | Matches the corresponding `tool_call_start` |
| `tool` | string | Tool function name |
| `result` | any \| null | Tool return value (null if error) |
| `error` | string \| null | Error message (null if success) |
| `duration_ms` | integer | Tool execution time in milliseconds |

---

### `approval_requested`

Emitted when a high-risk tool requires human approval. The stream **does not close** — it waits for the reviewer's decision. The `approval_id` uniquely identifies the pending approval record in Postgres.

```
event: approval_requested
id: evt_006
data: {
  "approval_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "tool_call_id": "tc_refund_01",
  "tool": "issue_refund",
  "args": {"order_id": "12345", "amount": 50.00},
  "risk": "high",
  "expires_at": "2026-06-24T21:00:00Z",
  "queue_url": "https://appsmith.agentshield.internal/approval-queue"
}
```

**Fields:**
| Field | Type | Description |
|---|---|---|
| `approval_id` | UUID string | ID of the Postgres approval record |
| `tool_call_id` | string | The pending tool call ID |
| `tool` | string | Tool requiring approval |
| `args` | object | Arguments to the tool (shown to reviewer) |
| `risk` | string | Always `"high"` or `"critical"` |
| `expires_at` | ISO 8601 datetime | When the approval auto-rejects |
| `queue_url` | string | Direct link to Appsmith approval queue (optional, for UI integration) |

**Frontend behavior on receiving this event:**
- Display an "Awaiting approval" banner with tool name and args
- Show countdown to `expires_at`
- Provide optional deep link to `queue_url` for reviewer

---

### `approval_decided`

Emitted when a reviewer approves or rejects the pending approval. Execution resumes (if approved) or terminates gracefully (if rejected).

```
event: approval_decided
id: evt_007
data: {
  "approval_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "tool_call_id": "tc_refund_01",
  "decision": "approved",
  "reviewer": "jane.smith@company.com",
  "reviewer_notes": "Customer is a premium member, refund approved",
  "decided_at": "2026-06-24T20:15:33Z"
}
```

```
event: approval_decided
id: evt_008
data: {
  "approval_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "tool_call_id": "tc_refund_01",
  "decision": "rejected",
  "reviewer": "bob.jones@company.com",
  "reviewer_notes": "Refund exceeds policy limit",
  "decided_at": "2026-06-24T20:20:11Z"
}
```

```
event: approval_decided
id: evt_009
data: {
  "approval_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "tool_call_id": "tc_refund_01",
  "decision": "timed_out",
  "reviewer": null,
  "reviewer_notes": null,
  "decided_at": "2026-06-24T21:00:00Z"
}
```

**Fields:**
| Field | Type | Description |
|---|---|---|
| `approval_id` | UUID string | Matches the `approval_requested` event |
| `tool_call_id` | string | The tool call that was pending |
| `decision` | `"approved"` \| `"rejected"` \| `"timed_out"` | Reviewer's decision |
| `reviewer` | string \| null | Email of reviewer (null if timed_out) |
| `reviewer_notes` | string \| null | Optional notes from reviewer |
| `decided_at` | ISO 8601 datetime | When the decision was made |

---

### `done`

Final event in every stream. Signals the end of agent execution. The connection closes after this event.

```
event: done
id: evt_010
data: {
  "thread_id": "thread_abc123",
  "usage": {
    "input_tokens": 340,
    "output_tokens": 128,
    "total_tokens": 468,
    "model": "gpt-4o-mini"
  },
  "trace_id": "trace_langfuse_xyz",
  "duration_ms": 1842
}
```

**Fields:**
| Field | Type | Description |
|---|---|---|
| `thread_id` | string | Conversation thread ID (pass in next turn) |
| `usage.input_tokens` | integer | LLM input tokens used |
| `usage.output_tokens` | integer | LLM output tokens generated |
| `usage.total_tokens` | integer | Sum of input + output |
| `usage.model` | string | Model used for this turn |
| `trace_id` | string \| null | Langfuse trace ID for observability link |
| `duration_ms` | integer | Total wall-clock time for this turn |

---

### `error`

Emitted if an unrecoverable error occurs. The stream closes after this event.

```
event: error
id: evt_011
data: {
  "code": "safety_blocked",
  "message": "Request was blocked by safety scanner",
  "thread_id": "thread_abc123"
}
```

```
event: error
id: evt_012
data: {
  "code": "tool_execution_failed",
  "message": "lookup_order timed out after 5000ms",
  "thread_id": "thread_abc123"
}
```

**Error codes:**
| Code | Trigger |
|---|---|
| `safety_blocked` | Output safety scan blocked the response |
| `opa_denied` | OPA denied a tool call (not high-risk — just denied) |
| `tool_execution_failed` | Tool returned an unrecoverable error |
| `checkpoint_failed` | Could not write LangGraph checkpoint to Postgres |
| `internal_error` | Unexpected server error |

---

## Complete Example Stream

A full example of a high-risk tool call flow:

```
event: text_delta
id: evt_001
data: {"content": "I'll process that refund for you.", "index": 0}

event: tool_call_start
id: evt_002
data: {"tool_call_id": "tc_001", "tool": "lookup_order", "args": {"order_id": "12345"}, "risk": "low"}

event: tool_call_end
id: evt_003
data: {"tool_call_id": "tc_001", "tool": "lookup_order", "result": {"status": "delivered", "amount": 49.99}, "duration_ms": 312}

event: text_delta
id: evt_004
data: {"content": "Order 12345 was delivered. I'll initiate the refund now.", "index": 1}

event: tool_call_start
id: evt_005
data: {"tool_call_id": "tc_002", "tool": "issue_refund", "args": {"order_id": "12345", "amount": 49.99}, "risk": "high"}

event: approval_requested
id: evt_006
data: {"approval_id": "apr_abc123", "tool_call_id": "tc_002", "tool": "issue_refund", "args": {"order_id": "12345", "amount": 49.99}, "risk": "high", "expires_at": "2026-06-24T21:30:00Z", "queue_url": "https://appsmith.agentshield.internal/approvals/apr_abc123"}

[... stream is held open for up to 30min ...]

event: approval_decided
id: evt_007
data: {"approval_id": "apr_abc123", "tool_call_id": "tc_002", "decision": "approved", "reviewer": "jane@company.com", "reviewer_notes": null, "decided_at": "2026-06-24T20:45:12Z"}

event: tool_call_end
id: evt_008
data: {"tool_call_id": "tc_002", "tool": "issue_refund", "result": {"refund_id": "ref_xyz", "status": "processed"}, "duration_ms": 891}

event: text_delta
id: evt_009
data: {"content": "Your refund of $49.99 has been processed. Refund ID: ref_xyz.", "index": 2}

event: done
id: evt_010
data: {"thread_id": "thread_abc123", "usage": {"input_tokens": 340, "output_tokens": 128, "total_tokens": 468, "model": "gpt-4o-mini"}, "trace_id": "trace_lf_789", "duration_ms": 47523}
```

---

## Client Implementation Notes

### Using Vercel AI SDK

```typescript
import { useChat } from 'ai/react';

// AgentShield SSE is compatible with Vercel AI SDK's streamText protocol.
// Map events in a custom transport:
const { messages, append } = useChat({
  api: '/agents/order-agent/chat/stream',
  streamProtocol: 'text',  // use 'text' mode with manual event parsing
});
```

### Using `EventSource` directly

```typescript
const source = new EventSource('/agents/order-agent/chat/stream', {
  method: 'POST',  // Note: native EventSource doesn't support POST; use polyfill
  body: JSON.stringify({ message, thread_id }),
  headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
});

source.addEventListener('text_delta', (e) => {
  const { content } = JSON.parse(e.data);
  appendText(content);
});

source.addEventListener('approval_requested', (e) => {
  const { approval_id, tool, args } = JSON.parse(e.data);
  showApprovalBanner({ approval_id, tool, args });
});

source.addEventListener('done', (e) => {
  const { usage, thread_id } = JSON.parse(e.data);
  setThreadId(thread_id);
  source.close();
});
```

### Using `curl` for testing

```bash
# Full streaming test
curl -N -X POST http://localhost:8080/chat/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT" \
  -d '{"message":"Issue a refund for order 12345","thread_id":"test-thread-01"}'
```

---

## SDK Implementation Requirements

The `agentshield_sdk.streaming` module must:

1. Convert LangGraph `astream_events()` to SSE events
2. Map LangGraph event types to SSE events:
   - `on_chat_model_stream` → `text_delta`
   - `on_tool_start` → `tool_call_start`
   - `on_tool_end` → `tool_call_end`
   - `on_interrupt` (LangGraph interrupt) → `approval_requested`
3. Hold the HTTP connection open during `approval_requested` state
4. Resume emission after `approval_decided` is received
5. Always emit `done` as the last event, even on error (emit `error` then `done`)

# SSE Frame Contracts — POC-2b

Three frame vocabularies. Keep them distinct — the No-Bandaid rule requires ONE reader, but the
client-facing vocabulary differs from the pod-facing one.

---

## A. Client-facing workflow stream — `POST /workflows/{id}/runs/stream`

Data-only SSE frames (`data: <json>\n\n`), in graph order per member:

```jsonc
{ "type": "agent_start", "author": "researcher" }
{ "type": "token",       "author": "researcher", "content": "Looking up the record…" }   // reactive only
{ "type": "tool_call",   "author": "researcher", "tool": "lookup_fact", "status": "ok" }
{ "type": "rationale",   "author": "researcher", "content": "Searching so the answerer has a source." }
{ "type": "agent_end",   "author": "researcher" }
// … next member …
{ "type": "agent_start", "author": "answerer" }
{ "type": "token",       "author": "answerer", "content": "The record is…" }
{ "type": "agent_end",   "author": "answerer" }
{ "type": "done",        "run_id": "…" }
```

- **Durable member**: only `agent_start` → `agent_end` (no `token`/`tool_call`/`rationale`) — accepted asymmetry.
- **Error**: `{ "type": "error", "message": "…" }` then `{ "type": "done", "run_id": "…" }`.
- `author` is ALWAYS the member agent name. The frontend routes each frame to the author's bubble via
  `openAuthorBubble`/`routeToken`/`attachToolCall`/`attachRationale`.
- The internal `{ "type": "__member_end__", … }` sentinel is filtered server-side and NEVER appears here.

---

## B. Client-facing single-agent chat — `GET /agents/{name}/chat/{run_id}/stream` (EXTENDED)

Unchanged except tool chips now flow (the L473 drop is removed):

```jsonc
{ "type": "agent_start", "author": "my-agent" }
{ "type": "token",       "author": "my-agent", "content": "…" }
{ "type": "tool_call",   "author": "my-agent", "tool": "lookup_fact", "status": "ok" }   // NEW (was dropped)
{ "type": "approval_requested", "author": "my-agent", "approval_id": "…", "tool": "…", "risk": "high" }
{ "type": "error",       "message": "…" }
{ "type": "done",        "run_id": "…" }
```

- No `rationale` frame for single-agent chat (scope="agent").
- `author` = the agent name (single speaker → the degenerate case; the reducer matches any assistant bubble).

---

## C. Registry-internal normalized frames — `stream_pod_chat_frames` (Python dicts, NOT SSE)

The ONE pod-SSE reader yields these dicts; callers serialize. **No run-level `done`** — the caller owns it.

```python
{"type": "agent_start", "author": author}
{"type": "token",       "author": author, "content": str}
{"type": "tool_call",   "author": author, "tool": str, "status": "ok" | "error"}
{"type": "rationale",   "author": author, "content": str}
{"type": "approval_requested", "author": author, **payload}
{"type": "error",       "author": author, "message": str}
```

**Translation table (member pod event → normalized frame):**

| Pod `event:` | Normalized dict |
|---|---|
| (before first token) | `agent_start` |
| `text_delta` | `token` (content from payload) |
| `tool_call_start` | `tool_call` status=`ok` (tool from `payload.tool`/`payload.tool_name`) |
| `tool_call_end` | `tool_call` status=`error` ONLY if payload signals error; else skipped (one chip per call) |
| `rationale` | `rationale` |
| `error` | `error` |
| `approval_requested` | `approval_requested` |
| `done` | (not yielded — reader continues to EOF to capture a trailing `rationale`) |

---

## D. Orchestrator-internal sentinel (never serialized to any client)

`_dispatch_stream` / `_run_step_stream` yield a terminal sentinel the mode walkers consume for routing:

```python
{"type": "__member_end__", "author": agent_name,
 "status": "completed" | "failed" | "awaiting_approval",
 "output": str | None, "error": str | None}
```

The SSE serializer in `stream_workflow_run` drops any frame with `type == "__member_end__"`.

---

## E. Frontend consumption (CatalogChatPage)

```
agent_start → openAuthorBubble(prev, author, mk)
token       → routeToken(prev, author, content, mk)
tool_call   → attachToolCall(prev, author, {tool_name: f.tool, status: f.status}, mk)
rationale   → attachRationale(prev, author, f.content, mk)
agent_end   → (no-op; bubble already open)
done        → stop reader
error       → append "[Error: …]" to the open bubble
```
`mk(author)` seeds a rich `Message{role:"assistant", content:"", author, toolCalls:[], rationale:null}`.
Amber rationale visibility is gated on the page-level `showRationale` checkbox (passed to `AttributedBubble`).

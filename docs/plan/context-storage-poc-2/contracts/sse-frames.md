# Contract — Chat SSE frames (with `author`)

**Producer**: `services/registry-api/routers/chat.py::_proxy_agent_stream` (single-agent chat only — see research.md D1).
**Consumers**: `studio/src/pages/AgentChatPage.tsx`, `studio/src/pages/CatalogChatPage.tsx` (single-agent path).
**Transport**: `text/event-stream`, each frame `data: <json>\n\n` (data-only; no SSE `event:` name — the frontend reads `d.type`).

`author` = the agent name (the `{name}` path param). Additive: clients ignoring `author` are unaffected. Workflow runs do NOT use this stream.

---

## Frame catalogue

### `agent_start` — NEW (emitted exactly once, right after the upstream 200, before the first token)
```json
{"type": "agent_start", "author": "refund-agent"}
```
Signals the client to open a fresh labeled assistant bubble for `author`.

### `token` — CHANGED (`author` added)
```json
{"type": "token", "content": "Your refund of ", "author": "refund-agent"}
```
Before POC-2: `{"type":"token","content":"Your refund of "}`.

### `done` — unchanged
```json
{"type": "done", "run_id": "b1f2…"}
```

### `error` — unchanged
```json
{"type": "error", "message": "Agent returned HTTP 503"}
```
(followed by a `done` frame)

### `approval_requested` — unchanged
```json
{"type": "approval_requested", "approval_id": "…", "tool": "wire_transfer", "risk": "high", "args": {…}}
```

---

## Example single-agent stream
```
data: {"type":"agent_start","author":"refund-agent"}

data: {"type":"token","content":"Your ","author":"refund-agent"}

data: {"type":"token","content":"refund is approved.","author":"refund-agent"}

data: {"type":"done","run_id":"b1f2c3…"}

```

---

## Backend edit map (`chat.py`)
- `_proxy_agent_stream(...)` signature (L373) gains `author: str`.
- After `response.status_code == 200` check (before the `async for line` loop, ~L444): `yield _emit({"type":"agent_start","author":author})`.
- `text_delta` branch (L459): `yield _emit({"type":"token","content":payload.get("content",""),"author":author})`.
- Call sites pass `author=name`: `stream_chat` (L786), `stream_deployment_chat` (L976). In `resume_stream_chat` the token frame (L1122) also adds `"author": name` (one-speaker resume — same agent).

## Reducer mapping (frontend)
| Frame | Reducer call |
|---|---|
| `agent_start` | `openAuthorBubble(prev, d.author, mk)` |
| `token` | `routeToken(prev, d.author, d.content, mk)` |
| `done`/`error`/`approval_requested` | unchanged handlers |

where `mk = (author) => ({ role: "assistant", content: "", author })`.

## Test hook (suite-75 T-S75-007)
Start a `/chat` turn on `CHAT_AGENT`, read the SSE, assert at least one frame with `type == "token"` and `author == CHAT_AGENT`.

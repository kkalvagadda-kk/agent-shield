# Consumer Chat API Contract

**Phase B — New endpoints in `services/registry-api/routers/chat.py`**
**Mounted at:** registered in `main.py` via `app.include_router(chat_router)` with prefix `/api/v1/agents`

---

## POST /api/v1/agents/{name}/chat

**Purpose:** Start a new chat session with a deployed agent. Validates caller's grant, confirms a running deployment exists, creates a `PlaygroundRun` record in production context, and returns a stream URL.

### Auth
- Requires `Authorization: Bearer <token>` (Keycloak JWT)
- Caller's `sub` claim is resolved to a team via `user_team_assignments`

### Request

```
POST /api/v1/agents/{name}/chat
Content-Type: application/json
Authorization: Bearer <jwt>
```

```json
{
  "message": "What is the CPU utilization of pipeline X?",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | The user's chat input |
| `session_id` | string (UUID) | No | Client-generated session UUID for conversation continuity. If omitted, server generates one. |

### Success Response — 200 OK

```json
{
  "run_id": "7b9d3f2a-...",
  "session_id": "550e8400-...",
  "stream_url": "/api/v1/agents/customer-intelligence-agent/chat/7b9d3f2a-.../stream",
  "agent_name": "customer-intelligence-agent",
  "deployment_id": "a1b2c3d4-..."
}
```

| Field | Description |
|-------|-------------|
| `run_id` | UUID of the created `PlaygroundRun`. Use to open the SSE stream. |
| `session_id` | Echo of input or server-generated UUID. Persist client-side for continuity. |
| `stream_url` | Full path (not origin-relative) to the SSE stream endpoint. Open with `EventSource`. |
| `agent_name` | Echo of the `{name}` route param. |
| `deployment_id` | UUID of the running `Deployment` record. |

### Error Responses

| Status | Condition |
|--------|-----------|
| 401 Unauthorized | No or invalid JWT |
| 403 Forbidden | `"User has no team assignment."` — user's Keycloak sub not in `user_team_assignments` |
| 403 Forbidden | `"Team '{team}' does not have access to agent '{name}'."` — no active, unexpired `asset_grant` |
| 404 Not Found | `"Agent '{name}' not found."` — no active agent with that name |
| 503 Service Unavailable | `"Agent '{name}' has no running deployment. Deploy it first."` — no `deployment` row with `status='running'` |

### Access Rules

1. If caller's team == agent's `team` (owner team) → allow without grant check
2. Else look up grant in `asset_grants` where `asset_id=agent.id AND asset_type='agent' AND grantee_team=caller_team AND revoked_at IS NULL`
3. If grant exists and `expires_at` is null or in the future → allow
4. Otherwise → 403

---

## GET /api/v1/agents/{name}/chat/{run_id}/stream

**Purpose:** SSE stream for a specific chat run. Returns tokens as `data:` events. Ownership-checked so only the session's creator can stream it.

### Auth
- Requires `Authorization: Bearer <jwt>` (same caller who created the run)
- `EventSource` in browsers sends cookies but not `Authorization` headers — the frontend must append the token as a query param or use a fetch-based EventSource polyfill if the JWT is the only auth mechanism.

  **Workaround used in Phase B:** Frontend calls `startAgentChat` to get `stream_url`, then opens `new EventSource(stream_url + "?token=" + kc.token)`. Backend reads `token` query param if `Authorization` header is absent.

  (If Keycloak session cookies are available — e.g., Studio runs same-origin as the API — the cookie is sent automatically and the query-param workaround is not needed. Phase B uses the query-param approach for portability.)

### Request

```
GET /api/v1/agents/{name}/chat/{run_id}/stream
Authorization: Bearer <jwt>   (or ?token=<jwt>)
```

### Success Response — 200 OK

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

Event stream format:
```
data: {"type": "token", "content": "Hello"}

data: {"type": "token", "content": " there"}

data: {"type": "done", "run_id": "7b9d3f2a-..."}

```

| Event type | Fields | Meaning |
|------------|--------|---------|
| `token` | `content: string` | Append this text to the current assistant message |
| `done` | `run_id: string` | Stream complete. Close EventSource. |
| `error` | `message: string` | Runtime error during generation. Close EventSource, show error. |

### Error Responses

| Status | Condition |
|--------|-----------|
| 401 | No/invalid JWT |
| 403 | `run.user_id != caller.sub` — not your run |
| 404 | `run_id` not found or `run.agent_name != name` |

---

## Frontend EventSource Pattern (B3)

```typescript
const res = await startAgentChat(name!, { message: input, session_id: sessionId });
const url = res.stream_url + `?token=${kc.token}`;
const source = new EventSource(url);

// Append empty assistant bubble before first token arrives
setMessages(prev => [...prev, { role: "assistant", content: "" }]);

source.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === "token") {
    setMessages(prev => {
      const copy = [...prev];
      copy[copy.length - 1] = { ...copy[copy.length - 1], content: copy[copy.length - 1].content + data.content };
      return copy;
    });
  } else if (data.type === "done") {
    source.close();
    setIsStreaming(false);
  }
};

source.onerror = () => {
  source.close();
  setIsStreaming(false);
};
```

---

## Phase B Chat Scope Notes

**Phase B does NOT implement:**
- Real LLM/agent invocation — the stream endpoint plays back `run.input_message` word-by-word as a placeholder. Full agent execution is deferred to Phase D (out of scope for this plan).
- Multi-turn conversation history — `session_id` is tracked client-side only. Each `POST /chat` is a new `PlaygroundRun`.
- HITL mid-stream pause — production HITL is handled by the existing `/approvals/` workflow; the chat endpoint will integrate with it in Phase D.
- File attachments, tool results display — plaintext streaming only.

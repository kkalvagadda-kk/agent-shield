# Contract — List Conversations endpoints + `ConversationSummary`

Two `require_user` endpoints, one shared DTO, one shared read query. All ownership-scoped
to the caller's `sub`. No request body (GET). No new storage.

---

## Schema: `ConversationSummary`

| Field | Type | Notes |
|---|---|---|
| `thread_id` | string | conversation key (== `session_id`) |
| `session_id` | string \| null | echo of `thread_id` |
| `agent_name` | string | agent the conversation is with |
| `title` | string \| null | first user message; `null` if no user turn |
| `message_count` | integer | rows in the thread |
| `last_activity` | string (ISO-8601 datetime) | `max(created_at)` |
| `environment` | `"sandbox"` \| `"production"` | derived from `deployment_id` |
| `deployment_id` | string (uuid) \| null | the deployment the thread ran against |

Ordering: `last_activity` DESC. Titling is deferred to Haiku (POC-1b); until then
`title` = first user message.

---

## Endpoint 1 — scoped (docked History + deployment tab)

```
GET /api/v1/agents/{name}/memory/conversations
```

Auth: `require_user`. Router: `routers/memory.py` (prefix `/api/v1/agents`).

| Query param | Type | Default | Notes |
|---|---|---|---|
| `deployment_id` | string (uuid) | — | when present, only that deployment's threads |
| `limit` | int | 100 | `1..200` |
| `offset` | int | 0 | `>=0` |

Behaviour:
- `404` if agent `{name}` does not exist (`_get_agent_or_404`).
- Filters `agent_name = {name}` AND `user_id = caller.sub` (+ `deployment_id` when given).
- `200` → `ConversationSummary[]` (may be empty).

```jsonc
// GET /api/v1/agents/refund-bot/memory/conversations?deployment_id=6b1e...&limit=50
[
  {
    "thread_id": "b2f1c0de-...-a1",
    "session_id": "b2f1c0de-...-a1",
    "agent_name": "refund-bot",
    "title": "I need a refund for order 4471",
    "message_count": 6,
    "last_activity": "2026-07-16T14:22:09.113Z",
    "environment": "production",
    "deployment_id": "6b1e...-9c"
  }
]
```

## Endpoint 2 — cross-agent (standalone page)

```
GET /api/v1/me/conversations
```

Auth: `require_user`. Router: `routers/me.py` (prefix `/api/v1/me`).

| Query param | Type | Default | Notes |
|---|---|---|---|
| `limit` | int | 100 | `1..200` |
| `offset` | int | 0 | `>=0` |

Behaviour:
- No agent/deployment filter — every thread owned by `caller.sub`, across all agents and
  both environments.
- Each row carries `environment` so the standalone page's All/Sandbox/Production filter is
  a pure client predicate (no server round-trip on filter change).
- `200` → `ConversationSummary[]`.

---

## Ownership contract (tested by suite-78)

- A caller only ever receives rows where `agent_memory.user_id = caller.sub`. Two users
  with conversations on the same agent see **disjoint** lists.
- Identity comes from the JWT (`require_user`), never from a query/body param.
- Scoped endpoint with a sandbox `deployment_id` returns only sandbox threads; with a
  `production_deployments.id` returns only production threads (each tagged accordingly).

## Seeding a selected conversation (existing endpoint, reused)

Selecting a row rehydrates its transcript via the existing:

```
GET /api/v1/agents/{name}/memory?thread_id={thread_id}&deployment_id={deployment_id}&limit=200
```

→ `AgentMemoryResponse[]` (oldest-first by `message_index`). Continue-with-context needs
**no new endpoint**: re-POSTing chat with `session_id = thread_id` reloads prior turns
server-side.

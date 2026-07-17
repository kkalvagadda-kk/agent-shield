# POC-5 — Data Model

POC-5 introduces **no new storage**. It adds one read-side aggregate over the existing
`agent_memory` table and one response DTO (`ConversationSummary`). No Alembic migration.

---

## 1. Source table (unchanged) — `agent_memory`

`models.py::AgentMemory` (head migration `0064_agent_memory_shared_thread`). Columns POC-5
reads:

| Column | Type | POC-5 role |
|---|---|---|
| `thread_id` | `varchar(256)` | **group key** (== `session_id` == conversation_id) |
| `session_id` | `varchar(256)` null | echoed into the summary (== `thread_id`) |
| `agent_name` | `varchar(256)` | which agent the conversation is with |
| `user_id` | `varchar(256)` null | **ownership** filter (`= caller.sub`) |
| `role` | `varchar(16)` | `title` picks the first `role='user'` content |
| `content` | `text` | title source |
| `message_index` | `int` | orders the title pick + counts |
| `created_at` | `timestamptz` | `last_activity = max`, ordering |
| `deployment_id` | `uuid` null | **environment derivation** (join `production_deployments`) |

No column is added. Existing indexes (`ix_agent_memory_thread_msg`,
`idx_agent_memory_thread_scope`) cover the group/order.

## 2. Environment derivation (the one subtlety)

`agent_memory` has **no `environment` column** (see research §R2). Environment is derived
by the two-table split — a row is **production iff `deployment_id ∈ production_deployments`**,
else **sandbox** (a sandbox `deployments.id`, or `NULL`):

```
environment := CASE WHEN bool_or(pd.id IS NOT NULL) THEN 'production' ELSE 'sandbox' END
               -- pd = LEFT JOIN production_deployments ON agent_memory.deployment_id = pd.id
```

`deployment_id` is constant within a `thread_id`, so `bool_or` collapses cleanly per group.

## 3. The aggregate query (authoritative)

`memory.list_conversations` runs exactly this (raw SQL — `array_agg … FILTER` + `bool_or`
have no clean ORM form):

```sql
SELECT
  am.thread_id                                              AS thread_id,
  min(am.session_id)                                        AS session_id,   -- == thread_id
  min(am.agent_name)                                        AS agent_name,   -- representative
  (array_agg(am.content ORDER BY am.message_index)
     FILTER (WHERE am.role = 'user'))[1]                    AS title,        -- first user msg
  count(*)                                                  AS message_count,
  max(am.created_at)                                        AS last_activity,
  min(am.deployment_id)::text                               AS deployment_id,
  CASE WHEN bool_or(pd.id IS NOT NULL)
       THEN 'production' ELSE 'sandbox' END                 AS environment
FROM agent_memory am
LEFT JOIN production_deployments pd ON am.deployment_id = pd.id
WHERE am.user_id = :user_id
  AND (CAST(:agent_name AS text)    IS NULL OR am.agent_name    = :agent_name)
  AND (CAST(:deployment_id AS uuid) IS NULL OR am.deployment_id = CAST(:deployment_id AS uuid))
GROUP BY am.thread_id
ORDER BY max(am.created_at) DESC
LIMIT :limit OFFSET :offset;
```

Bind params: `user_id` (str, required), `agent_name` (str|None), `deployment_id`
(str|None), `limit` (int), `offset` (int). `title` is `NULL` for a thread with no
`role='user'` row (rendered as a fallback label client-side). For a `workflow_run`-scope
thread, multiple `agent_name`s exist; `min(agent_name)` picks a stable representative
(these are secondary — the primary resume target is `scope='agent'` chat threads).

Each result row maps directly onto `ConversationSummary` (via `._mapping`).

## 4. `ConversationSummary` DTO

### Pydantic (`schemas.py`)

```python
class ConversationSummary(BaseModel):
    thread_id: str
    session_id: str | None = None
    agent_name: str
    title: str | None = None          # first user message; None if none
    message_count: int
    last_activity: datetime
    environment: str                  # 'sandbox' | 'production'
    deployment_id: uuid.UUID | None = None
```

### TypeScript (`registryApi.ts`)

```ts
export interface ConversationSummary {
  thread_id: string;
  session_id: string | null;
  agent_name: string;
  title: string | null;
  message_count: number;
  last_activity: string;              // ISO-8601
  environment: "sandbox" | "production";
  deployment_id: string | null;
}
```

## 5. Ownership & scoping invariants

- **Ownership**: every query is `WHERE user_id = :caller.sub`. A user can never list
  another user's conversations. Enforced in `require_user`-guarded endpoints only
  (never trust a query param for identity).
- **Scoped read** (`GET /agents/{name}/memory/conversations?deployment_id=`): adds
  `agent_name = {name}` (from path) and, when present, `deployment_id`. Sandbox and
  production deployments each return only their own threads.
- **Cross-agent read** (`GET /me/conversations`): `agent_name` and `deployment_id` both
  omitted; every owned thread returns, each carrying its derived `environment` so the
  standalone page filter is a pure client predicate.

## 6. Known data caveats (ledger, not blockers)

- Rows with `user_id IS NULL` (daemon/legacy) are invisible to both endpoints — correct;
  they have no owner to scope to.
- `title` may be `NULL` (no user turn yet) → client renders `"Untitled conversation"`.
- Haiku-generated titles are **deferred to POC-1b**; POC-5 title = first user message.

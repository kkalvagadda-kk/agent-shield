# Data Model — POC-2b Rich Multi-Agent Workflow Console

**No new migration.** POC-2b reuses schema already present at Alembic head `0064`
(`agent_memory` scope/message_kind, `run_steps`). This document specifies how existing
tables are read/written and the projection shapes, so a cold agent needs no schema guesswork.

---

## 1. `agent_memory` (existing) — rationale rows (2b-ii)

Relevant columns (models.py L1779):

| Column | Type | Use in POC-2b |
|---|---|---|
| `thread_id` | `VARCHAR(256)` | the conversation/transcript key = the workflow run's `conversation_id` |
| `agent_name` | `VARCHAR(256)` | the AUTHORING member (row-level attribution) |
| `role` | `VARCHAR(16)` | `'assistant'` for a rationale row |
| `content` | `TEXT` | the one-sentence rationale text |
| `scope` | `VARCHAR(16)` | `'workflow_run'` for shared-thread rows |
| `message_kind` | `VARCHAR(16)` | **`'rationale'`** (already in the CHECK constraint from 0064) |
| `message_index` | `INT` | monotonic per thread; `UNIQUE(thread_id, message_index)` |
| `workflow_run_id` | `UUID` | the parent workflow run id |

**Write** (runner → `POST /agents/{name}/memory` → `ConversationStore.append`): a workflow member's
turn now writes THREE messages in one append (order preserved by `message_index`):
```
{role:"user",      content:<user query>,     message_kind:"user"}
{role:"assistant", content:<final output>,   message_kind:"agent_output"}
{role:"assistant", content:<rationale>,       message_kind:"rationale"}   # only when rationale != "" and scope=="workflow_run"
```
`ConversationStore.append` already persists per-message `message_kind` (conversation_store.py L47),
so no store change is needed — only the runner passes the extra message.

**Read (reload — tree projection)**: for each run-tree child, the tree endpoint selects the
latest rationale row:
```sql
SELECT content FROM agent_memory
 WHERE thread_id = :conversation_id AND scope = 'workflow_run'
   AND message_kind = 'rationale' AND agent_name = :child_agent_name
 ORDER BY message_index DESC LIMIT 1;
```
→ `AgentRunResponse.rationale = row.content or None`.
`conversation_id = parent.session_id or str(run_id)` (see §4).

**Read (LLM context injection)**: unchanged — `_load_memory_context` (runner) reads `scope='workflow_run'`
transcript; rationale rows come back tagged `message_kind='rationale'` and are prefixed `[<author>]:`
for peers (existing behavior, main.py L450). Rationale is intentionally part of the shared context a
downstream member reads (design §5.2).

---

## 2. `run_steps` (existing) — tool-call marker rows (2b-i)

Relevant columns (models.py L1631):

| Column | Type | Use in POC-2b |
|---|---|---|
| `run_id` | `UUID` (polymorphic, no FK) | the CHILD AgentRun id of the member |
| `step_number` | `INT` | per-child sequence (orchestrator counter), `UNIQUE(run_id, step_number)` |
| `name` | `VARCHAR(255)` | the tool name |
| `status` | `VARCHAR(24)` | `'completed'` (tool ok) or `'failed'` (tool error) — must satisfy the CHECK |
| `output` | `JSONB` | **marker** `{"kind":"tool_call","tool":<name>,"status":"ok"|"error"}` |

**Write** (streaming orchestrator, reactive members): `_dispatch_stream` writes one `RunStep` per observed
`tool_call` frame. A reactive member and a durable member are never the same run, so `step_number`
never collides with the durable step-update callback's rows.

**Read (tree projection)**:
```sql
SELECT name, output FROM run_steps
 WHERE run_id = :child_id AND output->>'kind' = 'tool_call'
 ORDER BY step_number;
```
→ `AgentRunResponse.tool_calls = [ToolCallProjection(tool_name=name, status=output->>'status')]`.

**Durable members**: their native run_steps lack the `output.kind='tool_call'` marker → not projected as
chips on reload (gap-ledgered). Live: durable members emit no tool_call frames (poll-only).

---

## 3. Projection & request schemas (schemas.py — new/changed)

```python
class ToolCallProjection(BaseModel):
    tool_name: str
    status: str                         # "ok" | "error"

class WorkflowRunStreamRequest(BaseModel):
    message: str
    session_id: str | None = None

class AgentRunResponse(BaseModel):      # ADD two non-ORM fields (defaults; set manually like trace_url)
    ...
    tool_calls: list[ToolCallProjection] = Field(default_factory=list)
    rationale: str | None = None
```

Frontend mirror (`registryApi.ts`):
```ts
interface AgentRunItem {
  ...
  tool_calls?: { tool_name: string; status: string }[];
  rationale?: string | null;
}
```

---

## 4. `agent_runs` (existing) — the workflow-run transcript key

- The workflow PARENT run row already carries `session_id` (models.py L1560). POC-2b sets
  `parent.session_id = body.session_id` in the NEW stream endpoint (when a session is supplied), and
  leaves it NULL for the non-stream `/runs` path.
- **Transcript key resolution** (single source of truth): `conversation_id = parent.session_id or str(run_id)`.
  - non-stream `/runs`: session_id NULL → key = run_id = parent_run_id (matches today's `_run_step` `conversation_id=parent_run_id`).
  - stream `/runs/stream` with a session: key = session_id (threads across turns — POC-5 direction).
- The orchestrator threads `conversation_id` from the endpoint through `orchestrate_stream` → walkers →
  `_run_step_stream` → `_dispatch_stream`, replacing the hardcoded `conversation_id = parent_run_id`
  in `_run_step`.

---

## 5. In-flight frame model (not persisted — see contracts/sse-frames.md)

The live channel carries typed frames; every richness frame (`tool_call`, `rationale`) also has a
persisted counterpart (run_steps / agent_memory) so reload reproduces the stream. The internal
`__member_end__` sentinel is orchestrator-only and is filtered out of the client SSE.

---

## 6. Invariants

1. **Reload == stream.** Every `tool_call` frame → a `run_steps` marker row; every `rationale` frame →
   an `agent_memory` rationale row. A reloaded tree reproduces the same chips + rationale.
2. **Author on every richness row.** `run_steps.run_id` scopes tool chips to the child; `agent_memory.agent_name`
   scopes rationale to the authoring member. No cross-member leakage of chips/rationale.
3. **Message index is monotonic + unique per thread** (0064 `UNIQUE(thread_id, message_index)` + advisory-lock
   allocation in `ConversationStore.append`) — the extra rationale message cannot collide.
4. **Non-blocking rationale.** `_extract_tool_rationale` returns `""` for tool-less members → no rationale
   message written, no frame emitted, no amber box. Never gate a run on it.

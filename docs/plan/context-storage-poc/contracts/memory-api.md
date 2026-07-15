# Contract — Memory read/write API shapes

Endpoints in `services/registry-api/routers/memory.py` (mounted at `/api/v1/agents`). Called service-to-service by the declarative-runner (no JWT; ownership is enforced at the chat edge — see thread-ownership.md).

## Schemas (`services/registry-api/schemas.py`)

`MemoryMessage` (add `message_kind`):
```python
class MemoryMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system|tool)$")
    content: str
    message_kind: str | None = Field(None, pattern="^(user|agent_output|rationale)$")
    # None → derived on save: role=='user' → 'user', else 'agent_output'
```

`MemorySaveTurnRequest` (add `scope`, `workflow_run_id`):
```python
class MemorySaveTurnRequest(BaseModel):
    thread_id: str                       # == conversation_id (transcript key)
    messages: list[MemoryMessage]
    session_id: str | None = None
    user_id: str | None = None
    deployment_id: str | None = None
    scope: str = Field("agent", pattern="^(agent|workflow_run)$")
    workflow_run_id: str | None = None
    author_agent_name: str | None = None  # overrides the path {name} as row author
                                          # (a workflow member writes under its own name)
```

`AgentMemoryResponse` (add `agent_name`, `message_kind`, `scope`):
```python
class AgentMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    agent_name: str
    thread_id: str
    role: str
    content: str
    message_index: int
    message_kind: str
    scope: str
    created_at: datetime
    user_id: str | None = None
    session_id: str | None = None
    deployment_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
```

## `POST /agents/{name}/memory`

Save a turn. Body = `MemorySaveTurnRequest`. Routes through `get_conversation_store().append(conversation_id=body.thread_id, agent_name=body.author_agent_name or name, team=agent.team, turns=[...], scope=body.scope, user_id=body.user_id, deployment_id=body.deployment_id, workflow_run_id=body.workflow_run_id)`. Keeps the `memory_enabled` 400 guard. Returns `list[AgentMemoryResponse]` (201).

## `GET /agents/{name}/memory`

List / load transcript. Query params:
| Param | Type | Default | Meaning |
|---|---|---|---|
| `thread_id` | str? | — | conversation key |
| `scope` | str | `agent` | `agent` \| `workflow_run` |
| `user_id` | str? | — | constrains `agent`-scope reads to one user |
| `deployment_id` | str? | — | scopes to a deployment |
| `limit` | int | 50 | 1..200 |
| `offset` | int | 0 | (agent scope only) |

Routes through `get_conversation_store().load(...)`. Ordering: **`message_index`** (ascending → oldest-first transcript). For `scope='workflow_run'` the `agent_name` path segment is used only as a hint; the read returns **all authors** for that `thread_id`. Returns `list[AgentMemoryResponse]`.

## `DELETE /agents/{name}/memory/{thread_id}` and `/memory/clear`

Route through `store.erase(...)`. Behavior unchanged from today except the store indirection. (Checkpoint-spanning erasure is S8/Tighten — not here.)

## Runner-side call shapes (`declarative-runner/main.py`)

Load (member/agent):
```
GET /api/v1/agents/{AGENT_NAME}/memory
    ?thread_id={conversation_id}&scope={scope}&user_id={user_id}
     &deployment_id={AGENTSHIELD_DEPLOYMENT_ID}&limit=20
```
Save (agent, scope='agent'):
```
POST /api/v1/agents/{AGENT_NAME}/memory
{ "thread_id": conversation_id, "user_id": user_id, "deployment_id": dep_id,
  "scope": "agent",
  "messages": [ {"role":"user","content":...,"message_kind":"user"},
                {"role":"assistant","content":...,"message_kind":"agent_output"} ] }
```
Save (workflow member, scope='workflow_run'):
```
POST /api/v1/agents/{AGENT_NAME}/memory
{ "thread_id": conversation_id, "scope": "workflow_run",
  "workflow_run_id": parent_run_id, "author_agent_name": AGENT_NAME,
  "user_id": user_id, "deployment_id": dep_id,
  "messages": [ {"role":"user","content":<step input>,"message_kind":"user"},
                {"role":"assistant","content":<verbatim output>,"message_kind":"agent_output"} ] }
```

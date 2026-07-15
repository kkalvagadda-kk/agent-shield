# Contract — `ConversationStore` port (§4.1)

New files: `services/registry-api/conversation_store.py` (port + default adapter), `services/registry-api/store_factory.py` (choke point).

## Types

```python
from typing import Literal, Protocol, TypedDict
from typing_extensions import NotRequired
from models import AgentMemory

Scope = Literal["agent", "workflow_run"]

class Turn(TypedDict):
    role: str                          # user | assistant | system | tool
    content: str
    agent_name: NotRequired[str | None]     # author (returned by workflow_run reads)
    message_kind: NotRequired[str]          # user | agent_output | rationale
```

## Port

```python
class ConversationStore(Protocol):
    async def append(
        self, *,
        conversation_id: str,
        agent_name: str,
        team: str,
        turns: list[Turn],
        scope: Scope = "agent",
        user_id: str | None = None,
        deployment_id: str | None = None,
        workflow_run_id: str | None = None,
    ) -> list[AgentMemory]:
        """Append a turn (1+ messages) to the transcript keyed by conversation_id.
        Allocates message_index atomically (advisory lock; see data-model §5).
        Persists scope / workflow_run_id / per-message message_kind."""

    async def load(
        self, *,
        conversation_id: str,
        scope: Scope = "agent",
        limit: int,
        agent_name: str | None = None,
        user_id: str | None = None,
        deployment_id: str | None = None,
    ) -> list[Turn]:
        """Load the last `limit` turns ordered by message_index.
          scope='agent'        → filter (agent_name, conversation_id) + user_id when given.
          scope='workflow_run' → filter conversation_id + scope; DROP agent_name filter
                                 (cross-agent read); each Turn carries its author agent_name."""

    async def erase(
        self, *,
        conversation_id: str | None = None,
        agent_name: str | None = None,
        user_id: str | None = None,
        deployment_id: str | None = None,
    ) -> int:
        """GDPR/erasure. Returns rows removed. (Checkpoint-spanning erasure is S8/Tighten.)"""
```

## Default adapter

`PostgresConversationStore(ConversationStore)` — delegates to the `memory.py` service functions (`save_turn`, `load_context`, `delete_thread`/`clear_agent_memory`). It is the ONLY module that maps `Turn`↔`AgentMemory` for the transcript. Security invariants (scope handling, user_id constraint, workflow_run agent_name drop) are enforced here per the port contract.

## Choke point

```python
# store_factory.py
import os
from conversation_store import ConversationStore, PostgresConversationStore

def get_conversation_store() -> ConversationStore:
    backend = os.getenv("CONVERSATION_STORE", "postgres")
    if backend == "postgres":
        return PostgresConversationStore()
    raise ValueError(f"Unknown CONVERSATION_STORE={backend!r}")
```

## Rules (from §4.1)

- Exactly one constructor (`get_conversation_store`); callers depend on the `Protocol`.
- No `AgentMemory`/SQL access for the transcript outside the adapter + `memory.py`.
- `scope` and `user_id` are required-by-signature parameters of `load` — an adapter cannot silently forget them (security-invariant-in-contract).
- Adapters are independently swappable: a future `RedisConversationStore`/other log store ships as a new class + a `CONVERSATION_STORE` value, with zero change to `routers/memory.py`.

## Live callers (no-orphan gate)

- Constructed: `store_factory.get_conversation_store`.
- Called: `routers/memory.py` `save_turn` (→ `append`), `list_memory` (→ `load`), `delete_memory_thread`/`clear_memory` (→ `erase`).

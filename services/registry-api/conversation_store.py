"""ConversationStore port + default Postgres adapter (§4.1).

The transcript seam: `routers/memory.py` and the runner talk to a
`ConversationStore` Protocol, never to `AgentMemory`/SQL directly. The default
`PostgresConversationStore` delegates to the `memory.py` service functions
(`save_turn`, `load_context`, `delete_thread`, `clear_agent_memory`), which own
the actual SQL. Security invariants (scope handling, user_id constraint, the
workflow_run agent_name-drop) live in that layer per the port contract.

The port is intentionally narrow (append/load/erase — exactly what POC-0/1 uses)
but backed by the real adapter so the seam ships from day one. A future
`RedisConversationStore`/other log store is a new class + a `CONVERSATION_STORE`
value, with zero change to callers.

Note on `db`: the abstract signatures in contracts/conversation-store.md omit the
transport detail. The Postgres adapter delegates to `memory.py`, which requires an
`AsyncSession` unit-of-work, so `db` is the first parameter of each method here.
The factory stays argument-less (the store is stateless; the request-scoped session
flows through each call), keeping a single construction choke point.
"""
from __future__ import annotations

from typing import Literal, Protocol, TypedDict

from typing_extensions import NotRequired

from sqlalchemy.ext.asyncio import AsyncSession

import memory
from models import AgentMemory

Scope = Literal["agent", "workflow_run"]


class Turn(TypedDict):
    role: str  # user | assistant | system | tool
    content: str
    agent_name: NotRequired[str | None]  # author (returned by workflow_run reads)
    message_kind: NotRequired[str]  # user | agent_output | rationale


class ConversationStore(Protocol):
    async def append(
        self,
        db: AsyncSession,
        *,
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
        ...

    async def load(
        self,
        db: AsyncSession,
        *,
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
        ...

    async def erase(
        self,
        db: AsyncSession,
        *,
        conversation_id: str | None = None,
        agent_name: str | None = None,
        user_id: str | None = None,
        deployment_id: str | None = None,
    ) -> int:
        """GDPR/erasure. Returns rows removed. (Checkpoint-spanning erasure is S8/Tighten.)"""
        ...


class PostgresConversationStore:
    """Default adapter — the ONLY place that maps Turn↔AgentMemory for the
    transcript, delegating all SQL to the `memory.py` service functions."""

    async def append(
        self,
        db: AsyncSession,
        *,
        conversation_id: str,
        agent_name: str,
        team: str,
        turns: list[Turn],
        scope: Scope = "agent",
        user_id: str | None = None,
        deployment_id: str | None = None,
        workflow_run_id: str | None = None,
    ) -> list[AgentMemory]:
        messages = [
            {
                "role": t["role"],
                "content": t["content"],
                # message_kind is optional on the Turn; memory.save_turn derives a
                # sensible default from role when absent.
                **({"message_kind": t["message_kind"]} if t.get("message_kind") else {}),
            }
            for t in turns
        ]
        return await memory.save_turn(
            db,
            agent_name=agent_name,
            team=team,
            thread_id=conversation_id,
            messages=messages,
            user_id=user_id,
            session_id=conversation_id,
            deployment_id=deployment_id,
            scope=scope,
            workflow_run_id=workflow_run_id,
        )

    async def load(
        self,
        db: AsyncSession,
        *,
        conversation_id: str,
        scope: Scope = "agent",
        limit: int,
        agent_name: str | None = None,
        user_id: str | None = None,
        deployment_id: str | None = None,
    ) -> list[Turn]:
        rows = await memory.load_context(
            db,
            agent_name=agent_name or "",
            thread_id=conversation_id,
            window=limit,
            deployment_id=deployment_id,
            scope=scope,
            # user_id constrains agent-scope reads only; load_context ignores it for
            # workflow_run (a run belongs to one initiating user, spans authors).
            user_id=user_id if scope == "agent" else None,
        )
        turns: list[Turn] = []
        for r in rows:
            turn: Turn = {"role": r["role"], "content": r["content"]}
            if "agent_name" in r:
                turn["agent_name"] = r["agent_name"]
            if "message_kind" in r:
                turn["message_kind"] = r["message_kind"]
            turns.append(turn)
        return turns

    async def erase(
        self,
        db: AsyncSession,
        *,
        conversation_id: str | None = None,
        agent_name: str | None = None,
        user_id: str | None = None,
        deployment_id: str | None = None,
    ) -> int:
        if agent_name is None:
            raise ValueError("erase requires agent_name")
        if conversation_id is not None:
            return await memory.delete_thread(
                db,
                agent_name=agent_name,
                thread_id=conversation_id,
                deployment_id=deployment_id,
            )
        return await memory.clear_agent_memory(
            db,
            agent_name=agent_name,
            deployment_id=deployment_id,
        )

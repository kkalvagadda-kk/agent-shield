"""
Memory router — CRUD + semantic search for agent conversation memory.

Endpoints
---------
  POST   /api/v1/agents/{name}/memory                — save a turn
  GET    /api/v1/agents/{name}/memory                — list memory (paginated)
  POST   /api/v1/agents/{name}/memory/search         — semantic search
  DELETE /api/v1/agents/{name}/memory/{thread_id}    — delete thread (GDPR)
  DELETE /api/v1/agents/{name}/memory/clear           — wipe all memory
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Agent
from schemas import (
    AgentMemoryResponse,
    MemorySaveTurnRequest,
    MemorySearchRequest,
    MemorySearchResult,
)
import memory as memory_service
from store_factory import get_conversation_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["memory"])


async def _get_agent_or_404(name: str, db: AsyncSession) -> Agent:
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")
    return agent


@router.post(
    "/{name}/memory",
    response_model=list[AgentMemoryResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Save a conversation turn to memory",
)
async def save_turn(
    name: str,
    body: MemorySaveTurnRequest,
    db: AsyncSession = Depends(get_db),
) -> list[AgentMemoryResponse]:
    agent = await _get_agent_or_404(name, db)
    if not agent.memory_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Memory is not enabled for agent '{name}'.",
        )

    # All transcript access goes through the ConversationStore seam (§4.1) — the
    # router never touches the transcript ORM model directly. A workflow member
    # may write under its own name via author_agent_name; otherwise the path
    # {name} is the author.
    turns = [
        {
            "role": m.role,
            "content": m.content,
            # message_kind may be None → the store/save layer derives it from role.
            **({"message_kind": m.message_kind} if m.message_kind else {}),
        }
        for m in body.messages
    ]
    store = get_conversation_store()
    rows = await store.append(
        db,
        conversation_id=body.thread_id,
        agent_name=body.author_agent_name or name,
        team=agent.team,
        turns=turns,
        scope=body.scope,
        user_id=body.user_id,
        deployment_id=body.deployment_id,
        workflow_run_id=body.workflow_run_id,
    )
    await db.commit()
    return [AgentMemoryResponse.model_validate(r) for r in rows]


@router.get(
    "/{name}/memory",
    response_model=list[AgentMemoryResponse],
    summary="List memory messages",
)
async def list_memory(
    name: str,
    thread_id: Optional[str] = Query(None),
    scope: str = Query("agent"),
    user_id: Optional[str] = Query(None),
    deployment_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AgentMemoryResponse]:
    """Load a transcript through the ConversationStore, oldest-first by
    message_index. The read is conversation-keyed (thread_id):
      scope='agent'        → per-agent transcript, constrained by user_id when given.
      scope='workflow_run' → shared transcript, the agent_name filter is dropped so
                             every member's tagged rows come back in index order.
    """
    await _get_agent_or_404(name, db)
    if not thread_id:
        # The transcript store is conversation-keyed; a transcript read needs a
        # thread_id. (The legacy cross-thread "list all" view is not a store
        # operation — see the memory-api contract.)
        return []

    store = get_conversation_store()
    turns = await store.load(
        db,
        conversation_id=thread_id,
        scope=scope,
        limit=limit,
        agent_name=name,
        user_id=user_id,
        deployment_id=deployment_id,
    )
    if offset:
        turns = turns[offset:]

    # The store returns message-level Turns (role/content/+author/message_kind),
    # not row metadata; enrich with the router-known conversation keys so the
    # response carries thread_id/scope/agent_name. id/message_index/created_at are
    # row-level and absent on a transcript read (see AgentMemoryResponse).
    return [
        AgentMemoryResponse(
            id=None,
            agent_name=t.get("agent_name") or name,
            thread_id=thread_id,
            role=t["role"],
            content=t["content"],
            message_index=None,
            message_kind=t.get("message_kind")
            or ("user" if t["role"] == "user" else "agent_output"),
            scope=scope,
            created_at=None,
        )
        for t in turns
    ]


@router.post(
    "/{name}/memory/search",
    response_model=list[MemorySearchResult],
    summary="Semantic search over agent memory",
)
async def search_memory(
    name: str,
    body: MemorySearchRequest,
    db: AsyncSession = Depends(get_db),
) -> list[MemorySearchResult]:
    await _get_agent_or_404(name, db)

    query_embedding = [0.0] * 1536  # placeholder
    results = await memory_service.search_memory(
        db=db,
        agent_name=name,
        query_embedding=query_embedding,
        top_k=body.top_k,
        deployment_id=body.deployment_id,
    )
    return [MemorySearchResult(**r) for r in results]


# NOTE: /clear must be declared BEFORE /{thread_id} — FastAPI matches routes in
# definition order, so a static path segment ("clear") has to precede the
# path-parameter route or DELETE /memory/clear would bind thread_id="clear".
@router.delete(
    "/{name}/memory/clear",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Wipe all memory for an agent",
)
async def clear_memory(
    name: str,
    deployment_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_agent_or_404(name, db)
    store = get_conversation_store()
    await store.erase(db, agent_name=name, deployment_id=deployment_id)
    await db.commit()


@router.delete(
    "/{name}/memory/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete all memory for a thread (GDPR)",
)
async def delete_memory_thread(
    name: str,
    thread_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_agent_or_404(name, db)
    store = get_conversation_store()
    count = await store.erase(db, conversation_id=thread_id, agent_name=name)
    await db.commit()
    if count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No memory found for thread.")

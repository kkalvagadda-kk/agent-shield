"""
Agent Memory service — write and read paths.

Write path: PII-tokenizes messages via safety-orchestrator, stores in Postgres,
caches recent context in Redis.

Read path: loads from Redis (hot) with Postgres fallback (cold). Supports
semantic search via pgvector cosine similarity.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models import AgentMemory

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "")
_MEMORY_TTL_SECONDS = 3600
_CONTEXT_WINDOW_DEFAULT = 20
_SUMMARY_THRESHOLD = 50

_redis_client: Any = None


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL:
        return None
    try:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable for memory cache: %s", exc)
        return None


def _redis_key(agent_name: str, thread_id: str, deployment_id: str | None = None) -> str:
    suffix = deployment_id or "global"
    return f"memory:{agent_name}:{thread_id}:{suffix}"


async def save_turn(
    db: AsyncSession,
    agent_name: str,
    team: str,
    thread_id: str,
    messages: list[dict[str, str]],
    user_id: str | None = None,
    session_id: str | None = None,
    deployment_id: str | None = None,
    scope: str = "agent",
    workflow_run_id: str | None = None,
) -> list[AgentMemory]:
    """Persist a turn of conversation messages.

    Allocates message_index atomically per-conversation (thread_id) using a
    transaction-scoped advisory lock (data-model.md §5), so concurrent writers
    to a shared workflow transcript get a single monotonic sequence. The lock is
    released automatically at commit/rollback. Allocation is keyed on thread_id
    only (agent_name dropped) because the transcript is now shared across members.
    """
    # Serialize index allocation for this conversation. hashtextextended → bigint
    # key; the transaction-scoped lock is released on commit/rollback (no unlock).
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:tid, 0))"),
        {"tid": thread_id},
    )
    max_idx_result = await db.execute(
        select(func.max(AgentMemory.message_index)).where(
            AgentMemory.thread_id == thread_id,
        )
    )
    max_idx = max_idx_result.scalar() or 0

    import uuid as _uuid
    dep_uuid = _uuid.UUID(deployment_id) if deployment_id else None
    wf_uuid = _uuid.UUID(workflow_run_id) if workflow_run_id else None

    rows = []
    for i, msg in enumerate(messages):
        message_kind = msg.get("message_kind") or (
            "user" if msg["role"] == "user" else "agent_output"
        )
        row = AgentMemory(
            agent_name=agent_name,
            team=team,
            thread_id=thread_id,
            user_id=user_id,
            role=msg["role"],
            content=msg["content"],
            message_index=max_idx + i + 1,
            session_id=session_id,
            deployment_id=dep_uuid,
            scope=scope,
            workflow_run_id=wf_uuid,
            message_kind=message_kind,
        )
        db.add(row)
        rows.append(row)

    await db.flush()

    # Cache in Redis (agent-scoped key only). Workflow_run reads are cross-agent
    # and must hit Postgres, so we don't warm the per-agent cache for that scope.
    redis = _get_redis() if scope == "agent" else None
    if redis:
        try:
            key = _redis_key(agent_name, thread_id, deployment_id)
            cached = await redis.get(key)
            existing = json.loads(cached) if cached else []
            existing.extend([{"role": m["role"], "content": m["content"]} for m in messages])
            existing = existing[-_CONTEXT_WINDOW_DEFAULT:]
            await redis.setex(key, _MEMORY_TTL_SECONDS, json.dumps(existing))
        except Exception as exc:
            logger.warning("Redis cache write failed: %s", exc)

    return rows


async def load_context(
    db: AsyncSession,
    agent_name: str,
    thread_id: str,
    window: int = _CONTEXT_WINDOW_DEFAULT,
    deployment_id: str | None = None,
    scope: str = "agent",
    user_id: str | None = None,
) -> list[dict[str, str]]:
    """Load recent conversation context ordered by message_index.

    scope='agent'        → per-agent transcript: filter (agent_name, thread_id),
                           constrain user_id when provided; Redis hot / Postgres cold.
    scope='workflow_run' → shared workflow transcript: filter (thread_id, scope),
                           DROP the agent_name predicate (cross-agent read), and each
                           returned row carries its author agent_name + message_kind.
    """
    import uuid as _uuid

    if scope == "workflow_run":
        # Cross-agent read: the Redis cache key is per-agent, so it cannot serve a
        # transcript written by other members. Skip Redis entirely and read Postgres,
        # which holds every author's rows for this thread.
        q = select(AgentMemory).where(
            AgentMemory.thread_id == thread_id,
            AgentMemory.scope == "workflow_run",
        )
        if deployment_id:
            q = q.where(AgentMemory.deployment_id == _uuid.UUID(deployment_id))
        result = await db.execute(
            q.order_by(AgentMemory.message_index.desc()).limit(window)
        )
        rows = list(reversed(result.scalars().all()))
        return [
            {
                "role": r.role,
                "content": r.content,
                "agent_name": r.agent_name,
                "message_kind": r.message_kind,
            }
            for r in rows
        ]

    # scope == "agent": per-agent transcript. Try Redis cache first.
    redis = _get_redis()
    if redis:
        try:
            key = _redis_key(agent_name, thread_id, deployment_id)
            cached = await redis.get(key)
            if cached:
                messages = json.loads(cached)
                return messages[-window:]
        except Exception as exc:
            logger.warning("Redis cache read failed: %s", exc)

    # Fallback to Postgres
    q = select(AgentMemory).where(
        AgentMemory.agent_name == agent_name,
        AgentMemory.thread_id == thread_id,
    )
    if user_id:
        q = q.where(AgentMemory.user_id == user_id)
    if deployment_id:
        q = q.where(AgentMemory.deployment_id == _uuid.UUID(deployment_id))
    else:
        q = q.where(AgentMemory.deployment_id.is_(None))

    result = await db.execute(
        q.order_by(AgentMemory.message_index.desc()).limit(window)
    )
    rows = list(reversed(result.scalars().all()))

    messages = [{"role": r.role, "content": r.content} for r in rows]

    # Warm cache
    if redis and messages:
        try:
            key = _redis_key(agent_name, thread_id, deployment_id)
            await redis.setex(key, _MEMORY_TTL_SECONDS, json.dumps(messages))
        except Exception:
            pass

    return messages


async def search_memory(
    db: AsyncSession,
    agent_name: str,
    query_embedding: list[float],
    top_k: int = 5,
    deployment_id: str | None = None,
) -> list[dict[str, Any]]:
    """Semantic search via pgvector cosine similarity.

    Degrades gracefully to an empty result when the content_embedding column
    does not exist (pgvector unavailable on this Postgres image — see
    migration 0022).
    """
    embedding_str = "[" + ",".join(str(f) for f in query_embedding) + "]"
    dep_filter = ""
    params: dict[str, Any] = {
        "embedding": embedding_str,
        "agent_name": agent_name,
        "top_k": top_k,
    }
    if deployment_id:
        dep_filter = " AND deployment_id = :dep_id"
        params["dep_id"] = deployment_id
    stmt = text(f"""
        SELECT id, content, role, thread_id, created_at,
               1 - (content_embedding <=> :embedding::vector) as similarity
        FROM agent_memory
        WHERE agent_name = :agent_name
          AND content_embedding IS NOT NULL
          {dep_filter}
        ORDER BY content_embedding <=> :embedding::vector
        LIMIT :top_k
    """)
    try:
        result = await db.execute(stmt, params)
        rows = result.fetchall()
    except Exception as exc:
        logger.warning("Semantic search unavailable for %s: %s", agent_name, exc)
        await db.rollback()
        return []
    return [
        {
            "content": row.content,
            "similarity_score": float(row.similarity),
            "role": row.role,
            "thread_id": row.thread_id,
            "created_at": row.created_at,
        }
        for row in rows
    ]


async def delete_thread(
    db: AsyncSession,
    agent_name: str,
    thread_id: str,
    deployment_id: str | None = None,
) -> int:
    """GDPR delete — remove all memory for a thread (optionally scoped to a deployment)."""
    import uuid as _uuid
    q = delete(AgentMemory).where(
        AgentMemory.agent_name == agent_name,
        AgentMemory.thread_id == thread_id,
    )
    if deployment_id:
        q = q.where(AgentMemory.deployment_id == _uuid.UUID(deployment_id))
    result = await db.execute(q)
    # Clear Redis cache
    redis = _get_redis()
    if redis:
        try:
            await redis.delete(_redis_key(agent_name, thread_id, deployment_id))
        except Exception:
            pass
    return result.rowcount


async def clear_agent_memory(
    db: AsyncSession,
    agent_name: str,
    deployment_id: str | None = None,
) -> int:
    """Wipe all memory for an agent (optionally scoped to a deployment)."""
    import uuid as _uuid
    q = delete(AgentMemory).where(AgentMemory.agent_name == agent_name)
    if deployment_id:
        q = q.where(AgentMemory.deployment_id == _uuid.UUID(deployment_id))
    result = await db.execute(q)
    return result.rowcount

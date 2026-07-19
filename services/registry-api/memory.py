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

from models import AgentMemory, AgentRun

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
    # v2: cached entries now carry row metadata (message_index/created_at). The
    # version prefix invalidates pre-fix entries that held only {role, content},
    # so a cache hit can't serve a metadata-less transcript to the read API.
    return f"memory:v2:{agent_name}:{thread_id}:{suffix}"


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
                # Row-level metadata the transcript API (AgentMemoryResponse) needs.
                # The runner's context-injection read ignores these extra keys.
                "message_index": r.message_index,
                "created_at": r.created_at.isoformat() if r.created_at else None,
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
    # When deployment_id is absent, add NO deployment predicate. thread_id (session)
    # + user_id already scope the read. The pre-POC default forced deployment_id
    # IS NULL — correct only while writes were untagged; once POC-0 began tagging
    # every write with a real deployment_id, that default excluded every row an
    # external caller (Studio MemoryTab, e2e suite) could read. The read must
    # reconcile with the write's scoping, not assume legacy NULL rows.

    result = await db.execute(
        q.order_by(AgentMemory.message_index.desc()).limit(window)
    )
    rows = list(reversed(result.scalars().all()))

    messages = [
        {
            "role": r.role,
            "content": r.content,
            "message_index": r.message_index,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]

    # Warm cache
    if redis and messages:
        try:
            key = _redis_key(agent_name, thread_id, deployment_id)
            await redis.setex(key, _MEMORY_TTL_SECONDS, json.dumps(messages))
        except Exception:
            pass

    return messages


async def list_recent(
    db: AsyncSession,
    *,
    agent_name: str,
    deployment_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    """Cross-thread recent rows for an agent, newest-first (by created_at).

    Restores the pre-POC transcript-list contract that the Memory tab relies on
    to enumerate an agent's conversations: `thread_id` is an OPTIONAL filter, not a
    requirement. A per-thread transcript read is `load_context` (conversation-keyed,
    ordered by message_index); this is the "which conversations exist" read, ordered
    by wall-clock across threads. Returns full ORM rows so the router can serialize
    row metadata (id/message_index/created_at) — no lossy Turn projection.

    Scoping mirrors load_context: filter by deployment_id / user_id ONLY when given
    (never force IS NULL). Cross-user visibility for the agent-owner view matches the
    prior behavior; per-user privacy scoping is a Tighten concern (S9)."""
    import uuid as _uuid

    q = select(AgentMemory).where(AgentMemory.agent_name == agent_name)
    if user_id:
        q = q.where(AgentMemory.user_id == user_id)
    if deployment_id:
        q = q.where(AgentMemory.deployment_id == _uuid.UUID(deployment_id))
    q = q.order_by(AgentMemory.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return list(result.scalars().all())


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


# ---------------------------------------------------------------------------
# POC-5 — conversation list (read-only aggregate; no new storage / migration)
# ---------------------------------------------------------------------------
_LIST_CONVERSATIONS_SQL = text("""
SELECT
  am.thread_id                                              AS thread_id,
  min(am.session_id)                                        AS session_id,
  min(am.agent_name)                                        AS agent_name,
  (array_agg(am.content ORDER BY am.message_index)
     FILTER (WHERE am.role = 'user'))[1]                    AS title,
  count(*)                                                  AS message_count,
  max(am.created_at)                                        AS last_activity,
  -- deployment_id is a UUID; Postgres has no min(uuid) aggregate, so pick the
  -- thread's first-seen non-null deployment_id deterministically by message order.
  (array_agg(am.deployment_id ORDER BY am.message_index)
     FILTER (WHERE am.deployment_id IS NOT NULL))[1]::text  AS deployment_id,
  CASE WHEN bool_or(pd.id IS NOT NULL)
       THEN 'production' ELSE 'sandbox' END                 AS environment
FROM agent_memory am
LEFT JOIN production_deployments pd ON am.deployment_id = pd.id
WHERE am.user_id = :user_id
  AND (CAST(:agent_name AS text)    IS NULL OR am.agent_name    = :agent_name)
  AND (CAST(:deployment_id AS uuid) IS NULL OR am.deployment_id = CAST(:deployment_id AS uuid))
GROUP BY am.thread_id
ORDER BY max(am.created_at) DESC
LIMIT :limit OFFSET :offset
""")


# A WORKFLOW's transcript rows are authored by its MEMBERS (agent_name = the
# member, user_id = NULL), so the per-agent list above never matches a workflow.
# The workflow's identity + ownership live on its PARENT run instead
# (agent_runs.workflow_id / .user_id / .session_id == the transcript thread_id).
# So we scope the transcript threads via a semi-join to the workflow's parent runs
# (DISTINCT session_id — avoids fan-out when a session recurs across runs) and take
# the display name from the workflow, not the member rows. Ownership comes from the
# parent run's user_id (the member rows' user_id is NULL by construction).
_LIST_WORKFLOW_CONVERSATIONS_SQL = text("""
SELECT
  am.thread_id                                              AS thread_id,
  min(am.session_id)                                        AS session_id,
  CAST(:workflow_name AS text)                              AS agent_name,
  (array_agg(am.content ORDER BY am.message_index)
     FILTER (WHERE am.role = 'user'))[1]                    AS title,
  count(*)                                                  AS message_count,
  max(am.created_at)                                        AS last_activity,
  (array_agg(am.deployment_id ORDER BY am.message_index)
     FILTER (WHERE am.deployment_id IS NOT NULL))[1]::text  AS deployment_id,
  CASE WHEN bool_or(pd.id IS NOT NULL)
       THEN 'production' ELSE 'sandbox' END                 AS environment
FROM agent_memory am
LEFT JOIN production_deployments pd ON am.deployment_id = pd.id
WHERE am.scope = 'workflow_run'
  AND am.thread_id IN (
    SELECT DISTINCT run.session_id
    FROM agent_runs run
    WHERE run.workflow_id = CAST(:workflow_id AS uuid)
      AND run.parent_run_id IS NULL
      AND run.user_id = :user_id
      AND run.session_id IS NOT NULL
  )
GROUP BY am.thread_id
ORDER BY max(am.created_at) DESC
LIMIT :limit OFFSET :offset
""")


async def list_workflow_conversations(
    db: AsyncSession,
    *,
    workflow_id: str,
    workflow_name: str,
    user_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Per-thread conversation summaries for a WORKFLOW, newest-first.

    Same shape as :func:`list_conversations` (ConversationSummary), but the thread
    set is scoped through the workflow's parent runs (workflow_id + owner) instead
    of a member agent_name, and ownership comes from the parent run's user_id (the
    workflow_run transcript rows carry a NULL user_id). Returns [] for a workflow
    the caller has never run.
    """
    result = await db.execute(
        _LIST_WORKFLOW_CONVERSATIONS_SQL,
        {
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "user_id": user_id,
            "limit": limit,
            "offset": offset,
        },
    )
    return [dict(row) for row in result.mappings().all()]


async def list_workflow_memory(
    db: AsyncSession,
    *,
    workflow_id: str,
    user_id: str,
    thread_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[AgentMemory]:
    """Workflow memory ENTRIES scoped through the workflow's parent runs.

    Mirrors :func:`list_workflow_conversations`' ownership semi-join (the thread set
    is the workflow's parent runs' session_ids for this owner — the workflow_run
    transcript rows carry ``user_id = NULL``), but returns individual ``AgentMemory``
    rows (entries), not per-thread summaries. Two orderings, matching the per-agent
    ``GET /agents/{name}/memory`` dual behavior:

      thread_id given  → one thread's transcript, ORDER BY message_index ASC (replay → G1)
      thread_id absent → recent entries across the workflow's threads, created_at DESC (tab → G2)

    Returns ``[]`` for a workflow the caller has never run. No ``workflow_name`` needed —
    entries keep their author ``agent_name`` (the member), which the tab/replay want to show.
    """
    import uuid as _uuid

    q = select(AgentMemory).where(
        AgentMemory.scope == "workflow_run",
        AgentMemory.thread_id.in_(
            select(AgentRun.session_id)
            .where(
                AgentRun.workflow_id == _uuid.UUID(workflow_id),
                AgentRun.parent_run_id.is_(None),
                AgentRun.user_id == user_id,
                AgentRun.session_id.isnot(None),
            )
            .distinct()
        ),
    )
    if thread_id:
        q = q.where(AgentMemory.thread_id == thread_id).order_by(AgentMemory.message_index.asc())
    else:
        q = q.order_by(AgentMemory.created_at.desc())
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return list(result.scalars().all())


async def list_conversations(
    db: AsyncSession,
    *,
    user_id: str,
    agent_name: str | None = None,
    deployment_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Per-thread conversation summaries for the caller, newest-first.

    Aggregates ``agent_memory`` grouped by ``thread_id``: title = first user
    message, message_count, last_activity, session_id, agent_name, and a DERIVED
    ``environment`` (production iff the thread's deployment_id is in
    production_deployments, else sandbox — there is NO environment column).
    Ownership-scoped: only the caller's own rows (``user_id = :user_id``); rows
    with a NULL user_id (daemon/legacy) are never listed. Optional agent_name /
    deployment_id narrow the scope (the deployment_id filter backs the docked
    History panel + the deployment Overview conversations tab).
    """
    result = await db.execute(
        _LIST_CONVERSATIONS_SQL,
        {
            "user_id": user_id,
            "agent_name": agent_name,
            "deployment_id": deployment_id,
            "limit": limit,
            "offset": offset,
        },
    )
    return [dict(row) for row in result.mappings().all()]

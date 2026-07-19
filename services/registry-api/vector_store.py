"""VectorStore port + pgvector adapter (POC-4) — the S5 tenant-isolation enforcement point.

Mirrors the POC-0 ConversationStore seam: callers depend on the `VectorStore`
Protocol, `store_factory.get_vector_store()` is the only construction choke point.

S5 (tenant isolation) is structural here, not a runtime check bolted on:
  * `team` and `kb_id` are REQUIRED keyword args on both `index` and `search`.
  * `search` puts `WHERE team = :team AND kb_id = :kb_id` into EVERY query — there
    is no overload that omits them and no "search all" path.
  * an empty/None `team` or `kb_id` raises `ValueError` BEFORE any SQL is built
    (fail-closed — never a broad query).

So `search(team="A", kb_id=<B's kb>)` returns `[]`: no chunk has both team='A'
AND that kb_id.

The pgvector `vector(384)` `embedding` column is written/read only through raw
`text()` SQL here (mirrors memory.search_memory) — it is deliberately NOT on the
KnowledgeChunk ORM mapper. When pgvector is absent (the embedding column is
missing), `search` catches the error, rolls back, and degrades to a keyword scan
that is STILL team+kb scoped (S5 holds in the fallback too), returning hits with
score 0.0. On EKS pgvector is present, so the fallback is dev-only.
"""
from __future__ import annotations

import logging
from typing import Protocol, TypedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from embedding_client import EMBEDDING_DIM

logger = logging.getLogger(__name__)


class ChunkToIndex(TypedDict):
    source_id: str
    chunk_index: int
    content: str
    embedding: list[float]  # len == EMBEDDING_DIM


class SearchHit(TypedDict):
    chunk_id: str
    source_id: str
    content: str
    score: float  # cosine similarity in [0,1]; 0.0 in the keyword fallback


class VectorStore(Protocol):
    async def index(
        self,
        db: AsyncSession,
        *,
        team: str,
        kb_id: str,
        chunks: list[ChunkToIndex],
    ) -> int:
        """Insert chunk rows (content + embedding) for (team, kb_id). Returns rows
        written. `team`/`kb_id` are stamped onto every row — never taken from a
        chunk dict."""
        ...

    async def search(
        self,
        db: AsyncSession,
        *,
        team: str,
        kb_id: str,
        query_embedding: list[float],
        k: int = 5,
        query_text: str | None = None,
    ) -> list[SearchHit]:
        """Top-k chunks by cosine similarity, SCOPED to (team, kb_id). `team` and
        `kb_id` are REQUIRED keyword args — there is no overload that omits them and
        no 'search all' path. Fail-closed: an empty/None team or kb_id raises
        ValueError (never a broad query). `query_text` is optional and used only by
        the keyword fallback when pgvector is absent."""
        ...


def _format_vector(embedding: list[float]) -> str:
    """Format a float list as a pgvector literal '[f,f,…]' (same as
    memory.search_memory)."""
    return "[" + ",".join(str(f) for f in embedding) + "]"


class PgVectorStore:
    """Default adapter — stateless; the request-scoped AsyncSession flows through
    each call (like PostgresConversationStore), so caching the instance is fine."""

    async def index(
        self,
        db: AsyncSession,
        *,
        team: str,
        kb_id: str,
        chunks: list[ChunkToIndex],
    ) -> int:
        # Fail-closed: never write chunks without a tenant scope.
        if not team or not kb_id:
            raise ValueError("index requires non-empty team and kb_id (fail-closed)")

        stmt = text(
            """
            INSERT INTO knowledge_chunks
                (id, kb_id, team, source_id, chunk_index, content, embedding)
            VALUES
                (gen_random_uuid(), :kb_id, :team, :source_id, :chunk_index,
                 :content, CAST(:embedding AS vector))
            """
        )
        written = 0
        for c in chunks:
            embedding = c["embedding"]
            if len(embedding) != EMBEDDING_DIM:
                raise ValueError(
                    f"embedding dim {len(embedding)} != EMBEDDING_DIM {EMBEDDING_DIM}"
                )
            await db.execute(
                stmt,
                {
                    "kb_id": kb_id,
                    "team": team,
                    "source_id": c["source_id"],
                    "chunk_index": c["chunk_index"],
                    "content": c["content"],
                    "embedding": _format_vector(embedding),
                },
            )
            written += 1
        return written

    async def search(
        self,
        db: AsyncSession,
        *,
        team: str,
        kb_id: str,
        query_embedding: list[float],
        k: int = 5,
        query_text: str | None = None,
    ) -> list[SearchHit]:
        # Fail-closed BEFORE any SQL — an empty/None scope is never a broad query.
        if not team or not kb_id:
            raise ValueError("search requires non-empty team and kb_id (fail-closed)")
        if len(query_embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"query embedding dim {len(query_embedding)} != "
                f"EMBEDDING_DIM {EMBEDDING_DIM}"
            )

        q = _format_vector(query_embedding)
        stmt = text(
            """
            SELECT id, source_id, content,
                   1 - (embedding <=> CAST(:q AS vector)) AS score
            FROM knowledge_chunks
            WHERE team = :team AND kb_id = :kb_id      -- MANDATORY predicate (S5)
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :k
            """
        )
        try:
            result = await db.execute(
                stmt, {"q": q, "team": team, "kb_id": kb_id, "k": k}
            )
            rows = result.fetchall()
        except Exception as exc:
            # pgvector absent → the embedding column doesn't exist. The aborted tx
            # must be rolled back before the fallback query, which stays team+kb
            # scoped (S5 holds).
            logger.warning(
                "Vector search unavailable (team=%s kb=%s): %s — keyword fallback",
                team,
                kb_id,
                exc,
            )
            await db.rollback()
            return await self._keyword_search(
                db, team=team, kb_id=kb_id, k=k, query_text=query_text
            )

        return [
            {
                "chunk_id": str(row.id),
                "source_id": str(row.source_id),
                "content": row.content,
                "score": float(row.score),
            }
            for row in rows
        ]

    async def _keyword_search(
        self,
        db: AsyncSession,
        *,
        team: str,
        kb_id: str,
        k: int,
        query_text: str | None,
    ) -> list[SearchHit]:
        """Degraded keyword scan for pgvector-absent dev DBs. STILL scoped by
        `team AND kb_id` (S5 holds); score is 0.0. When a raw query string is
        available it filters by ILIKE, otherwise it returns the first-k scoped
        chunks so retrieval degrades rather than 500s."""
        if query_text:
            stmt = text(
                """
                SELECT id, source_id, content
                FROM knowledge_chunks
                WHERE team = :team AND kb_id = :kb_id
                  AND content ILIKE :pattern
                ORDER BY chunk_index
                LIMIT :k
                """
            )
            params = {
                "team": team,
                "kb_id": kb_id,
                "pattern": f"%{query_text}%",
                "k": k,
            }
        else:
            stmt = text(
                """
                SELECT id, source_id, content
                FROM knowledge_chunks
                WHERE team = :team AND kb_id = :kb_id
                ORDER BY chunk_index
                LIMIT :k
                """
            )
            params = {"team": team, "kb_id": kb_id, "k": k}
        result = await db.execute(stmt, params)
        rows = result.fetchall()
        return [
            {
                "chunk_id": str(row.id),
                "source_id": str(row.source_id),
                "content": row.content,
                "score": 0.0,
            }
            for row in rows
        ]

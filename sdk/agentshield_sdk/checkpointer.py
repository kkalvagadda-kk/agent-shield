"""
LangGraph checkpointer factory.

Returns an AsyncPostgresSaver when DIRECT_DATABASE_URL is set (cluster
deployment), or a MemorySaver for local dev.

DIRECT_DATABASE_URL should point directly to the Postgres primary with
asyncpg (bypasses PgBouncer, which does not support LISTEN/NOTIFY needed
by LangGraph's HITL resume flow).

Example DIRECT_DATABASE_URL:
    postgresql+asyncpg://agentuser:pass@postgres-primary:5432/agentshield
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Module-global: keep the connection pool open for the pod's lifetime so the
# AsyncPostgresSaver stays live (from_conn_string is an @asynccontextmanager and
# would close the saver on exit — see research.md §5).
_pool = None


async def get_checkpointer():
    """Return the appropriate LangGraph checkpointer based on environment.

    Returns a process-lifetime `AsyncPostgresSaver` over an explicitly-opened
    module-global pool when DIRECT_DATABASE_URL is set.

    Fail-loud: MemorySaver is returned ONLY when DIRECT_DATABASE_URL is unset
    (local dev). When the URL IS set but construction fails, we log the error and
    raise RuntimeError — never a silent MemorySaver fallback, which would pin
    tenant state in pod RAM and break cross-replica HITL resume.
    """
    url = os.getenv("DIRECT_DATABASE_URL", "")

    if not url:
        logger.info(
            "DIRECT_DATABASE_URL not set — using in-memory checkpointer (local dev)"
        )
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]

        return MemorySaver()

    global _pool
    try:
        from psycopg_pool import AsyncConnectionPool  # type: ignore[import]
        from langgraph.checkpoint.postgres.aio import (  # type: ignore[import]
            AsyncPostgresSaver,
        )

        # psycopg wants a plain URL (no SQLAlchemy +asyncpg driver suffix); the
        # readiness probe in the runner strips it the same way.
        conninfo = url.replace("+asyncpg", "")
        _pool = AsyncConnectionPool(
            conninfo=conninfo,
            max_size=10,
            open=False,  # avoid opening the async pool in the constructor
            # Liveness-check every connection before handing it out, and recycle
            # idle ones, so a connection that Postgres (or the mesh) closed while
            # the pod sat idle is transparently replaced. Without this the pool
            # returns a dead [BAD] connection and the next checkpointer op fails
            # with "server closed the connection unexpectedly" — e.g. a workflow
            # member run 500s after an idle period even though Postgres is healthy.
            check=AsyncConnectionPool.check_connection,
            max_idle=120.0,
            max_lifetime=1800.0,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        await _pool.open()
        saver = AsyncPostgresSaver(_pool)
        await saver.setup()
        logger.info("AsyncPostgresSaver ready (pool-backed, pod-lifetime)")
        return saver
    except Exception as exc:
        logger.error(
            "AsyncPostgresSaver init FAILED with DIRECT_DATABASE_URL set: %s", exc
        )
        raise RuntimeError(
            f"checkpointer init failed (fail-loud, no MemorySaver fallback): {exc}"
        ) from exc

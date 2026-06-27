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


async def get_checkpointer():
    """Return the appropriate LangGraph checkpointer based on environment.

    Returns:
        AsyncPostgresSaver if DIRECT_DATABASE_URL is set, MemorySaver otherwise.
    """
    url = os.getenv("DIRECT_DATABASE_URL", "")

    if not url:
        logger.info("DIRECT_DATABASE_URL not set — using in-memory checkpointer")
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]

        return MemorySaver()

    logger.info("Initialising AsyncPostgresSaver for LangGraph checkpointing")
    try:
        from langgraph.checkpoint.postgres.aio import (  # type: ignore[import]
            AsyncPostgresSaver,
        )

        checkpointer = AsyncPostgresSaver.from_conn_string(url)
        await checkpointer.setup()
        return checkpointer
    except Exception as exc:
        logger.error(
            "Failed to initialise AsyncPostgresSaver (%s) — falling back to MemorySaver",
            exc,
        )
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]

        return MemorySaver()

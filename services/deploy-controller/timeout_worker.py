"""
AgentShield Deploy Controller — Timeout Worker.

Background asyncio task that periodically sweeps the approvals table for
pending rows whose expires_at has passed, marks them as 'timed_out', and
issues a Postgres NOTIFY so any waiting agent thread is woken up immediately.

Connection model: one persistent asyncpg connection, with a reconnect loop
that waits 5 s and re-connects if the connection drops.
"""

import asyncio
import logging
import os

import asyncpg

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 30


async def timeout_worker() -> None:
    """Background task: marks expired pending approvals as 'timed_out' and NOTIFYs waiters."""
    # SQLAlchemy uses the postgresql+asyncpg:// scheme; asyncpg.connect needs postgresql://
    database_url = os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    while True:  # outer reconnect loop
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(database_url)
            logger.info("timeout_worker: connected to database")

            while True:  # inner poll loop
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                try:
                    rows = await conn.fetch(
                        "SELECT id FROM approvals"
                        " WHERE status = 'pending' AND expires_at < now()"
                    )
                    for row in rows:
                        approval_id = str(row["id"])
                        await conn.execute(
                            "UPDATE approvals SET status = 'timed_out' WHERE id = $1",
                            row["id"],
                        )
                        await conn.execute(f"NOTIFY approvals, '{approval_id}'")
                        logger.info(
                            "timeout_worker: expired approval %s → timed_out",
                            approval_id,
                        )
                except asyncio.CancelledError:
                    raise
                except asyncpg.InterfaceError as exc:
                    # Connection was dropped — break inner loop to trigger reconnect
                    logger.error("timeout_worker: connection lost: %s", exc)
                    break
                except Exception as exc:  # noqa: BLE001
                    # Query/data error — log and keep polling
                    logger.error("timeout_worker: error processing timeouts: %s", exc)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "timeout_worker: connection error: %s — reconnecting in 5 s", exc
            )
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001
                    pass

        # Reached when inner loop breaks (connection lost) or outer except fires.
        # CancelledError bypasses this via the re-raise above.
        await asyncio.sleep(5)

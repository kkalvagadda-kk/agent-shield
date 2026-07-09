"""
AgentShield Deploy Controller — Sandbox TTL Worker.

Background asyncio task that sweeps deployments (agent + workflow) whose
ttl_hours has expired and transitions them from 'running' → 'terminating'.
The main reconciler loop (_handle_lifecycle_transitions) handles the actual
K8s teardown once the status is 'terminating'.
"""

import asyncio
import logging
import os

import asyncpg

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 60


async def ttl_worker() -> None:
    """Flip expired sandbox deployments from running → terminating."""
    database_url = os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    while True:
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(database_url)
            logger.info("ttl_worker: connected to database")

            while True:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                try:
                    # Agent sandbox deployments
                    agent_rows = await conn.fetch("""
                        UPDATE deployments
                        SET status = 'terminating'
                        WHERE status = 'running'
                          AND ttl_hours IS NOT NULL
                          AND deployed_at + (ttl_hours * interval '1 hour') < now()
                        RETURNING id
                    """)
                    for row in agent_rows:
                        logger.info("ttl_worker: agent deployment %s → terminating (TTL expired)", row["id"])

                    # Workflow deployments
                    wf_rows = await conn.fetch("""
                        UPDATE workflow_deployments
                        SET status = 'terminating'
                        WHERE status = 'running'
                          AND ttl_hours IS NOT NULL
                          AND deployed_at + (ttl_hours * interval '1 hour') < now()
                        RETURNING id
                    """)
                    for row in wf_rows:
                        logger.info("ttl_worker: workflow deployment %s → terminating (TTL expired)", row["id"])

                except asyncio.CancelledError:
                    raise
                except asyncpg.InterfaceError as exc:
                    logger.error("ttl_worker: connection lost: %s", exc)
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.error("ttl_worker: error: %s", exc)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("ttl_worker: connection error: %s — reconnecting in 5s", exc)
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.sleep(5)

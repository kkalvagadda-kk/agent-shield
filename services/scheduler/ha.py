"""
HA coordination for the scheduler via PostgreSQL advisory locks.

With 2+ scheduler replicas, every replica's APScheduler fires the same cron job
at (approximately) the same instant. To dispatch exactly once, each replica
tries to acquire a transaction-scoped advisory lock keyed on
(trigger_id, fire_minute_epoch) before dispatching. Only the replica that wins
the lock dispatches; the lock auto-releases at transaction end, and the key is
unique per fire so a later fire is never blocked by an earlier one.
"""
from __future__ import annotations

import logging
import zlib

logger = logging.getLogger(__name__)


def _lock_key(trigger_id: str, fire_epoch: int) -> int:
    """Deterministic signed 64-bit key from trigger_id + fire time."""
    raw = f"{trigger_id}:{fire_epoch}".encode()
    # 63-bit positive value fits a Postgres bigint advisory-lock key.
    return zlib.crc32(raw) & 0x7FFFFFFF


def try_claim_fire(conn, trigger_id: str, fire_epoch: int) -> bool:
    """Return True iff this replica won the right to dispatch this fire.

    Uses a session-level advisory lock that we deliberately never release —
    the key is unique per (trigger, fire minute), so holding it for the process
    lifetime is fine and guarantees single dispatch across replicas.
    """
    key = _lock_key(trigger_id, fire_epoch)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        won = bool(cur.fetchone()[0])
    conn.commit()
    if won:
        logger.info("claimed fire trigger=%s epoch=%d (key=%d)", trigger_id, fire_epoch, key)
    else:
        logger.debug("lost fire race trigger=%s epoch=%d", trigger_id, fire_epoch)
    return won

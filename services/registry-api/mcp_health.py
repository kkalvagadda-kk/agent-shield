"""
MCP health-check sweep (Phase 2, WS-A / FR-MCP-22) — keeps every registered MCP
server's reachability fresh and visible.

Phase 1 flipped ``mcp_servers.status`` between ``connected`` and ``error`` only on
an explicit ``/sync`` or a live tool-call failure, so a server that died BETWEEN
syncs kept a stale ``connected``. This periodic loop closes that gap: every
``mcp_health_check_interval_seconds`` it probes each server via the MCP Proxy's
``POST /internal/health`` (a lightweight ``tools/list`` — NO discovery, NO
``Tool``-row write) and folds the verdict into ``status`` / ``health_detail``.

Ownership (invariant): the loop runs INSIDE registry-api, which owns the DB, and
writes ``mcp_servers`` directly through its own ORM session. The proxy is only an
HTTP probe — it never touches the DB. There is deliberately no health-writeback
endpoint (contracts/registry-api-internal-mcp-phase2.md).

Single-flight (invariant): with N registry-api replicas, every replica runs this
loop, but the ``health_detail.consecutive_failures`` read-modify-write must happen
on exactly ONE replica per sweep — otherwise two replicas would both increment and
mis-flip status. A Postgres advisory lock (``pg_try_advisory_lock``, mirroring
``services/scheduler/ha.py``) elects the sweep owner; a non-owner returns 0 without
probing or writing.

``last_synced_at`` is NEVER written here — that column means "last DISCOVERY", and
a health probe does no discovery (data-model.md §1/§2a, research.md C3). Health
writes ``health_detail.last_success_at`` instead.
"""
from __future__ import annotations

import asyncio
import logging
import zlib
from datetime import datetime, timezone

from config import settings

logger = logging.getLogger(__name__)

# 63-bit positive advisory-lock key for the health sweep. The scheduler
# (services/scheduler/ha.py) takes single-bigint session locks keyed as
# ``crc32(...) & 0x7FFFFFFF`` — i.e. 31-bit values in [0, 2**31). We seed from the
# same crc32 idiom but set bit 62, so this key is always > 2**31 and can NEVER
# collide with any scheduler fire lock, while staying positive for a signed
# Postgres bigint (< 2**63). Value: 4611686020559757442.
_SWEEP_LOCK_KEY = (zlib.crc32(b"mcp-health-sweep") & 0x7FFFFFFF) | (1 << 62)

# Per-server exponential-backoff state: server_id (str) -> remaining sweeps to skip.
# In-memory only (lost on registry-api restart → at worst one extra probe of a
# hard-down server after a restart; not worth a DB column — data-model.md gap
# ledger). Keeps a hard-down server from being probed every single sweep.
_backoff_skip: dict[str, int] = {}


def reset_backoff(server_id: str) -> None:
    """Clear a server's exponential probe-skip so the next sweep probes it again.

    Called when an operator explicitly re-checks a server (POST /mcp-servers/{id}/sync
    = "check this now") or fixes its config: a fixed server must recover promptly, not
    wait out the accumulated backoff (up to mcp_health_max_backoff_cycles sweeps). No-op
    if the server has no pending skip. Best-effort, in-memory — mirrors the per-replica
    caveat of _backoff_skip itself (a sync served by replica A clears A's skip; replica
    B re-probes on its own schedule).
    """
    _backoff_skip.pop(server_id, None)


async def _probe_and_apply(session, server) -> bool:
    """Probe one server and fold the verdict into its ``status`` / ``health_detail``.

    Applies the data-model.md §2a threshold + backoff state machine. Returns True
    iff ``status`` or ``health_detail`` changed. Never writes ``last_synced_at``.
    """
    from mcp_proxy_client import health_check_server
    from models import MCPOAuthGrant
    from sqlalchemy import select

    key = str(server.id)
    prev = dict(server.health_detail or {})
    prev_status = server.status
    prev_failures = int(prev.get("consecutive_failures") or 0)
    # Health NEVER edits schema_drift (that is discovery's concern) — preserve it.
    schema_drift = list(prev.get("schema_drift") or [])
    threshold = settings.mcp_health_failure_threshold

    # Phase 4 WS-2 (C9): an OAuth external server can only be probed AS an authorized
    # user — the proxy needs a per-user token to reach it. Probe AS the most-recently-
    # authorized user (updated_at desc). With NO authorized grant, an OAuth server is
    # simply "not yet connected", NOT an error: reflect a needs_auth-style state WITHOUT
    # hitting the proxy (an unauthenticated OAuth probe would only fail closed) and
    # without incrementing failures / triggering backoff. The static/internal path
    # (external_auth_mode != 'oauth') is unchanged: probe_user_sub stays None.
    probe_user_sub: str | None = None
    if server.external_auth_mode == "oauth":
        probe_user_sub = (
            await session.execute(
                select(MCPOAuthGrant.user_sub)
                .where(
                    MCPOAuthGrant.server_id == server.id,
                    MCPOAuthGrant.status == "authorized",
                )
                .order_by(MCPOAuthGrant.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if probe_user_sub is None:
            new_status = "disconnected"
            new_hd = {
                "last_error": None,
                "last_success_at": prev.get("last_success_at"),
                "consecutive_failures": 0,
                "schema_drift": schema_drift,
                "oauth": "needs_auth",
            }
            _backoff_skip.pop(key, None)  # not a failure — no backoff to keep
            server.status = new_status
            server.health_detail = new_hd
            return new_status != prev_status or new_hd != prev

    # A RuntimeError (transport / non-200 from the proxy) is a failed probe, not an
    # error to surface — treat it identically to a 200 ok=false body.
    ok = False
    reason: str | None = None
    try:
        resp = await health_check_server(server.id, user_sub=probe_user_sub)
        ok = bool(resp.get("ok"))
        if not ok:
            reason = resp.get("health_detail") or "health probe failed (no detail)"
    except RuntimeError as exc:
        ok = False
        reason = f"proxy unreachable: {exc}"

    if ok:
        # Success → connected, reset failures, refresh last_success_at. A single good
        # probe recovers a server that was in 'error'.
        new_status = "connected"
        new_hd = {
            "last_error": None,
            "last_success_at": datetime.now(timezone.utc).isoformat(),
            "consecutive_failures": 0,
            "schema_drift": schema_drift,
        }
        _backoff_skip.pop(key, None)  # recovered — clear any pending backoff
    else:
        new_failures = prev_failures + 1
        # Flip to 'error' only after >= threshold consecutive failures; below the
        # threshold the status is left unchanged (a transient blip stays 'connected').
        new_status = "error" if new_failures >= threshold else prev_status
        new_hd = {
            "last_error": reason,
            # last_success_at is a SUCCESS timestamp — untouched on failure.
            "last_success_at": prev.get("last_success_at"),
            "consecutive_failures": new_failures,
            "schema_drift": schema_drift,
        }
        # Sustained failure (now at/over threshold → status is 'error'): back off so a
        # hard-down server is not probed every sweep. Exponential in the number of
        # failures beyond the threshold, capped at mcp_health_max_backoff_cycles.
        if new_failures >= threshold:
            exp = min(new_failures - threshold, 30)  # guard against an absurd shift
            _backoff_skip[key] = min(
                1 << exp, settings.mcp_health_max_backoff_cycles
            )

    server.status = new_status
    server.health_detail = new_hd
    # NOTE: deliberately does NOT set server.last_synced_at — a health probe is not a
    # discovery (data-model.md §1/§2a, research.md C3).
    return new_status != prev_status or new_hd != prev


async def _sweep_once() -> int:
    """Run one health sweep under an advisory-lock single-flight.

    Acquires ``pg_try_advisory_lock(_SWEEP_LOCK_KEY)`` on a DEDICATED connection; if
    not won, another replica owns this sweep → return 0 without probing or writing.
    If won: enumerate every ``MCPServer``, skip any in exponential backoff, probe the
    rest with bounded concurrency, apply the threshold/backoff state machine, commit,
    and ALWAYS release the lock in a finally. Returns the count of servers whose
    status/health_detail changed.
    """
    from db import AsyncSessionLocal, engine
    from models import MCPServer
    from sqlalchemy import select, text

    # A dedicated raw connection for the lock — separate from the ORM session that
    # does the reads/writes. The advisory lock is session-scoped, so acquire + unlock
    # must ride the SAME connection; holding one open transaction on it pins the
    # PgBouncer server backend for the whole sweep so the lock semantics hold through
    # transaction pooling.
    async with engine.connect() as lock_conn:
        acquired = (
            await lock_conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": _SWEEP_LOCK_KEY}
            )
        ).scalar()
        if not acquired:
            # Another replica owns this sweep.
            await lock_conn.rollback()
            return 0

        try:
            changed = 0
            async with AsyncSessionLocal() as session:
                servers = (
                    await session.execute(select(MCPServer))
                ).scalars().all()

                # Decrement backoff serially (before dispatching probes) and collect
                # the servers that are due this sweep.
                to_probe = []
                for server in servers:
                    skip = _backoff_skip.get(str(server.id), 0)
                    if skip > 0:
                        _backoff_skip[str(server.id)] = skip - 1
                        continue
                    to_probe.append(server)

                if to_probe:
                    sem = asyncio.Semaphore(settings.mcp_health_check_concurrency)

                    async def _guarded(srv):
                        async with sem:
                            return await _probe_and_apply(session, srv)

                    results = await asyncio.gather(
                        *[_guarded(s) for s in to_probe]
                    )
                    changed = sum(1 for r in results if r)
                    # Probes always write status + health_detail; persist them.
                    await session.commit()
            return changed
        finally:
            await lock_conn.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": _SWEEP_LOCK_KEY}
            )
            await lock_conn.commit()


async def mcp_health_loop() -> None:
    """Long-running background task — sweep on an interval, forever.

    Mirrors ``cost_backfill.cost_backfill_loop``: an exception in a sweep is logged
    and the loop continues (it must NEVER crash registry-api); ``CancelledError`` is
    re-raised so shutdown can cancel it cleanly.
    """
    logger.info(
        "mcp health sweep started (interval=%ss)",
        settings.mcp_health_check_interval_seconds,
    )
    while True:
        try:
            n = await _sweep_once()
            if n:
                logger.info(
                    "mcp health: updated status/health_detail for %d server(s)", n
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — never let the loop die
            logger.warning("mcp health sweep error: %s", exc)
        await asyncio.sleep(settings.mcp_health_check_interval_seconds)

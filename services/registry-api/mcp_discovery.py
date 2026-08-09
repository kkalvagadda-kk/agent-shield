"""
Shared MCP discovery core (MCP-as-tool-source).

`_materialize_and_discover` + `_mark_server_error` were originally private helpers
of ``routers/mcp_servers.py``. Phase 2 (WS-B / FR-MCP-07) adds a THIRD caller — the
``POST /api/v1/internal/mcp/list-changed`` re-sync endpoint in
``routers/internal_mcp.py`` — which must run byte-identical discovery to a manual
``POST /mcp-servers/{id}/sync``. Rather than fork the logic (the exact duplication
the constitution rejects), the two helpers are MOVED here verbatim so there is one
implementation with three callers (register, ``/sync``, ``/list-changed``).

This is a pure relocation — the register / ``/sync`` / ``PUT`` paths behave
identically (guarded by ``suite-84-mcp-tools.sh`` staying green). ``_now_iso`` moves
with them because it is only used by ``_materialize_and_discover``.

Also hosts the ``/list-changed`` coalesce state (data-model.md §3, research.md C5):
a per-server ``asyncio.Lock`` serialises re-syncs and a per-server last-resync
timestamp deduplicates bursts / cross-replica notifications within
``MIN_RESYNC_INTERVAL_SECONDS``. In-memory, per-replica — nothing persisted.

Ownership invariant (unchanged): ALL ``Tool``-row / server-row writes happen HERE,
inside registry-api's ORM session (caller commits). The MCP Proxy only connects +
lists; it never touches the DB.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from mcp_proxy_client import discover_server
from mcp_secrets import materialize_server_secret
from models import MCPServer, Tool

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_server_error(server: MCPServer, reason: str) -> None:
    """Fold a materialize/discover failure into the server row: status='error',
    health_detail.last_error populated, consecutive_failures incremented. The insert
    and the Secret are NEVER rolled back — registration is not all-or-nothing (contract
    step 6). Tool rows are left untouched (a failed discover cannot know what vanished).
    """
    prev = server.health_detail or {}
    server.status = "error"
    server.health_detail = {
        "last_error": reason,
        "last_success_at": prev.get("last_success_at"),
        "consecutive_failures": int(prev.get("consecutive_failures") or 0) + 1,
        "schema_drift": list(prev.get("schema_drift") or []),
    }


async def _materialize_and_discover(
    db: AsyncSession,
    server: MCPServer,
    *,
    acknowledge_schema_drift: bool,
    user_sub: str | None = None,
) -> dict:
    """Shared register / sync core (contract POST steps 3-7 + the /sync vanished-tool
    and schema-drift passes). Mutates `server` and its child `Tool` rows in the session
    (caller commits). Returns the sync counters.

    ``user_sub`` (Phase 4 WS-2, C9): the authorizing user for an OAuth external server,
    threaded into ``discover_server`` so the proxy lists tools AS that user's OAuth token.
    The OAuth callback (``routers/mcp_oauth.py``) passes it; register / ``/sync`` /
    ``/list-changed`` leave it ``None`` → byte-identical Phase-2 discovery.
    """
    counters = {
        "tools_added": 0,
        "tools_updated": 0,
        "tools_inactivated": 0,
        "schema_drift_detected": [],
    }

    # `acknowledge_schema_drift` clears prior unacknowledged drift entries BEFORE this
    # sync records new ones — honored regardless of the sync's outcome.
    if acknowledge_schema_drift:
        hd = dict(server.health_detail or {})
        hd["schema_drift"] = []
        server.health_detail = hd

    # (contract step 3) materialize the per-server credential Secret. A failure is
    # folded into a discover-error result — do NOT 5xx and do NOT roll back the insert.
    try:
        await materialize_server_secret(db, server)
    except Exception as exc:  # noqa: BLE001 — any k8s/crypto failure becomes status=error
        logger.warning(
            "mcp_servers: materialize_server_secret failed for %s: %s", server.id, exc
        )
        _mark_server_error(server, f"credential materialization failed: {exc}")
        return counters

    # (contract step 4) call the proxy. A RuntimeError (transport / 401/403/422/5xx) is
    # treated identically to an ok:false body → status='error'. ``user_sub`` (when set,
    # OAuth callback path) tells the proxy which user's OAuth token to list AS (C9).
    try:
        resp = await discover_server(server.id, user_sub=user_sub)
    except RuntimeError as exc:
        _mark_server_error(server, str(exc))
        return counters

    if not resp.get("ok"):
        _mark_server_error(
            server, resp.get("health_detail") or "discovery failed (no detail)"
        )
        return counters

    # (contract step 5) success — upsert one Tool row per discovered tool.
    reported = resp.get("tools") or []
    existing = (
        await db.execute(select(Tool).where(Tool.mcp_server_id == server.id))
    ).scalars().all()
    by_mcp_name: dict[str, Tool] = {
        t.mcp_tool_name: t for t in existing if t.mcp_tool_name is not None
    }
    seen: set[str] = set()

    prev_hd = server.health_detail or {}
    schema_drift = list(prev_hd.get("schema_drift") or [])
    drift_detected: list[str] = []
    now = _now_iso()

    for dt in reported:
        raw_name = dt.get("name")
        if not raw_name:
            continue
        seen.add(raw_name)
        input_schema = dt.get("input_schema")
        description = dt.get("description")
        existing_tool = by_mcp_name.get(raw_name)

        if existing_tool is None:
            # First-discovery insert (data-model.md "Tool row shape for mcp_tool").
            db.add(
                Tool(
                    name=f"{server.name}__{raw_name}",
                    display_name=raw_name,
                    description=description,
                    type="mcp_tool",
                    input_schema=input_schema,
                    risk_level="low",  # D4 default; admin can raise via PUT /tools/{id}
                    side_effecting=True,  # conservative — real side effects unknown
                    pii_deanonymize_allowed=False,  # fail-closed default
                    owner_team=server.owner_team,  # the only work team-scoping needs
                    # The registrant is the creator (migration 0081). Catalog
                    # visibility is `published OR created_by == caller`, so a NULL
                    # creator on a private row is invisible to EVERYONE — the same
                    # defect shape as mcp-discovered-tools-invisible-after-private-default.
                    created_by=server.created_by,
                    status="active",
                    mcp_server_id=server.id,
                    mcp_tool_name=raw_name,
                )
            )
            counters["tools_added"] += 1
        else:
            changed = False
            # Reappeared upstream after a prior vanish → reactivate.
            if existing_tool.status != "active":
                existing_tool.status = "active"
                changed = True
            # Schema drift → auto-apply the new schema immediately AND flag it (OQ-5).
            if existing_tool.input_schema != input_schema:
                existing_tool.input_schema = input_schema
                schema_drift.append(
                    {"tool_name": existing_tool.name, "detected_at": now}
                )
                drift_detected.append(existing_tool.name)
                changed = True
            if description is not None and existing_tool.description != description:
                existing_tool.description = description
                changed = True
            if changed:
                counters["tools_updated"] += 1

    # (contract §sync) vanished-upstream tools → status='inactive', NEVER row-deleted.
    for t in existing:
        if t.mcp_tool_name not in seen and t.status == "active":
            t.status = "inactive"
            counters["tools_inactivated"] += 1

    # (contract step 5) success bookkeeping on the server row.
    server.status = "connected"
    server.last_synced_at = datetime.now(timezone.utc)
    server.list_changed_supported = bool(resp.get("list_changed_supported", False))
    server.discovered_tool_count = len(reported)
    server.health_detail = {
        "last_error": None,
        "last_success_at": now,
        "consecutive_failures": 0,
        "schema_drift": schema_drift,
    }
    counters["schema_drift_detected"] = drift_detected
    return counters


# ---------------------------------------------------------------------------
# /list-changed coalesce state (WS-B / FR-MCP-07 — data-model.md §3, research.md C5)
# ---------------------------------------------------------------------------
# Per-server last-resync wall-time (from time.monotonic) and per-server lock. The
# re-sync endpoint serialises under the lock and deduplicates bursts / cross-replica
# notifications: a second re-sync within MIN_RESYNC_INTERVAL_SECONDS returns
# coalesced=true without re-discovering. In-memory, per-replica; nothing persisted.
_last_resync: dict[str, float] = {}
_resync_locks: dict[str, asyncio.Lock] = {}
MIN_RESYNC_INTERVAL_SECONDS: int = settings.mcp_list_changed_min_resync_interval_seconds

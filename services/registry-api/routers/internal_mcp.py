"""
Internal MCP endpoints — cluster-internal, NetworkPolicy-trusted (unauthenticated).

  POST /api/v1/internal/mcp/authorize-tool-call  {caller_sa_subject, server_id, mcp_tool_name}
       → {allowed: bool}
  POST /api/v1/internal/mcp/list-changed         {server_id}
       → {ok, coalesced, tools_added, tools_updated, tools_inactivated, reason}

The cross-team half of the MCP Proxy's §3b coarse authz floor. The proxy calls this
ONLY when a caller's team differs from the target server's ``owner_team`` (own-team is
answered locally in the proxy with zero hops). It returns only a boolean — never a
secret, credential, or server URL — so, like every other internal registry-api endpoint
(see ``routers/internal.py``), it runs no TokenReview: the proxy has already
TokenReview-verified the caller's identity before calling here.

Always answers ``200`` (``allowed: false`` is a normal answer, not an error). ``422``
only on a malformed body. Uses the SAME ``team_may_use_tool`` resolver as the deploy
gate — one implementation, two callers.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import mcp_discovery
from db import AsyncSessionLocal
from models import MCPServer, Tool
from tool_access import team_may_use_tool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/internal/mcp", tags=["internal"])


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


class AuthorizeToolCallRequest(BaseModel):
    # A non-empty SA subject; the team is derived from its `agents-{team}` namespace.
    caller_sa_subject: str = Field(..., min_length=1)
    # Typed as UUID so an invalid/missing id is a 422 (malformed body), not a 200.
    server_id: uuid.UUID
    mcp_tool_name: str = Field(..., min_length=1)


class AuthorizeToolCallResponse(BaseModel):
    allowed: bool


def _team_from_sa_subject(subject: str) -> str | None:
    """Derive the caller's team from a Kubernetes SA subject
    ``system:serviceaccount:agents-{team}:{sa_name}``.

    Any subject whose namespace is not of the ``agents-`` form (or an empty team) →
    ``None``, which the caller maps to ``allowed: false`` — not an error. Structural,
    not a guard bolted on: a non-agent caller simply holds no team grant.
    """
    parts = subject.split(":")
    if len(parts) < 4 or parts[0] != "system" or parts[1] != "serviceaccount":
        return None
    namespace = parts[2]
    prefix = "agents-"
    if not namespace.startswith(prefix):
        return None
    team = namespace[len(prefix):]
    return team or None


@router.post(
    "/authorize-tool-call",
    response_model=AuthorizeToolCallResponse,
    summary="Cross-team MCP tool-call grant check (cluster-internal)",
)
async def authorize_tool_call(
    body: AuthorizeToolCallRequest,
    db: AsyncSession = Depends(_get_db),
) -> AuthorizeToolCallResponse:
    caller_team = _team_from_sa_subject(body.caller_sa_subject)
    if caller_team is None:
        # Non-`agents-` subject → no team → not allowed (defensive; the proxy will
        # already have 403'd such a caller).
        logger.info(
            "authorize_tool_call: subject %r has no agents- team — allowed=false",
            body.caller_sa_subject,
        )
        return AuthorizeToolCallResponse(allowed=False)

    tool = (
        await db.execute(
            select(Tool).where(
                Tool.mcp_server_id == body.server_id,
                Tool.mcp_tool_name == body.mcp_tool_name,
            )
        )
    ).scalar_one_or_none()
    if tool is None:
        logger.info(
            "authorize_tool_call: no tool for server=%s mcp_tool_name=%r — allowed=false",
            body.server_id, body.mcp_tool_name,
        )
        return AuthorizeToolCallResponse(allowed=False)

    allowed = await team_may_use_tool(db, caller_team, tool.id)
    logger.info(
        "authorize_tool_call: team=%s server=%s tool=%s allowed=%s",
        caller_team, body.server_id, tool.id, allowed,
    )
    return AuthorizeToolCallResponse(allowed=allowed)


# ---------------------------------------------------------------------------
# POST /api/v1/internal/mcp/list-changed  (WS-B / FR-MCP-07)
# ---------------------------------------------------------------------------
class ListChangedRequest(BaseModel):
    # Typed as UUID so a bad/missing id is a 422 (malformed body), not a 200.
    server_id: uuid.UUID


class ListChangedResponse(BaseModel):
    ok: bool
    coalesced: bool = False
    tools_added: int = 0
    tools_updated: int = 0
    tools_inactivated: int = 0
    reason: str | None = None  # e.g. "server_not_found"; None on success


@router.post(
    "/list-changed",
    response_model=ListChangedResponse,
    summary="Re-sync an MCP server's tools after notifications/tools/list_changed",
)
async def list_changed(
    body: ListChangedRequest,
    db: AsyncSession = Depends(_get_db),
) -> ListChangedResponse:
    """Proxy-subscription callback: an upstream server announced
    ``notifications/tools/list_changed`` (debounced proxy-side), so re-run discovery
    for that server using the SHARED ``_materialize_and_discover`` — identical to a
    manual ``POST /mcp-servers/{id}/sync``. registry-api owns every ``Tool``-row write;
    the proxy never touches the DB.

    Always ``200`` on a valid body (``422`` only on a malformed one). An unknown /
    deleted server is a normal answer (``ok=false reason=server_not_found``), not a
    4xx — the proxy may still hold a stale subscription and needs this to stop. A
    discovery failure inside the shared core (proxy unreachable / server down) is also
    ``200`` with ``ok=false`` (same convention as ``/sync``: a successful API call
    reporting an unhealthy server).

    NetworkPolicy-trusted, unauthenticated — identical posture to ``authorize-tool-call``
    (no TokenReview); returns only counters, never a secret/URL.
    """
    server_key = str(body.server_id)

    server = (
        await db.execute(select(MCPServer).where(MCPServer.id == body.server_id))
    ).scalar_one_or_none()
    if server is None:
        logger.info(
            "list_changed: server %s not found — ok=false server_not_found", server_key
        )
        return ListChangedResponse(ok=False, reason="server_not_found")

    # Coalesce guard (cross-replica / burst dedup, research.md C5): serialise per-server
    # under a lock, and skip a re-sync that lands within MIN_RESYNC_INTERVAL_SECONDS of
    # the previous one. Lock created on first use for this server.
    lock = mcp_discovery._resync_locks.get(server_key)
    if lock is None:
        lock = asyncio.Lock()
        mcp_discovery._resync_locks[server_key] = lock

    async with lock:
        now = time.monotonic()
        last = mcp_discovery._last_resync.get(server_key, 0.0)
        if now - last < mcp_discovery.MIN_RESYNC_INTERVAL_SECONDS:
            logger.info(
                "list_changed: server %s coalesced (%.1fs since last re-sync)",
                server_key, now - last,
            )
            return ListChangedResponse(ok=True, coalesced=True)

        mcp_discovery._last_resync[server_key] = now
        counters = await mcp_discovery._materialize_and_discover(
            db, server, acknowledge_schema_drift=False
        )
        await db.commit()

    # A failed re-sync leaves status='error' (via _mark_server_error); a good one sets
    # status='connected'. Report ok off that outcome (same convention as /sync).
    ok = server.status == "connected"
    logger.info(
        "list_changed: server %s re-synced ok=%s added=%d updated=%d inactivated=%d",
        server_key, ok, counters["tools_added"], counters["tools_updated"],
        counters["tools_inactivated"],
    )
    return ListChangedResponse(
        ok=ok,
        coalesced=False,
        tools_added=counters["tools_added"],
        tools_updated=counters["tools_updated"],
        tools_inactivated=counters["tools_inactivated"],
    )

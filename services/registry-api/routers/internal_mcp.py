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
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from kubernetes.client.rest import ApiException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import k8s
import mcp_discovery
from config import settings
from credential_provider import CredentialNotFound, CredentialRef, get_provider
from db import AsyncSessionLocal
from mcp_oauth import (
    OAuthDiscoveryError,
    OAuthFlowError,
    discover_oauth_metadata,
    refresh_access_token,
)
from models import MCPOAuthGrant, MCPServer, Tool
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


# ---------------------------------------------------------------------------
# POST /api/v1/internal/mcp/oauth/access-token  (WS-2 / FR-MCP OAuth, T010)
#
# THE ONE INTERNAL MCP ENDPOINT THAT AUTHENTICATES ITS CALLER. The two sibling
# endpoints above return only a boolean / counters and rely on the NetworkPolicy +
# the proxy's own inbound TokenReview (internal == unauthenticated). THIS endpoint is
# different: it hands out a live OAuth *access token* (a bearer). Leaving it
# unauthenticated would be a credential-harvest confused-deputy, so it (contract §5, C4):
#   1. reads `Authorization: Bearer <proxy SA token>` + TokenReviews it (audience
#      MCP_PROXY_SA_AUDIENCE) — missing/invalid/wrong-audience → 401;
#   2. pins the verified subject to the mcp-proxy SA (MCP_PROXY_SA_SUBJECT) — an
#      authenticated-but-wrong caller → 403.
# Every *token* outcome (no grant / needs re-auth / revoked) is a 200 with NO token
# (fail-closed): the proxy turns those into an upstream `is_error`, never a 5xx.
# ---------------------------------------------------------------------------
class OAuthAccessTokenRequest(BaseModel):
    # Typed as UUID so a bad/missing id is a 422 (malformed body), not a 200.
    server_id: uuid.UUID
    user_sub: str = Field(..., min_length=1)


class OAuthAccessTokenResponse(BaseModel):
    status: str  # "authorized" | "needs_auth" | "error"
    access_token: str | None = None  # present ONLY when status == "authorized"
    expires_at: datetime | None = None
    detail: str | None = None  # reason when status != "authorized"


def _bearer_from_header(authorization: str | None) -> str | None:
    """Extract the raw token from an ``Authorization: Bearer <token>`` header.

    Returns None for a missing header or a non-``Bearer`` scheme (the caller maps None
    to 401). Never raises."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


async def _authenticate_proxy_sa(authorization: str | None) -> None:
    """Authenticate the caller as the mcp-proxy SA, or raise 401/403.

    TokenReview the bearer (audience ``MCP_PROXY_SA_AUDIENCE``), require
    ``status.authenticated`` AND the audience present in ``status.audiences``, then pin
    ``status.user.username`` to ``MCP_PROXY_SA_SUBJECT``. The deliberate break from
    "internal == unauthenticated" (contract §5): this endpoint returns a bearer.

    * Missing / malformed / unauthenticated / wrong-audience token → **401**.
    * Authenticated but NOT the mcp-proxy SA → **403**.
    """
    token = _bearer_from_header(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="missing or malformed bearer token")

    audience = settings.mcp_proxy_sa_audience
    try:
        review = await k8s.create_token_review(token, audience)
    except ApiException as exc:
        # A TokenReview API error (not an auth verdict) — we cannot assert identity, so
        # fail closed as 401. Never trust an unreviewable token.
        logger.warning("oauth_access_token: TokenReview API error: %s", exc)
        raise HTTPException(status_code=401, detail="token review failed") from exc

    status_obj = getattr(review, "status", None)
    if status_obj is None or not getattr(status_obj, "authenticated", False):
        raise HTTPException(status_code=401, detail="token not authenticated")

    audiences = getattr(status_obj, "audiences", None) or []
    if audience not in audiences:
        # Right token, wrong audience (e.g. an agentshield-mcp-proxy token replayed here).
        logger.warning(
            "oauth_access_token: token audiences %s missing %r", audiences, audience
        )
        raise HTTPException(status_code=401, detail="token audience mismatch")

    user = getattr(status_obj, "user", None)
    subject = getattr(user, "username", "") if user is not None else ""
    if subject != settings.mcp_proxy_sa_subject:
        logger.warning(
            "oauth_access_token: caller subject %r != pinned proxy SA %r — 403",
            subject, settings.mcp_proxy_sa_subject,
        )
        raise HTTPException(status_code=403, detail="caller is not the mcp-proxy SA")


@router.post(
    "/oauth/access-token",
    response_model=OAuthAccessTokenResponse,
    summary="Mint a fresh OAuth access token for the proxy (TokenReview'd, subject-pinned)",
)
async def oauth_access_token(
    body: OAuthAccessTokenRequest,
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(_get_db),
) -> OAuthAccessTokenResponse:
    """Return a fresh upstream OAuth access token for ``(server_id, user_sub)``.

    Auth: TokenReview + subject-pin the mcp-proxy SA (401/403). Token logic (data-model
    §4): load the grant ``FOR UPDATE`` (single-writer row lock so two concurrent
    refreshes cannot double-rotate + invalidate each other — RFC 9700). No usable grant
    → ``200 {needs_auth|error}`` (no token). Else resolve the refresh token via the
    provider, discover the AS, and refresh — CAPTURING any rotated refresh token and
    re-storing it BEFORE returning (a stale RT after rotation is a permanent lockout).
    A refresh failure (``invalid_grant`` = revoked/expired) poisons the grant to
    ``error`` and returns ``200 {error}`` — fail-closed, never a 5xx.

    registry-api holds only the refresh token (never an access token), so every pull
    performs a live refresh; the *proxy* caches the returned access token in memory.
    """
    # (1) AuthN — the ONLY internal MCP endpoint that authenticates (it hands out a bearer).
    await _authenticate_proxy_sa(authorization)

    # (2) Load the grant FOR UPDATE. The row lock serialises concurrent pulls for the
    #     same (server, user): the second waiter blocks here until the first commits,
    #     then re-reads the just-rotated refresh token — no double-rotate/invalidate.
    #     Held until commit (success) or session close (rollback on any early return).
    grant = (
        await db.execute(
            select(MCPOAuthGrant)
            .where(
                MCPOAuthGrant.server_id == body.server_id,
                MCPOAuthGrant.user_sub == body.user_sub,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if grant is None:
        return OAuthAccessTokenResponse(
            status="needs_auth", detail="no authorized grant for (server, user)"
        )
    if grant.status != "authorized":
        # needs_auth or error → fail closed with the recorded reason (re-authorize).
        outcome = grant.status if grant.status in ("needs_auth", "error") else "needs_auth"
        return OAuthAccessTokenResponse(
            status=outcome,
            detail=grant.last_error or f"grant status is {grant.status!r}",
        )
    if not grant.credential_ref:
        # authorized but no refresh-token pointer — structurally unexpected; fail closed.
        return OAuthAccessTokenResponse(
            status="needs_auth", detail="grant has no stored refresh token"
        )

    # (3) Resolve the refresh token from the provider (NEVER persisted in a column).
    provider = get_provider()
    ref = CredentialRef.parse(grant.credential_ref)
    try:
        stored = await provider.get(ref)
    except CredentialNotFound:
        # The pointer resolves to nothing → the user must re-authorize (fail closed).
        return OAuthAccessTokenResponse(
            status="needs_auth", detail="stored refresh token not found"
        )
    refresh_token = stored.get("refresh_token") if isinstance(stored, dict) else None
    if not refresh_token:
        # No refresh token. RFC 6749 makes the refresh token OPTIONAL — a classic GitHub
        # OAuth App (and any AS that hands back a long-lived / non-expiring access token)
        # returns only an access token. Serve the STORED access token directly: valid while
        # token_expires_at is None (non-expiring) or still in the future; needs_auth once it
        # has actually expired (nothing can renew it, so the user re-authorizes).
        access_token = stored.get("access_token") if isinstance(stored, dict) else None
        if not access_token:
            return OAuthAccessTokenResponse(
                status="needs_auth",
                detail="stored credential has neither a refresh nor an access token",
            )
        if (
            grant.token_expires_at is not None
            and grant.token_expires_at <= datetime.now(timezone.utc)
        ):
            grant.status = "needs_auth"
            grant.last_error = "access token expired and no refresh token to renew"
            grant.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return OAuthAccessTokenResponse(
                status="needs_auth", detail="access token expired — re-authorize"
            )
        return OAuthAccessTokenResponse(
            status="authorized",
            access_token=access_token,
            expires_at=grant.token_expires_at,
        )

    # (4) Load the server + the SAME client that obtained the refresh token, discover the
    #     AS, and refresh.
    server = (
        await db.execute(select(MCPServer).where(MCPServer.id == body.server_id))
    ).scalar_one_or_none()
    if server is None:
        # The grant outlived its server (CASCADE should have removed it) — fail closed.
        return OAuthAccessTokenResponse(status="needs_auth", detail="server not found")

    try:
        meta = await discover_oauth_metadata(server.server_url)
    except OAuthDiscoveryError as exc:
        # A transient upstream/discovery failure must NOT poison a valid grant to 'error'
        # (the refresh token is still good) — report 'error' for THIS pull only so the
        # next pull retries cleanly once the AS recovers. The proxy fails closed either way.
        logger.info(
            "oauth_access_token: discovery failed server=%s user=%s (grant unchanged): %s",
            body.server_id, body.user_sub, exc,
        )
        return OAuthAccessTokenResponse(status="error", detail=f"discovery failed: {exc}")

    # Reuse the callback's client resolver so the refresh uses the IDENTICAL client that
    # obtained the refresh token (oauth_client_ref, else pre-registered AuthConfig; never
    # re-registers). Lazy import mirrors the repo's cross-router pattern and avoids an
    # import-time cycle (routers/mcp_oauth pulls in mcp_discovery).
    from routers.mcp_oauth import _load_client_for_exchange

    client = await _load_client_for_exchange(db, server)
    if client is None:
        return OAuthAccessTokenResponse(
            status="error", detail="no OAuth client available for refresh"
        )

    try:
        tokens = await refresh_access_token(meta, client, refresh_token=refresh_token)
    except OAuthFlowError as exc:
        # invalid_grant = the refresh token was revoked/expired → the user MUST re-auth.
        # Poison the grant to 'error' (persisted) and fail closed with 200.
        grant.status = "error"
        grant.last_error = f"refresh failed: {exc}"[:2000]
        grant.updated_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(
            "oauth_access_token: refresh failed server=%s user=%s: %s",
            body.server_id, body.user_sub, exc,
        )
        return OAuthAccessTokenResponse(status="error", detail=str(exc))

    # (5) ROTATION (RFC 9700): if the AS rotated the refresh token, re-store it BEFORE
    #     returning — a stale refresh token after rotation is a permanent lockout. The
    #     rotate lands in the provider store (a different table/session than the locked
    #     grant row), so it is safe under the FOR UPDATE lock held on mcp_oauth_grants.
    rotated = bool(tokens.refresh_token and tokens.refresh_token != refresh_token)
    if rotated:
        await provider.rotate(ref, {"refresh_token": tokens.refresh_token})

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(tokens.expires_in))
        if tokens.expires_in
        else None
    )
    grant.status = "authorized"
    grant.token_expires_at = expires_at
    grant.last_error = None
    if tokens.scope:
        grant.scopes = tokens.scope
    grant.updated_at = datetime.now(timezone.utc)
    await db.commit()  # persists rotation/expiry AND releases the FOR UPDATE lock

    logger.info(
        "oauth_access_token: refreshed server=%s user=%s (rotated=%s exp=%s)",
        body.server_id, body.user_sub, rotated, expires_at,
    )
    return OAuthAccessTokenResponse(
        status="authorized", access_token=tokens.access_token, expires_at=expires_at
    )

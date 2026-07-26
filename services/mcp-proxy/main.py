"""
MCP Proxy — FastAPI app.

In-cluster Service agentshield-mcp-proxy:8080. The proxy is a pure MCP wire client
and the credential custodian for every registered server. It:
  - GET /health, GET /ready          — unauthenticated probes, 200 unconditionally
  - POST /internal/discover          — admin plane (registry-api only): connect +
                                       initialize + tools/list against a server
  - POST /internal/tools/call        — data plane (agent pods): authorize + execute
                                       one tool call

Auth (design §3b): every /internal/* request carries a projected SA token with
audience agentshield-mcp-proxy, verified via K8s TokenReview. Discover additionally
requires the caller subject to equal registry-api's SA. tools/call additionally
applies the coarse team-scope floor (own-team fast path; cross-team → registry-api
callback). Tool/transport failures are HTTP 200 with an error body (never 5xx);
only real auth/body failures use 401/403/422.

The proxy NEVER touches the DB or AGENTSHIELD_ENCRYPTION_KEY — connection info +
credentials come only from per-server K8s Secrets (research.md B3/B13).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Response

import authn
import authz
import config
import credentials
import identity
import keycloak_client
import mcp_client
import oauth_tokens
import session_cache
import subscription_manager
from credentials import ServerSecretNotFound
from schemas import (
    McpDiscoveredTool,
    McpDiscoverRequest,
    McpDiscoverResponse,
    McpHealthRequest,
    McpHealthResponse,
    McpToolCallRequest,
    McpToolCallResponse,
)
from session_cache import CachedSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="mcp-proxy", version="0.1.0")

_TRACE_HEADER = "X-AgentShield-Trace-ID"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bearer_from_header(authorization: str | None) -> str | None:
    """Extract the token from an `Authorization: Bearer <token>` header, or None."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _echo_trace(response: Response, trace_id: str | None) -> None:
    if trace_id:
        response.headers[_TRACE_HEADER] = trace_id


async def _authenticate(authorization: str | None) -> str:
    """Verify the bearer token → SA subject, or raise 401 (design §3b)."""
    token = _bearer_from_header(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    sa_subject = await authn.verify_bearer_token(token)
    if not sa_subject:
        raise HTTPException(status_code=401, detail="invalid or wrong-audience token")
    return sa_subject


async def _maybe_subscribe_list_changed(server_id: str, list_changed_supported: bool) -> None:
    """Fire-and-forget: ensure a list_changed subscriber when the server supports it (WS-B).

    Called after a SUCCESSFUL /internal/discover and /internal/health. Subscription
    setup MUST NOT break the (already successful) discover/health response, so any
    failure here is swallowed + logged. ensure_subscription is itself idempotent and
    non-blocking (it only spawns a task), so re-discovering/re-probing the same server
    never creates a second subscriber.
    """
    if not list_changed_supported:
        return
    try:
        await subscription_manager.ensure_subscription(server_id)
    except Exception as exc:  # noqa: BLE001 — never fail the discover/health response
        logger.warning(
            "mcp-proxy: ensure_subscription failed for %s (ignored): %s", server_id, exc
        )


# ---------------------------------------------------------------------------
# Probes — unauthenticated, 200 unconditionally (never depends on a downstream)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    # 200 regardless of any downstream MCP server's health — a server outage must
    # not crash-loop the proxy. Report whether the in-cluster k8s client is up.
    import k8s_client  # local import: avoid touching k8s at module load

    return {"status": "ok", "k8s_initialized": k8s_client._k8s_initialized}


# ---------------------------------------------------------------------------
# /internal/discover — admin plane (caller = registry-api only)
# ---------------------------------------------------------------------------

@app.post("/internal/discover", response_model=McpDiscoverResponse)
async def discover(
    req: McpDiscoverRequest,
    response: Response,
    authorization: str | None = Header(default=None),
    x_agentshield_trace_id: str | None = Header(default=None),
) -> McpDiscoverResponse:
    _echo_trace(response, x_agentshield_trace_id)

    sa_subject = await _authenticate(authorization)
    # Admin plane: only registry-api's SA may discover. Any other authenticated
    # subject → 403 (this is not agent-facing).
    if sa_subject != config.REGISTRY_API_SA_SUBJECT:
        raise HTTPException(status_code=403, detail="discover is admin-plane (registry-api only)")

    server_id = str(req.server_id)

    # Read the per-server Secret → connect → initialize → list_tools. Any failure
    # here is a 200 error body (status='error'), NOT an HTTP 5xx — registry-api
    # treats a failed connect as a successful API call about an unhealthy server.
    try:
        connection = await credentials.read_server_secret(server_id)
    except ServerSecretNotFound as exc:
        return McpDiscoverResponse(ok=False, status="error", health_detail=str(exc))

    # WS-C: resolve the upstream credential for the server's identity_mode (admin plane →
    # none=static, service_identity/on_behalf_of=platform service token). A mint failure is a
    # reachability outcome (200 error body), never a 5xx. (OBO admin plane uses the service
    # token — discovery lists the server AS the platform; it never impersonates a user, so
    # no OnBehalfOf* exception can arise here with is_data_plane=False / user_sub=None.)
    #
    # WS-2 (Phase 4): for an OAuth server, registry-api's discover-as-user passes req.user_sub
    # so admin-plane discovery pulls THAT user's upstream access token (C9). If it is missing/
    # unavailable, resolve_headers raises an OAuth* exception — caught by the generic handler
    # below → the existing 200 ok=false "identity: …" body (fail-closed, never a 5xx). For a
    # static server req.user_sub is ignored and this is byte-identical to Phase 2.
    try:
        headers = await identity.resolve_headers(
            connection, user_sub=req.user_sub, is_data_plane=False
        )
    except Exception as exc:  # noqa: BLE001 — identity/mint/OAuth failure
        logger.warning("mcp-proxy discover: identity resolution failed for %s: %s", server_id, exc)
        return McpDiscoverResponse(ok=False, status="error", health_detail=f"identity: {exc}")

    try:
        session = await mcp_client.connect_and_initialize(
            connection.server_url, headers
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp-proxy discover: connect failed for %s: %s", server_id, exc)
        return McpDiscoverResponse(
            ok=False,
            status="error",
            health_detail=f"connect failed to {connection.server_url}: {exc}",
        )

    try:
        tools = await session.list_tools()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp-proxy discover: list_tools failed for %s: %s", server_id, exc)
        await session.close()
        return McpDiscoverResponse(
            ok=False,
            status="error",
            health_detail=f"tools/list failed: {exc}",
            protocol_version=session.protocol_version,
            list_changed_supported=session.list_changed_supported,
        )

    # Cache the live session for the subsequent tools/call fast path.
    await session_cache.set_session(
        server_id, CachedSession(session=session, connection=connection)
    )

    # WS-B: discover is the first place a fresh server's list_changed capability is
    # known — start the subscriber now if the server supports it (idempotent).
    await _maybe_subscribe_list_changed(server_id, session.list_changed_supported)

    return McpDiscoverResponse(
        ok=True,
        status="connected",
        protocol_version=session.protocol_version,
        list_changed_supported=session.list_changed_supported,
        tools=[
            McpDiscoveredTool(
                name=t.name, description=t.description, input_schema=t.input_schema
            )
            for t in tools
        ],
    )


# ---------------------------------------------------------------------------
# /internal/health — admin plane (caller = registry-api health loop only)
# ---------------------------------------------------------------------------

@app.post("/internal/health", response_model=McpHealthResponse)
async def health_check(
    req: McpHealthRequest,
    response: Response,
    authorization: str | None = Header(default=None),
    x_agentshield_trace_id: str | None = Header(default=None),
) -> McpHealthResponse:
    """Lightweight reachability probe for registry-api's health loop.

    Mirrors /internal/discover's auth + error-to-200 shape: authenticate the SA
    token, require the registry-api SA subject (admin plane), read the per-server
    Secret, reuse (or open) the pooled session, and run tools/list as the liveness
    check. It returns the tool COUNT, never the tool list, and writes NOTHING to any
    DB (the proxy has no DB — Phase-1 invariant). ANY reachability failure (missing
    Secret, connect error, tools/list error) is a 200 ok=false body with a reason —
    never a 5xx. Only a missing/wrong-audience token (401) or a non-registry-api
    subject (403) is a real HTTP error.

    WS-C (Task 9): the pooled session is opened with identity.resolve_headers on the ADMIN
    plane (user_sub=None, is_data_plane=False) — none=static, service_identity/on_behalf_of=
    the platform service token. A mint failure is a 200 ok=false body, never a 5xx.
    """
    _echo_trace(response, x_agentshield_trace_id)

    sa_subject = await _authenticate(authorization)
    # Admin plane: only registry-api's health loop may probe. Same restriction as
    # /internal/discover — any other authenticated subject → 403.
    if sa_subject != config.REGISTRY_API_SA_SUBJECT:
        raise HTTPException(status_code=403, detail="health is admin-plane (registry-api only)")

    server_id = str(req.server_id)

    # 1. Read the per-server Secret. A missing/malformed Secret is a reachability
    #    outcome (ok=false), never a 5xx. (The connection is also used to report the
    #    server_url on a connect failure below.)
    try:
        connection = await credentials.read_server_secret(server_id)
    except ServerSecretNotFound as exc:
        return McpHealthResponse(ok=False, status="error", health_detail=str(exc))

    # 2. Resolve the admin-plane upstream credential. A service-identity mint failure is a
    #    reachability outcome (200 ok=false), never a 5xx. (No OnBehalfOf* here: admin plane.)
    try:
        headers = await identity.resolve_headers(connection, user_sub=None, is_data_plane=False)
    except Exception as exc:  # noqa: BLE001 — identity/mint failure
        logger.warning("mcp-proxy health: identity resolution failed for %s: %s", server_id, exc)
        return McpHealthResponse(ok=False, status="error", health_detail=f"identity: {exc}")

    # 3. Reuse the pooled session, or open one on a miss (with the resolved headers).
    #    Any connect failure → evict + 200 ok=false (never 5xx).
    try:
        cached = await session_cache.get_or_create(server_id, user_sub=None, headers=headers)
    except ServerSecretNotFound as exc:
        # A race (Secret deleted between the read above and here) — still a 200 body.
        return McpHealthResponse(ok=False, status="error", health_detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp-proxy health: connect failed for %s: %s", server_id, exc)
        await session_cache.evict(server_id)
        return McpHealthResponse(
            ok=False,
            status="error",
            health_detail=f"connect failed to {connection.server_url}: {exc}",
        )

    # 4. tools/list is the liveness probe (bounded by MCP_CONNECT_TIMEOUT_SECONDS).
    #    On failure evict so the next cycle reconnects. No Tool-row write happens here.
    try:
        tools = await cached.session.list_tools()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp-proxy health: tools/list failed for %s: %s", server_id, exc)
        await session_cache.evict(server_id)
        return McpHealthResponse(
            ok=False,
            status="error",
            health_detail=f"tools/list failed: {exc}",
            protocol_version=cached.session.protocol_version,
            list_changed_supported=cached.session.list_changed_supported,
        )

    # 5. Success. WS-B: a periodic health probe is also a place the capability is
    #    (re)observed — ensure the subscriber exists if supported (idempotent; a re-probe
    #    of an already-subscribed server is a no-op).
    await _maybe_subscribe_list_changed(server_id, cached.session.list_changed_supported)

    return McpHealthResponse(
        ok=True,
        status="connected",
        health_detail=None,
        protocol_version=cached.session.protocol_version,
        list_changed_supported=cached.session.list_changed_supported,
        tool_count=len(tools),
    )


# ---------------------------------------------------------------------------
# /internal/tools/call — data plane (caller = agent pod)
# ---------------------------------------------------------------------------

@app.post("/internal/tools/call", response_model=McpToolCallResponse)
async def tools_call(
    req: McpToolCallRequest,
    response: Response,
    authorization: str | None = Header(default=None),
    x_agentshield_trace_id: str | None = Header(default=None),
    x_user_sub: str | None = Header(default=None),  # end-user subject — drives on_behalf_of
) -> McpToolCallResponse:
    _echo_trace(response, x_agentshield_trace_id)

    # 1. AuthN — verify the SA token → caller subject (401 on failure).
    sa_subject = await _authenticate(authorization)

    server_id = str(req.server_id)

    # 2. Resolve connection metadata (owner_team + identity_mode). From an already-open
    #    session (zero reads) or a single Secret read (reused to open the session below —
    #    no double read). Keyed on the composite (server_id, x_user_sub); for a
    #    none/service_identity server the user_sub is ignored and this is the shared
    #    session, so metadata is user-independent. A missing Secret is a 200 error body.
    cached = session_cache.peek(server_id, x_user_sub)
    if cached is not None:
        connection = cached.connection
    else:
        try:
            connection = await credentials.read_server_secret(server_id)
        except ServerSecretNotFound as exc:
            return McpToolCallResponse(is_error=True, error=str(exc))

    # 3. AuthZ floor (§3b).
    allowed = await authz.authorize_tool_call(
        sa_subject, server_id, req.mcp_tool_name, connection.owner_team
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="team-scope floor: not authorized for this server/tool")

    # 4. WS-C: resolve the upstream credential for the server's identity_mode (data plane).
    #    An on_behalf_of outcome is a per-call tool error the LLM should see — a 200 is_error
    #    body, NOT a 5xx/403. This runs even on a cache hit so an on_behalf_of server ALWAYS
    #    fails closed here before any pooled session (incl. an admin-plane one) is ever used.
    try:
        headers = await identity.resolve_headers(
            connection, user_sub=x_user_sub, is_data_plane=True
        )
    except identity.OAuthUserRequired:
        # WS-2 fail-closed: an OAuth server with no end-user identity — never a silent
        # unauthenticated upstream call. A per-call error the LLM should see (200 is_error).
        return McpToolCallResponse(
            is_error=True,
            error="this MCP server requires OAuth authorization but no user identity was provided",
        )
    except identity.OAuthAuthorizationRequired as exc:
        # registry-api says the grant is needs_auth — the user must (re-)authorize in Studio.
        return McpToolCallResponse(
            is_error=True,
            error=f"this MCP server requires OAuth authorization — authorize it in Studio ({exc})",
        )
    except identity.OAuthTokenUnavailable as exc:
        # Transport/registry-api/refresh failure fetching the access token — fail closed.
        return McpToolCallResponse(is_error=True, error=f"OAuth token unavailable: {exc}")
    except identity.OnBehalfOfIdentityRequired:
        return McpToolCallResponse(
            is_error=True,
            error="this MCP server requires a user identity (on_behalf_of) but none was provided",
        )
    except identity.OnBehalfOfNotAvailable:
        return McpToolCallResponse(
            is_error=True,
            error="on-behalf-of token exchange is not yet available (blocked on Decision 29)",
        )
    except Exception as exc:  # noqa: BLE001 — service-identity mint failure → 200 error body
        logger.warning("mcp-proxy tools/call: identity resolution failed for %s: %s", server_id, exc)
        return McpToolCallResponse(is_error=True, error=f"identity: {exc}")

    # 5. Get (or lazily create) the live session — reusing the connection + resolved headers
    #    read on a miss so the Secret is read exactly once. Keyed on the composite key.
    if cached is None:
        try:
            session = await mcp_client.connect_and_initialize(connection.server_url, headers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp-proxy tools/call: connect failed for %s: %s", server_id, exc)
            return McpToolCallResponse(
                is_error=True, error=f"connect failed to {connection.server_url}: {exc}"
            )
        cached = CachedSession(session=session, connection=connection)
        await session_cache.set_session(server_id, cached, user_sub=x_user_sub)

    # 6. Execute, with ONE evict-and-retry on a transport/protocol failure (a raised
    #    exception — a tool that *ran* but reported an error returns is_error=True without
    #    raising, and is NOT retried). For a service_identity server the failure may be a
    #    rotated/expired minted token, so we invalidate the token cache before the retry —
    #    the retry then mints fresh, self-healing a mid-life token rotation. WS-2: symmetric
    #    for an OAuth server — an upstream 401 from a just-expired cached access token
    #    self-heals when we invalidate the per-(server,user) cache so the retry re-pulls a
    #    fresh token from registry-api. (We do NOT sniff the exception for a "401": that
    #    string-match is fragile; a transport failure is rare and always re-fetching on retry
    #    is strictly safe.)
    try:
        call_result = await cached.session.call_tool(req.mcp_tool_name, req.arguments)
    except Exception as exc:  # noqa: BLE001 — transport/protocol/upstream-auth error
        logger.warning(
            "mcp-proxy tools/call: %s failed (%s) — evict + retry once", req.mcp_tool_name, exc
        )
        if connection.identity_mode == "service_identity":
            keycloak_client.invalidate(connection.identity_audience)
        if connection.external_auth_mode == "oauth" and x_user_sub:
            oauth_tokens.invalidate(server_id, x_user_sub)
        await session_cache.evict(server_id, x_user_sub)
        try:
            # Re-read the Secret (picks up a rotated static credential) + re-resolve identity
            # (fresh minted token if invalidated) + reconnect.
            fresh = await credentials.read_server_secret(server_id)
            retry_headers = await identity.resolve_headers(
                fresh, user_sub=x_user_sub, is_data_plane=True
            )
            session = await mcp_client.connect_and_initialize(fresh.server_url, retry_headers)
            cached = CachedSession(session=session, connection=fresh)
            await session_cache.set_session(server_id, cached, user_sub=x_user_sub)
            call_result = await cached.session.call_tool(req.mcp_tool_name, req.arguments)
        except (
            identity.OAuthUserRequired,
            identity.OAuthAuthorizationRequired,
            identity.OAuthTokenUnavailable,
        ) as exc2:
            # WS-2: a needs_auth/unavailable surfacing on the re-resolve (e.g. the grant was
            # revoked between the two calls) stays on the 200-error path — never a 5xx.
            return McpToolCallResponse(
                is_error=True, error=f"oauth token unavailable: {exc2}"
            )
        except (identity.OnBehalfOfIdentityRequired, identity.OnBehalfOfNotAvailable) as exc2:
            # Defensive: the same identity_mode already yielded a 200 is_error above, so this
            # is normally unreachable — but keep an on_behalf_of server on the 200-error path.
            return McpToolCallResponse(
                is_error=True, error=str(exc2) or "on_behalf_of identity not available"
            )
        except ServerSecretNotFound as exc2:
            return McpToolCallResponse(is_error=True, error=str(exc2))
        except Exception as exc2:  # noqa: BLE001
            logger.warning(
                "mcp-proxy tools/call: retry of %s failed: %s", req.mcp_tool_name, exc2
            )
            return McpToolCallResponse(
                is_error=True, error=f"MCP call failed after retry: {exc2}"
            )

    return McpToolCallResponse(
        result=call_result.result,
        is_error=call_result.is_error,
        error=call_result.result if call_result.is_error else None,
        structured_content=call_result.structured,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.PORT)

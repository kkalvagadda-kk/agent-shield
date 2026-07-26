"""
AgentShield Registry API — MCP OAuth 2.1 dance endpoints (Phase 4 WS-2, T008).

The user-facing half of the OAuth authorization-code + PKCE flow for an external MCP
server that advertises OAuth 2.1. registry-api is the single writer that owns the store
(``research.md`` C3): it runs the interactive dance here, stores the resulting refresh
token via WS-1's ``credential_provider`` (never in a column), and records a per-``(server,
user)`` grant. The proxy only ever *reads* a fresh access token (P7+, out of scope here).

Endpoints (contract ``registry-api-oauth-phase4.md`` §1-4):
  POST   /api/v1/mcp-servers/{id}/oauth/authorize  — begin the dance (require_user)
  GET    /api/v1/mcp-servers/oauth/callback        — upstream redirect URI (no auth;
                                                      trust = Fernet state + PKCE + code)
  GET    /api/v1/mcp-servers/{id}/oauth/status     — the caller's grant status (require_user)
  DELETE /api/v1/mcp-servers/{id}/oauth            — disconnect / revoke (require_user)

HARD invariants (task + contract):
  * **No token in a URL/redirect.** The callback 302 carries ONLY ``?oauth=<outcome>`` —
    never the code, access token, or refresh token. The refresh token goes ONLY into the
    provider store; the grant row holds a ``credential_ref`` pointer, not the secret.
  * **Fernet state is the CSRF/replay guard.** The callback trusts ONLY ``read_state`` for
    ``(server_id, user_sub, code_verifier)`` — never a raw value from the query. A
    tampered/expired state → ``?oauth=invalid_state`` with NO grant mutation.
  * **Fail-closed, never a 5xx to the browser.** A discovery/exchange failure sets the
    grant ``status='error'`` + ``last_error`` and redirects ``?oauth=error`` — the callback
    never raises a 5xx that would show the user a stack trace.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import require_user
from config import settings
from credential_provider import (
    CredentialNotFound,
    CredentialRef,
    get_provider,
    mcp_oauth_client_ref,
    mcp_oauth_refresh_ref,
)
from crypto import decrypt_json
from db import get_db
from mcp_discovery import _materialize_and_discover
from mcp_oauth import (
    OAuthClientInfo,
    OAuthDiscoveryError,
    OAuthFlowError,
    OAuthMetadata,
    OAuthStateError,
    build_authorization_url,
    discover_oauth_metadata,
    exchange_code,
    generate_pkce_pair,
    make_state,
    read_state,
    register_client,
)
from models import AuthConfig, MCPOAuthGrant, MCPServer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/mcp-servers", tags=["mcp-servers", "oauth"])

# Best-effort AS revocation (RFC 7009) is a short JSON round-trip — keep it tight so a
# slow/hostile revocation endpoint cannot stall a disconnect.
_REVOKE_TIMEOUT = httpx.Timeout(10.0)


# ---------------------------------------------------------------------------
# Response models (contract §1, §3)
# ---------------------------------------------------------------------------
class OAuthAuthorizeResponse(BaseModel):
    """Contract §1 — Studio sets ``window.location.href = authorization_url``."""

    authorization_url: str


class McpOAuthStatusResponse(BaseModel):
    """Contract §3 — the calling user's grant status for the Studio badge."""

    server_id: uuid.UUID
    user_sub: str
    status: str  # "needs_auth" | "authorized" | "error"
    scopes: str | None = None
    token_expires_at: datetime | None = None
    last_error: str | None = None
    external_auth_mode: str  # "static" | "oauth" — lets Studio hide the panel for static


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _get_server(server_id: uuid.UUID, db: AsyncSession) -> MCPServer:
    server = (
        await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    ).scalar_one_or_none()
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_id}' not found.",
        )
    return server


async def _get_grant(
    server_id: uuid.UUID, user_sub: str, db: AsyncSession
) -> MCPOAuthGrant | None:
    return (
        await db.execute(
            select(MCPOAuthGrant).where(
                MCPOAuthGrant.server_id == server_id,
                MCPOAuthGrant.user_sub == user_sub,
            )
        )
    ).scalar_one_or_none()


async def _upsert_grant(
    db: AsyncSession,
    server_id: uuid.UUID,
    user_sub: str,
    **fields,
) -> MCPOAuthGrant:
    """Create-or-update the ``(server_id, user_sub)`` grant, setting ``fields`` on it.

    Bumps ``updated_at`` so the health loop's "most-recently-authorized user" pick (C9)
    tracks the latest successful authorize/refresh.
    """
    grant = await _get_grant(server_id, user_sub, db)
    if grant is None:
        grant = MCPOAuthGrant(server_id=server_id, user_sub=user_sub)
        db.add(grant)
    for k, v in fields.items():
        setattr(grant, k, v)
    grant.updated_at = datetime.now(timezone.utc)
    return grant


async def _mark_grant_error(
    db: AsyncSession, server_id: uuid.UUID, user_sub: str, reason: str
) -> None:
    """Fold a callback failure into the grant: ``status='error'`` + ``last_error``.

    Only mutates a grant we can key from the *decrypted* state (server_id + user_sub) —
    never from a raw query value. The refresh-token ref is left intact (a failed exchange
    never wrote one; a failed refresh keeps the prior token for a later retry).
    """
    await _upsert_grant(
        db, server_id, user_sub, status="error", last_error=reason[:2000]
    )


def _studio_redirect(server_id, outcome: str) -> RedirectResponse:
    """302 back to the Studio detail page carrying ONLY the ``?oauth=`` outcome flag.

    NEVER carries the code/token/refresh token (HARD invariant). ``outcome`` ∈
    ``connected | denied | invalid_state | error``.
    """
    base = (settings.studio_base_url or "").rstrip("/")
    return RedirectResponse(
        url=f"{base}/mcp-servers/{server_id}?oauth={outcome}",
        status_code=status.HTTP_302_FOUND,
    )


def _studio_redirect_no_server(outcome: str) -> RedirectResponse:
    """302 back to the Studio server LIST when we cannot recover a server_id (a state so
    tampered/expired it will not decrypt) — carries only ``?oauth=<outcome>``."""
    base = (settings.studio_base_url or "").rstrip("/")
    return RedirectResponse(
        url=f"{base}/mcp-servers?oauth={outcome}",
        status_code=status.HTTP_302_FOUND,
    )


async def _read_auth_config_creds(db: AsyncSession, auth_config: AuthConfig) -> dict:
    """Resolve an AuthConfig's credential dict through the WS-1 provider seam.

    Dual-read: the provider when a ``credential_ref`` is set (new/backfilled rows), else
    the retained legacy ``credentials_encrypted`` column. Mirrors
    ``mcp_secrets.materialize_server_secret`` EXACTLY (no getattr sniff, explicit legacy
    branch). Returns ``{}`` when nothing is stored.
    """
    if auth_config.credential_ref is not None:
        try:
            return await get_provider().get(
                CredentialRef.parse(auth_config.credential_ref)
            )
        except CredentialNotFound:
            return {}
    if auth_config.credentials_encrypted:
        return decrypt_json(auth_config.credentials_encrypted)
    return {}


async def _preregistered_client(
    db: AsyncSession, server: MCPServer
) -> OAuthClientInfo | None:
    """Fallback client from a pre-registered ``client_id``/``client_secret`` in the
    server's AuthConfig (contract §1 step 4 — ledgered low-impact debt).

    Only used when the AS advertises no ``registration_endpoint``. Returns ``None`` when
    no usable ``client_id`` is present so the caller can 409 ``oauth_no_client``.
    """
    if server.auth_config_id is None:
        return None
    ac = (
        await db.execute(
            select(AuthConfig).where(AuthConfig.id == server.auth_config_id)
        )
    ).scalar_one_or_none()
    if ac is None:
        return None
    creds = await _read_auth_config_creds(db, ac)
    by_key = {k.lower(): v for k, v in creds.items() if isinstance(v, str)}
    client_id = by_key.get("client_id")
    if not client_id:
        return None
    return OAuthClientInfo(
        client_id=client_id, client_secret=by_key.get("client_secret")
    )


async def _resolve_client(
    db: AsyncSession, server: MCPServer, meta: OAuthMetadata
) -> OAuthClientInfo:
    """Resolve the OAuth client for ``server`` (contract §1 step 4).

    Order: (1) reuse the stored ``oauth_client_ref`` if it still resolves; (2) else if the
    AS advertises Dynamic Client Registration, register + persist the client secret via the
    provider and set ``oauth_client_ref``; (3) else fall back to a pre-registered client
    from the AuthConfig; (4) else 409 ``oauth_no_client``. The client secret lives ONLY
    behind the provider — ``oauth_client_ref`` is a pointer.
    """
    provider = get_provider()

    if server.oauth_client_ref:
        try:
            data = await provider.get(CredentialRef.parse(server.oauth_client_ref))
        except CredentialNotFound:
            data = None  # dangling ref → re-register below
        if data and data.get("client_id"):
            return OAuthClientInfo(
                client_id=data["client_id"], client_secret=data.get("client_secret")
            )

    if meta.registration_endpoint:
        try:
            client = await register_client(meta, settings.mcp_oauth_callback_url)
        except OAuthFlowError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "oauth_registration_failed", "message": str(exc)},
            ) from exc
        ref = mcp_oauth_client_ref(server.id)
        await provider.put(
            ref,
            {"client_id": client.client_id, "client_secret": client.client_secret},
        )
        server.oauth_client_ref = str(ref)
        return client

    fallback = await _preregistered_client(db, server)
    if fallback is not None:
        return fallback

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "oauth_no_client",
            "message": (
                "the authorization server advertises no Dynamic Client Registration "
                "endpoint and no pre-registered client_id is configured on the server's "
                "AuthConfig"
            ),
        },
    )


async def _load_client_for_exchange(
    db: AsyncSession, server: MCPServer
) -> OAuthClientInfo | None:
    """Load the OAuth client for the callback's code exchange (a DIFFERENT request than
    authorize — possibly a different replica).

    Reads the stored ``oauth_client_ref`` (set by authorize), else the pre-registered
    AuthConfig client. NEVER re-registers here (authorize owns registration). Returns
    ``None`` if no client can be resolved → the callback fails closed.
    """
    provider = get_provider()
    if server.oauth_client_ref:
        try:
            data = await provider.get(CredentialRef.parse(server.oauth_client_ref))
        except CredentialNotFound:
            data = None
        if data and data.get("client_id"):
            return OAuthClientInfo(
                client_id=data["client_id"], client_secret=data.get("client_secret")
            )
    return await _preregistered_client(db, server)


async def _best_effort_revoke(
    meta: OAuthMetadata, client: OAuthClientInfo, refresh_token: str
) -> None:
    """RFC 7009 token revocation at the AS ``revocation_endpoint`` — best-effort.

    A confidential client authenticates via ``client_secret_post``. ANY failure is logged
    and swallowed (revocation is a courtesy; the durable disconnect is the local
    ``provider.delete``). Never raises.
    """
    if not meta.revocation_endpoint:
        return
    form = {"token": refresh_token, "token_type_hint": "refresh_token"}
    if client.client_id:
        form["client_id"] = client.client_id
    if client.client_secret:
        form["client_secret"] = client.client_secret
    try:
        async with httpx.AsyncClient(timeout=_REVOKE_TIMEOUT) as http:
            await http.post(meta.revocation_endpoint, data=form)
    except httpx.HTTPError as exc:
        logger.info(
            "mcp_oauth: best-effort revoke failed at %s: %s",
            meta.revocation_endpoint, exc,
        )


# ---------------------------------------------------------------------------
# POST /{server_id}/oauth/authorize  (contract §1)
# ---------------------------------------------------------------------------
@router.post(
    "/{server_id}/oauth/authorize",
    response_model=OAuthAuthorizeResponse,
    summary="Begin the OAuth authorization-code dance for an external MCP server",
)
async def start_oauth_authorization(
    server_id: uuid.UUID,
    user: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> OAuthAuthorizeResponse:
    """Discover the AS, resolve/register the client, mint PKCE + a Fernet ``state``, and
    return the upstream ``authorization_url``. The caller (jwt.sub) becomes the authorizing
    user, bound INSIDE the encrypted state so the callback cannot be steered elsewhere.
    """
    caller_sub = user.get("sub")
    if not caller_sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token has no subject"
        )

    server = await _get_server(server_id, db)
    if server.external_auth_mode != "oauth":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "not_oauth_server",
                "message": (
                    "this server's external_auth_mode is not 'oauth' — nothing to "
                    "authorize"
                ),
            },
        )
    if not settings.mcp_oauth_callback_url:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "oauth_not_configured",
                "message": (
                    "MCP_OAUTH_CALLBACK_URL is not configured — OAuth cannot run without "
                    "a registered redirect URI"
                ),
            },
        )

    try:
        meta = await discover_oauth_metadata(server.server_url)
    except OAuthDiscoveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "oauth_discovery_failed", "message": str(exc)},
        ) from exc

    client = await _resolve_client(db, server, meta)

    verifier, challenge = generate_pkce_pair()
    state = make_state(
        {
            "server_id": str(server_id),
            "user_sub": caller_sub,
            "code_verifier": verifier,
        },
        ttl_seconds=settings.mcp_oauth_state_ttl_seconds,
    )

    # Lazily create the grant in needs_auth (contract §1 step 6) — the callback flips it to
    # authorized. The client registration (oauth_client_ref) set above is committed here too.
    await _upsert_grant(db, server_id, caller_sub, status="needs_auth")
    await db.commit()

    scope = " ".join(meta.scopes_supported) if meta.scopes_supported else None
    authorization_url = build_authorization_url(
        meta,
        client,
        redirect_uri=settings.mcp_oauth_callback_url,
        state=state,
        code_challenge=challenge,
        scope=scope,
        resource=meta.resource,
    )
    logger.info(
        "mcp_oauth: authorize start server=%s user=%s (client_id=%s)",
        server_id, caller_sub, client.client_id,
    )
    return OAuthAuthorizeResponse(authorization_url=authorization_url)


# ---------------------------------------------------------------------------
# GET /oauth/callback  (contract §2) — the upstream redirect URI
# ---------------------------------------------------------------------------
@router.get(
    "/oauth/callback",
    summary="Upstream OAuth redirect URI (302s back to Studio; never JSON, never 5xx)",
)
async def oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    iss: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The URL registered with every upstream AS. Trust comes from the Fernet ``state`` +
    PKCE + the one-time ``code`` — NOT from any raw query value. Always 302s to Studio with
    a ``?oauth=`` flag; never returns JSON, never 5xxes.
    """
    # (1) Recover the trusted (server_id, user_sub, code_verifier) from the ENCRYPTED
    #     state. A tampered/expired state mutates nothing (contract §2 step 2).
    try:
        payload = read_state(state or "")
    except OAuthStateError:
        logger.info("mcp_oauth: callback with invalid/expired state — no grant mutation")
        return _studio_redirect_no_server("invalid_state")

    try:
        server_id = uuid.UUID(str(payload["server_id"]))
        user_sub = str(payload["user_sub"])
        code_verifier = str(payload["code_verifier"])
    except (KeyError, ValueError):
        logger.info("mcp_oauth: callback state missing required fields")
        return _studio_redirect_no_server("invalid_state")

    # From here every failure redirects (never 5xx). A missing server → error (the grant
    # is keyed on a server that no longer exists).
    server = (
        await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    ).scalar_one_or_none()
    if server is None:
        logger.info("mcp_oauth: callback for deleted server %s", server_id)
        return _studio_redirect(server_id, "error")

    try:
        # (contract §2 step 1) The user denied / the AS returned an error.
        if error:
            reason = error_description or error
            await _mark_grant_error(db, server_id, user_sub, reason)
            await db.commit()
            logger.info(
                "mcp_oauth: callback denied server=%s user=%s: %s",
                server_id, user_sub, reason,
            )
            return _studio_redirect(server_id, "denied")

        if not code:
            await _mark_grant_error(
                db, server_id, user_sub, "callback missing authorization code"
            )
            await db.commit()
            return _studio_redirect(server_id, "error")

        # Re-discover to reach the token endpoint + validate the issuer.
        try:
            meta = await discover_oauth_metadata(server.server_url)
        except OAuthDiscoveryError as exc:
            await _mark_grant_error(
                db, server_id, user_sub, f"discovery failed on callback: {exc}"
            )
            await db.commit()
            return _studio_redirect(server_id, "error")

        # (contract §2 step 3) RFC 9207 issuer check when the AS supplied `iss`.
        if iss and meta.issuer and iss != meta.issuer:
            await _mark_grant_error(
                db, server_id, user_sub,
                f"issuer mismatch (got {iss!r}, expected {meta.issuer!r})",
            )
            await db.commit()
            return _studio_redirect(server_id, "error")

        client = await _load_client_for_exchange(db, server)
        if client is None:
            await _mark_grant_error(
                db, server_id, user_sub,
                "no OAuth client available for code exchange (was authorize run?)",
            )
            await db.commit()
            return _studio_redirect(server_id, "error")

        # (contract §2 step 4) Exchange the one-time code (+ PKCE verifier) for tokens.
        try:
            tokens = await exchange_code(
                meta,
                client,
                code=code,
                code_verifier=code_verifier,
                redirect_uri=settings.mcp_oauth_callback_url,
            )
        except OAuthFlowError as exc:
            await _mark_grant_error(
                db, server_id, user_sub, f"code exchange failed: {exc}"
            )
            await db.commit()
            return _studio_redirect(server_id, "error")

        if not tokens.refresh_token:
            # No refresh token → the proxy could never mint a fresh access token later.
            # Fail closed rather than record a grant that cannot be sustained.
            await _mark_grant_error(
                db, server_id, user_sub,
                "authorization server issued no refresh token (offline access "
                "required for sustained tool calls)",
            )
            await db.commit()
            return _studio_redirect(server_id, "error")

        # (contract §2 step 5) Store the refresh token ONLY behind the provider; the grant
        # holds a ref, never the token. HARD invariant: no token in the redirect.
        refresh_ref = mcp_oauth_refresh_ref(server_id, user_sub)
        await get_provider().put(
            refresh_ref, {"refresh_token": tokens.refresh_token}
        )
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(tokens.expires_in))
            if tokens.expires_in
            else None
        )
        await _upsert_grant(
            db,
            server_id,
            user_sub,
            status="authorized",
            credential_ref=str(refresh_ref),
            scopes=tokens.scope,
            token_expires_at=expires_at,
            last_error=None,
        )
        await db.commit()
        logger.info(
            "mcp_oauth: callback authorized server=%s user=%s (scopes=%s)",
            server_id, user_sub, tokens.scope,
        )
    except Exception as exc:  # noqa: BLE001 — the callback must NEVER 5xx to the browser
        logger.exception(
            "mcp_oauth: unexpected callback failure server=%s user=%s: %s",
            server_id, user_sub, exc,
        )
        try:
            await db.rollback()
            await _mark_grant_error(
                db, server_id, user_sub, f"unexpected callback error: {exc}"
            )
            await db.commit()
        except Exception:  # noqa: BLE001 — even the error write is best-effort
            logger.warning("mcp_oauth: could not record callback error grant")
        return _studio_redirect(server_id, "error")

    # (contract §2 step 6) Trigger discovery AS the authorizing user (best-effort — a
    # discovery failure does NOT un-authorize the grant, which is already committed).
    try:
        await _materialize_and_discover(
            db, server, acknowledge_schema_drift=False, user_sub=user_sub
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — never fail the callback on discovery
        logger.warning(
            "mcp_oauth: post-authorize discovery failed for %s (grant still "
            "authorized): %s",
            server_id, exc,
        )

    return _studio_redirect(server_id, "connected")


# ---------------------------------------------------------------------------
# GET /{server_id}/oauth/status  (contract §3)
# ---------------------------------------------------------------------------
@router.get(
    "/{server_id}/oauth/status",
    response_model=McpOAuthStatusResponse,
    summary="The calling user's OAuth grant status for a server (Studio badge)",
)
async def oauth_status(
    server_id: uuid.UUID,
    user: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> McpOAuthStatusResponse:
    """Report ONLY the caller's own grant (keyed on jwt.sub). A never-authorized user
    (no grant row) → a synthesized ``needs_auth``. Never returns another user's status,
    the token, or its ref.
    """
    caller_sub = user.get("sub")
    if not caller_sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token has no subject"
        )
    server = await _get_server(server_id, db)
    grant = await _get_grant(server_id, caller_sub, db)
    if grant is None:
        return McpOAuthStatusResponse(
            server_id=server_id,
            user_sub=caller_sub,
            status="needs_auth",
            external_auth_mode=server.external_auth_mode,
        )
    return McpOAuthStatusResponse(
        server_id=server_id,
        user_sub=caller_sub,
        status=grant.status,
        scopes=grant.scopes,
        token_expires_at=grant.token_expires_at,
        last_error=grant.last_error,
        external_auth_mode=server.external_auth_mode,
    )


# ---------------------------------------------------------------------------
# DELETE /{server_id}/oauth  (contract §4) — disconnect / revoke
# ---------------------------------------------------------------------------
@router.delete(
    "/{server_id}/oauth",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect / revoke the calling user's OAuth grant for a server",
)
async def disconnect_oauth(
    server_id: uuid.UUID,
    user: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Revoke ONLY the caller's own grant: best-effort revoke at the AS, delete the refresh
    token from the provider, and reset the grant to ``needs_auth``. Idempotent — a missing
    grant is a 204 no-op. Never touches another user's grant.
    """
    caller_sub = user.get("sub")
    if not caller_sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token has no subject"
        )
    server = await _get_server(server_id, db)  # 404 if the server is gone
    grant = await _get_grant(server_id, caller_sub, db)
    if grant is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    ref_str = grant.credential_ref
    if ref_str:
        ref = CredentialRef.parse(ref_str)
        # Best-effort revoke at the AS (needs the refresh token + client + revocation_ep).
        try:
            refresh = await get_provider().get(ref)
            client = await _load_client_for_exchange(db, server)
            meta = await discover_oauth_metadata(server.server_url)
            token = refresh.get("refresh_token") if isinstance(refresh, dict) else None
            if client is not None and token:
                await _best_effort_revoke(meta, client, token)
        except (CredentialNotFound, OAuthDiscoveryError) as exc:
            logger.info(
                "mcp_oauth: skipping AS revoke for server=%s user=%s: %s",
                server_id, caller_sub, exc,
            )
        except Exception as exc:  # noqa: BLE001 — revoke is a courtesy, never fatal
            logger.info(
                "mcp_oauth: AS revoke errored (ignored) server=%s user=%s: %s",
                server_id, caller_sub, exc,
            )
        # Durable disconnect: drop the refresh token from the provider store (idempotent).
        try:
            await get_provider().delete(ref)
        except Exception as exc:  # noqa: BLE001 — provider.delete is idempotent/best-effort
            logger.warning(
                "mcp_oauth: provider.delete(%s) failed (grant still reset): %s",
                ref_str, exc,
            )

    grant.status = "needs_auth"
    grant.credential_ref = None
    grant.token_expires_at = None
    grant.last_error = None
    grant.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(
        "mcp_oauth: disconnected server=%s user=%s", server_id, caller_sub
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

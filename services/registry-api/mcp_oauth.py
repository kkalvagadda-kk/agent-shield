"""
AgentShield Registry API — MCP OAuth 2.1 mechanics for external servers (Phase 4 WS-2).

Hand-rolled OAuth 2.1 (authorization-code + PKCE **S256**) with plain ``httpx`` and small
local Pydantic models. This module deliberately does **not** import the ``mcp`` SDK: the
SDK's ``OAuthClientProvider`` is an ``httpx.Auth`` hook that drives the whole flow inside a
single coroutine, and it cannot span the platform's authorize→browser→callback flow (two
independent HTTP requests, possibly on two registry-api replicas) — so Phase 4 reuses the
SDK's *mechanics* conceptually but orchestrates them here (research.md C6; plan.md
"Complexity Tracking").

Where each piece runs (research.md C3, HEADLINE): the interactive dance, the refresh-token
storage (via WS-1's ``credential_provider``), and the refresh→access exchange **with
rotation** all run in registry-api — the single writer that owns the store. The proxy only
*reads* a fresh access token. This module is the stateless mechanics library those callers
(``routers/mcp_oauth.py`` at T008, ``routers/internal_mcp.py`` at T010) invoke.

Cross-request ``state`` (research.md C5): the ``state`` carried through the upstream redirect
is a Fernet token — ``crypto.encrypt_json({server_id, user_sub, code_verifier, nonce, iat,
exp})`` — so any replica can verify it with the master key registry-api already holds, with
no pending table. ``make_state`` seals it; ``read_state`` decrypts + checks the TTL.

Refresh-token rotation (RFC 9700 / OAuth 2.1): ``refresh_access_token`` returns any ROTATED
refresh token the AS issues (``OAuthTokenResponse.refresh_token``); the caller MUST re-store
it — a stale refresh token after rotation locks the user out. When the AS does not rotate,
``.refresh_token`` is ``None`` and the caller keeps the prior token.

Spec references: MCP authorization spec (``.well-known/oauth-protected-resource`` →
``.well-known/oauth-authorization-server``), RFC 8414 (AS metadata), RFC 9728
(protected-resource metadata), RFC 7591 (Dynamic Client Registration), RFC 7636 (PKCE),
RFC 8707 (resource indicators), RFC 9700 (refresh-token rotation).

Design: docs/plan/mcp-tool-source-phase4/plan.md (Key Interfaces),
docs/plan/mcp-tool-source-phase4/contracts/registry-api-oauth-phase4.md.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse

import httpx
from pydantic import BaseModel, ConfigDict

import crypto

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
# Upstream calls are short JSON round-trips; keep the timeout tight so a slow/hostile
# authorization server can't stall a registry-api worker.
_HTTP_TIMEOUT = httpx.Timeout(15.0)
# Default Fernet-`state` TTL (seconds). Kept short — the value is dead ~60s after the
# redirect (research.md C5, default 600 per MCP_OAUTH_STATE_TTL_SECONDS). T008 threads the
# config-driven value through ``make_state(payload, ttl_seconds=...)``.
DEFAULT_STATE_TTL_SECONDS = 600


# ---------------------------------------------------------------------------
# Typed errors (each maps to a distinct HTTP/redirect outcome in the routers)
# ---------------------------------------------------------------------------
class OAuthFlowError(Exception):
    """A step in the OAuth dance failed (DCR, code exchange, or refresh).

    Carries the upstream reason (e.g. ``invalid_grant`` on refresh = a revoked/expired
    refresh token). The callback/internal endpoints map this to a fail-closed ``error``
    grant state, never a 5xx to the browser (contract §2/§5)."""


class OAuthDiscoveryError(Exception):
    """The MCP server / authorization server ``.well-known`` metadata could not be
    fetched or was malformed. The authorize endpoint maps this to ``502
    oauth_discovery_failed`` (contract §1)."""


class OAuthStateError(Exception):
    """The Fernet ``state`` failed verification (tampered / wrong key) or its TTL passed.
    The callback maps this to ``?oauth=invalid_state`` with **no** grant mutation
    (contract §2)."""


# ---------------------------------------------------------------------------
# Minimal Pydantic models (the spec-defined JSON shapes; extras ignored)
# ---------------------------------------------------------------------------
class OAuthMetadata(BaseModel):
    """Merged authorization-server + protected-resource metadata.

    ``authorization_endpoint`` / ``token_endpoint`` are required to run the flow;
    ``registration_endpoint`` gates RFC 7591 DCR; ``revocation_endpoint`` gates
    best-effort revoke on disconnect; ``resource`` is the RFC 8707 canonical server URL
    tokens are scoped to."""

    model_config = ConfigDict(extra="ignore")

    issuer: str | None = None
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    revocation_endpoint: str | None = None
    scopes_supported: list[str] | None = None
    resource: str | None = None


class OAuthClientInfo(BaseModel):
    """A client registration — DCR-issued or pre-registered. ``client_secret`` is None
    for a public (PKCE-only) client."""

    model_config = ConfigDict(extra="ignore")

    client_id: str
    client_secret: str | None = None


class OAuthTokenResponse(BaseModel):
    """An RFC 6749 token endpoint response.

    ``refresh_token`` is the ROTATED refresh token when the AS rotated it (RFC 9700),
    else None — on refresh the caller re-stores it when present and keeps the prior one
    when absent."""

    model_config = ConfigDict(extra="ignore")

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _origin(url: str) -> str:
    """The scheme://host[:port] origin of a URL (well-known documents live at the root)."""
    parts = urllib.parse.urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise OAuthDiscoveryError(f"invalid server URL for OAuth discovery: {url!r}")
    return f"{parts.scheme}://{parts.netloc}"


async def _try_get_json(client: httpx.AsyncClient, url: str) -> dict | None:
    """GET ``url`` and return its JSON dict, or None on any error (a probe — the caller
    falls through to the next candidate). Never raises."""
    try:
        resp = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _extract_oauth_error(resp: httpx.Response) -> str:
    """A human-readable reason from an OAuth error response (RFC 6749 §5.2)."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:200] or f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        err = body.get("error")
        desc = body.get("error_description")
        if err and desc:
            return f"{err}: {desc}"
        if err:
            return str(err)
    return f"HTTP {resp.status_code}"


def _as_metadata_candidates(as_url: str) -> list[str]:
    """Candidate well-known metadata URLs for an authorization-server issuer URL.

    Covers RFC 8414 (``.well-known/oauth-authorization-server`` at the origin root) and
    OIDC discovery (``.well-known/openid-configuration``, both root and issuer-path).
    Path-aware RFC 8414 insertion (issuer with a path segment) is a known simplification —
    ledgered; the stub AS + typical MCP servers serve at the origin root."""
    origin = _origin(as_url)
    issuer = as_url.rstrip("/")
    candidates = [
        f"{origin}/.well-known/oauth-authorization-server",
        f"{issuer}/.well-known/openid-configuration",
        f"{origin}/.well-known/openid-configuration",
    ]
    # De-dup, preserve order.
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


async def _token_request(
    meta: OAuthMetadata, client: OAuthClientInfo, data: dict
) -> OAuthTokenResponse:
    """POST an ``application/x-www-form-urlencoded`` grant to the token endpoint.

    A confidential client authenticates via ``client_secret_post`` (secret in the body);
    a public client (PKCE, no secret) omits it. Non-200 → ``OAuthFlowError`` carrying the
    upstream reason (``invalid_grant`` = revoked/expired on refresh)."""
    form = dict(data)
    if client.client_secret:
        form["client_secret"] = client.client_secret
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
        try:
            resp = await http.post(
                meta.token_endpoint,
                data=form,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OAuthFlowError(
                f"token endpoint unreachable ({meta.token_endpoint}): {exc}"
            ) from exc
    if resp.status_code != 200:
        raise OAuthFlowError(
            f"token endpoint returned {resp.status_code}: {_extract_oauth_error(resp)}"
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise OAuthFlowError("token endpoint returned a non-JSON body") from exc
    if not isinstance(payload, dict) or "access_token" not in payload:
        raise OAuthFlowError("token response missing access_token")
    return OAuthTokenResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# PKCE (RFC 7636, S256)
# ---------------------------------------------------------------------------
def generate_pkce_pair() -> tuple[str, str]:
    """Return a ``(code_verifier, code_challenge)`` PKCE S256 pair.

    The verifier is a high-entropy URL-safe secret stashed in the encrypted ``state``;
    the challenge (base64url(SHA-256(verifier)), no padding) rides in the authorization
    URL. Used by the authorize endpoint (T008)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
async def discover_oauth_metadata(server_url: str) -> OAuthMetadata:
    """Discover the OAuth metadata for an external MCP server.

    Per the MCP authorization spec: fetch ``{origin}/.well-known/oauth-protected-resource``
    (RFC 9728) for the resource identifier + its authorization server(s), then fetch each
    AS's ``.well-known/oauth-authorization-server`` (RFC 8414) / ``openid-configuration``
    for the endpoints. When no protected-resource document exists, fall back to the AS
    metadata co-located at the server's own origin. Returns the merged
    :class:`OAuthMetadata`. Raises :class:`OAuthDiscoveryError` when no usable AS metadata
    (with both an authorization and a token endpoint) can be found."""
    origin = _origin(server_url)
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT, follow_redirects=True
    ) as client:
        # 1. Protected-resource metadata (RFC 9728) — optional.
        prm = await _try_get_json(
            client, f"{origin}/.well-known/oauth-protected-resource"
        )
        resource: str | None = None
        prm_scopes: list[str] | None = None
        as_urls: list[str] = []
        if prm:
            resource = prm.get("resource") or server_url
            servers = prm.get("authorization_servers")
            if isinstance(servers, list):
                as_urls = [s for s in servers if isinstance(s, str)]
            scopes = prm.get("scopes_supported")
            if isinstance(scopes, list):
                prm_scopes = scopes

        # 2. Authorization-server metadata (RFC 8414 / OIDC). Try each advertised AS,
        #    then the resource's own origin (AS co-located with the server).
        as_meta: dict | None = None
        candidate_urls: list[str] = []
        for as_url in as_urls:
            candidate_urls.extend(_as_metadata_candidates(as_url))
        candidate_urls.extend(_as_metadata_candidates(origin))
        seen: set[str] = set()
        for url in candidate_urls:
            if url in seen:
                continue
            seen.add(url)
            as_meta = await _try_get_json(client, url)
            if as_meta:
                break

    if not as_meta:
        raise OAuthDiscoveryError(
            f"no OAuth authorization-server metadata found for {server_url!r} "
            "(.well-known/oauth-authorization-server / openid-configuration)"
        )

    auth_ep = as_meta.get("authorization_endpoint")
    token_ep = as_meta.get("token_endpoint")
    if not auth_ep or not token_ep:
        raise OAuthDiscoveryError(
            f"authorization-server metadata for {server_url!r} is missing an "
            "authorization_endpoint or token_endpoint"
        )

    as_scopes = as_meta.get("scopes_supported")
    return OAuthMetadata(
        issuer=as_meta.get("issuer"),
        authorization_endpoint=auth_ep,
        token_endpoint=token_ep,
        registration_endpoint=as_meta.get("registration_endpoint"),
        revocation_endpoint=as_meta.get("revocation_endpoint"),
        scopes_supported=(as_scopes if isinstance(as_scopes, list) else None)
        or prm_scopes,
        resource=resource or server_url,
    )


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------
async def register_client(meta: OAuthMetadata, redirect_uri: str) -> OAuthClientInfo:
    """Register a client with the AS via RFC 7591 Dynamic Client Registration.

    Raises :class:`OAuthFlowError` when the AS advertises no ``registration_endpoint``
    (the caller then falls back to a pre-registered client from the server's AuthConfig,
    or ``409 oauth_no_client`` — contract §1 step 4)."""
    if not meta.registration_endpoint:
        raise OAuthFlowError(
            "authorization server does not advertise a registration_endpoint "
            "(RFC 7591 Dynamic Client Registration unsupported)"
        )
    body = {
        "client_name": "AgentShield",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
        try:
            resp = await http.post(
                meta.registration_endpoint,
                json=body,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OAuthFlowError(
                f"client registration endpoint unreachable "
                f"({meta.registration_endpoint}): {exc}"
            ) from exc
    if resp.status_code not in (200, 201):
        raise OAuthFlowError(
            f"dynamic client registration failed "
            f"({resp.status_code}): {_extract_oauth_error(resp)}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise OAuthFlowError("client registration returned a non-JSON body") from exc
    client_id = data.get("client_id") if isinstance(data, dict) else None
    if not client_id:
        raise OAuthFlowError("client registration response missing client_id")
    return OAuthClientInfo(client_id=client_id, client_secret=data.get("client_secret"))


# ---------------------------------------------------------------------------
# Authorization URL (authorization-code + PKCE S256)
# ---------------------------------------------------------------------------
def build_authorization_url(
    meta: OAuthMetadata,
    client: OAuthClientInfo,
    *,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scope: str | None = None,
    resource: str | None = None,
) -> str:
    """Build the AS ``authorization_endpoint`` URL for the authorization-code + PKCE S256
    flow. Pure (no I/O): the caller generates the PKCE pair via :func:`generate_pkce_pair`
    (stashing the verifier in ``state``) and passes the S256 ``code_challenge`` here.

    ``resource`` (RFC 8707) defaults to the discovered ``meta.resource`` — the canonical
    server URL the token is scoped to."""
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if scope:
        params["scope"] = scope
    effective_resource = resource or meta.resource
    if effective_resource:
        params["resource"] = effective_resource
    sep = "&" if "?" in meta.authorization_endpoint else "?"
    return f"{meta.authorization_endpoint}{sep}{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# Token exchange + refresh (with rotation)
# ---------------------------------------------------------------------------
async def exchange_code(
    meta: OAuthMetadata,
    client: OAuthClientInfo,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> OAuthTokenResponse:
    """Exchange an authorization ``code`` (+ PKCE ``code_verifier``) for tokens at the
    token endpoint. Captures ``access_token``, ``refresh_token``, ``expires_in``,
    ``scope``. Raises :class:`OAuthFlowError` on failure (contract §2 step 4)."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client.client_id,
        "code_verifier": code_verifier,
    }
    if meta.resource:
        data["resource"] = meta.resource
    return await _token_request(meta, client, data)


async def refresh_access_token(
    meta: OAuthMetadata,
    client: OAuthClientInfo,
    *,
    refresh_token: str,
) -> OAuthTokenResponse:
    """Exchange a refresh token for a fresh access token (``grant_type=refresh_token``).

    ROTATION (RFC 9700 / OAuth 2.1): the returned :class:`OAuthTokenResponse` carries a
    ``refresh_token`` iff the AS rotated it — the caller MUST persist the rotated token
    (a stale refresh token after rotation locks the user out). When the AS does not
    rotate, ``refresh_token`` is None and the caller keeps the prior one. Raises
    :class:`OAuthFlowError` on failure (``invalid_grant`` = revoked/expired → the caller
    sets the grant to ``error`` and fails closed — contract §5 step 6)."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client.client_id,
    }
    if meta.resource:
        data["resource"] = meta.resource
    return await _token_request(meta, client, data)


# ---------------------------------------------------------------------------
# Cross-request state (Fernet-signed, TTL'd) — research.md C5
# ---------------------------------------------------------------------------
def make_state(payload: dict, ttl_seconds: int | None = None) -> str:
    """Seal ``payload`` (``{server_id, user_sub, code_verifier}``) into an opaque,
    tamper-proof, TTL'd ``state`` string.

    Adds a random ``nonce`` (if absent), an issued-at ``iat``, and an ``exp`` = iat + TTL,
    then Fernet-encrypts the envelope with the registry-api master key (``crypto``). Any
    replica can verify it — no pending table (research.md C5). T008 passes the
    config-driven ``ttl_seconds`` (``MCP_OAUTH_STATE_TTL_SECONDS``, default 600)."""
    ttl = DEFAULT_STATE_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
    now = int(time.time())
    envelope = {
        **payload,
        "nonce": payload.get("nonce") or secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + ttl,
    }
    return crypto.encrypt_json(envelope)


def read_state(state: str) -> dict:
    """Decrypt + verify a ``state`` produced by :func:`make_state`.

    Raises :class:`OAuthStateError` when the token fails Fernet verification (tampered /
    wrong key) or its ``exp`` has passed. On success returns the decrypted payload
    (``server_id``, ``user_sub``, ``code_verifier``, ``nonce``, ``iat``, ``exp``)."""
    try:
        data = crypto.decrypt_json(state)
    except Exception as exc:  # crypto raises RuntimeError on InvalidToken
        raise OAuthStateError(
            "state failed verification (tampered, wrong key, or malformed)"
        ) from exc
    exp = data.get("exp")
    if not isinstance(exp, (int, float)) or int(time.time()) > int(exp):
        raise OAuthStateError("state expired")
    return data

"""
OAuth access-token READER (WS-2 external OAuth — contracts/mcp-proxy-oauth-phase4.md §1/§2).

When an EXTERNAL MCP server is registered with `external_auth_mode == "oauth"`, the proxy
must present a short-lived upstream ACCESS token as the upstream `Authorization`. This
module is that token's sole custodian ON THE PROXY: it PULLS a fresh access token from
registry-api (which owns the refresh token and performs the refresh-with-rotation), caches
it in memory per `(server_id, user_sub)`, serves it until near expiry, and drops it on an
upstream 401 so the next call re-pulls.

HARD invariants (design §3b / plan invariants — violating any is a security fail):
  - The proxy holds NO refresh token, NO DB, NO master key. It receives only a short-lived
    access token and caches it IN MEMORY ONLY — NEVER persisted to disk (mirrors
    keycloak_client._token_cache; the cache dies with the replica). registry-api performs
    a live refresh on every pull, so a cached token is only ever a fresh access token.
  - The pull is authenticated with the proxy's OWN projected SA token (audience
    agentshield-registry-api), read FRESH from a file mount on every actual pull (projected
    tokens rotate ~hourly). It is NEVER the master AGENTSHIELD_ENCRYPTION_KEY and NEVER a
    k8s get-secrets API read.
  - Every failure fails CLOSED, loud: no user_sub → OAuthUserRequired; registry-api says
    needs_auth → OAuthAuthorizationRequired; status='error'/transport/non-2xx/missing token
    → OAuthTokenUnavailable. The caller (identity.resolve_headers → the endpoint) renders
    each as a 200 is_error body — NEVER a downgrade to static/service creds, NEVER a 5xx.

The per-(server,user) cache lives HERE (not in identity.py) deliberately: `invalidate` is
called from main.py as `oauth_tokens.invalidate(server_id, user_sub)`, so the cache and its
drop hook sit in the same low-level module the pull path uses — keeping identity.py a pure,
stateless credential-selection layer (symmetric to keycloak_client, whose per-audience
cache and `invalidate` live in that module for the same reason).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

import config

logger = logging.getLogger(__name__)


class OAuthUserRequired(Exception):
    """An OAuth server call arrived with no user identity (`user_sub`).

    Fail-closed: an OAuth access token is user-scoped, so the proxy must NOT present an
    unauthenticated / platform credential upstream for an OAuth server — it raises, and
    the caller renders a 200 is_error 'user identity required' body (never a downgrade)."""


class OAuthAuthorizationRequired(Exception):
    """registry-api reports the (server, user) grant is not usable (status='needs_auth').

    The user must (re-)authorize the server in Studio. Fail-closed: NEVER a silent
    unauthenticated upstream call — the caller renders a 200 is_error 're-authorize' body."""


class OAuthTokenUnavailable(Exception):
    """The access token could not be produced: status='error' (refresh/discovery failed),
    a non-2xx from registry-api, a transport failure, or an 'authorized' body missing the
    token. Distinct from OAuthAuthorizationRequired (a definitive 'not authorized' verdict):
    this is 'could not get an answer'. Also fail-closed to a 200 is_error body."""


# (server_id, user_sub) -> (access_token, exp_epoch_seconds). In-memory, per-replica.
# NEVER persisted (design invariant: the proxy stores no token). Keyed per user because
# OAuth access tokens are user-scoped (unlike keycloak_client's per-audience cache, whose
# client-credentials tokens are NOT user-scoped).
_access_cache: dict[tuple[str, str], tuple[str, int]] = {}


def _parse_expires_at(expires_at: object) -> int | None:
    """Parse registry-api's `expires_at` (ISO-8601 str, epoch number, or None) to epoch
    seconds. None / unparseable → None (the caller then caches for a short conservative
    window so it re-pulls soon rather than trusting a token forever)."""
    if expires_at is None:
        return None
    if isinstance(expires_at, (int, float)):
        return int(expires_at)
    if isinstance(expires_at, str):
        try:
            # FastAPI serializes the datetime to ISO-8601 (may end in 'Z').
            dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except ValueError:
            return None
    return None


def _read_sa_token() -> str:
    """Read the file-mounted projected SA token (audience agentshield-registry-api) fresh.

    Raises OAuthTokenUnavailable if the mount is missing/empty — the pull cannot
    authenticate to registry-api without it (fail closed, never an unauthenticated call)."""
    path = config.MCP_PROXY_REGISTRY_API_TOKEN_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError as exc:
        raise OAuthTokenUnavailable(
            f"registry-api SA token file not readable at {path}: {exc}"
        ) from exc
    if not token:
        raise OAuthTokenUnavailable(f"registry-api SA token file {path} is empty")
    return token


async def get_oauth_access_token(server_id: str, user_sub: str) -> str:
    """Return a fresh upstream OAuth access token for ``(server_id, user_sub)``.

    Serves a cached token while ``now < exp - config.OAUTH_ACCESS_TOKEN_CACHE_SKEW_SECONDS``.
    On a miss: read the projected SA token fresh, then
    ``POST config.REGISTRY_API_OAUTH_TOKEN_URL {server_id, user_sub}`` with
    ``Authorization: Bearer <SA token>`` (timeout ``REGISTRY_API_TIMEOUT_SECONDS``).
    registry-api resolves the refresh token, refreshes-with-rotation, and returns
    ``{status, access_token, expires_at}``.

    Raises:
        OAuthAuthorizationRequired — status == 'needs_auth' (the user must authorize).
        OAuthTokenUnavailable      — status == 'error', a non-2xx, a transport failure,
                                     a non-JSON body, or an 'authorized' body with no token.
    The caller renders each as a 200 is_error body — never a 5xx, never a downgrade.
    """
    key = (server_id, user_sub)
    now = int(time.time())
    cached = _access_cache.get(key)
    if cached is not None:
        token, exp = cached
        if now < exp - config.OAUTH_ACCESS_TOKEN_CACHE_SKEW_SECONDS:
            return token
        # Near/at expiry — drop and re-pull below.
        _access_cache.pop(key, None)

    sa_token = _read_sa_token()  # fresh file read per actual pull (projected tokens rotate)

    try:
        async with httpx.AsyncClient(timeout=config.REGISTRY_API_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                config.REGISTRY_API_OAUTH_TOKEN_URL,
                json={"server_id": server_id, "user_sub": user_sub},
                headers={"Authorization": f"Bearer {sa_token}"},
            )
    except Exception as exc:  # noqa: BLE001 — transport/DNS/timeout: registry-api unreachable
        raise OAuthTokenUnavailable(
            f"registry-api oauth token endpoint unreachable: {exc}"
        ) from exc

    if resp.status_code // 100 != 2:
        # 401/403 (SA token rejected — projected-token/audience misconfig) or 5xx. Never
        # log the body (it may echo a token) — status only.
        raise OAuthTokenUnavailable(
            f"registry-api oauth token endpoint returned HTTP {resp.status_code}"
        )

    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise OAuthTokenUnavailable(
            f"registry-api oauth token response was not JSON: {exc}"
        ) from exc

    status = payload.get("status")
    if status == "needs_auth":
        raise OAuthAuthorizationRequired(
            payload.get("detail") or "server needs (re-)authorization"
        )
    if status != "authorized":
        # status == 'error' (refresh/discovery failed) or any unexpected value → unavailable.
        raise OAuthTokenUnavailable(
            payload.get("detail") or f"oauth token status {status!r}"
        )

    access_token = payload.get("access_token")
    if not access_token:
        raise OAuthTokenUnavailable("authorized response had no access_token")

    exp = _parse_expires_at(payload.get("expires_at"))
    if exp is None:
        # No parseable exp — cache for a short conservative window so we re-pull soon rather
        # than trust a token forever (registry-api just performed a live refresh, so the
        # token is fresh; the short TTL only bounds a missing-exp response).
        exp = now + 60

    _access_cache[key] = (access_token, exp)
    return access_token


def invalidate(server_id: str, user_sub: str) -> None:
    """Drop the cached access token for ``(server_id, user_sub)`` — called on an upstream
    401 so the next call re-pulls a fresh token from registry-api (a rotated/expired cached
    token self-heals). A no-op if nothing is cached for that key."""
    _access_cache.pop((server_id, user_sub), None)

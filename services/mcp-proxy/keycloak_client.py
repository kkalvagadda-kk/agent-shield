"""
Keycloak client-credentials minter (WS-C service-identity — contracts/mcp-proxy-internal-phase2.md §2/§4).

When an internal MCP server is registered with `identity_mode == "service_identity"`,
the proxy presents the PLATFORM's own identity to it — a Keycloak service-account token
minted with the proxy's OWN confidential client. This module is the sole token custodian:
it performs the OAuth2 client-credentials grant, caches the resulting bearer per audience,
and exposes `invalidate` so a mid-life token rotation self-heals (main.py's 401-refresh).

HARD invariants (design §3b least-privilege — violating any is a security fail):
  - The client secret is read FRESH from a FILE mount (config.MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH)
    on every actual mint. It is NEVER read via a k8s get-secrets API call, and it is NEVER
    the master AGENTSHIELD_ENCRYPTION_KEY. The proxy holds no DB and no master key.
  - A missing secret file / unset token URL / non-2xx / unreachable Keycloak all raise
    RuntimeError. The caller (identity.resolve_headers → the endpoint) turns that into a
    200 error / health_detail body, never a 5xx.

The per-audience cache lives HERE (not in identity.py) deliberately: `invalidate` is called
from main.py as `keycloak_client.invalidate(audience)`, so the cache and its drop hook must
sit in the same low-level module the mint path uses — keeping identity.py a pure, stateless
credential-selection layer and avoiding a circular import (identity imports keycloak_client).
"""
from __future__ import annotations

import base64
import json
import logging
import time

import httpx

import config

logger = logging.getLogger(__name__)

# identity_audience (None == default audience) -> (access_token, exp_epoch_seconds).
# Client-credentials tokens are NOT user-scoped, so caching by audience is safe and
# lets a hot proxy reuse one token across many tools/call requests until near expiry.
# On-behalf-of tokens are per-user and NEVER cached here (and are blocked in Phase 2).
_token_cache: dict[str | None, tuple[str, int]] = {}


def _parse_jwt_exp(token: str) -> int | None:
    """Extract the 'exp' claim (epoch seconds) from a JWT WITHOUT verifying it.

    Mirrors authn._parse_token_exp — the signature is trusted because Keycloak just
    issued the token over TLS; we only read `exp` to bound the cache. Returns None if
    the payload is unparseable (→ caller treats the token as immediately-stale).
    """
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=="  # base64url, no padding — add generous padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return int(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 — a malformed token just means "no cached exp"
        return None


def _read_client_secret() -> str:
    """Read the file-mounted Keycloak client secret fresh. Raises RuntimeError if the
    mount is missing/empty — service-identity cannot proceed without it (fail closed)."""
    path = config.MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            secret = fh.read().strip()
    except OSError as exc:
        raise RuntimeError(
            f"keycloak client secret file not readable at {path}: {exc}"
        ) from exc
    if not secret:
        raise RuntimeError(f"keycloak client secret file {path} is empty")
    return secret


async def mint_service_account_token(audience: str | None = None) -> tuple[str, int]:
    """Return a valid service-account bearer for `audience` as (access_token, exp_epoch).

    Returns a cached token while it is still fresh (now < exp − KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS);
    otherwise performs the OAuth2 client-credentials grant against config.KEYCLOAK_TOKEN_URL
    (client_id=config.MCP_PROXY_KEYCLOAK_CLIENT_ID, client_secret read fresh from the file
    mount), includes the `audience` form param when non-None, parses `exp` from the returned
    JWT, caches, and returns it.

    Raises RuntimeError on: unset token URL, missing/empty secret file, non-2xx, unreachable
    Keycloak, or a response missing access_token. The caller surfaces this as a 200 error body.
    """
    now = int(time.time())
    cached = _token_cache.get(audience)
    if cached is not None:
        token, exp = cached
        if now < exp - config.KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS:
            return token, exp
        # Near/at expiry — drop and re-mint below.
        _token_cache.pop(audience, None)

    token_url = config.KEYCLOAK_TOKEN_URL
    if not token_url:
        raise RuntimeError(
            "KEYCLOAK_TOKEN_URL is not set — cannot mint a service-identity token"
        )

    client_secret = _read_client_secret()  # fresh file read per actual mint
    data = {
        "grant_type": "client_credentials",
        "client_id": config.MCP_PROXY_KEYCLOAK_CLIENT_ID,
        "client_secret": client_secret,
    }
    if audience:
        data["audience"] = audience

    try:
        async with httpx.AsyncClient(timeout=config.REGISTRY_API_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception as exc:  # noqa: BLE001 — transport/DNS/timeout: Keycloak unreachable
        raise RuntimeError(f"keycloak token endpoint unreachable: {exc}") from exc

    if resp.status_code // 100 != 2:
        # Never log the response body (may echo secrets) — status + reason only.
        raise RuntimeError(
            f"keycloak client-credentials grant failed: HTTP {resp.status_code}"
        )

    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"keycloak token response was not JSON: {exc}") from exc

    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("keycloak token response had no access_token")

    exp = _parse_jwt_exp(access_token)
    if exp is None:
        # No parseable exp — fall back to expires_in, else a short conservative window
        # so we re-mint soon rather than trust a token forever.
        expires_in = payload.get("expires_in")
        exp = now + int(expires_in) if expires_in else now + 60

    _token_cache[audience] = (access_token, exp)
    return access_token, exp


def invalidate(audience: str | None) -> None:
    """Drop the cached token for `audience` (called on an upstream 401 so the next mint
    fetches a fresh token). A no-op if nothing is cached for that audience."""
    _token_cache.pop(audience, None)

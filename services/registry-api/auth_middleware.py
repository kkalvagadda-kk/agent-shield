"""Optional JWT verification for registry-api.

Verifies Keycloak-issued JWTs using the realm's JWKS endpoint.
Returns the decoded claims on success, None when no token is present.
Raises HTTP 401 for invalid/expired tokens.

Usage:
    # Optional — returns None if no Authorization header
    user = Depends(get_optional_user)

    # Required — raises 401 if not authenticated
    user = Depends(require_user)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://agentshield-keycloak")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "agentshield")
JWKS_URL = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"

# ── JWKS cache ────────────────────────────────────────────────────────────────

_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_jwks_lock = asyncio.Lock()
JWKS_TTL = 300  # re-fetch every 5 minutes


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_fetched_at
    async with _jwks_lock:
        if time.monotonic() - _jwks_fetched_at < JWKS_TTL and _jwks_cache:
            return _jwks_cache
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(JWKS_URL)
                r.raise_for_status()
                _jwks_cache = r.json()
                _jwks_fetched_at = time.monotonic()
                return _jwks_cache
        except Exception as exc:
            logger.warning("Failed to fetch JWKS from %s: %s", JWKS_URL, exc)
            return _jwks_cache  # return stale cache if available


# ── Token verification ────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


async def _decode_token(token: str) -> dict | None:
    from jose import JWTError, jwt  # lazy import; jose is already in requirements

    try:
        jwks = await _get_jwks()
        if not jwks:
            logger.warning("JWKS empty — cannot verify token")
            return None
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience="account",
            options={"verify_aud": False},  # KC tokens use client-id or "account"
        )
        return claims
    except JWTError as exc:
        logger.debug("JWT verification failed: %s", exc)
        return None


async def get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict | None:
    """Returns decoded JWT claims or None if no/invalid token."""
    if creds is None:
        return None
    claims = await _decode_token(creds.credentials)
    return claims


async def require_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Returns decoded JWT claims; raises 401 if missing or invalid."""
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = await _decode_token(creds.credentials)
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims

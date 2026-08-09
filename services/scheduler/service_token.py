"""Client-credentials token minting for an internal service caller.

Identity propagation **Phase 3** (`docs/design/identity-propagation-architecture.md` §4.5).

WHY
---
Three services told registry-api who they were with a string the wire carried in plain
text and nothing checked:

    scheduler        POST /internal/runs/start   {"run_by": "serviceaccount:scheduler"}
    event-gateway    POST /internal/runs/start   {"run_by": "serviceaccount:event-gateway"}
    eval-runner      POST /playground/runs       X-User-Sub: eval-runner

Any caller with VPC network reach could send any of those. `eval-runner` in particular
was a member of `playground.py::_SERVICE_IDENTITIES`, which skipped BOTH the role gate
and the agent-owner check — so the string was not merely an audit label, it was an
authorization bypass.

This mints a real Keycloak `client_credentials` token instead. The receiving side reads
`azp` from the **verified** token (`auth_middleware.is_trusted_service`), and `azp` is
inside the RS256 signature — reproducing it requires this client's secret.

VENDORED, NOT SHARED
--------------------
`services/*` and `sdk/` have no shared package; each image vendors its deps. This file is
byte-identical in `services/scheduler/`, `services/event-gateway/` and
`services/eval-runner/`, the same arrangement `run_context.py` uses. Keep them identical.

SYNC AND ASYNC
--------------
The three callers are not written alike — scheduler dispatches from an APScheduler
callback with `httpx.post` (sync), event-gateway and eval-runner use `httpx.AsyncClient`.
Both entry points are provided rather than forcing one style on the other; a given
process only ever exercises one of them, and the cache below is per-process either way.

FAIL CLOSED
-----------
`token()` raises when it cannot mint. It deliberately does NOT return None-and-continue:
a dispatch without a credential is a request registry-api will reject anyway, so
"continue without a token" would convert an authentication failure into a confusing
downstream 401 attributed to the wrong layer. Callers catch, log, and skip the fire —
which is the same path an unreachable registry-api already takes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://agentshield-keycloak")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "agentshield")
TOKEN_URL = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"

# Which client this process authenticates as. Set per-Deployment in the chart; there is
# no default, because a wrong-but-plausible default would authenticate one service as
# another and the receiving side would believe it.
CLIENT_ID = os.getenv("SERVICE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SERVICE_CLIENT_SECRET", "")

# Refresh this many seconds before the token actually expires, so a token minted just
# before a slow request does not expire mid-flight. Mirrors keycloak_client._admin_token.
_EXPIRY_SKEW = 30.0

_cache: dict[str, Any] = {}
_sync_lock = threading.Lock()
_async_lock = asyncio.Lock()


class ServiceTokenError(RuntimeError):
    """Could not obtain a service token — the caller must NOT proceed unauthenticated."""


def _cached() -> str | None:
    tok = _cache.get("token")
    if tok and time.time() < _cache.get("expires_at", 0.0) - _EXPIRY_SKEW:
        return tok
    return None


def _store(body: dict) -> str:
    token = body.get("access_token")
    if not token:
        raise ServiceTokenError("Keycloak returned no access_token")
    _cache["token"] = token
    _cache["expires_at"] = time.time() + float(body.get("expires_in", 60))
    return token


def _form() -> dict[str, str]:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ServiceTokenError(
            "SERVICE_CLIENT_ID / SERVICE_CLIENT_SECRET are not set — this process cannot "
            "prove which service it is. Check the Deployment env and the "
            "agentshield-service-clients Secret."
        )
    return {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }


def token() -> str:
    """Sync: a valid access token for this service. Raises ServiceTokenError."""
    cached = _cached()
    if cached:
        return cached
    with _sync_lock:
        cached = _cached()  # another thread may have minted while we waited
        if cached:
            return cached
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(TOKEN_URL, data=_form())
                resp.raise_for_status()
                return _store(resp.json())
        except ServiceTokenError:
            raise
        except Exception as exc:
            raise ServiceTokenError(f"client_credentials mint failed for {CLIENT_ID!r}: {exc}") from exc


async def token_async() -> str:
    """Async twin of `token()`. Raises ServiceTokenError."""
    cached = _cached()
    if cached:
        return cached
    async with _async_lock:
        cached = _cached()
        if cached:
            return cached
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(TOKEN_URL, data=_form())
                resp.raise_for_status()
                return _store(resp.json())
        except ServiceTokenError:
            raise
        except Exception as exc:
            raise ServiceTokenError(f"client_credentials mint failed for {CLIENT_ID!r}: {exc}") from exc


def auth_header() -> dict[str, str]:
    """Sync convenience: the Authorization header dict. Raises ServiceTokenError."""
    return {"Authorization": f"Bearer {token()}"}


async def auth_header_async() -> dict[str, str]:
    """Async convenience: the Authorization header dict. Raises ServiceTokenError."""
    return {"Authorization": f"Bearer {await token_async()}"}

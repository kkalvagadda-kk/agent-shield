"""In-pod Bearer auth for e2e drivers — mints on demand, refreshes before expiry.

WHY THIS EXISTS (and why a static token is not enough)
------------------------------------------------------
Keycloak issues access tokens with `expires_in = 300` (5 minutes). The long
suites run FAR past that: suite-71 drives create + deploy + park + resume + four
orchestration modes + alerting and takes ~25 minutes. A token minted once in
bash at suite start is dead by the time the later cases run.

That is exactly how T-S71-005 failed after the first auth fix: every earlier case
passed, then the two trigger creations at the ~25-minute mark returned

    POST /api/v1/agents/s71-fail-.../triggers  401 Unauthorized

and the suite reported `pos_run=None` — an alerting failure, three layers away
from the real cause (an expired token). Same misdirection the suites already
suffered from; a shorter version of the bug this whole helper exists to fix.

So authentication is a per-REQUEST concern, not a per-RUN one. `BearerAuth` is an
`httpx.Auth`, which httpx re-evaluates on every request for both sync and async
clients — so a 25-minute driver refreshes ~5 times without any suite knowing.

USAGE (driver side, after e2e_install_pyauth has copied this into the pod):

    import sys; sys.path.insert(0, "/tmp")
    from e2e_auth import BearerAuth

    c = httpx.AsyncClient(base_url=BASE, headers=HDR, auth=BearerAuth(), timeout=60)

Keep `X-User-Sub` in `headers` — it is the audit stamp (`armed_by`, `created_by`)
and is NOT interchangeable with authentication. Conflating the two is what left
fifteen suites dead; see docs/bugs/trigger-e2e-suites-dead-since-require-user.md.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

import httpx

KC_URL = os.environ.get(
    "E2E_KC_URL",
    "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token",
)
KC_USER = os.environ.get("E2E_KC_USER", "platform-admin")
KC_PASS = os.environ.get("E2E_KC_PASS", "PlatformAdmin2024")
KC_CLIENT = os.environ.get("E2E_KC_CLIENT", "agentshield-studio")

# Refresh this many seconds BEFORE the token actually expires. A request that
# starts valid can still land after expiry on a slow call (deploys, eval Jobs),
# so the margin is generous relative to the 300s lifespan.
_SKEW_SECONDS = 60

_cached_token: str | None = None
_cached_until: float = 0.0


def mint() -> str:
    """Fetch a fresh access token. Raises with a message that NAMES THE CAUSE."""
    data = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": KC_CLIENT,
            "username": KC_USER,
            "password": KC_PASS,
        }
    ).encode()
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(KC_URL, data=data), timeout=15
        ).read()
    except Exception as exc:  # noqa: BLE001 — re-raised with context below
        raise RuntimeError(
            f"could not obtain a Keycloak token for {KC_USER} at {KC_URL}: {exc}. "
            "Trigger CRUD requires a real JWT since 76b3570 — X-User-Sub alone returns 401."
        ) from exc
    body = json.loads(raw)
    global _cached_token, _cached_until
    _cached_token = body["access_token"]
    _cached_until = time.time() + max(0, int(body.get("expires_in", 300)) - _SKEW_SECONDS)
    return _cached_token


def bearer() -> str:
    """A currently-valid token, minting only when the cached one is near expiry."""
    if _cached_token is None or time.time() >= _cached_until:
        return mint()
    return _cached_token


class BearerAuth(httpx.Auth):
    """Attaches a fresh Bearer to every request.

    httpx calls `auth_flow` per request for BOTH sync and async clients, which is
    the whole point — a static `headers={"Authorization": ...}` is evaluated once
    at client construction and cannot survive a suite longer than the token.
    """

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {bearer()}"
        yield request

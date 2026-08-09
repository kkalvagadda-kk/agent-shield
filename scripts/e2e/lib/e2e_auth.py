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

ONE DRIVER, SEVERAL PERSONAS (added 2026-08-09)
----------------------------------------------
`BearerAuth()` with no arguments is platform-admin, as it always was. Pass
credentials to authenticate as somebody else:

    admin    = httpx.AsyncClient(base_url=BASE, auth=BearerAuth(), timeout=60)
    reviewer = httpx.AsyncClient(base_url=BASE, timeout=60,
                                 auth=BearerAuth("s70-reviewer", "Persona2024!"))

WHY THIS WAS NEEDED. `suite-70`'s T-S70-003 asserts that a NON-reviewer's decide is
rejected 403. It expressed "non-reviewer" as a per-request `X-User-Sub` header while
the client carried `auth=BearerAuth()`. httpx merges per-request headers over client
headers but RE-APPLIES `auth=` on every request — so the call went out as
platform-admin, `caller_is_admin` was true, the 403 branch was skipped, and the case
returned 200. Once identity comes from the credential (identity P3), a suite cannot
express a second persona with a header; it needs a second CREDENTIAL. The token cache
therefore had to stop being module-global, because one shared cache can only ever hold
one identity.

Each instance keeps its own token and refreshes on its own clock, so a long driver can
hold an admin client and two persona clients at once and all three stay valid past the
300s token lifetime.
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


def _mint_for(username: str, password: str, client_id: str) -> tuple[str, float]:
    """Password-grant a token for ONE named identity. Returns (token, valid_until).

    Pure: it touches no module state, so two identities cannot clobber each other's
    token. Raises with a message that NAMES THE CAUSE and the USER, because "401" three
    layers away from an expired or misspelled persona is the failure mode this whole
    file exists to prevent.
    """
    data = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
        }
    ).encode()
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(KC_URL, data=data), timeout=15
        ).read()
    except Exception as exc:  # noqa: BLE001 — re-raised with context below
        raise RuntimeError(
            f"could not obtain a Keycloak token for {username} at {KC_URL}: {exc}. "
            "Trigger CRUD requires a real JWT since 76b3570 — X-User-Sub alone returns 401. "
            "If this is a persona, e2e_ensure_persona must have created it FIRST."
        ) from exc
    body = json.loads(raw)
    valid_until = time.time() + max(0, int(body.get("expires_in", 300)) - _SKEW_SECONDS)
    return body["access_token"], valid_until


def mint() -> str:
    """Fetch a fresh platform-admin access token (module-level cache)."""
    global _cached_token, _cached_until
    _cached_token, _cached_until = _mint_for(KC_USER, KC_PASS, KC_CLIENT)
    return _cached_token


def bearer() -> str:
    """A currently-valid platform-admin token, minting only when near expiry."""
    if _cached_token is None or time.time() >= _cached_until:
        return mint()
    return _cached_token


def sub_of(token: str) -> str:
    """The `sub` INSIDE a token — never a value the caller chose.

    A suite that invents a sub and types it into a header is not testing the path a
    human takes; since identity P3 such a sub cannot authenticate at all. Read the
    subject out of the credential instead. No signature check here on purpose: the
    server verifies, this is only for assertions.
    """
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    import base64  # local: keeps the module's import surface unchanged for old callers

    return json.loads(base64.urlsafe_b64decode(payload))["sub"]


class BearerAuth(httpx.Auth):
    """Attaches a fresh Bearer to every request.

    httpx calls `auth_flow` per request for BOTH sync and async clients, which is
    the whole point — a static `headers={"Authorization": ...}` is evaluated once
    at client construction and cannot survive a suite longer than the token.

    `BearerAuth()` is platform-admin and shares the module cache, so every existing
    caller is unchanged. Passing a username switches to a PER-INSTANCE identity and a
    PER-INSTANCE cache — see the module docstring for why a header cannot do this job.
    """

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        client_id: str | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._client_id = client_id or KC_CLIENT
        # Per-instance cache, used only when this instance names its own user.
        self._token: str | None = None
        self._until: float = 0.0
        if username is not None and password is None:
            raise ValueError(
                f"BearerAuth({username!r}) needs a password — a username alone would "
                "silently fall back to platform-admin, which is exactly the bug that "
                "made suite-70's non-reviewer case authenticate as an admin."
            )

    @property
    def username(self) -> str:
        """Who this instance authenticates as. Useful in failure messages."""
        return self._username or KC_USER

    def token(self) -> str:
        """A currently-valid token for THIS instance's identity."""
        if self._username is None:
            return bearer()  # platform-admin, module cache — unchanged behaviour
        if self._token is None or time.time() >= self._until:
            self._token, self._until = _mint_for(
                self._username, self._password, self._client_id
            )
        return self._token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token()}"
        yield request

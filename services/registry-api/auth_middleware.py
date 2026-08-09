"""Optional JWT verification for registry-api.

Verifies Keycloak-issued JWTs using the realm's JWKS endpoint.
Returns the decoded claims on success, None when no token is present.
Raises HTTP 401 for invalid/expired tokens.

Usage:
    # Optional — returns None if no Authorization header
    user = Depends(get_optional_user)

    # Required — raises 401 if not authenticated
    user = Depends(require_user)

    # Identity-propagation Phase 3 — who is calling, as ONE answer
    caller = Depends(resolve_caller)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Literal, Mapping

import httpx
from fastapi import Depends, HTTPException, Query, Request, status
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
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Returns decoded JWT claims; raises 401 if missing or invalid.

    Supports two token sources:
    1. Authorization: Bearer <token> header (standard)
    2. ?token=<token> query param (for EventSource/SSE which can't send headers)
    """
    raw_token: str | None = None
    if creds is not None:
        raw_token = creds.credentials
    else:
        # Fall back to ?token= query param (EventSource compatibility)
        raw_token = request.query_params.get("token")

    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = await _decode_token(raw_token)
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


# ── Verifiable service identity (identity-propagation Phase 3) ───────────────
#
# WHY THIS EXISTS
# ---------------
# Three internal callers — eval-runner, scheduler, event-gateway — asserted who they
# were with a plain string nobody checked: `X-User-Sub: eval-runner` on ~18 eval-runner
# calls, and `"run_by": "serviceaccount:scheduler"` in the body of every POST to
# `/internal/runs/start`. Any caller with VPC network reach could send either.
#
# `azp` ("authorized party") is the answer, and specifically NOT a claim the caller
# chooses: Keycloak sets it to the client-id the token was ISSUED TO, and it sits inside
# the RS256 signature. Reproducing it requires the client's secret. So the same JWKS
# verification `require_user` already performs is what makes this unforgeable — there is
# no second crypto path to get wrong.
#
# NOTE this is AUTHENTICATION only. "Which service is this?" is a different question from
# "may it do this?" — see identity-propagation-architecture.md §4.2. A verified
# `scheduler` token says the scheduler is calling; it does not say the run it wants to
# start is one the scheduler may start. Endpoints still apply their own rules.
_TRUSTED_SERVICE_CLIENTS = frozenset({"eval-runner", "scheduler", "event-gateway"})


def is_trusted_service(claims: Mapping[str, Any] | None) -> str | None:
    """The trusted service this token was issued to, or None.

    Pure function over ALREADY-VERIFIED claims — it performs no I/O and no signature
    check of its own, because it must never be reachable with an unverified payload.
    Callers get claims from `_decode_token` (via `get_optional_user` / `resolve_caller`),
    which is the only thing in this module that turns a string into claims.
    """
    if not claims:
        return None
    azp = str(claims.get("azp") or "")
    return azp if azp in _TRUSTED_SERVICE_CLIENTS else None


@dataclass(frozen=True)
class Caller:
    """WHO is calling — one answer, produced in one place.

    Before this existed each endpoint invented its own resolution and they disagreed:

      * `approvals.decide_approval`  `x_user_sub or x_user_id or JWT.sub or body.reviewer_id`
      * `playground.create_playground_run`  `JWT.sub or x_user_sub or "dev"`, plus a
        `_SERVICE_IDENTITIES = {"eval-runner"}` string set that skipped the role gate
      * `internal.start_internal_run`  no authentication at all
      * 15 other routers  some mix of the above

    The first of those put a PLAINTEXT HEADER AHEAD OF THE VERIFIED TOKEN, so a request
    carrying a valid JWT could still be attributed to whoever the header named. That is
    strictly worse than having no auth, because it looks authenticated.

    `kind` makes the three cases explicit instead of leaving each reader to infer them
    from which field happens to be truthy — a service is not "a user whose sub is in a
    magic set", it is its own kind. Readers branch on `kind`; nothing sniffs strings.

    Attributes:
        kind:         "user" (a human's verified token), "service" (a verified trusted
                      service client), or "anonymous" (no/invalid credential).
        sub:          the verified `sub`. Empty for anonymous. For a service this is the
                      service ACCOUNT's subject — never a human.
        service_name: the trusted client-id, "" unless kind == "service".
        claims:       the full verified claims, or None when anonymous.
    """

    kind: Literal["user", "service", "anonymous"]
    sub: str
    service_name: str
    claims: dict[str, Any] | None

    @property
    def is_service(self) -> bool:
        return self.kind == "service"

    @property
    def is_authenticated(self) -> bool:
        return self.kind != "anonymous"


ANONYMOUS = Caller(kind="anonymous", sub="", service_name="", claims=None)


def caller_from_claims(claims: Mapping[str, Any] | None) -> Caller:
    """Classify already-verified claims. Separate from the dependency so callers that
    already hold claims (and endpoints under test) can classify without a Request."""
    if not claims:
        return ANONYMOUS
    service = is_trusted_service(claims)
    sub = str(claims.get("sub") or "")
    if service is not None:
        return Caller(kind="service", sub=sub, service_name=service, claims=dict(claims))
    if not sub:
        # A verified token with no subject identifies nobody. Treating it as a user
        # would produce an empty-string identity, which is exactly the value OPA's
        # Gate 6 floor exists to reject.
        return ANONYMOUS
    return Caller(kind="user", sub=sub, service_name="", claims=dict(claims))


async def resolve_caller(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Caller:
    """FastAPI dependency — resolve the caller from the credential, and ONLY from it.

    Deliberately reads no `X-User-Sub` / `X-User-Id` header and no request body. An
    identity a caller can type is not an identity; the credential is the identity.
    Never raises: endpoints decide whether `anonymous` is acceptable for them, so that
    "is a credential required here?" stays a per-endpoint decision visible at the
    endpoint, rather than being hidden in this dependency.
    """
    raw_token = creds.credentials if creds is not None else request.query_params.get("token")
    if not raw_token:
        return ANONYMOUS
    return caller_from_claims(await _decode_token(raw_token))

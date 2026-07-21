"""
AuthN — verify an inbound `Authorization: Bearer <token>` via K8s TokenReview.

Every /internal/* request MUST carry a projected SA token whose audience is
MCP_PROXY_AUDIENCE (design §3b). Verification:
  1. TokenReview (audience-scoped) → require status.authenticated == true
     AND MCP_PROXY_AUDIENCE ∈ status.audiences.
  2. Extract status.user.username = system:serviceaccount:<ns>:<sa> as the caller.

Positive reviews are cached per replica, keyed by sha256(token), until the token's
`exp` (parsed base64url from the JWT payload the same way opa_client._parse_sa_subject
does) — so a hot pod does not hit the K8s API on every call. Missing / invalid /
wrong-audience → returns None → the caller maps to 401.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time

from kubernetes.client.rest import ApiException

import config
import k8s_client

logger = logging.getLogger(__name__)

# token_hash -> (sa_subject, expiry_epoch_seconds). Positive reviews only.
_review_cache: dict[str, tuple[str, float]] = {}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_token_exp(token: str) -> float | None:
    """Extract the 'exp' claim (epoch seconds) from a JWT without verifying it.

    Mirrors opa_client._parse_sa_subject's base64url payload decode. Signature is
    validated by TokenReview against the K8s API — we only read exp to bound the
    positive-review cache. Returns None if unparseable.
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
        return float(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 — a malformed token just means "no cached exp"
        return None


def _cache_ttl_bound(token: str) -> float:
    """Compute the positive-review cache expiry: min(token exp, now + max cap)."""
    now = time.time()
    cap = now + config.TOKEN_REVIEW_CACHE_MAX_TTL_SECONDS
    exp = _parse_token_exp(token)
    if exp is None:
        return cap
    return min(exp, cap)


async def verify_bearer_token(token: str) -> str | None:
    """Verify a bearer token and return its SA subject, or None if invalid.

    None covers: empty/malformed token, TokenReview not authenticated, or the
    token's audiences not including MCP_PROXY_AUDIENCE. The caller turns None into
    a 401 — this function never raises for an auth failure.
    """
    if not token:
        return None

    key = _token_hash(token)
    now = time.time()

    cached = _review_cache.get(key)
    if cached is not None:
        sa_subject, expiry = cached
        if now < expiry:
            return sa_subject
        # Expired — drop and re-review.
        _review_cache.pop(key, None)

    try:
        review = await k8s_client.create_token_review(token)
    except ApiException as exc:
        # A TokenReview API error (not an auth verdict) — cannot assert identity,
        # so treat as unauthenticated (401). Never cache a failure.
        logger.warning("mcp-proxy authn: TokenReview API error: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp-proxy authn: TokenReview unexpected error: %s", exc)
        return None

    status = getattr(review, "status", None)
    if status is None or not getattr(status, "authenticated", False):
        return None

    audiences = getattr(status, "audiences", None) or []
    if config.MCP_PROXY_AUDIENCE not in audiences:
        # Right token, wrong audience (e.g. an OPA-audience token) — reject.
        logger.warning(
            "mcp-proxy authn: token audiences %s missing %s",
            audiences,
            config.MCP_PROXY_AUDIENCE,
        )
        return None

    user = getattr(status, "user", None)
    sa_subject = getattr(user, "username", "") if user is not None else ""
    if not sa_subject:
        return None

    _review_cache[key] = (sa_subject, _cache_ttl_bound(token))
    return sa_subject

"""
AuthZ — the coarse team-scope floor (design §3b).

This is defense-in-depth, NOT an OPA re-implementation: OPA already ran the
fine-grained per-call allow/require_approval inside governed_tool before the proxy
was reached. This floor only stops a pod that BYPASSED governed_tool from reaching
an arbitrary server's credentials.

To keep the proxy off the DB, the caller's team is derived from its SA subject's
namespace — system:serviceaccount:agents-{team}:{sa} → {team} (research.md B15). A
non-`agents-` namespace fails the floor. `owner_team` comes from the per-server
Secret (credentials.py). Fast path: caller_team == owner_team → allow, zero hops.
Cross-team → one NetworkPolicy-trusted registry-api callback, short-TTL cached.
"""
from __future__ import annotations

import logging
import time

import httpx

import config

logger = logging.getLogger(__name__)

_AGENTS_NS_PREFIX = "agents-"

# (caller_sa_subject, server_id, mcp_tool_name) -> (allowed, expiry_epoch_seconds).
# Only cross-team decisions are cached; own-team is answered locally with no hop.
_authz_cache: dict[tuple[str, str, str], tuple[bool, float]] = {}


def team_from_sa_subject(sa_subject: str) -> str | None:
    """Extract {team} from system:serviceaccount:agents-{team}:{sa}, else None.

    Returns None for any subject whose namespace is not of the `agents-{team}`
    form — the floor treats that as "no derivable team" → deny.
    """
    if not sa_subject:
        return None
    parts = sa_subject.split(":")
    # Expected shape: ["system", "serviceaccount", "<namespace>", "<sa-name>"]
    if len(parts) != 4 or parts[0] != "system" or parts[1] != "serviceaccount":
        return None
    namespace = parts[2]
    if not namespace.startswith(_AGENTS_NS_PREFIX):
        return None
    team = namespace[len(_AGENTS_NS_PREFIX):]
    return team or None


async def _cross_team_allowed(
    caller_sa_subject: str, server_id: str, mcp_tool_name: str
) -> bool:
    """Ask registry-api whether this caller may use this cross-team tool.

    The endpoint is NetworkPolicy-trusted and returns only a boolean (never a
    secret or URL) — so it needs no TokenReview. Any transport failure fails
    closed (deny): a floor that cannot confirm access must not grant it.
    """
    url = f"{config.REGISTRY_API_URL}/api/v1/internal/mcp/authorize-tool-call"
    payload = {
        "caller_sa_subject": caller_sa_subject,
        "server_id": server_id,
        "mcp_tool_name": mcp_tool_name,
    }
    try:
        async with httpx.AsyncClient(timeout=config.REGISTRY_API_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mcp-proxy authz: cross-team callback failed for %s/%s: %s — deny",
            server_id,
            mcp_tool_name,
            exc,
        )
        return False
    return bool(data.get("allowed", False))


async def authorize_tool_call(
    caller_sa_subject: str,
    server_id: str,
    mcp_tool_name: str,
    owner_team: str | None,
) -> bool:
    """The §3b team floor for a single tools/call. True == allowed.

    Own-team is a zero-hop local decision; cross-team consults registry-api and
    caches the verdict per (subject, server, tool) with a short TTL.
    """
    caller_team = team_from_sa_subject(caller_sa_subject)
    if caller_team is None:
        # Not an agents-{team} pod → cannot belong to any team → deny.
        return False

    # Fast path: same team owns the server. Zero hops.
    if owner_team is not None and caller_team == owner_team:
        return True

    key = (caller_sa_subject, str(server_id), mcp_tool_name)
    now = time.time()
    cached = _authz_cache.get(key)
    if cached is not None:
        allowed, expiry = cached
        if now < expiry:
            return allowed
        _authz_cache.pop(key, None)

    allowed = await _cross_team_allowed(caller_sa_subject, str(server_id), mcp_tool_name)
    _authz_cache[key] = (allowed, now + config.AUTHZ_CACHE_TTL_SECONDS)
    return allowed

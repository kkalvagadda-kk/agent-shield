"""RunContext — VENDORED COPY. Keep byte-identical to services/registry-api/run_context.py.

This copy exists because no shared package spans `services/*` and `sdk/` — each vendors
its own dependencies (design §4.3). `suite-99` asserts the three copies agree; three
copies that drift are worse than one that is awkward.

THIS PROCESS MUST NEVER MINT A ROOT CONTEXT. The runner cannot authenticate a human, so
anything it minted from scratch would be self-asserted identity wearing a valid signature
— exactly the forgeable-attribution defect the design closes. It VERIFIES what
registry-api minted and EXTENDS it with its own hop. `mint` is present only because
`extend` re-signs through it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# The header every internal hop carries. One name, not a per-path zoo.
RCT_HEADER = "X-AgentShield-Run-Context"

# Cap on actor_chain. A handoff loop would otherwise grow the token without bound and
# turn a header into a denial-of-service. 20 is far above any real orchestration depth.
MAX_ACTOR_CHAIN = 20

# Default lifetime. Short on purpose: this token rides internal hops that complete in
# seconds. Anything that must outlive a human pause is re-hydrated from the durable
# anchor instead (§4.4) — the token is NOT the system of record for identity.
DEFAULT_TTL_SECONDS = 900

_ENV_KEY = "AGENTSHIELD_INTERNAL_SIGNING_KEY"


class RunContextError(Exception):
    """Verification failed. Callers must treat this as 'no identity', never as 'trusted'."""


@dataclass
class RunContext:
    """Who authorized this run, and whose authority it still carries N hops later."""

    # The authorizing human's Keycloak sub. Empty ONLY for a standing daemon with no
    # human owner on record — never as a fallback for "we lost it". A caller that cannot
    # name the user must not mint a context with "" to make an error go away.
    user_sub: str = ""
    user_team: str = ""
    # Agent names already traversed, root-first. Appended at each hop, never replaced:
    # the point is lineage, and a replaced chain is a lost audit trail.
    actor_chain: list[str] = field(default_factory=list)
    # True when the ACTING principal is a verified service rather than a person. Set only
    # from a verified service JWT (§4.5) — never from client input, which is the
    # forgeable-attribution defect this whole document exists to close.
    is_service_call: bool = False
    service_name: str | None = None
    origin: str = ""  # playground | production | eval | schedule | webhook

    def to_claims(self) -> dict:
        return asdict(self)

    @classmethod
    def from_claims(cls, claims: dict) -> "RunContext":
        chain = claims.get("actor_chain") or []
        if not isinstance(chain, list):
            raise RunContextError("actor_chain must be a list")
        return cls(
            user_sub=str(claims.get("user_sub") or ""),
            user_team=str(claims.get("user_team") or ""),
            actor_chain=[str(a) for a in chain],
            is_service_call=bool(claims.get("is_service_call")),
            service_name=claims.get("service_name") or None,
            origin=str(claims.get("origin") or ""),
        )


def signing_key() -> bytes:
    """The shared HMAC secret. Raises if absent — see why below.

    A missing key must FAIL, never fall back to a default or to unsigned tokens. An
    unsigned run context is worse than none: every downstream hop would treat forged
    identity as verified, and OPA would happily authorize it. This mirrors R0's decision
    that a missing role row is corruption rather than a kind of user.
    """
    raw = os.environ.get(_ENV_KEY, "")
    if not raw:
        raise RunContextError(
            f"{_ENV_KEY} is not set. It is mounted via secretKeyRef into registry-api and "
            "into every agent pod (deploy-controller/manifest_builder.py). Without it a run "
            "carries no verifiable identity and OPA Gate 6 denies every tool call."
        )
    return raw.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint(ctx: RunContext, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Sign a RunContext into a wire token. registry-api only.

    declarative-runner and the SDK vendor this module but must never call `mint` for a
    ROOT context — they have no way to authenticate a human, so anything they minted from
    scratch would be self-asserted identity. They call `extend`.
    """
    if len(ctx.actor_chain) > MAX_ACTOR_CHAIN:
        raise RunContextError(f"actor_chain exceeds {MAX_ACTOR_CHAIN}")
    claims = ctx.to_claims()
    claims["exp"] = int(time.time()) + int(ttl_seconds)
    payload = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(signing_key(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify(token: str) -> RunContext:
    """Verify and decode. Raises RunContextError on ANY problem.

    Never returns a partially-trusted result. A caller that wants to degrade gracefully
    catches the exception and proceeds with no identity — explicitly — rather than being
    handed a context it cannot tell apart from a verified one.
    """
    if not token or "." not in token:
        raise RunContextError("malformed run-context token")
    payload, _, sig = token.rpartition(".")
    expected = hmac.new(signing_key(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    # compare_digest, not ==: a short-circuiting comparison leaks the signature one byte
    # at a time to anyone who can time the request.
    if not hmac.compare_digest(sig, expected):
        raise RunContextError("run-context signature mismatch")
    try:
        claims = json.loads(_unb64(payload))
    except Exception as exc:
        raise RunContextError(f"undecodable run-context payload: {exc}") from exc
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise RunContextError("run-context token expired")
    ctx = RunContext.from_claims(claims)
    if len(ctx.actor_chain) > MAX_ACTOR_CHAIN:
        raise RunContextError(f"actor_chain exceeds {MAX_ACTOR_CHAIN}")
    return ctx


def extend(token: str, agent_name: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Verify, append this hop to the actor chain, re-mint with a fresh expiry.

    Appending — never replacing — is what makes the chain an audit trail. The re-mint is
    what lets a long orchestration outlive the original TTL without widening it: each hop
    is individually short-lived, so a leaked token from hop 1 does not stay useful.
    """
    ctx = verify(token)
    if agent_name and (not ctx.actor_chain or ctx.actor_chain[-1] != agent_name):
        ctx.actor_chain = [*ctx.actor_chain, agent_name]
    if len(ctx.actor_chain) > MAX_ACTOR_CHAIN:
        raise RunContextError(
            f"actor_chain exceeds {MAX_ACTOR_CHAIN} at '{agent_name}' — probable handoff loop"
        )
    return mint(ctx, ttl_seconds)

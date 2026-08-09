"""The DURABLE identity anchor — write it once at run start, re-hydrate it at resume.

Design: docs/design/identity-propagation-architecture.md §4.4. Identity P1 (write) + P1.5
(read). registry-api ONLY — this is deliberately *not* part of `run_context.py`, which is
vendored byte-identical into declarative-runner and the SDK and must stay dependency-free.

WHY AN ANCHOR EXISTS AT ALL
---------------------------
The RCT is a transport token with a 900-second TTL, and that TTL is correct: it rides
synchronous internal hops that finish in seconds, so a leaked token stops being useful
almost immediately. But a HITL approval can sit for hours. Carrying the token across that
pause would force the TTL up to "long enough for the slowest human", which throws away the
only property the short TTL was buying.

So identity is persisted on the run row and the token is re-minted from it at resume. The
token is transport; the row is the system of record. That inversion is the whole of §4.4.

Without this, every post-approval OPA re-check sees `user_id=""` and Gate 6 denies the
resumed run — the identity floor has been live since WS-2 (`agentshield.rego:22,101-108`,
AND-ed into `allow` at `:116`), so this is a live denial, not a hypothetical.

ONE PRODUCER, AND WHY IT MATTERS MORE HERE THAN USUAL
-----------------------------------------------------
`open_run_context` builds the `RunContext`, persists it, and mints the token from the SAME
object. If the row and the token were built separately they could disagree, and the
disagreement would be invisible: the run would start as one user and resume as another,
with OPA correctly authorizing both. A divergence that both halves of the system consider
valid is not something a test finds by accident.

This repo has three postmortems for two copies of one rule (`start_chat` vs
`start_deployment_chat`, `webhook_clients.py` vs `agent_endpoints.py`,
`approvals._ADMIN_ROLES` vs `rbac`). This one would have been worse than all of them,
because the two copies encode *authority* rather than behaviour.

WHY THE ANCHOR IS NOT JUST `user_id`
------------------------------------
`playground_runs.user_id` and `agent_runs.run_by` already exist, and re-deriving a
`RunContext` from them was the obvious cheaper option. It is wrong for three reasons:

  * `run_by` for a daemon is a SERVICE subject (`serviceaccount:scheduler`), not a human.
    Re-minting `user_sub=run_by` would hand a daemon run a fabricated human identity and
    walk it straight through the `user_delegated` arm of the identity floor.
  * `user_team` is not on either row, and a re-derivation would have to re-query it — at
    which point a user who changed teams mid-pause resumes with the WRONG team, silently
    changing which grants Decision 45 intersects against.
  * `origin`, `actor_chain` and `is_service_call` have no columns at all, so a resumed run
    would lose its lineage and look like a root run.

Storing the object means the resumed run carries exactly the identity the run started with,
which is the only defensible answer to "whose authority is this".
"""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AgentRun, PlaygroundRun
from run_context import RunContext, RunContextError, mint

logger = logging.getLogger(__name__)


def build_context(
    *,
    user_sub: str,
    user_team: str = "",
    origin: str,
    is_service_call: bool = False,
    service_name: str | None = None,
    actor_chain: list[str] | None = None,
) -> RunContext:
    """Assemble the identity object. Pure — no DB, no minting, no side effects.

    Separate from `mint_for` so a caller that already has a `Principal` (the production
    path) can construct the same object without pretending to be the playground.
    """
    return RunContext(
        user_sub=user_sub or "",
        user_team=user_team or "",
        actor_chain=list(actor_chain or []),
        is_service_call=is_service_call,
        service_name=service_name,
        origin=origin,
    )


def mint_for(ctx: RunContext, *, label: str) -> str | None:
    """Mint, or return None with a loud log. NEVER raises.

    Minting fails when `AGENTSHIELD_INTERNAL_SIGNING_KEY` is absent. A run that proceeds
    with no token is denied `missing_user_identity` at its first tool call — the SAME
    outcome as before P1. So a missing key degrades to the old behaviour rather than to a
    run that silently acts unattributed, which is the one outcome worth crashing over.

    `label` is only for the log line. A warning that does not say WHICH run lost its
    identity is a warning nobody can act on.
    """
    try:
        return mint(ctx)
    except RunContextError as exc:
        logger.warning(
            "%s: could not mint a run context (%s) — this run carries NO identity and "
            "user_delegated tool calls will be denied missing_user_identity", label, exc,
        )
        return None
    except Exception as exc:  # noqa: BLE001 — identity must never break run creation
        logger.warning("%s: unexpected mint failure (%s)", label, exc)
        return None


def anchor_value(ctx: RunContext) -> dict:
    """What goes in the `run_context` JSONB column.

    Deliberately the claims WITHOUT `exp`. The anchor is not a token and has no expiry —
    storing one would create a second, silent way for a paused run to lose its identity,
    and the whole point of the anchor is that it outlives the token.
    """
    return ctx.to_claims()


def inherit_anchor(parent_claims: dict | None, agent_name: str) -> dict | None:
    """Derive a CHILD run's anchor from its parent's, appending one hop.

    A workflow member acts under the WORKFLOW's authority, not its own — the same rule
    `AgentRun.run_by` inheritance already encodes at the audit layer. This carries that
    rule to the identity layer so the two cannot disagree.

    Returns None when the parent has no anchor: a member of a pre-migration workflow run
    inherits nothing, which is honest. Fabricating an empty context here would give the
    child an identity its parent never had.
    """
    if not parent_claims:
        return None
    try:
        ctx = RunContext.from_claims(parent_claims)
    except RunContextError as exc:
        logger.warning("inherit_anchor: parent anchor unreadable (%s) — child gets none", exc)
        return None
    if agent_name and (not ctx.actor_chain or ctx.actor_chain[-1] != agent_name):
        ctx.actor_chain = [*ctx.actor_chain, agent_name]
    return anchor_value(ctx)


_STR_FIELDS = ("user_sub", "user_team", "origin", "service_name")


def _anchor_is_well_formed(claims: object, label: str) -> bool:
    """Type-check a stored anchor before it becomes an identity.

    WHY THIS LIVES HERE AND NOT IN `run_context.from_claims`
    --------------------------------------------------------
    `run_context.py` parses a SIGNED token — by the time `from_claims` runs, the payload
    has already been authenticated with HMAC, so being liberal there is reasonable. This
    module's input is a **JSONB column**, which carries no signature at all. Validating at
    the boundary where the data stops being trusted is the correct split, and it also
    avoids touching a file that is vendored byte-identical into two other services.

    THE HOLE THIS CLOSES, found by T-S101-006 rather than by reasoning:
    `from_claims` does `str(claims.get("user_sub") or "")`, which happily stringifies ANY
    value. An anchor holding `{"user_sub": {"a": 1}}` minted a perfectly valid token whose
    user is the literal text `{'a': 1}` — corruption laundered into an identity that OPA
    would then authorize. A garbage anchor must produce NO token, never a plausible one.
    """
    if not isinstance(claims, dict):
        logger.warning("run-context anchor for %s is not an object (%s)", label, type(claims).__name__)
        return False
    for field in _STR_FIELDS:
        val = claims.get(field)
        if val is not None and not isinstance(val, str):
            logger.warning(
                "run-context anchor for %s has non-string %s (%s) — refusing to mint an "
                "identity from it", label, field, type(val).__name__,
            )
            return False
    chain = claims.get("actor_chain")
    if chain is not None and (
        not isinstance(chain, list) or any(not isinstance(a, str) for a in chain)
    ):
        logger.warning("run-context anchor for %s has a malformed actor_chain", label)
        return False
    if claims.get("is_service_call") is not None and not isinstance(claims["is_service_call"], bool):
        logger.warning("run-context anchor for %s has a non-boolean is_service_call", label)
        return False
    return True


def remint(claims: dict | None, *, label: str, extend_with: str | None = None) -> str | None:
    """Mint a FRESH token from a stored anchor. The one place a resume regains identity.

    Split out of `rehydrate` because not every resume path can look the anchor up by
    thread_id: the chat resume keys its LangGraph thread by `session_id`, not by a run id
    (`chat._chat_thread_id`), so it holds the run row already and a second UUID lookup
    would simply miss. Both paths mint through here so a run cannot resume as one
    principal on one door and another principal on the next.

    Returns None for a missing or unreadable anchor. None means "we do not know", and
    every caller must send nothing rather than mint `user_sub=""` — which would be an
    ASSERTION that the run has no user, not an admission of ignorance.
    """
    if not claims or not _anchor_is_well_formed(claims, label):
        return None
    try:
        ctx = RunContext.from_claims(claims)
    except Exception as exc:  # noqa: BLE001 — see TOTAL below
        # A corrupt anchor is corruption, not a kind of identity (Decision 40/41's shape).
        logger.warning("run-context remint: anchor for %s is unreadable (%s)", label, exc)
        return None
    if extend_with and (not ctx.actor_chain or ctx.actor_chain[-1] != extend_with):
        ctx.actor_chain = [*ctx.actor_chain, extend_with]
    token = mint_for(ctx, label=f"remint({label})")
    if token:
        logger.info(
            "run-context reminted for %s: user=%s team=%s origin=%s chain=%s",
            label, ctx.user_sub or "(none)", ctx.user_team or "(none)",
            ctx.origin, ctx.actor_chain,
        )
    return token


async def rehydrate(db: AsyncSession, thread_id: str, *, extend_with: str | None = None) -> str | None:
    """Load the anchor for `thread_id` and mint a FRESH token from it. P1.5.

    `thread_id` on a durable run is the run's own id — a `PlaygroundRun.id` for the sandbox
    path and an `AgentRun.id` for production and for workflow members. Both are checked,
    PlaygroundRun first, because `routers/approvals.py` already discriminates the two that
    way and a second ordering here would be a second rule.

    Returns None when there is no anchor: a run started before this migration, a reactive
    run that never had one, or a thread_id that is not a run id at all. None means "no
    identity", and every caller must treat it as such — sending nothing is fail-closed and
    matches pre-P1.5 behaviour exactly. It must never become a mint with `user_sub=""`,
    which would be an ASSERTION that the run has no user rather than an admission that we
    do not know.

    `extend_with` appends one hop to the actor chain before minting — used when the
    resumed run is a workflow member re-entering under the parent's authority.

    TOTAL: this NEVER raises, and that is a hard contract rather than defensive habit.
    Every caller is a resume path, and four of the five sit inside a broad
    `except Exception: return` that treats any error as "the resume failed" — so an
    exception escaping here would not degrade identity, it would silently cancel the
    resume and hang the run. Losing identity is a denial the operator can see; losing the
    resume is a run that never finishes and names nothing. The guarantee lives here, once,
    rather than as five copies of a try/except at the call sites.
    """
    try:
        tid = _uuid.UUID(str(thread_id))
    except (ValueError, TypeError, AttributeError):
        return None

    try:
        claims = (
            await db.execute(select(PlaygroundRun.run_context).where(PlaygroundRun.id == tid))
        ).scalar_one_or_none()
        if not claims:
            claims = (
                await db.execute(select(AgentRun.run_context).where(AgentRun.id == tid))
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — see TOTAL above
        logger.warning(
            "run-context rehydrate: anchor lookup for %s failed (%s) — resuming WITHOUT "
            "identity rather than not resuming at all", thread_id, exc,
        )
        return None
    if not claims:
        logger.info(
            "run-context rehydrate: no anchor for thread %s — the resumed run will carry "
            "no identity (pre-P1 run, or a reactive thread with no anchor)", thread_id,
        )
        return None

    return remint(claims, label=f"thread {thread_id}", extend_with=extend_with)

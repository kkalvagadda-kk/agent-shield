"""A tool's catalog visibility — forward by cascade, reverse by its owner.

Decision 47 options C (forward) and #4 (reverse). Companion to migration 0080.

THE REVERSE LIVES HERE TOO, DELIBERATELY
----------------------------------------
`plan_tool_cascade` decides which tools become org-wide; `may_unpublish_tool` decides who
may take one back. Both answer "whose decision is a tool's visibility", and splitting them
across two modules is how the two ends of one lifecycle drift apart. Note they are NOT
symmetric and must not be made so — see `may_unpublish_tool` for why the reverse is not a
cascade.

WHY A MODULE
------------
Two places need the same answer and they are in different routers:

  * `agents.publish_agent` — the GUARD. It must refuse (422) when a bound tool cannot be
    cascaded, at SUBMIT time, so the request never reaches a reviewer in a state that
    cannot be approved cleanly.
  * `admin.approve_publish_request` — the ACT. It flips those tools to `published`.

If those two computed the set separately they would eventually disagree, and the failure
would be silent in the worst direction: the guard passes a request whose approval then
publishes a set the submitter never saw, or blocks one the approval would have handled.
This repo has three postmortems for two copies of one rule. One function, two callers.

WHY THE SET IS COMPUTED AT APPROVE TIME AND NOT SNAPSHOT AT SUBMIT
------------------------------------------------------------------
Decision 47 rejected a `cascade_publish` column for this reason. A submit-time snapshot
goes stale between submit and approve — a tool can be unbound, rebound, or published by
another agent's cascade in between — and the audit record would then name what was
intended rather than what happened. Deriving it twice from live rows means the reviewer
sees the current truth and the audit records the actual effect.

WHAT DOES NOT CASCADE, AND WHY
------------------------------
Only tools the agent's OWN team owns. Publishing an agent must never be a way to make
another team's private draft discoverable org-wide — that would turn one team's review
into a publication decision about someone else's work. Those block the request instead
(`tool_not_publishable_cross_team`), so the resolution is explicit: the owning team
publishes it, or the agent stops binding it.

A NULL `owner_team` also blocks. It cannot be attributed to a team, so there is nobody
whose decision the cascade would be carrying out. Since 0.2.267 `create_tool` derives the
owner, so a NULL owner on an UNPUBLISHED row means a direct database write or a row from
before that change — fail closed and say so rather than guess.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Agent, AgentTool, Tool
from rbac import get_user_global_role, get_user_team


@dataclass(frozen=True)
class CascadePlan:
    """What publishing this agent would do to its tools."""

    # Own-team tools that are not yet published. These flip on approve.
    will_publish: list[Tool]
    # Tools that are not published and not the agent's team's to publish. These BLOCK.
    blocked: list[Tool]
    # Already published. Listed so the reviewer sees the full dependency set rather than
    # only the delta — "this agent uses 6 tools, 2 of which become org-wide" reads very
    # differently from "2 tools become org-wide".
    already_published: list[Tool]

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked)


async def plan_tool_cascade(db: AsyncSession, agent_id, agent_team: str | None) -> CascadePlan:
    """Derive the cascade for one agent from live rows.

    `agent_team` is passed rather than read off a relationship so the caller states which
    team's authority the cascade is exercising. The two callers both have the agent
    loaded; making it explicit keeps the ownership question visible at the call site
    instead of buried one attribute access deep.
    """
    tools = (
        await db.execute(
            select(Tool)
            .join(AgentTool, AgentTool.tool_id == Tool.id)
            .where(AgentTool.agent_id == agent_id)
        )
    ).scalars().all()

    will_publish: list[Tool] = []
    blocked: list[Tool] = []
    already_published: list[Tool] = []

    for tool in tools:
        if tool.publish_status == "published":
            already_published.append(tool)
        elif agent_team and tool.owner_team == agent_team:
            will_publish.append(tool)
        else:
            blocked.append(tool)

    return CascadePlan(
        will_publish=will_publish,
        blocked=blocked,
        already_published=already_published,
    )


@dataclass(frozen=True)
class UnpublishAuthority:
    """Why the caller may (or may not) unpublish, not merely whether."""

    allowed: bool
    # 'creator' | 'owner_team' | 'platform-admin' | None
    basis: str | None


async def may_unpublish_tool(db: AsyncSession, tool: Tool, caller_sub: str) -> UnpublishAuthority:
    """Decision 47 #4 — who may take a tool back out of the org-wide catalog.

    Three arms, in the order a human would reason about them:

      * **creator** — you published it by riding along with your agent; you can take it
        back. This is the arm Decision 47 names first.
      * **owning team** — ownership is team-level (Decision 46). A tool must not become
        unmaintainable because the one person who created it left; the team that OWNS it
        can act on it.
      * **platform-admin** — NOT in Decision 47's sentence, and added deliberately. A
        platform-admin is the person who APPROVED the publish. Without this arm the only
        actor who can put a tool into the org-wide catalog cannot take it back out, which
        is precisely the one-way ratchet the decision exists to break. Leaving it out
        would have rebuilt the problem inside its own fix.

    Note what this is NOT: it is not `catalog_visibility_clause`. Seeing a tool is
    creator-scoped; *acting on ownership* is team-scoped. Those are the two axes Decision
    46/47 separate, and answering one with the other's column is the error CORRECTION 2
    records. A teammate can unpublish a tool they could not have seen as a draft — correct,
    because by the time it is published everyone can see it anyway.
    """
    if tool.created_by and tool.created_by == caller_sub:
        return UnpublishAuthority(True, "creator")

    # owner_team is checked before the role lookup so the common case costs one query.
    caller_team = await get_user_team(db, caller_sub)
    if tool.owner_team and caller_team and tool.owner_team == caller_team:
        return UnpublishAuthority(True, "owner_team")

    # Raises NoPlatformRole for a row-less sub (Decision 40/41), which main.create_app
    # maps to 403 — the right answer for a caller whose identity is corrupt, and not
    # something to swallow into a quiet "not allowed".
    if await get_user_global_role(db, caller_sub) == "platform-admin":
        return UnpublishAuthority(True, "platform-admin")

    return UnpublishAuthority(False, None)


async def published_agents_using(db: AsyncSession, tool_id) -> list[Agent]:
    """Published agents still bound to this tool — a COURTESY, never a gate.

    Decision 47: *unpublish removes discoverability, never capability.* A bound agent
    keeps working, because binding is by id and USE is governed by `owner_team` plus
    grants (`tool_access.team_may_use_tool`) — never by `publish_status`. The pod does not
    even read the catalog: since the CORRECTION-2 fix the SDK resolver calls
    `GET /agents/{name}/tools`, the binding endpoint, which has no publish filter.

    So this list must be SHOWN and must never BLOCK. Turning it into a precondition would
    assert a dependency that does not exist, and would let any team freeze another team's
    tool in the catalog forever by binding it to a published agent.
    """
    return list(
        (
            await db.execute(
                select(Agent)
                .join(AgentTool, AgentTool.agent_id == Agent.id)
                .where(AgentTool.tool_id == tool_id, Agent.publish_status == "published")
                .order_by(Agent.name)
            )
        ).scalars().all()
    )


def blocked_detail(plan: CascadePlan, agent_team: str | None) -> dict:
    """The 422 body for a blocked cascade.

    Same shape as the existing `critical_risk_not_publishable` and `tool_grants_missing`
    so the Studio error path does not need a new branch. Names every blocking tool and its
    owner: "publish is blocked" without saying by what leaves the submitter guessing, and
    the fix (ask that team to publish, or unbind) depends entirely on which tool it is.
    """
    return {
        "error": "tool_not_publishable_cross_team",
        "agent_team": agent_team,
        "tools": [
            {
                "id": str(t.id),
                "name": t.name,
                "owner_team": t.owner_team,
                "publish_status": t.publish_status,
            }
            for t in plan.blocked
        ],
    }

"""Which tools ride along when an agent is published — one producer, two readers.

Decision 47 option C. Companion to migration 0080.

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

from models import AgentTool, Tool


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

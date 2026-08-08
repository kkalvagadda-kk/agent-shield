"""Who may SEE a catalog row — one producer for tools and skills.

Decision 47. Companion to migration 0080.

WHY THIS IS A MODULE AND NOT TWO INLINE `or_(...)`
--------------------------------------------------
`list_tools` and `list_skills` each carried their own copy of the predicate. Two copies of
one rule is the shape this repo has three postmortems for — `start_chat` vs
`start_deployment_chat`, `webhook_clients.py` vs `agent_endpoints.py`,
`approvals._ADMIN_ROLES` vs `rbac`. Every time, one copy got the fix and the other did not.
Migration 0080 made this predicate load-bearing (before it, both arms matched almost every
row because the default was 'published'), so it gets one producer before it gets a second
reader.

THE RULE: CREATOR-SCOPED, SAME AS AGENTS AND WORKFLOWS
------------------------------------------------------
    published OR created_by == the caller

Identical to `agents.py:248` and `composite_workflows.py:205`. Decision 47 is named for the
agent pattern — *"drafts are yours until you share"* — and all four artifact types now
answer the question the same way.

CORRECTED 2026-08-08. This module briefly scoped visibility to the caller's TEAM
(`owner_team == caller_team`). That was wrong twice over:

  * It broke the pattern the decision is named after. A draft agent is invisible to your
    teammates until it is published; that is the accepted, shipped model, and there is no
    reason a draft tool should behave differently.
  * The justification for it — "otherwise a contributor's new tool is invisible to their own
    teammates" — described the agent model and called it a bug.

It also conflated the two axes Kalyan separated explicitly. Decision 46 is the USE axis:
`owner_team` decides who may CALL a tool, via `tool_access.team_may_use_tool` and the publish
cascade. Decision 47 is the VISIBILITY axis: who SEES it in a catalog. Ownership is
team-level; discoverability is creator-level. Answering the second question with the first
one's column is what produced the error.

`owner_team` derivation stays exactly as it is — it is the other axis and it is correct.

NO ANONYMOUS BRANCH — DELIBERATE
--------------------------------
This function takes a caller and assumes there is one, because every route that calls it now
requires authentication.

It previously had a `CallerKind.IN_CLUSTER_MACHINE` arm that applied NO publish filter at
all, so a tokenless in-cluster caller saw every row including other teams' private drafts.
That existed because agent pods fetched the global catalog by tool name with no credential,
and filtering it returned zero rows and killed them at startup.

The pods were asking the wrong question. `GET /api/v1/agents/{name}/tools`
(`agent_tools.py`) already returns exactly the tools an agent is BOUND to — the same set OPA
Gate 3 authorizes — and a pod's authority over a tool has always been its binding, never the
catalog flag. The resolver now calls that, authenticated with the pod's ServiceAccount token,
and the anonymous arm is gone rather than narrowed.
"""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement


def catalog_visibility_clause(
    *,
    publish_status_col,
    created_by_col,
    caller_sub: str,
) -> ColumnElement[bool]:
    """The WHERE predicate for a human browsing a catalog.

    Keyword-only on purpose: both column arguments are the same type, and a positional swap
    would silently invert the rule.

    There is deliberately no platform-admin arm. An admin browsing a catalog is still
    browsing a catalog; if an admin needs to see another team's private drafts, that is a
    review surface (Decision 47 step D) with its own endpoint and its own audit trail, not a
    widened list filter.
    """
    return or_(publish_status_col == "published", created_by_col == caller_sub)

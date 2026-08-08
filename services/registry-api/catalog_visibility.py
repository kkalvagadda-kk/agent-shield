"""Who may SEE a catalog row — one producer for tools and skills.

Decisions 46 + 47. Companion to migration 0080.

WHY THIS IS A MODULE AND NOT TWO INLINE `or_(...)`
--------------------------------------------------
`list_tools` and `list_skills` each carried their own copy of the visibility predicate:

    or_(publish_status == "published", created_by == caller)

Two copies of one rule is the shape this repo has three postmortems for — `start_chat` vs
`start_deployment_chat`, `webhook_clients.py` vs `agent_endpoints.py`,
`approvals._ADMIN_ROLES` vs `rbac`. Every time, one copy got the fix and the other did not.
Migration 0080 makes this predicate load-bearing (before it, both branches matched almost
every row because the default was 'published'), so it gets one producer before it gets a
second reader.

THE TWO CALLER KINDS ARE AN EXPLICIT PARAMETER, NOT A SNIFF
-----------------------------------------------------------
This rule genuinely has two contexts, and they want different answers. That is exactly the
case CLAUDE.md says to solve with a named context parameter rather than priority
fallthrough or `getattr` type-sniffing, so `CallerKind` is passed in by the handler:

  HUMAN — somebody browsing the catalog in Studio. Discoverability is the question, and the
      answer is: rows published to the shared library, plus everything the caller's own TEAM
      owns. Team, not creator: Decision 46 makes the creating team the owner, so a
      contributor's draft must be visible to their teammates. Creator-scoping it (the old
      behaviour) would have meant a tool nobody but its author could see the moment 0080
      made the default private.

  IN_CLUSTER_MACHINE — an agent pod resolving a tool it is already BOUND to. No publish
      filter at all. What authorizes a pod to use a tool is the binding (`agent_tools`) plus
      OPA Gate 3 — never the catalog flag. `publish_status` answers "may this be discovered
      and adopted", which is a different question from "may this pod run the thing it was
      deployed with", and conflating them is what `asset_grants` already does wrong (the
      visibility-vs-authority conflict tracked as G-R3-3).

      This branch is reachable only by a caller with no token, and agent pods are the only
      such callers: the SDK `tool_resolver` (GET /api/v1/tools/?name=X) and
      declarative-runner `workflow_executor.py:232,247`. registry-api's Service is not
      exposed outside the cluster.

      It DOES widen what an anonymous in-cluster caller can enumerate, from every published
      row to every row. That is a deliberate, ledgered trade against the alternative —
      breaking every SDK agent that binds a tool created after 0080 — and it closes when
      identity Phase 3 gives these callers a verifiable service identity, at which point the
      predicate becomes "tools this agent is bound to" and this branch disappears. Tracked
      in docs/testing/manual-ui-e2e-test-plan.md.

There is deliberately no third kind for platform-admin. An admin browsing the catalog is
still browsing a catalog; if an admin needs to see another team's private drafts, that is a
review surface (Decision 47 step D) with its own endpoint and its own audit trail, not a
widened list filter.
"""
from __future__ import annotations

from enum import Enum

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement


class CallerKind(str, Enum):
    """Which question the caller is asking. Set by the handler, never inferred downstream."""

    HUMAN = "human"
    IN_CLUSTER_MACHINE = "in_cluster_machine"


def catalog_visibility_clause(
    *,
    publish_status_col,
    owner_team_col,
    caller_kind: CallerKind,
    caller_team: str | None,
) -> ColumnElement[bool] | None:
    """The WHERE predicate for a catalog listing. `None` means no restriction.

    Keyword-only on purpose: the two column arguments are the same type and are named
    differently on the two models (`Tool.owner_team` vs `Skill.team`). Positional args here
    would be a silent swap waiting to happen.

    Returns None — rather than a tautology like `true` — for the machine kind, so the caller
    applies nothing at all and a reader can see at the call site that no filter was added.
    """
    if caller_kind is CallerKind.IN_CLUSTER_MACHINE:
        return None

    published = publish_status_col == "published"
    if not caller_team:
        # A human with no team assignment. Under R0 a row-less user is an invariant
        # violation that fails earlier with 403, so this is the narrow legitimate case of a
        # caller whose team is genuinely unset. They see the shared library and nothing
        # else — never a fallback to "everything", which is how a missing value turns into
        # a permission.
        return published
    return or_(published, owner_team_col == caller_team)

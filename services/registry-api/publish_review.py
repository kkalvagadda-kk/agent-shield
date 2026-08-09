"""What a reviewer is shown before authorizing a publish — Decision 47 step D.

WHY A MODULE
------------
The approve button is the ONLY place in the authorization stack where a human decides.
OPA, the HITL router, the eval gate and the cross-team 422 are all machine-enforced and
testable; this one is not, and it is the last gate before an artifact becomes org-wide.
Today that human is shown an asset name, a submitter, a timestamp, a percentage and a
colour — the tools are not on the screen at all (`grep -c "tool" AdminPublishRequestsPage
.tsx` was 0). G-R3-11.

Two things live here, and both are here for the SAME reason: they are already computed
somewhere else and must not be computed twice.

  * `resolve_request_evals` — lifted verbatim out of `admin.list_publish_requests`. The
    review payload needs the same score/threshold/provenance triple the queue row shows.
    Writing a single-request version of it would be the fourth copy of a rule that already
    has a postmortem (Decision 32: the map used to be keyed by ASSET id, so every pending
    request for one agent got the same latest eval and a reviewer approved a release on a
    number that did not describe it). Extracted rather than duplicated; the list endpoint
    now calls this too, so the two cannot disagree.
  * the cascade set comes from `publish_cascade.plan_tool_cascade` — the SAME producer the
    submit guard and the approve action use. A third implementation here would drift in
    the worst direction: the reviewer is shown one set and approve publishes another.

WHY THE PAYLOAD IS ONE REQUEST
------------------------------
A drawer that fires six calls renders half-populated, and the reviewer cannot tell which
half is missing. A partially-rendered review screen is worse than none, because it looks
like it worked.

DECISIONS TAKEN HERE (docs/design/publish-review-surface.md §8, resolved 2026-08-08)
------------------------------------------------------------------------------------
D-1  `python_code` is returned IN FULL. It is the thing being approved — arbitrary code
     the platform will execute. Redacting it would need a secret-detector that does not
     exist, and a redactor with false negatives is worse than none because it advertises
     a safety it does not have. This exposes nothing new: the route is platform-admin
     only, and any platform-admin can already read the same field from `GET /tools/{id}`.
     It moves the code to where the decision is made.
D-2  The auth config's NAME, never its value. `payments-api-key` is the signal — that the
     tool carries a real credential and publishing widens who can fire it. The value is
     never loaded here.
D-3  Agents are reviewable. Workflows return `review_supported=false` with a reason rather
     than a 404 or a half-payload: a workflow's tools arrive through its members, so the
     shape genuinely differs, and a payload that silently omitted them would read as "this
     workflow has no tools". Deferred, and it says so on the screen.
D-4  Approval stays all-or-nothing. Per-tool refusal would need a partial-cascade concept
     Decision 47 does not have; the reviewer rejects and the submitter unbinds.
D-5  `grantee_teams` from the pending approve body is surfaced — it is already an input to
     approve and has never been visible on the queue.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Agent, AgentKnowledgeBinding, AgentVersion, AuthConfig, EvalRun,
    KnowledgeBase, MCPServer, PublishRequest,
)
from publish_cascade import plan_tool_cascade

# THE threshold rule — imported, never re-derived. Its own docstring is the postmortem
# for what happens when it gets copied.
from routers.eval_runner import effective_pass_threshold


@dataclass(frozen=True)
class EvalFacts:
    """The four values a reviewer needs to read a score as evidence.

    `source` is not decoration. A score alone cannot distinguish "this version's eval"
    from "some other version's", and those demand different reviewer behaviour: the first
    is evidence, the second is a warning.
    """

    score: float | None
    run_id: uuid.UUID | None
    threshold: float | None
    source: str  # "version" | "agent_latest" | "none"


async def resolve_request_evals(
    db: AsyncSession,
    requests: list[PublishRequest],
    agent_name_by_asset_id: dict[uuid.UUID, str],
) -> dict[uuid.UUID, EvalFacts]:
    """Resolve each request's eval AGAINST THE VERSION IT PINS, keyed by REQUEST id.

    Batched — two queries regardless of how many requests come in. The list endpoint is an
    admin view whose whole job is rendering many rows, so an N+1 here is a page-load
    regression; the review endpoint passes a single-element list and pays nothing for the
    generality.

    Requests with no resolvable eval are OMITTED from the map rather than given a
    placeholder, so the caller's `.get()` produces `source="none"`. A pinned version with
    no eval must never borrow another version's score — that silent borrow IS the bug
    Decision 32 fixed. Regression: suite-89 T-S89-001..004.
    """
    out: dict[uuid.UUID, EvalFacts] = {}
    agent_requests = [r for r in requests if r.asset_id in agent_name_by_asset_id]
    if not agent_requests:
        return out

    pinned = {r.id: r.source_version_id for r in agent_requests if r.source_version_id}
    unpinned = [r for r in agent_requests if not r.source_version_id]

    # (1) Version-pinned requests — the common case, and the one that was wrong.
    if pinned:
        rows = (await db.execute(
            select(EvalRun)
            .where(
                EvalRun.agent_version_id.in_(set(pinned.values())),
                EvalRun.status == "completed",
            )
            .order_by(EvalRun.agent_version_id, EvalRun.completed_at.desc())
            .distinct(EvalRun.agent_version_id)
        )).scalars().all()
        by_version = {run.agent_version_id: run for run in rows}
        for req_id, version_id in pinned.items():
            run = by_version.get(version_id)
            if run is not None:
                out[req_id] = EvalFacts(
                    run.overall_score, run.id, effective_pass_threshold(run), "version"
                )

    # (2) Legacy requests pinning no version. `POST /agents/{name}/publish` always pins
    #     one now, so this covers rows written before that landed. LABELLED
    #     `agent_latest`, never silent — the number is not evidence about the thing being
    #     published, and the reviewer has to be able to see that.
    if unpinned:
        names = [n for n in (agent_name_by_asset_id.get(r.asset_id) for r in unpinned) if n]
        if names:
            rows = (await db.execute(
                select(EvalRun)
                .where(EvalRun.agent_name.in_(names), EvalRun.status == "completed")
                .order_by(EvalRun.agent_name, EvalRun.completed_at.desc())
                .distinct(EvalRun.agent_name)
            )).scalars().all()
            by_name = {run.agent_name: run for run in rows}
            for r in unpinned:
                run = by_name.get(agent_name_by_asset_id.get(r.asset_id))
                if run is not None:
                    out[r.id] = EvalFacts(
                        run.overall_score, run.id, effective_pass_threshold(run),
                        "agent_latest",
                    )

    return out


# Risk order for display. HIGHEST FIRST, then the cascade, then the rest — the reviewer's
# eye must land on the most consequential row without scrolling, and "what becomes org-wide"
# is the consequence they cannot undo.
_RISK_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


async def build_review_payload(db: AsyncSession, pr: PublishRequest) -> dict:
    """Everything the reviewer must see for one publish request.

    Returns a plain dict rather than an ORM graph: the drawer is a read surface and every
    field here is deliberately chosen (D-1/D-2), so a `from_attributes` model that widened
    silently when a column was added would be the wrong default for a screen whose whole
    purpose is showing exactly what was decided upon.
    """
    if pr.asset_type != "agent":
        # D-3. NOT a 404 and NOT a partial payload — a workflow drawer that rendered an
        # empty tool list would read as "this workflow has no tools", which is the
        # silently-unreviewed outcome the requirement rules out.
        return {
            "request_id": str(pr.id),
            "asset_type": pr.asset_type,
            "review_supported": False,
            "unsupported_reason": (
                f"Review detail is not built for asset_type={pr.asset_type!r}. A workflow's "
                "tools arrive through its member agents, so the payload shape differs. "
                "Approving from here shows you the queue row only."
            ),
        }

    agent = (await db.execute(select(Agent).where(Agent.id == pr.asset_id))).scalar_one_or_none()
    if agent is None:
        return {
            "request_id": str(pr.id),
            "asset_type": pr.asset_type,
            "review_supported": False,
            "unsupported_reason": "The agent this request points at no longer exists.",
        }

    # ---- the version being published -------------------------------------------------
    version = None
    if pr.source_version_id:
        version = (await db.execute(
            select(AgentVersion).where(AgentVersion.id == pr.source_version_id)
        )).scalar_one_or_none()

    # ---- eval, through the shared resolver -------------------------------------------
    facts = (await resolve_request_evals(db, [pr], {agent.id: agent.name})).get(
        pr.id, EvalFacts(None, None, None, "none")
    )

    # ---- tools + the cascade, from the ONE producer ----------------------------------
    plan = await plan_tool_cascade(db, agent.id, agent.team)
    disposition = (
        [(t, "will_publish") for t in plan.will_publish]
        + [(t, "blocked") for t in plan.blocked]
        + [(t, "already_published") for t in plan.already_published]
    )

    # Credential NAMES only (D-2), resolved in one query rather than per tool.
    auth_ids = {t.auth_config_id for t, _ in disposition if t.auth_config_id}
    auth_names: dict[uuid.UUID, str] = {}
    if auth_ids:
        for row in (await db.execute(
            select(AuthConfig.id, AuthConfig.name).where(AuthConfig.id.in_(auth_ids))
        )).all():
            auth_names[row.id] = row.name

    mcp_ids = {t.mcp_server_id for t, _ in disposition if t.mcp_server_id}
    mcp_names: dict[uuid.UUID, str] = {}
    if mcp_ids:
        for row in (await db.execute(
            select(MCPServer.id, MCPServer.name).where(MCPServer.id.in_(mcp_ids))
        )).all():
            mcp_names[row.id] = row.name

    tools = [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "type": t.type,
            "risk_level": t.risk_level,
            "owner_team": t.owner_team,
            "publish_status": t.publish_status,
            # The cascade verdict, not a re-derivation of it.
            "disposition": how,
            "side_effecting": t.side_effecting,
            "pii_deanonymize_allowed": t.pii_deanonymize_allowed,
            # WHERE THE DATA GOES — the single highest-signal field on this screen.
            "http_method": t.http_method,
            "http_url": t.http_url,
            # D-1: in full.
            "python_code": t.python_code,
            # D-2: the name, never the value.
            "auth_config_name": auth_names.get(t.auth_config_id) if t.auth_config_id else None,
            "mcp_server_name": mcp_names.get(t.mcp_server_id) if t.mcp_server_id else None,
            "mcp_tool_name": t.mcp_tool_name,
        }
        for t, how in sorted(
            disposition,
            key=lambda pair: (
                _RISK_RANK.get((pair[0].risk_level or "").lower(), 9),
                {"will_publish": 0, "blocked": 1, "already_published": 2}[pair[1]],
                pair[0].name or "",
            ),
        )
    ]

    # ---- knowledge bases -------------------------------------------------------------
    kb_rows = (await db.execute(
        select(KnowledgeBase.id, KnowledgeBase.name, KnowledgeBase.team)
        .join(AgentKnowledgeBinding, AgentKnowledgeBinding.kb_id == KnowledgeBase.id)
        .where(AgentKnowledgeBinding.agent_id == agent.id)
    )).all()

    metadata = agent.metadata_ or {}

    return {
        "request_id": str(pr.id),
        "asset_type": "agent",
        "review_supported": True,
        "submitted_by": pr.submitted_by,
        "submitted_at": pr.submitted_at.isoformat() if pr.submitted_at else None,
        "status": pr.status,
        "agent": {
            "id": str(agent.id),
            "name": agent.name,
            "team": agent.team,
            "created_by": agent.created_by,
            "description": agent.description,
            # `daemon` is EXEMT from OPA's identity floor (`user_identity_ok`), so
            # approving one is a materially different risk decision — and the queue row
            # has never said which kind it is.
            "agent_class": agent.agent_class,
            "agent_type": agent.agent_type,
            "execution_shape": agent.execution_shape,
            "memory_enabled": agent.memory_enabled,
            "publish_status": agent.publish_status,
            "instructions": metadata.get("instructions"),
        },
        "version": None if version is None else {
            "id": str(version.id),
            "version_number": version.version_number,
            # For an `sdk` agent the image is USER-BUILT. The reviewer is approving a
            # container, and this is the only field that says which one.
            "image_tag": version.image_tag,
            "git_sha": version.git_sha,
            "git_branch": version.git_branch,
            "eval_passed": version.eval_passed,
            "adversarial_eval_passed": version.adversarial_eval_passed,
            "notes": version.notes,
        },
        "eval": {
            "score": facts.score,
            "run_id": str(facts.run_id) if facts.run_id else None,
            "pass_threshold": facts.threshold,
            "source": facts.source,
        },
        "tools": tools,
        "cascade": {
            "will_publish": [t.name for t in plan.will_publish],
            "blocked": [
                {"name": t.name, "owner_team": t.owner_team} for t in plan.blocked
            ],
            "already_published": [t.name for t in plan.already_published],
        },
        "knowledge_bases": [
            {"id": str(r.id), "name": r.name, "team": r.team} for r in kb_rows
        ],
    }

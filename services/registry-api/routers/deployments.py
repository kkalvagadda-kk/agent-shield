"""
AgentShield Registry API — Deployments router.

Endpoints
---------
  POST  /api/v1/agents/{name}/deploy       — deploy a specific agent version
  POST  /api/v1/agents/{name}/rollback     — roll back to a prior version
  GET   /api/v1/agents/{name}/deployments  — list all deployments for an agent
  GET   /api/v1/deployments/               — list deployments (filterable by status)
  PATCH /api/v1/deployments/{id}           — update deployment status (used by deploy-controller)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from crypto import decrypt_json
from agent_config import build_config_snapshot
from db import get_db
from observability_backend import get_observability_backend
from k8s import upsert_secret
from models import (
    Agent,
    AgentRun,
    AgentTool,
    AgentVersion,
    ApprovalAuthority,
    AssetGrant,
    CompositeWorkflow,
    Deployment,
    LLMProvider,
    ProductionDeployment,
    Tool,
    WorkflowDeployment,
)
from policy_generator import generate_and_store
from schemas import (
    AgentRunResponse,
    AgentStatsResponse,
    DeploymentActionRequest,
    DeploymentCreate,
    DeploymentResponse,
    PaginatedResponse,
    RollbackRequest,
    WorkflowDeploymentResponse,
)

# Namespace where LLM credential secrets are stored
_PLATFORM_NAMESPACE = "agentshield-platform"

logger = logging.getLogger(__name__)


async def _auto_grant_approval_authority(
    db: AsyncSession,
    tools: list[tuple[str, str]],
    team: str,
    granted_by: str,
) -> int:
    """Auto-grant ApprovalAuthority to team members for high/critical-risk tools.

    ``tools`` is a list of ``(tool_name, risk_level)`` pairs — source-agnostic so
    BOTH the sandbox deploy path (ORM Tool objects) and the production deploy path
    (config_snapshot dicts) can feed it, without either coupling to the other's
    data shape. Interim measure until RBAC lands: every team member can approve
    the team's high-risk tools. Called during deployment creation so the Approvals
    console works without manual admin setup. Idempotent — skips if an active
    (non-revoked) record already exists for a (user, tool) pair.

    Returns the number of new authority records created.
    """
    risky_tool_names = sorted({
        name for (name, risk) in tools
        if (risk or "").lower() in ("high", "critical")
    })
    if not risky_tool_names:
        return 0

    result = await db.execute(
        text("SELECT user_sub FROM user_team_assignments WHERE team_name = :team"),
        {"team": team},
    )
    team_members = [row[0] for row in result.all()]
    if not team_members:
        return 0

    created = 0
    for tool_name in risky_tool_names:
        for user_sub in team_members:
            existing = await db.execute(
                select(ApprovalAuthority.id).where(
                    ApprovalAuthority.resource_type == "tool",
                    ApprovalAuthority.resource_id == tool_name,
                    ApprovalAuthority.approver_user_id == user_sub,
                    ApprovalAuthority.revoked_at.is_(None),
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue

            db.add(ApprovalAuthority(
                resource_type="tool",
                resource_id=tool_name,
                approver_user_id=user_sub,
                granted_by=granted_by,
            ))
            created += 1

    if created:
        await db.flush()
        logger.info(
            "Auto-granted ApprovalAuthority: %d records for team '%s' "
            "(%d members x %d risky tools)",
            created, team, len(team_members), len(risky_tool_names),
        )

    return created


router = APIRouter(prefix="/api/v1/agents", tags=["deployments"])

# ---------------------------------------------------------------------------
# Global deployments router (separate prefix — used by deploy-controller)
# ---------------------------------------------------------------------------
global_deployments_router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])


class DeploymentStatusUpdate(BaseModel):
    status: str
    k8s_deployment_name: Optional[str] = None
    error_message: Optional[str] = None


@global_deployments_router.get(
    "/",
    response_model=PaginatedResponse[DeploymentResponse],
    summary="List deployments (filterable by status)",
)
async def list_all_deployments(
    status_filter: Optional[str] = Query(None, alias="status"),
    environment: str = Query("production", alias="environment"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[DeploymentResponse]:
    q = (
        select(Deployment, Agent.name.label("agent_name"))
        .join(Agent, Deployment.agent_id == Agent.id)
        .order_by(Deployment.deployed_at.desc())
    )
    if environment:
        q = q.where(Deployment.environment == environment)
    if status_filter:
        q = q.where(Deployment.status == status_filter)
    total_q = q.with_only_columns(Deployment.id)
    total = len((await db.execute(total_q)).all())
    rows = (await db.execute(q.limit(limit).offset(offset))).all()
    items = []
    for deployment, agent_name in rows:
        d = DeploymentResponse.model_validate(deployment)
        d.agent_name = agent_name
        items.append(d)
    return PaginatedResponse(items=items, total=total)


@global_deployments_router.patch(
    "/{deployment_id}",
    response_model=DeploymentResponse,
    summary="Update deployment status (deploy-controller callback)",
)
async def update_deployment_status(
    deployment_id: uuid.UUID,
    body: DeploymentStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> DeploymentResponse:
    result = await db.execute(select(Deployment).where(Deployment.id == deployment_id))
    deployment = result.scalar_one_or_none()
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found.")
    deployment.status = body.status
    if body.k8s_deployment_name is not None:
        deployment.k8s_deployment_name = body.k8s_deployment_name
    if body.error_message is not None:
        deployment.error_message = body.error_message
    if body.status in ("terminated", "rolled_back", "failed"):
        deployment.terminated_at = datetime.now(tz=timezone.utc)
    await db.flush()
    logger.info("update_deployment_status: id=%s status=%s", deployment_id, body.status)
    return DeploymentResponse.model_validate(deployment)


@global_deployments_router.get(
    "/workflows",
    response_model=list[WorkflowDeploymentResponse],
    summary="List workflow deployments (filterable by status/environment)",
)
async def list_all_workflow_deployments(
    status_filter: Optional[str] = Query(None, alias="status"),
    environment: Optional[str] = Query(None, alias="environment"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[WorkflowDeploymentResponse]:
    q = (
        select(WorkflowDeployment, CompositeWorkflow.name.label("wf_name"))
        .join(CompositeWorkflow, WorkflowDeployment.workflow_id == CompositeWorkflow.id)
        .order_by(WorkflowDeployment.deployed_at.desc())
    )
    if status_filter:
        q = q.where(WorkflowDeployment.status == status_filter)
    if environment:
        q = q.where(WorkflowDeployment.environment == environment)
    rows = (await db.execute(q.limit(limit))).all()
    items = []
    for wdep, wf_name in rows:
        d = WorkflowDeploymentResponse.model_validate(wdep)
        d.workflow_name = wf_name
        items.append(d)
    return items


# ---------------------------------------------------------------------------
# Deployment-scoped stats + runs
#
# A deployment's metrics belong to the deployment, not the artifact. The
# `context` param selects the run-isolation column explicitly — no fallthrough:
#   playground  → agent_runs.sandbox_deployment_id
#   production  → agent_runs.production_deployment_id
# ---------------------------------------------------------------------------
async def _validate_deployment(deployment_id: uuid.UUID, context: str, db: AsyncSession):
    """Verify the deployment exists in the table implied by `context`."""
    if context == "playground":
        model = Deployment
    elif context == "production":
        model = ProductionDeployment
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="context must be 'playground' or 'production'",
        )
    row = (await db.execute(select(model).where(model.id == deployment_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found.")


def _run_scope(deployment_id: uuid.UUID, context: str):
    """Return the AgentRun filter column value for the given context."""
    if context == "playground":
        return AgentRun.sandbox_deployment_id == deployment_id
    return AgentRun.production_deployment_id == deployment_id


@global_deployments_router.get(
    "/{deployment_id}/stats",
    response_model=AgentStatsResponse,
    summary="Get run statistics for a single deployment (last 24h)",
)
async def get_deployment_stats(
    deployment_id: uuid.UUID,
    context: str = Query("playground"),
    db: AsyncSession = Depends(get_db),
) -> AgentStatsResponse:
    from datetime import timedelta
    import math
    from sqlalchemy import case, func

    await _validate_deployment(deployment_id, context, db)
    scope = _run_scope(deployment_id, context)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)

    stats_q = select(
        func.count(AgentRun.id).label("run_count"),
        func.sum(case((AgentRun.status == "failed", 1), else_=0)).label("error_count"),
        func.sum(AgentRun.cost_usd).label("total_cost"),
    ).where(scope, AgentRun.started_at >= cutoff)
    row = (await db.execute(stats_q)).first()
    run_count = row.run_count or 0
    error_count = row.error_count or 0
    total_cost = float(row.total_cost or 0)
    error_rate = (error_count / run_count) if run_count > 0 else 0.0

    p50 = p95 = None
    if run_count > 0:
        latency_q = (
            select(AgentRun.latency_ms)
            .where(scope, AgentRun.started_at >= cutoff, AgentRun.latency_ms.isnot(None))
            .order_by(AgentRun.latency_ms)
        )
        latencies = [r[0] for r in (await db.execute(latency_q)).all()]
        if latencies:
            p50 = latencies[min(len(latencies) - 1, math.floor(len(latencies) * 0.5))]
            p95 = latencies[min(len(latencies) - 1, math.floor(len(latencies) * 0.95))]

    return AgentStatsResponse(
        run_count=run_count,
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        error_rate=round(error_rate, 4),
        total_cost_usd=round(total_cost, 6),
    )


@global_deployments_router.get(
    "/{deployment_id}/runs",
    response_model=list[AgentRunResponse],
    summary="List runs for a single deployment",
)
async def list_deployment_runs(
    deployment_id: uuid.UUID,
    context: str = Query("playground"),
    trigger_type: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRunResponse]:
    import os

    await _validate_deployment(deployment_id, context, db)
    q = (
        select(AgentRun)
        .where(_run_scope(deployment_id, context))
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if trigger_type:
        q = q.where(AgentRun.trigger_type == trigger_type)
    if status_filter:
        q = q.where(AgentRun.status == status_filter)
    rows = list((await db.execute(q)).scalars().all())

    obs = get_observability_backend()
    items: list[AgentRunResponse] = []
    for r in rows:
        resp = AgentRunResponse.model_validate(r)
        resp.trace_url = obs.build_trace_url(r.langfuse_trace_id)
        items.append(resp)
    return items


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
async def _resolve_agent(name: str, db: AsyncSession) -> Agent:
    """Return the Agent with the given name or raise 404."""
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )
    return agent


def _derive_k8s_namespace(agent: Agent) -> str:
    """Derive a deterministic k8s namespace from the agent's team name."""
    return f"agents-{agent.team.lower().replace(' ', '-')}"


# ---------------------------------------------------------------------------
# POST /{name}/deploy
# ---------------------------------------------------------------------------
@router.post(
    "/{name}/deploy",
    response_model=DeploymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Deploy an agent version",
)
async def deploy_agent(
    name: str,
    body: DeploymentCreate,
    x_user_team: Optional[str] = Header(default=None, alias="X-User-Team"),
    db: AsyncSession = Depends(get_db),
) -> DeploymentResponse:
    """Create a new Deployment for a specific agent version.

    Validations (Phase 9.2 pre-flight gates):
    - Agent must exist (404).
    - Version must exist and belong to that agent (404).
    - Deployer team must match agent owner team OR have a cross-team AssetGrant (403).
    - All tools assigned to the agent must have an active AssetGrant for deployer's team (422).
    - No tool may have risk_level='critical' (422).
    - Version must have ``eval_passed=True`` **only when environment='production'** (422 if not);
      sandbox/staging/canary deploys are ungated (the eval gate moved to publish — Decision 20).

    Returns 201 with the new Deployment in ``pending`` status (deploy-controller picks it up).
    """
    agent = await _resolve_agent(name, db)

    if body.version_id is not None:
        # Explicit version — verify it exists and belongs to this agent
        version_result = await db.execute(
            select(AgentVersion).where(
                AgentVersion.id == body.version_id,
                AgentVersion.agent_id == agent.id,
            )
        )
        version = version_result.scalar_one_or_none()
        if version is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Version '{body.version_id}' not found for agent '{name}'."
                ),
            )
    else:
        # No explicit version — snapshot the current agent config, but only mint a
        # NEW version when that snapshot actually differs from the latest one.
        # A no-op redeploy must reuse the existing version, not bump the number
        # (applies to all environments). Previously every deploy created a version,
        # inflating the history with byte-identical duplicates.
        latest_result = await db.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version_number.desc())
            .limit(1)
        )
        latest_version = latest_result.scalar_one_or_none()

        config_snapshot = build_config_snapshot(agent)

        # Auto-snapshot tools from agent_tools join table (authoritative bindings)
        bound_tools_result = await db.execute(
            select(Tool)
            .join(AgentTool, AgentTool.tool_id == Tool.id)
            .where(AgentTool.agent_id == agent.id)
        )
        bound_tools = bound_tools_result.scalars().all()
        tools_snapshot = [
            {"name": t.name, "risk": t.risk_level or "low"}
            for t in bound_tools
        ]

        def _canonical(cfg: dict | None, tools: list | None) -> str:
            # Canonical form for change-detection: sort dict keys AND the tools
            # list (its DB query order is non-deterministic) so only real content
            # changes register as a diff, not ordering noise.
            return json.dumps(
                {
                    "config": cfg or {},
                    "tools": sorted((tools or []), key=lambda t: t.get("name", "")),
                },
                sort_keys=True,
                default=str,
            )

        if latest_version is not None and _canonical(config_snapshot, tools_snapshot) == _canonical(
            latest_version.config, latest_version.tools
        ):
            # Unchanged — reuse the latest version, no new row, no version bump.
            version = latest_version
            logger.info(
                "deploy_agent: reusing version %d for agent '%s' (config unchanged)",
                version.version_number, name,
            )
        else:
            next_version = (latest_version.version_number if latest_version else 0) + 1
            version = AgentVersion(
                agent_id=agent.id,
                version_number=next_version,
                image_tag=None,
                config=config_snapshot,
                tools=tools_snapshot,
            )
            db.add(version)
            await db.flush()
            await db.refresh(version)
            logger.info(
                "deploy_agent: auto-created version %d for agent '%s' (config changed)",
                version.version_number, name,
            )

    # ── Pre-flight gate 1: deployer team check ────────────────────────────────
    deployer_team = x_user_team or agent.team  # fallback keeps backwards compat
    if deployer_team != agent.team:
        # Check for an active cross-team grant on the agent
        grant_result = await db.execute(
            select(AssetGrant.id).where(
                AssetGrant.asset_id == agent.id,
                AssetGrant.grantee_team == deployer_team,
                AssetGrant.revoked_at.is_(None),
            ).limit(1)
        )
        cross_grant = grant_result.scalar_one_or_none()
        if cross_grant is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "deployer_not_in_owner_team"},
            )

    # ── Pre-flight gate 2 & 4: load agent tools, check grants + critical risk ─
    tools_result = await db.execute(
        select(Tool)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent.id)
    )
    agent_tools = tools_result.scalars().all()

    # Gate 4: block critical-risk tools (bound via AgentTool OR declared in version)
    critical_tools = [t.name for t in agent_tools if t.risk_level == "critical"]
    version_tools = version.tools or []
    critical_tools += [
        t.get("name", "unknown")
        for t in version_tools
        if isinstance(t, dict) and t.get("risk", "").lower() == "critical"
    ]
    if critical_tools:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "critical_risk_tool_not_deployable",
                "offending_tools": critical_tools,
            },
        )

    # Gate 2: verify active grants for cross-team tools (own-team tools are
    # implicitly granted — only foreign tools need an explicit AssetGrant).
    missing_grants: list[str] = []
    for tool in agent_tools:
        if tool.owner_team == deployer_team or tool.owner_team is None:
            continue
        grant_result = await db.execute(
            select(AssetGrant.id).where(
                AssetGrant.asset_id == tool.id,
                AssetGrant.grantee_team == deployer_team,
                AssetGrant.revoked_at.is_(None),
            ).limit(1)
        )
        if grant_result.scalar_one_or_none() is None:
            missing_grants.append(tool.name)
    if missing_grants:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "tool_grants_missing",
                "missing_grants": missing_grants,
            },
        )

    # ── Pre-flight gate 3 + 3b: eval gates — PRODUCTION ONLY (Decision 20) ─────
    # Sandbox/staging/canary deploys are ungated so an agent can be deployed and
    # evaluated in the playground before it earns eval_passed. The eval gate now
    # lives on PUBLISH (see routers/agents.py publish_agent).
    if body.environment == "production":
        # Gate 3: eval gate — only passed versions may reach production
        if not version.eval_passed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Version '{body.version_id}' has not passed evaluation "
                    "(eval_passed=False). Run evals before deploying to production."
                ),
            )

        # Gate 3b: adversarial eval required when the version declares high/critical-risk tools
        version_tools_list = version.tools or []
        has_risky_tools = any(
            isinstance(t, dict) and t.get("risk", "low") in ("high", "critical")
            for t in version_tools_list
        )
        has_risky_bound_tools = any(
            t.risk_level in ("high", "critical") for t in agent_tools
        )
        if (has_risky_tools or has_risky_bound_tools) and not version.adversarial_eval_passed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Version '{body.version_id}' has not passed adversarial evaluation "
                    "(adversarial_eval_passed=False). Run adversarial evals before deploying to production."
                ),
            )

    # Resolve LLM provider and write K8s Secret if configured
    llm_secret_name = None
    llm_env_keys = None
    llm_provider_type = None
    llm_provider_model = None

    if agent.llm_provider_id:
        provider_result = await db.execute(
            select(LLMProvider).where(LLMProvider.id == agent.llm_provider_id)
        )
        provider = provider_result.scalar_one_or_none()
        if provider:
            credentials = decrypt_json(provider.credentials_encrypted)
            secret_name = f"agentshield-llm-{provider.id}"
            try:
                agent_namespace = _derive_k8s_namespace(agent)
                await upsert_secret(secret_name, agent_namespace, credentials)
                llm_secret_name = secret_name
                llm_env_keys = list(credentials.keys())
                llm_provider_type = provider.provider
                llm_provider_model = provider.default_model
                logger.info(
                    "deploy_agent: wrote LLM secret %s for provider '%s'",
                    secret_name,
                    provider.name,
                )
            except Exception as exc:
                logger.error(
                    "deploy_agent: failed to write LLM secret for provider '%s': %s",
                    provider.name,
                    exc,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to write LLM credentials secret: {exc}",
                )

    # Deployment name is the primary identifier in the overview UX. Use the
    # caller-provided name or generate "{agent}-{suffix}".
    deployment_name = body.name or f"{name}-{uuid.uuid4().hex[:4]}"

    deployment = Deployment(
        agent_id=agent.id,
        version_id=version.id,
        environment=body.environment,
        status="pending",
        replicas=body.replicas,
        canary_percent=None,
        k8s_namespace=_derive_k8s_namespace(agent),
        name=deployment_name,
        ttl_hours=body.ttl_hours,
        llm_secret_name=llm_secret_name,
        llm_env_keys=llm_env_keys,
        llm_provider_type=llm_provider_type,
        llm_provider_model=llm_provider_model,
    )
    db.add(deployment)
    await db.flush()
    await db.refresh(deployment)

    logger.info(
        "deploy_agent: created deployment %s for agent '%s' version %d "
        "(env=%s, replicas=%d, llm_provider=%s)",
        deployment.id,
        name,
        version.version_number,
        body.environment,
        body.replicas,
        llm_provider_type or "none",
    )

    # Generate OPA policy from version tools (non-fatal — deploy proceeds regardless)
    k8s_ns = deployment.k8s_namespace or "agents-platform"
    try:
        await generate_and_store(db, agent.id, name, version, namespace=k8s_ns)
    except Exception as exc:
        logger.warning("Policy generation failed for agent '%s' (non-fatal): %s", name, exc)

    # Auto-grant ApprovalAuthority to team members for high-risk tools
    # so the Approvals console works without manual admin setup (non-fatal).
    try:
        await _auto_grant_approval_authority(
            db,
            [(t.name, t.risk_level) for t in agent_tools],
            agent.team,
            granted_by=f"auto:deploy:{deployment.id}",
        )
    except Exception as exc:
        logger.warning(
            "Auto-grant ApprovalAuthority failed for agent '%s' (non-fatal): %s",
            name, exc,
        )

    # Emit Langfuse platform action trace
    from tracing import trace_platform_action
    trace_platform_action(
        trace_id=str(deployment.id),
        action="deploy",
        user_id=x_user_team,
        agent_name=name,
        metadata={
            "version_id": str(body.version_id),
            "environment": body.environment,
            "replicas": body.replicas,
        },
    )

    return DeploymentResponse.model_validate(deployment)


# ---------------------------------------------------------------------------
# POST /{name}/rollback
# ---------------------------------------------------------------------------
@router.post(
    "/{name}/rollback",
    response_model=DeploymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Roll back to the previous live deployment",
)
async def rollback_agent(
    name: str,
    body: RollbackRequest,
    db: AsyncSession = Depends(get_db),
) -> DeploymentResponse:
    """Roll back an agent to its previously live version.

    Strategy:
    - Find the most recent deployment with ``status='running'`` (live).
    - If ``target_version_id`` is provided use that version; otherwise use the
      version from the deployment that preceded the current live one.
    - Create a new Deployment record with ``status='deploying'`` pointing at
      the rollback version.

    Raises 404 if the agent has no live deployment or no prior deployment to
    roll back to.
    """
    agent = await _resolve_agent(name, db)

    # Find the most recent "running" (live) deployment
    live_result = await db.execute(
        select(Deployment)
        .where(
            Deployment.agent_id == agent.id,
            Deployment.status == "running",
        )
        .order_by(Deployment.deployed_at.desc())
        .limit(1)
    )
    live_deployment = live_result.scalar_one_or_none()
    if live_deployment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No live (running) deployment found for agent '{name}'.",
        )

    # Determine the rollback version
    if body.target_version_id is not None:
        # Caller specified an explicit version
        target_version_result = await db.execute(
            select(AgentVersion).where(
                AgentVersion.id == body.target_version_id,
                AgentVersion.agent_id == agent.id,
            )
        )
        rollback_version = target_version_result.scalar_one_or_none()
        if rollback_version is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Target version '{body.target_version_id}' not found "
                    f"for agent '{name}'."
                ),
            )
    else:
        # Auto-detect: find the deployment immediately before the live one
        prior_result = await db.execute(
            select(Deployment)
            .where(
                Deployment.agent_id == agent.id,
                Deployment.id != live_deployment.id,
            )
            .order_by(Deployment.deployed_at.desc())
            .limit(1)
        )
        prior_deployment = prior_result.scalar_one_or_none()
        if prior_deployment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No prior deployment found to roll back to for agent "
                    f"'{name}'."
                ),
            )

        # Load the version record from that prior deployment
        prior_version_result = await db.execute(
            select(AgentVersion).where(
                AgentVersion.id == prior_deployment.version_id
            )
        )
        rollback_version = prior_version_result.scalar_one_or_none()
        if rollback_version is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Could not resolve version for prior deployment "
                    f"'{prior_deployment.id}'."
                ),
            )

    rollback_deployment = Deployment(
        agent_id=agent.id,
        version_id=rollback_version.id,
        environment=live_deployment.environment,
        status="pending",
        replicas=live_deployment.replicas,
        canary_percent=None,
        k8s_namespace=_derive_k8s_namespace(agent),
        llm_secret_name=live_deployment.llm_secret_name,
        previous_version_id=live_deployment.version_id,
    )
    db.add(rollback_deployment)
    await db.flush()
    await db.refresh(rollback_deployment)

    logger.info(
        "rollback_agent: created rollback deployment %s for agent '%s' "
        "to version %d (from version_id=%s)",
        rollback_deployment.id,
        name,
        rollback_version.version_number,
        live_deployment.version_id,
    )
    return DeploymentResponse.model_validate(rollback_deployment)


# ---------------------------------------------------------------------------
# GET /{name}/deployments
# ---------------------------------------------------------------------------
@router.get(
    "/{name}/deployments",
    response_model=list[DeploymentResponse],
    summary="List all deployments for an agent",
)
async def list_deployments(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> list[DeploymentResponse]:
    """Return all deployments for an agent ordered by ``deployed_at`` DESC."""
    agent = await _resolve_agent(name, db)

    result = await db.execute(
        select(Deployment)
        .where(Deployment.agent_id == agent.id)
        .order_by(Deployment.deployed_at.desc())
    )
    deployments = result.scalars().all()

    logger.debug(
        "list_deployments: found %d deployment(s) for agent '%s'",
        len(deployments),
        name,
    )
    return [DeploymentResponse.model_validate(d) for d in deployments]


# ---------------------------------------------------------------------------
# PATCH /{name}/deployments/{deployment_id} — lifecycle action
#
# Mirror of the catalog action (routers/catalog.py update_deployment). Sets a
# transitional status the deploy-controller reconciler acts on. "Change a
# deployment's settings" is an UPGRADE to another version — never in-place
# mutation (a deployment's config is frozen to its version).
# ---------------------------------------------------------------------------
@router.patch(
    "/{name}/deployments/{deployment_id}",
    response_model=DeploymentResponse,
    summary="Suspend / Resume / Terminate / Upgrade a sandbox deployment",
)
async def update_sandbox_deployment(
    name: str,
    deployment_id: uuid.UUID,
    body: DeploymentActionRequest,
    db: AsyncSession = Depends(get_db),
) -> DeploymentResponse:
    agent = await _resolve_agent(name, db)
    dep = (await db.execute(
        select(Deployment).where(
            Deployment.id == deployment_id,
            Deployment.agent_id == agent.id,
        )
    )).scalar_one_or_none()
    if dep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found.")

    now = datetime.now(tz=timezone.utc)

    if body.action == "suspend":
        dep.status = "suspending"
        dep.suspended_at = now
    elif body.action == "resume":
        # Back to 'pending' so the controller's reconcile re-applies the manifest
        # (replicas restored → scaled back up). Sandbox pending IS reconciled.
        dep.status = "pending"
        dep.suspended_at = None
    elif body.action == "terminate":
        dep.status = "terminating"
    elif body.action == "upgrade":
        if not body.version_id:
            raise HTTPException(status_code=400, detail="version_id required for upgrade.")
        target = (await db.execute(
            select(AgentVersion).where(
                AgentVersion.id == body.version_id,
                AgentVersion.agent_id == agent.id,
            )
        )).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="Target version not found for this agent.")
        dep.previous_version_id = dep.version_id
        dep.version_id = body.version_id
        # 'pending' so the controller re-reconciles against the new version.
        dep.status = "pending"
    else:  # pragma: no cover — schema Literal guards this
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

    await db.flush()
    await db.refresh(dep)
    logger.info("update_sandbox_deployment: agent=%s dep=%s action=%s -> %s",
                name, deployment_id, body.action, dep.status)
    return DeploymentResponse.model_validate(dep)

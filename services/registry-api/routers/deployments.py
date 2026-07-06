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

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crypto import decrypt_json
from db import get_db
from k8s import upsert_secret
from models import Agent, AgentTool, AgentVersion, AssetGrant, Deployment, LLMProvider, Tool
from policy_generator import generate_and_store
from schemas import DeploymentCreate, DeploymentResponse, PaginatedResponse, RollbackRequest

# Namespace where LLM credential secrets are stored
_PLATFORM_NAMESPACE = "agentshield-platform"

logger = logging.getLogger(__name__)

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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[DeploymentResponse]:
    q = (
        select(Deployment, Agent.name.label("agent_name"))
        .join(Agent, Deployment.agent_id == Agent.id)
        .order_by(Deployment.deployed_at.desc())
    )
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

    # Verify the version exists and belongs to this agent
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

    # ── Pre-flight gate 1: deployer team check ────────────────────────────────
    deployer_team = x_user_team or agent.team  # fallback keeps backwards compat
    if deployer_team != agent.team:
        # Check for an active cross-team grant on the agent
        grant_result = await db.execute(
            select(AssetGrant).where(
                AssetGrant.asset_id == agent.id,
                AssetGrant.grantee_team == deployer_team,
                AssetGrant.revoked_at.is_(None),
            )
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

    # Gate 2: verify active grants for all tools
    missing_grants: list[str] = []
    for tool in agent_tools:
        grant_result = await db.execute(
            select(AssetGrant).where(
                AssetGrant.asset_id == tool.id,
                AssetGrant.grantee_team == deployer_team,
                AssetGrant.revoked_at.is_(None),
            )
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
                await upsert_secret(secret_name, _PLATFORM_NAMESPACE, credentials)
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

    deployment = Deployment(
        agent_id=agent.id,
        version_id=version.id,
        environment=body.environment,
        status="pending",
        replicas=body.replicas,
        canary_percent=None,
        k8s_namespace=_derive_k8s_namespace(agent),
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

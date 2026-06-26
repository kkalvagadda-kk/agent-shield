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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Agent, AgentVersion, Deployment
from schemas import DeploymentCreate, DeploymentResponse, PaginatedResponse, RollbackRequest

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
    db: AsyncSession = Depends(get_db),
) -> DeploymentResponse:
    """Create a new Deployment for a specific agent version.

    Validations:
    - Agent must exist (404).
    - Version must exist and belong to that agent (404).
    - Version must have ``eval_passed=True`` (422 if not).

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

    # Eval gate — only passed versions may be deployed
    if not version.eval_passed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Version '{body.version_id}' has not passed evaluation "
                "(eval_passed=False). Run evals before deploying."
            ),
        )

    deployment = Deployment(
        agent_id=agent.id,
        version_id=version.id,
        environment=body.environment,
        status="pending",
        replicas=body.replicas,
        canary_percent=None,
        k8s_namespace=_derive_k8s_namespace(agent),
    )
    db.add(deployment)
    await db.flush()
    await db.refresh(deployment)

    logger.info(
        "deploy_agent: created deployment %s for agent '%s' version %d "
        "(env=%s, replicas=%d)",
        deployment.id,
        name,
        version.version_number,
        body.environment,
        body.replicas,
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

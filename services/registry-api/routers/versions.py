"""
AgentShield Registry API — Agent Versions router.

Endpoints
---------
  POST   /api/v1/agents/{name}/versions              — register a new version
  GET    /api/v1/agents/{name}/versions              — list all versions (newest first)
  PATCH  /api/v1/agents/{name}/versions/{version_id} — patch eval result / status
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_config import build_config_snapshot
from db import get_db
from models import Agent, AgentRun, AgentTool, AgentVersion, Deployment, ProductionDeployment, PublishedVersion, Tool
from schemas import AgentVersionCreate, AgentVersionPatch, AgentVersionResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["versions"])


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


# ---------------------------------------------------------------------------
# POST /{name}/versions
# ---------------------------------------------------------------------------
@router.post(
    "/{name}/versions",
    response_model=AgentVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new agent version",
)
async def create_version(
    name: str,
    body: AgentVersionCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentVersionResponse:
    """Create a new version record for an existing agent.

    - Resolves the agent by name (404 if missing).
    - Auto-increments ``version_number`` based on the highest existing value.
    - ``image_tag`` must be non-empty for sdk agents (validated here when provided).
    """
    agent = await _resolve_agent(name, db)

    # Validate image_tag: if provided it must not be blank
    if body.image_tag is not None and not body.image_tag.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="image_tag must not be an empty string.",
        )

    # Determine next version_number
    max_result = await db.execute(
        select(AgentVersion.version_number)
        .where(AgentVersion.agent_id == agent.id)
        .order_by(AgentVersion.version_number.desc())
        .limit(1)
    )
    max_version = max_result.scalar_one_or_none()
    next_version = (max_version or 0) + 1

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
    # Merge with any explicitly provided tools from request body
    if body.tools:
        seen = {t["name"] for t in tools_snapshot}
        for t in body.tools:
            d = t.model_dump()
            if d.get("name") not in seen:
                tools_snapshot.append(d)

    config_snapshot = build_config_snapshot(agent)

    version = AgentVersion(
        agent_id=agent.id,
        version_number=next_version,
        image_tag=body.image_tag,
        agent_graph_id=body.agent_graph_id,
        tools=tools_snapshot,
        config=config_snapshot,
        eval_passed=body.eval_passed,
        adversarial_eval_passed=body.adversarial_eval_passed,
        git_sha=body.git_sha,
        git_branch=body.git_branch,
        notes=body.notes,
    )
    db.add(version)
    await db.flush()
    await db.refresh(version)

    logger.info(
        "create_version: registered version %d for agent '%s' (version_id=%s)",
        version.version_number,
        name,
        version.id,
    )
    return AgentVersionResponse.model_validate(version)


# ---------------------------------------------------------------------------
# GET /{name}/versions
# ---------------------------------------------------------------------------
@router.get(
    "/{name}/versions",
    response_model=list[AgentVersionResponse],
    summary="List all versions for an agent",
)
async def list_versions(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> list[AgentVersionResponse]:
    """Return all versions for an agent, ordered newest-first (by created_at DESC)."""
    agent = await _resolve_agent(name, db)

    result = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent.id)
        .order_by(AgentVersion.created_at.desc())
    )
    versions = result.scalars().all()

    logger.debug(
        "list_versions: found %d version(s) for agent '%s'", len(versions), name
    )
    return [AgentVersionResponse.model_validate(v) for v in versions]


# ---------------------------------------------------------------------------
# PATCH /{name}/versions/{version_id}
# ---------------------------------------------------------------------------
@router.patch(
    "/{name}/versions/{version_id}",
    response_model=AgentVersionResponse,
    summary="Patch agent version eval result",
)
async def patch_version(
    name: str,
    version_id: uuid.UUID,
    body: AgentVersionPatch,
    db: AsyncSession = Depends(get_db),
) -> AgentVersionResponse:
    """Update eval result fields on a specific version.

    Only ``eval_passed``, ``status``, and ``notes`` may be modified.
    Returns 404 if the agent or version does not exist.
    """
    agent = await _resolve_agent(name, db)

    result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == version_id,
            AgentVersion.agent_id == agent.id,
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version '{version_id}' not found for agent '{name}'.",
        )

    changed = False
    if body.eval_passed is not None:
        version.eval_passed = body.eval_passed
        changed = True
    if body.adversarial_eval_passed is not None:
        version.adversarial_eval_passed = body.adversarial_eval_passed
        changed = True
    if body.status is not None:
        version.status = body.status
        changed = True
    if body.notes is not None:
        version.notes = body.notes
        changed = True

    if changed:
        await db.flush()
        await db.refresh(version)

    logger.info(
        "patch_version: patched version %d (id=%s) for agent '%s'",
        version.version_number,
        version.id,
        name,
    )
    return AgentVersionResponse.model_validate(version)


# ---------------------------------------------------------------------------
# DELETE /{name}/versions/{version_id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{name}/versions/{version_id}",
    summary="Delete an agent version (cascades sandbox deployments)",
)
async def delete_version(
    name: str,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete an agent version.

    - Returns 404 if the agent or version does not exist.
    - Returns 409 if the version is referenced by an active production deployment.
    - Terminates all non-terminated sandbox Deployment rows for this version.
    """
    agent = await _resolve_agent(name, db)

    result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == version_id,
            AgentVersion.agent_id == agent.id,
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version '{version_id}' not found for agent '{name}'.",
        )

    # Check for active production deployments via the PublishedVersion chain.
    pub_ver_ids = (
        await db.execute(
            select(PublishedVersion.id).where(
                PublishedVersion.source_version_id == version_id
            )
        )
    ).scalars().all()
    if pub_ver_ids:
        prod_dep = (
            await db.execute(
                select(ProductionDeployment).where(
                    ProductionDeployment.version_id.in_(pub_ver_ids)
                )
            )
        ).scalar_one_or_none()
        if prod_dep is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete version with active production deployments. Terminate them first.",
            )

    # Delete all sandbox deployments for this version. The Deployment.version_id FK
    # is NOT NULL, so we must remove the rows before deleting the version.
    # AgentRun.sandbox_deployment_id references deployments — SET NULL first.
    from sqlalchemy import update
    all_deps = (
        await db.execute(
            select(Deployment).where(Deployment.version_id == version_id)
        )
    ).scalars().all()
    terminated_count = 0
    dep_ids = []
    for dep in all_deps:
        dep_ids.append(dep.id)
        if dep.status not in ("terminated",):
            terminated_count += 1

    if dep_ids:
        await db.execute(
            update(AgentRun)
            .where(AgentRun.sandbox_deployment_id.in_(dep_ids))
            .values(sandbox_deployment_id=None)
        )
        for dep in all_deps:
            await db.delete(dep)

    # Clear previous_version_id references from deployments that upgraded FROM this version
    await db.execute(
        update(Deployment)
        .where(Deployment.previous_version_id == version_id)
        .values(previous_version_id=None)
    )

    await db.delete(version)
    await db.commit()

    logger.info(
        "delete_version: deleted version %s for agent '%s' (terminated %d deployments)",
        version_id,
        name,
        terminated_count,
    )
    return {"deleted_version_id": str(version_id), "terminated_deployments": terminated_count}


# ---------------------------------------------------------------------------
# Standalone GET /api/v1/versions/{version_id} — used by deploy-controller
# ---------------------------------------------------------------------------
versions_global_router = APIRouter(prefix="/api/v1/versions", tags=["versions"])


@versions_global_router.get(
    "/{version_id}",
    response_model=AgentVersionResponse,
    summary="Get agent version by ID (deploy-controller use)",
)
async def get_version_by_id(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentVersionResponse:
    result = await db.execute(
        select(AgentVersion).where(AgentVersion.id == version_id)
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version '{version_id}' not found.",
        )
    return AgentVersionResponse.model_validate(version)

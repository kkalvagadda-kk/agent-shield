"""
AgentShield Registry API — AgentGraphs router.

Endpoints
---------
  POST  /api/v1/agent-graphs                                              — create a workflow
  GET   /api/v1/agent-graphs                                              — list workflows (filterable)
  GET   /api/v1/agent-graphs/{agent_graph_id}                                — get workflow + current definition
  PUT   /api/v1/agent-graphs/{agent_graph_id}                                — update workflow definition
  POST  /api/v1/agent-graphs/{agent_graph_id}/deploy                         — mark workflow as deployed
  GET   /api/v1/agent-graphs/{agent_graph_id}/versions                       — list all version snapshots
  POST  /api/v1/agent-graphs/{agent_graph_id}/versions/{version_number}/restore — restore a prior version
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import AgentGraph, AgentGraphVersion
from schemas import (
    AgentGraphCreate,
    AgentGraphDeployRequest,
    AgentGraphResponse,
    AgentGraphUpdate,
    AgentGraphVersionResponse,
    AgentGraphWithDefinitionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent-graphs", tags=["agent-graphs"])


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
async def _resolve_workflow(agent_graph_id: uuid.UUID, db: AsyncSession) -> AgentGraph:
    """Return the AgentGraph with the given id or raise 404."""
    result = await db.execute(
        select(AgentGraph).where(AgentGraph.id == agent_graph_id)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AgentGraph '{agent_graph_id}' not found.",
        )
    return workflow


async def _get_latest_version(
    agent_graph_id: uuid.UUID, db: AsyncSession
) -> AgentGraphVersion | None:
    """Return the highest-numbered AgentGraphVersion for this workflow, or None."""
    result = await db.execute(
        select(AgentGraphVersion)
        .where(AgentGraphVersion.agent_graph_id == agent_graph_id)
        .order_by(AgentGraphVersion.version_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _build_workflow_response(
    workflow: AgentGraph,
    current_version_number: int | None,
) -> dict:
    """Build a plain dict accepted by AgentGraphResponse.model_validate."""
    return {
        "id": workflow.id,
        "name": workflow.name,
        "team": workflow.team,
        "description": workflow.description,
        "status": workflow.status,
        "current_version_number": current_version_number,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "created_by": workflow.created_by,
    }


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------
@router.post(
    "/",
    response_model=AgentGraphResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workflow",
)
async def create_workflow(
    body: AgentGraphCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentGraphResponse:
    """Create a workflow record and persist its initial definition as version 1.

    Returns 409 if a workflow with the same name already exists for the team.
    """
    existing = await db.execute(
        select(AgentGraph).where(
            AgentGraph.name == body.name,
            AgentGraph.team == body.team,
        )
    )
    if existing.scalar_one_or_none() is not None:
        logger.warning(
            "create_workflow: name conflict — '%s' already exists for team '%s'",
            body.name,
            body.team,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A workflow named '{body.name}' already exists for "
                f"team '{body.team}'."
            ),
        )

    workflow = AgentGraph(
        name=body.name,
        team=body.team,
        description=body.description,
    )
    db.add(workflow)
    await db.flush()  # populate server-generated id / timestamps

    # Persist the initial definition as version 1
    initial_version = AgentGraphVersion(
        agent_graph_id=workflow.id,
        version_number=1,
        definition=body.definition,
        change_summary=body.change_summary,
    )
    db.add(initial_version)
    await db.flush()
    await db.refresh(workflow)

    logger.info(
        "create_workflow: created workflow '%s' (id=%s) for team '%s'",
        workflow.name,
        workflow.id,
        workflow.team,
    )
    return AgentGraphResponse.model_validate(
        _build_workflow_response(workflow, current_version_number=1)
    )


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=list[AgentGraphResponse],
    summary="List workflows",
)
async def list_workflows(
    team: Optional[str] = Query(None, description="Filter by team name"),
    db: AsyncSession = Depends(get_db),
) -> list[AgentGraphResponse]:
    """Return all workflows, optionally filtered by team, ordered by created_at DESC."""
    query = select(AgentGraph)
    if team is not None:
        query = query.where(AgentGraph.team == team)
    query = query.order_by(AgentGraph.created_at.desc())

    result = await db.execute(query)
    workflows = result.scalars().all()

    # Resolve latest version number for each workflow
    items: list[AgentGraphResponse] = []
    for wf in workflows:
        latest = await _get_latest_version(wf.id, db)
        items.append(
            AgentGraphResponse.model_validate(
                _build_workflow_response(
                    wf,
                    current_version_number=latest.version_number if latest else None,
                )
            )
        )

    logger.debug(
        "list_workflows: returning %d workflow(s) (team=%s)", len(items), team
    )
    return items


# ---------------------------------------------------------------------------
# GET /{agent_graph_id}
# ---------------------------------------------------------------------------
@router.get(
    "/{agent_graph_id}",
    response_model=AgentGraphWithDefinitionResponse,
    summary="Get workflow with current definition",
)
async def get_workflow(
    agent_graph_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentGraphWithDefinitionResponse:
    """Return a workflow along with its latest definition snapshot."""
    workflow = await _resolve_workflow(agent_graph_id, db)
    latest = await _get_latest_version(workflow.id, db)

    current_definition = (
        AgentGraphVersionResponse.model_validate(latest) if latest else None
    )

    base = _build_workflow_response(
        workflow,
        current_version_number=latest.version_number if latest else None,
    )
    base["current_definition"] = current_definition

    logger.debug(
        "get_workflow: fetched workflow '%s' (id=%s) version=%s",
        workflow.name,
        workflow.id,
        latest.version_number if latest else None,
    )
    return AgentGraphWithDefinitionResponse.model_validate(base)


# ---------------------------------------------------------------------------
# PUT /{agent_graph_id}
# ---------------------------------------------------------------------------
@router.put(
    "/{agent_graph_id}",
    response_model=AgentGraphResponse,
    summary="Update workflow definition",
)
async def update_workflow(
    agent_graph_id: uuid.UUID,
    body: AgentGraphUpdate,
    db: AsyncSession = Depends(get_db),
) -> AgentGraphResponse:
    """Update a workflow's definition (and/or metadata).

    Each call that includes a new ``definition`` increments the version counter
    and saves the prior definition as a AgentGraphVersion record.  Metadata-only
    updates (name, description) do not create a new version.
    """
    workflow = await _resolve_workflow(agent_graph_id, db)

    changed = False
    if body.name is not None:
        workflow.name = body.name
        changed = True
    if body.description is not None:
        workflow.description = body.description
        changed = True

    new_version_number: int | None = None

    if body.definition is not None:
        # Find the current highest version number
        latest = await _get_latest_version(workflow.id, db)
        next_version = (latest.version_number if latest else 0) + 1

        wf_version = AgentGraphVersion(
            agent_graph_id=workflow.id,
            version_number=next_version,
            definition=body.definition,
            change_summary=body.change_summary,
        )
        db.add(wf_version)
        new_version_number = next_version
        changed = True

    if changed:
        workflow.updated_at = datetime.now(tz=timezone.utc)
        await db.flush()
        await db.refresh(workflow)

    # Resolve the effective current version number for the response
    if new_version_number is None:
        latest = await _get_latest_version(workflow.id, db)
        new_version_number = latest.version_number if latest else None

    logger.info(
        "update_workflow: updated workflow '%s' (id=%s), version=%s",
        workflow.name,
        workflow.id,
        new_version_number,
    )
    return AgentGraphResponse.model_validate(
        _build_workflow_response(workflow, current_version_number=new_version_number)
    )


# ---------------------------------------------------------------------------
# POST /{agent_graph_id}/deploy
# ---------------------------------------------------------------------------
@router.post(
    "/{agent_graph_id}/deploy",
    response_model=AgentGraphResponse,
    summary="Mark workflow as deployed",
)
async def deploy_workflow(
    agent_graph_id: uuid.UUID,
    body: AgentGraphDeployRequest,
    db: AsyncSession = Depends(get_db),
) -> AgentGraphResponse:
    """Set the workflow status to ``deployed`` and record ``deployed_at``.

    The Deploy Controller polls for workflows in this state and creates the
    corresponding Kubernetes pod/service.
    """
    workflow = await _resolve_workflow(agent_graph_id, db)

    # Ensure there is at least one version to deploy
    latest = await _get_latest_version(workflow.id, db)
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"AgentGraph '{agent_graph_id}' has no versions — "
                "save a definition before deploying."
            ),
        )

    workflow.status = "published"
    workflow.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()
    await db.refresh(workflow)

    logger.info(
        "deploy_workflow: workflow '%s' (id=%s) marked as published (deployed)",
        workflow.name,
        workflow.id,
    )
    return AgentGraphResponse.model_validate(
        _build_workflow_response(
            workflow, current_version_number=latest.version_number
        )
    )


# ---------------------------------------------------------------------------
# GET /{agent_graph_id}/versions
# ---------------------------------------------------------------------------
@router.get(
    "/{agent_graph_id}/versions",
    response_model=list[AgentGraphVersionResponse],
    summary="List all versions for a workflow",
)
async def list_workflow_versions(
    agent_graph_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[AgentGraphVersionResponse]:
    """Return all AgentGraphVersion records ordered by version DESC (newest first)."""
    # Confirm the workflow exists first
    await _resolve_workflow(agent_graph_id, db)

    result = await db.execute(
        select(AgentGraphVersion)
        .where(AgentGraphVersion.agent_graph_id == agent_graph_id)
        .order_by(AgentGraphVersion.version_number.desc())
    )
    versions = result.scalars().all()

    logger.debug(
        "list_workflow_versions: found %d version(s) for workflow '%s'",
        len(versions),
        agent_graph_id,
    )
    return [AgentGraphVersionResponse.model_validate(v) for v in versions]


# ---------------------------------------------------------------------------
# POST /{agent_graph_id}/versions/{version_number}/restore
# ---------------------------------------------------------------------------
@router.post(
    "/{agent_graph_id}/versions/{version_number}/restore",
    response_model=AgentGraphResponse,
    summary="Restore a prior workflow version",
)
async def restore_workflow_version(
    agent_graph_id: uuid.UUID,
    version_number: int,
    db: AsyncSession = Depends(get_db),
) -> AgentGraphResponse:
    """Restore a prior workflow version.

    Saves the current latest definition as a new ``AgentGraphVersion`` snapshot,
    then copies the target version's definition as another new version, making
    it the effective current definition.

    Does NOT auto-deploy — caller must POST /{id}/deploy separately if needed.

    Returns 404 if the workflow or the requested version number does not exist.
    """
    # 1. Fetch workflow — raises 404 if missing
    workflow = await _resolve_workflow(agent_graph_id, db)

    # 2. Fetch the target AgentGraphVersion row — raises 404 if missing
    target_result = await db.execute(
        select(AgentGraphVersion).where(
            AgentGraphVersion.agent_graph_id == agent_graph_id,
            AgentGraphVersion.version_number == version_number,
        )
    )
    target_version = target_result.scalar_one_or_none()
    if target_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Version {version_number} not found for workflow '{agent_graph_id}'."
            ),
        )

    # 3. Determine the next two version numbers: one to snapshot current, one for restored
    latest = await _get_latest_version(agent_graph_id, db)
    current_max = latest.version_number if latest else 0
    snapshot_version_number = current_max + 1
    restored_version_number = current_max + 2

    # 4. Save current latest definition as a new snapshot (preserves history)
    if latest is not None:
        snapshot = AgentGraphVersion(
            agent_graph_id=workflow.id,
            version_number=snapshot_version_number,
            definition=latest.definition,
            change_summary=f"Auto-snapshot before restore to version {version_number}",
            created_by="system",
        )
        db.add(snapshot)

    # 5. Copy target definition as a new version, making it the current head
    restored = AgentGraphVersion(
        agent_graph_id=workflow.id,
        version_number=restored_version_number if latest is not None else snapshot_version_number,
        definition=target_version.definition,
        change_summary=f"Restored from version {version_number}",
        created_by="system",
    )
    db.add(restored)

    workflow.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()
    await db.refresh(workflow)

    new_version_number = restored.version_number
    logger.info(
        "restore_workflow_version: workflow '%s' (id=%s) restored from version %d "
        "as new version %d",
        workflow.name,
        workflow.id,
        version_number,
        new_version_number,
    )
    return AgentGraphResponse.model_validate(
        _build_workflow_response(workflow, current_version_number=new_version_number)
    )

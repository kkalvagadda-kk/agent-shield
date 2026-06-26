"""
AgentShield Registry API — Workflows router.

Endpoints
---------
  POST  /api/v1/workflows                                              — create a workflow
  GET   /api/v1/workflows                                              — list workflows (filterable)
  GET   /api/v1/workflows/{workflow_id}                                — get workflow + current definition
  PUT   /api/v1/workflows/{workflow_id}                                — update workflow definition
  POST  /api/v1/workflows/{workflow_id}/deploy                         — mark workflow as deployed
  GET   /api/v1/workflows/{workflow_id}/versions                       — list all version snapshots
  POST  /api/v1/workflows/{workflow_id}/versions/{version_number}/restore — restore a prior version
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
from models import Workflow, WorkflowVersion
from schemas import (
    WorkflowCreate,
    WorkflowDeployRequest,
    WorkflowResponse,
    WorkflowUpdate,
    WorkflowVersionResponse,
    WorkflowWithDefinitionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
async def _resolve_workflow(workflow_id: uuid.UUID, db: AsyncSession) -> Workflow:
    """Return the Workflow with the given id or raise 404."""
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        )
    return workflow


async def _get_latest_version(
    workflow_id: uuid.UUID, db: AsyncSession
) -> WorkflowVersion | None:
    """Return the highest-numbered WorkflowVersion for this workflow, or None."""
    result = await db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _build_workflow_response(
    workflow: Workflow,
    current_version_number: int | None,
) -> dict:
    """Build a plain dict accepted by WorkflowResponse.model_validate."""
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
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workflow",
)
async def create_workflow(
    body: WorkflowCreate,
    db: AsyncSession = Depends(get_db),
) -> WorkflowResponse:
    """Create a workflow record and persist its initial definition as version 1.

    Returns 409 if a workflow with the same name already exists for the team.
    """
    existing = await db.execute(
        select(Workflow).where(
            Workflow.name == body.name,
            Workflow.team == body.team,
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

    workflow = Workflow(
        name=body.name,
        team=body.team,
        description=body.description,
    )
    db.add(workflow)
    await db.flush()  # populate server-generated id / timestamps

    # Persist the initial definition as version 1
    initial_version = WorkflowVersion(
        workflow_id=workflow.id,
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
    return WorkflowResponse.model_validate(
        _build_workflow_response(workflow, current_version_number=1)
    )


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=list[WorkflowResponse],
    summary="List workflows",
)
async def list_workflows(
    team: Optional[str] = Query(None, description="Filter by team name"),
    db: AsyncSession = Depends(get_db),
) -> list[WorkflowResponse]:
    """Return all workflows, optionally filtered by team, ordered by created_at DESC."""
    query = select(Workflow)
    if team is not None:
        query = query.where(Workflow.team == team)
    query = query.order_by(Workflow.created_at.desc())

    result = await db.execute(query)
    workflows = result.scalars().all()

    # Resolve latest version number for each workflow
    items: list[WorkflowResponse] = []
    for wf in workflows:
        latest = await _get_latest_version(wf.id, db)
        items.append(
            WorkflowResponse.model_validate(
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
# GET /{workflow_id}
# ---------------------------------------------------------------------------
@router.get(
    "/{workflow_id}",
    response_model=WorkflowWithDefinitionResponse,
    summary="Get workflow with current definition",
)
async def get_workflow(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> WorkflowWithDefinitionResponse:
    """Return a workflow along with its latest definition snapshot."""
    workflow = await _resolve_workflow(workflow_id, db)
    latest = await _get_latest_version(workflow.id, db)

    current_definition = (
        WorkflowVersionResponse.model_validate(latest) if latest else None
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
    return WorkflowWithDefinitionResponse.model_validate(base)


# ---------------------------------------------------------------------------
# PUT /{workflow_id}
# ---------------------------------------------------------------------------
@router.put(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Update workflow definition",
)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdate,
    db: AsyncSession = Depends(get_db),
) -> WorkflowResponse:
    """Update a workflow's definition (and/or metadata).

    Each call that includes a new ``definition`` increments the version counter
    and saves the prior definition as a WorkflowVersion record.  Metadata-only
    updates (name, description) do not create a new version.
    """
    workflow = await _resolve_workflow(workflow_id, db)

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

        wf_version = WorkflowVersion(
            workflow_id=workflow.id,
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
    return WorkflowResponse.model_validate(
        _build_workflow_response(workflow, current_version_number=new_version_number)
    )


# ---------------------------------------------------------------------------
# POST /{workflow_id}/deploy
# ---------------------------------------------------------------------------
@router.post(
    "/{workflow_id}/deploy",
    response_model=WorkflowResponse,
    summary="Mark workflow as deployed",
)
async def deploy_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowDeployRequest,
    db: AsyncSession = Depends(get_db),
) -> WorkflowResponse:
    """Set the workflow status to ``deployed`` and record ``deployed_at``.

    The Deploy Controller polls for workflows in this state and creates the
    corresponding Kubernetes pod/service.
    """
    workflow = await _resolve_workflow(workflow_id, db)

    # Ensure there is at least one version to deploy
    latest = await _get_latest_version(workflow.id, db)
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Workflow '{workflow_id}' has no versions — "
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
    return WorkflowResponse.model_validate(
        _build_workflow_response(
            workflow, current_version_number=latest.version_number
        )
    )


# ---------------------------------------------------------------------------
# GET /{workflow_id}/versions
# ---------------------------------------------------------------------------
@router.get(
    "/{workflow_id}/versions",
    response_model=list[WorkflowVersionResponse],
    summary="List all versions for a workflow",
)
async def list_workflow_versions(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[WorkflowVersionResponse]:
    """Return all WorkflowVersion records ordered by version DESC (newest first)."""
    # Confirm the workflow exists first
    await _resolve_workflow(workflow_id, db)

    result = await db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version_number.desc())
    )
    versions = result.scalars().all()

    logger.debug(
        "list_workflow_versions: found %d version(s) for workflow '%s'",
        len(versions),
        workflow_id,
    )
    return [WorkflowVersionResponse.model_validate(v) for v in versions]


# ---------------------------------------------------------------------------
# POST /{workflow_id}/versions/{version_number}/restore
# ---------------------------------------------------------------------------
@router.post(
    "/{workflow_id}/versions/{version_number}/restore",
    response_model=WorkflowResponse,
    summary="Restore a prior workflow version",
)
async def restore_workflow_version(
    workflow_id: uuid.UUID,
    version_number: int,
    db: AsyncSession = Depends(get_db),
) -> WorkflowResponse:
    """Restore a prior workflow version.

    Saves the current latest definition as a new ``WorkflowVersion`` snapshot,
    then copies the target version's definition as another new version, making
    it the effective current definition.

    Does NOT auto-deploy — caller must POST /{id}/deploy separately if needed.

    Returns 404 if the workflow or the requested version number does not exist.
    """
    # 1. Fetch workflow — raises 404 if missing
    workflow = await _resolve_workflow(workflow_id, db)

    # 2. Fetch the target WorkflowVersion row — raises 404 if missing
    target_result = await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.version_number == version_number,
        )
    )
    target_version = target_result.scalar_one_or_none()
    if target_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Version {version_number} not found for workflow '{workflow_id}'."
            ),
        )

    # 3. Determine the next two version numbers: one to snapshot current, one for restored
    latest = await _get_latest_version(workflow_id, db)
    current_max = latest.version_number if latest else 0
    snapshot_version_number = current_max + 1
    restored_version_number = current_max + 2

    # 4. Save current latest definition as a new snapshot (preserves history)
    if latest is not None:
        snapshot = WorkflowVersion(
            workflow_id=workflow.id,
            version_number=snapshot_version_number,
            definition=latest.definition,
            change_summary=f"Auto-snapshot before restore to version {version_number}",
            created_by="system",
        )
        db.add(snapshot)

    # 5. Copy target definition as a new version, making it the current head
    restored = WorkflowVersion(
        workflow_id=workflow.id,
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
    return WorkflowResponse.model_validate(
        _build_workflow_response(workflow, current_version_number=new_version_number)
    )

"""
AgentShield Registry API — Skills router.

Endpoints
---------
  POST   /api/v1/skills/             — create a new skill
  GET    /api/v1/skills/             — list skills (filterable, paginated)
  GET    /api/v1/skills/{skill_id}   — get one skill by UUID
  PUT    /api/v1/skills/{skill_id}   — update skill fields
  DELETE /api/v1/skills/{skill_id}   — hard delete
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user, require_user
from catalog_visibility import catalog_visibility_clause
from db import get_db
from rbac import get_user_team
from models import Skill
from schemas import PaginatedResponse, SkillCreate, SkillResponse, SkillUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------
@router.post(
    "/",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new skill",
    # G-R3-6: skills.py had NO auth on any route. Mutations are gated; the READS stay
    # open because declarative-runner workflow_executor.py:232 fetches
    # GET /skills/{id} with no Authorization header. Same exemption shape as R1's
    # G-R1-*; closing it needs identity Phase 3's service identity.
    dependencies=[Depends(require_user)],
)
async def create_skill(
    body: SkillCreate,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> SkillResponse:
    """Create a new skill record.  Returns 409 if (name, team) already exists.

    Sets `created_by`. It never did — the column existed and stayed NULL on every row.
    Harmless while skills defaulted to `published`; after migration 0080 a private skill
    with a NULL creator matches neither arm of `published OR created_by == caller` and is
    invisible to everyone, including the person who just created it.
    """
    existing = await db.execute(
        select(Skill).where(Skill.name == body.name, Skill.team == body.team)
    )
    if existing.scalar_one_or_none() is not None:
        logger.warning(
            "create_skill: conflict — skill '%s' already exists for team '%s'",
            body.name,
            body.team,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A skill named '{body.name}' already exists for team '{body.team}'.",
        )

    skill = Skill(
        name=body.name,
        team=body.team,
        description=body.description,
        tool_ids=body.tool_ids,
        created_by=claims["sub"],
    )
    db.add(skill)
    await db.flush()
    await db.refresh(skill)

    logger.info("create_skill: created skill '%s' (id=%s)", skill.name, skill.id)
    return SkillResponse.model_validate(skill)


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[SkillResponse],
    summary="List skills",
)
async def list_skills(
    team: Optional[str] = Query(None, description="Filter by team name"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(50, ge=1, le=500, description="Records per page"),
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[SkillResponse]:
    """Return a paginated list of skills, optionally filtered by team."""
    caller = claims["sub"]

    base_query = select(Skill)
    count_query = select(func.count()).select_from(Skill)

    # Visibility (Decision 47 / migration 0080) — same producer as list_tools.
    # CREATOR-scoped, matching agents and workflows.
    vis = catalog_visibility_clause(
        publish_status_col=Skill.publish_status,
        created_by_col=Skill.created_by,
        caller_sub=caller,
    )
    base_query = base_query.where(vis)
    count_query = count_query.where(vis)

    if team is not None:
        base_query = base_query.where(Skill.team == team)
        count_query = count_query.where(Skill.team == team)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * page_size
    rows_result = await db.execute(
        base_query.order_by(Skill.created_at.desc()).limit(page_size).offset(offset)
    )
    skills = rows_result.scalars().all()

    logger.debug(
        "list_skills: returning %d/%d skills (team=%s, page=%d)",
        len(skills),
        total,
        team,
        page,
    )

    return PaginatedResponse[SkillResponse](
        items=[SkillResponse.model_validate(s) for s in skills],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /{skill_id}
# ---------------------------------------------------------------------------
@router.get(
    "/{skill_id}",
    response_model=SkillResponse,
    summary="Get skill by ID",
)
async def get_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SkillResponse:
    """Fetch a single skill by its UUID.  Returns 404 if not found."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found.",
        )

    logger.debug("get_skill: fetched skill '%s' (id=%s)", skill.name, skill.id)
    return SkillResponse.model_validate(skill)


# ---------------------------------------------------------------------------
# PUT /{skill_id}
# ---------------------------------------------------------------------------
@router.put(
    "/{skill_id}",
    response_model=SkillResponse,
    summary="Update skill",
    dependencies=[Depends(require_user)],  # G-R3-6 — see create_skill above
)
async def update_skill(
    skill_id: uuid.UUID,
    body: SkillUpdate,
    db: AsyncSession = Depends(get_db),
) -> SkillResponse:
    """Update mutable skill fields (name, description, tool_ids, status).
    Returns 404 if the skill does not exist."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found.",
        )

    changed = False
    if body.name is not None:
        skill.name = body.name
        changed = True
    if body.description is not None:
        skill.description = body.description
        changed = True
    if body.tool_ids is not None:
        skill.tool_ids = body.tool_ids
        changed = True
    if body.status is not None:
        skill.status = body.status
        changed = True

    if changed:
        skill.updated_at = datetime.now(tz=timezone.utc)
        await db.flush()
        await db.refresh(skill)

    logger.info("update_skill: updated skill '%s' (id=%s)", skill.name, skill.id)
    return SkillResponse.model_validate(skill)


# ---------------------------------------------------------------------------
# DELETE /{skill_id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete skill",
    dependencies=[Depends(require_user)],  # G-R3-6 — see create_skill above
)
async def delete_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Hard-delete a skill by UUID.  Returns 404 if not found."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found.",
        )

    await db.delete(skill)
    await db.flush()

    logger.info("delete_skill: deleted skill '%s' (id=%s)", skill_id, skill_id)

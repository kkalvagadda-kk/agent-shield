"""
Playground Dataset CRUD endpoints.

Endpoints
---------
  GET    /api/v1/playground/datasets         — list caller's datasets
  POST   /api/v1/playground/datasets         — create dataset
  GET    /api/v1/playground/datasets/{id}    — get one dataset
  PATCH  /api/v1/playground/datasets/{id}    — update name or items
  DELETE /api/v1/playground/datasets/{id}    — delete dataset
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user
from db import get_db
from models import PlaygroundDataset
from schemas import (
    PlaygroundDatasetCreate,
    PlaygroundDatasetResponse,
    PlaygroundDatasetUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/playground", tags=["datasets"])


async def _resolve_dataset(
    dataset_id: uuid.UUID,
    caller: Optional[str],
    db: AsyncSession,
    *,
    require_owner: bool = True,
) -> PlaygroundDataset:
    result = await db.execute(
        select(PlaygroundDataset).where(PlaygroundDataset.id == dataset_id)
    )
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if require_owner and caller and ds.owner_user_id != caller:
        raise HTTPException(status_code=403, detail="Not the dataset owner")
    return ds


# ---------------------------------------------------------------------------
# GET /api/v1/playground/datasets
# ---------------------------------------------------------------------------
@router.get(
    "/datasets",
    response_model=list[PlaygroundDatasetResponse],
    summary="List playground datasets",
)
async def list_datasets(
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[PlaygroundDatasetResponse]:
    """List datasets owned by the caller."""
    caller = (user or {}).get("sub") or x_user_sub
    q = select(PlaygroundDataset).order_by(PlaygroundDataset.created_at.desc())
    if caller:
        q = q.where(PlaygroundDataset.owner_user_id == caller)
    result = await db.execute(q)
    return [PlaygroundDatasetResponse.model_validate(d) for d in result.scalars().all()]


# ---------------------------------------------------------------------------
# POST /api/v1/playground/datasets
# ---------------------------------------------------------------------------
@router.post(
    "/datasets",
    status_code=status.HTTP_201_CREATED,
    response_model=PlaygroundDatasetResponse,
    summary="Create playground dataset",
)
async def create_dataset(
    body: PlaygroundDatasetCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> PlaygroundDatasetResponse:
    caller = (user or {}).get("sub") or x_user_sub or "dev"
    # `body.mode` is the authoring discriminator (reactive|durable|…). Persisting
    # it is what makes a durable dataset actually durable — dropping it (the E-0
    # bug) silently stored every dataset as reactive, so the eval mode/dataset
    # guard never fired and the durable branch never ran. Item validation
    # (incl. rejecting a malformed durable `expected_trajectory` with 422) runs
    # in PlaygroundDatasetCreate's `_check_items` validator against this mode.
    ds = PlaygroundDataset(
        owner_user_id=caller,
        name=body.name,
        mode=body.mode,
        schema_version=body.schema_version,
        items=body.items,
    )
    db.add(ds)
    await db.flush()
    logger.info(
        "create_dataset: id=%s name=%s owner=%s mode=%s items=%d",
        ds.id, ds.name, caller, ds.mode, len(body.items),
    )
    return PlaygroundDatasetResponse.model_validate(ds)


# ---------------------------------------------------------------------------
# GET /api/v1/playground/datasets/{dataset_id}
# ---------------------------------------------------------------------------
@router.get(
    "/datasets/{dataset_id}",
    response_model=PlaygroundDatasetResponse,
    summary="Get a playground dataset",
)
async def get_dataset(
    dataset_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> PlaygroundDatasetResponse:
    caller = (user or {}).get("sub") or x_user_sub
    ds = await _resolve_dataset(dataset_id, caller, db, require_owner=False)
    return PlaygroundDatasetResponse.model_validate(ds)


# ---------------------------------------------------------------------------
# PATCH /api/v1/playground/datasets/{dataset_id}
# ---------------------------------------------------------------------------
@router.patch(
    "/datasets/{dataset_id}",
    response_model=PlaygroundDatasetResponse,
    summary="Update dataset name or items",
)
async def update_dataset(
    dataset_id: uuid.UUID,
    body: PlaygroundDatasetUpdate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> PlaygroundDatasetResponse:
    caller = (user or {}).get("sub") or x_user_sub
    ds = await _resolve_dataset(dataset_id, caller, db, require_owner=True)
    if body.name is not None:
        ds.name = body.name
    if body.mode is not None:
        ds.mode = body.mode
    if body.items is not None:
        # Re-validate the incoming items against the EFFECTIVE mode (the restated
        # `body.mode`, else the dataset's stored mode). PlaygroundDatasetUpdate's
        # validator only sees `body.mode`, so a PATCH that changes durable items
        # without restating mode would validate them as reactive. Re-checking here
        # against the persisted mode keeps a durable dataset's items validated as
        # durable — a malformed `expected_trajectory` is rejected 422, not stored.
        from schemas import _validate_dataset_items

        effective_mode = body.mode or ds.mode
        try:
            _validate_dataset_items(body.items, effective_mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        ds.items = body.items
    await db.flush()
    return PlaygroundDatasetResponse.model_validate(ds)


# ---------------------------------------------------------------------------
# DELETE /api/v1/playground/datasets/{dataset_id}
# ---------------------------------------------------------------------------
@router.delete(
    "/datasets/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a playground dataset",
)
async def delete_dataset(
    dataset_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    caller = (user or {}).get("sub") or x_user_sub
    ds = await _resolve_dataset(dataset_id, caller, db, require_owner=True)
    await db.delete(ds)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dataset is referenced by one or more eval runs and cannot be deleted.",
        )
    logger.info("delete_dataset: id=%s", dataset_id)

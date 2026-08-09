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

from fastapi import APIRouter, Depends, HTTPException, status
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import Caller, resolve_caller
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
    """Load a dataset, enforcing ownership when `require_owner`.

    The ownership guard used to read `if require_owner and caller and ...`, and the write
    routes derived `caller = (user or {}).get("sub") or x_user_sub` under `get_optional_user`
    — which never raises. So a request with NO credential arrived with `caller=None`, the
    `caller and` operand made the guard no-op, and update/delete proceeded on ANY dataset.
    That was the only ownership check on either route. Datasets carry test inputs and
    expected outputs, which is frequently real business logic.

    The same file already fixed the LIST path with an explicit deny-by-default `else`
    (see `list_datasets`); the write path kept the failing-open shape. `caller` is now
    required to be non-empty whenever `require_owner` is set — an unidentified caller is
    refused before the comparison, never by it.
    """
    if require_owner and not caller:
        # BEFORE the row lookup, not after. Structural: the ONLY way to skip an ownership
        # comparison is to not ask for one (require_owner=False), never to arrive without an
        # identity. Answering 404 first would also disclose which dataset IDs exist to a
        # caller who has not identified itself — the same 401-beats-404 rule the approvals
        # decide path states (routers/approvals.py) and the tool-unpublish path enforces.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to modify a dataset.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = await db.execute(
        select(PlaygroundDataset).where(PlaygroundDataset.id == dataset_id)
    )
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if require_owner and ds.owner_user_id != caller:
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
    # Same single source as the rest of the router. The deny-by-default `else` below stays as
    # a second line of defence, but it can no longer be REACHED by omitting a credential —
    # which is strictly better than catching it afterwards.
    identity: Caller = Depends(resolve_caller),
    db: AsyncSession = Depends(get_db),
) -> list[PlaygroundDatasetResponse]:
    """List datasets owned by the caller."""
    caller = identity.require_user_sub()
    q = select(PlaygroundDataset).order_by(PlaygroundDataset.created_at.desc())
    if caller:
        q = q.where(PlaygroundDataset.owner_user_id == caller)
    else:
        # DENY-BY-DEFAULT — see the twin comment in `routers/eval_runner.py`.
        # This route used `get_optional_user` (returns None, never raises) with the
        # ownership filter inside `if caller:` and no else, and registry-api has no
        # global auth middleware. An anonymous caller therefore received EVERY
        # playground dataset — and datasets carry test inputs and expected outputs,
        # which is frequently real business logic.
        #
        # Fixed here in the same change as eval-runs on purpose: patching only the
        # route that happened to be under edit would have left this one standing as
        # the next instance of a class `agents.py` already documented.
        #
        # Regression: suite-89 T-S89-006.
        q = q.where(sa.false())
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
    # ONE identity source for this whole router, and it is the credential.
    #
    # This route had `caller = (user or {}).get("sub") or x_user_sub or "dev"`, so a dataset's
    # OWNER could be a typed header or the literal string "dev" — measured on the cluster:
    # rows owned by `dev` and by `e2e-suite9-user` sit next to rows owned by real subs.
    # Leaving that while update/delete moved to the credential would have been worse than
    # either choice alone: the owner recorded at create and the caller compared at write
    # would be drawn from DIFFERENT sources, so a legitimate owner gets 403 on their own
    # dataset. Ownership only means something if both sides name the same kind of thing.
    identity: Caller = Depends(resolve_caller),
    db: AsyncSession = Depends(get_db),
) -> PlaygroundDatasetResponse:
    caller = identity.require_user_sub()
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
    # Same single source. `require_owner=False` is retained deliberately — reading another
    # team member's dataset is allowed today — but a CREDENTIAL is not optional: dataset items
    # are test inputs and expected outputs, which is frequently real business logic. Whether
    # the read should ALSO be owner- or team-scoped is a separate policy question; it is in the
    # gap ledger rather than changed silently here.
    identity: Caller = Depends(resolve_caller),
    db: AsyncSession = Depends(get_db),
) -> PlaygroundDatasetResponse:
    caller = identity.require_user_sub()
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
    # `X-User-Sub` is GONE from this signature. Requiring a credential was not enough on its
    # own: the owner comparison was `ds.owner_user_id != caller` with
    # `caller = (user or {}).get("sub") or x_user_sub`, so any caller could satisfy it by
    # TYPING the owner's sub in a header. That is the same forgeable identity identity P3
    # removed from the approvals routes — an identity the caller supplies is not an identity.
    identity: Caller = Depends(resolve_caller),
    db: AsyncSession = Depends(get_db),
) -> PlaygroundDatasetResponse:
    caller = identity.require_user_sub()
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
    # Same as update_dataset: the credential, and only the credential. Deletion is the
    # least recoverable thing this router does, so it was the worst place to accept a
    # header-supplied owner.
    identity: Caller = Depends(resolve_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    caller = identity.require_user_sub()
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

"""
Webhook client registration (WS-4) — per-application credentials + allowlist.

  POST   /api/v1/triggers/{trigger_id}/clients              — RETIRED, 410 Gone
  GET    /api/v1/triggers/{trigger_id}/clients              — list (never the secret)
  PATCH  /api/v1/triggers/{trigger_id}/clients/{client_id}  — RETIRED, 410 Gone
  DELETE /api/v1/triggers/{trigger_id}/clients/{client_id}  — RETIRED, 410 Gone

Write endpoints retired (webhook-application-identity T011)
-------------------------------------------------------------
The event-gateway no longer reads `webhook_clients` for signature verification —
it resolves `applications` + `artifact_role_grants` instead (T009/T010). Creating,
updating, or deleting a row here would be a dead end: the gateway would never see
it. `create_webhook_client`, `update_webhook_client`, and `delete_webhook_client`
therefore return `410 Gone` unconditionally, before any DB access, redirecting
callers to `POST /api/v1/teams/{team}/applications` (create a reusable
application) followed by `POST /api/v1/artifacts/{artifact_type}/{artifact_id}/grants`
with `role='invoker'` (authorize it). See
`docs/design/todo/webhook-application-identity.md`. `list_webhook_clients` (GET)
is untouched — existing rows (including ones backfilled into `applications` by
migration 0070) remain visible for operator reference.

Why this is its own router, keyed on `trigger_id` ALONE
-------------------------------------------------------
A webhook client belongs to a *trigger*, and nothing about it depends on what the
trigger targets. Agent triggers are served by `routers/triggers.py` under
`/api/v1/agents/{name}/triggers/...` and workflow triggers by
`routers/composite_workflows.py` under `/api/v1/workflows/{id}/triggers/...` —
but both are rows in the SAME `agent_triggers` table (a workflow trigger is one
with `workflow_id` set). Bolting `/clients` onto both would produce two
hand-maintained copies of identical logic: exactly the two-parallel-paths drift
that `docs/bugs/side-effecting-lost-on-declarative-runner-path.md` documents,
where the second copy silently dropped a field and a fail-closed default hid it
for weeks. ONE router on the trigger's own id serves both shapes, so the workflow
hook gets client signing for free and there is no second path to drift.

Secret handling
---------------
The secret is generated here, returned exactly once in the 201, and stored
Fernet-encrypted (`crypto.encrypt_json`). It is **encrypted, not hashed**: the
gateway must recompute `HMAC_SHA256(secret, ...)` to verify a signature, so the
raw value has to be recoverable. Reveal-once is guaranteed by the *type* system,
not by handler discipline — `WebhookClientResponse` has no `secret` field, so no
read path can leak it even if someone adds one later.

Authorization mirrors the sibling trigger routers (`triggers.py`,
`composite_workflows.py`): `get_optional_user` for the `created_by` audit stamp.
Trigger-scoped ownership enforcement is a gap those routers share and is not
introduced here (see the WS-4 gap ledger).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user
from crypto import encrypt_json
from db import get_db
from models import AgentTrigger, WebhookClient
from schemas import (
    WebhookClientCreate,
    WebhookClientCreatedResponse,
    WebhookClientResponse,
    WebhookClientUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/triggers", tags=["webhook-clients"])

# `whsec_` prefix mirrors the industry convention (Stripe/Svix) so a leaked secret
# is greppable in logs and recognizable to the operator pasting it into a sender.
_SECRET_PREFIX = "whsec_"


async def _get_trigger(trigger_id: uuid.UUID, db: AsyncSession) -> AgentTrigger:
    """Resolve a trigger by id alone — agent-targeted or workflow-targeted alike."""
    result = await db.execute(select(AgentTrigger).where(AgentTrigger.id == trigger_id))
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found")
    return trigger


async def _get_client(
    trigger_id: uuid.UUID, client_id: str, db: AsyncSession
) -> WebhookClient:
    await _get_trigger(trigger_id, db)
    result = await db.execute(
        select(WebhookClient).where(
            WebhookClient.trigger_id == trigger_id,
            WebhookClient.client_id == client_id,
        )
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(
            status_code=404,
            detail=f"Client '{client_id}' not registered on trigger '{trigger_id}'",
        )
    return client


@router.post(
    "/{trigger_id}/clients",
    response_model=WebhookClientCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook_client(
    trigger_id: uuid.UUID,
    body: WebhookClientCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> WebhookClientCreatedResponse:
    """Register an application against this trigger's allowlist.

    The plaintext secret is returned HERE AND NOWHERE ELSE, ever.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="webhook_clients registration is retired. Use POST /api/v1/teams/{team}/applications to create a reusable application, then POST /api/v1/artifacts/{artifact_type}/{artifact_id}/grants with role='invoker' to authorize it — see docs/design/todo/webhook-application-identity.md.",
    )
    trigger = await _get_trigger(trigger_id, db)
    if trigger.trigger_type != "webhook":
        raise HTTPException(
            status_code=400,
            detail="Only webhook triggers accept signing clients",
        )

    secret = _SECRET_PREFIX + secrets.token_urlsafe(32)
    client = WebhookClient(
        trigger_id=trigger.id,
        client_id=body.client_id,
        secret_encrypted=encrypt_json({"secret": secret}),
        created_by=(user or {}).get("sub") or x_user_sub,
    )
    db.add(client)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # UNIQUE(trigger_id, client_id) — the allowlist key is per-trigger, so the
        # same client_id on a DIFFERENT trigger is legal and must still succeed.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Client '{body.client_id}' is already registered on this trigger",
        )

    # THE UPGRADE (WS-4). Registering the first application is the moment per-app auth
    # becomes possible, so it is the moment the trigger switches to it — in the SAME
    # transaction as the client insert, so the mode and the allowlist can never
    # disagree. This is what keeps `client_signed` ⟺ "at least one client exists":
    # birthing a trigger `client_signed` with an empty allowlist would instead mean a
    # trigger that authenticates nobody, and `auth_mode` is not settable through the
    # trigger API, so there would be no supported way back to a working token trigger.
    #
    # ONE-WAY on purpose. Deleting the last client does NOT revert to `token`: a revoke
    # must lock the door, not silently re-open the coarse per-trigger bearer token that
    # the operator upgraded away from. A trigger whose every client is revoked
    # correctly authenticates nobody until one is re-registered — fail closed.
    #
    # The coarse token stops working the instant this flips. That is the intended,
    # explicit, operator-initiated cutover (plan §1: migrate senders one at a time);
    # suite-76 T-S76-005 asserts the bare token is rejected afterwards, which is what
    # proves the mode is a real branch and not a try-token-then-fall-back chain.
    if trigger.auth_mode != "client_signed":
        logger.info(
            "trigger %s auth_mode token -> client_signed (first client '%s' registered)",
            trigger_id, body.client_id,
        )
        trigger.auth_mode = "client_signed"

    await db.commit()
    await db.refresh(client)
    logger.info(
        "registered webhook client '%s' on trigger %s", body.client_id, trigger_id
    )
    # Constructed explicitly (not from_attributes): the secret exists only in this
    # local variable and is never read back off the row.
    return WebhookClientCreatedResponse(
        client_id=client.client_id,
        secret=secret,
        created_at=client.created_at,
    )


@router.get("/{trigger_id}/clients", response_model=list[WebhookClientResponse])
async def list_webhook_clients(
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[WebhookClient]:
    """List this trigger's allowlist. The response model has no secret field."""
    await _get_trigger(trigger_id, db)
    result = await db.execute(
        select(WebhookClient)
        .where(WebhookClient.trigger_id == trigger_id)
        .order_by(WebhookClient.created_at)
    )
    return list(result.scalars().all())


@router.patch(
    "/{trigger_id}/clients/{client_id}", response_model=WebhookClientResponse
)
async def update_webhook_client(
    trigger_id: uuid.UUID,
    client_id: str,
    body: WebhookClientUpdate,
    db: AsyncSession = Depends(get_db),
) -> WebhookClient:
    """Enable/disable a client. The gateway reads `enabled` on every request, so a
    disable takes effect on the very next webhook — no cache to invalidate."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="webhook_clients registration is retired. Use POST /api/v1/teams/{team}/applications to create a reusable application, then POST /api/v1/artifacts/{artifact_type}/{artifact_id}/grants with role='invoker' to authorize it — see docs/design/todo/webhook-application-identity.md.",
    )
    client = await _get_client(trigger_id, client_id, db)
    client.enabled = body.enabled
    await db.commit()
    await db.refresh(client)
    logger.info(
        "set webhook client '%s' on trigger %s enabled=%s",
        client_id,
        trigger_id,
        body.enabled,
    )
    return client


@router.delete(
    "/{trigger_id}/clients/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # response_model=None is REQUIRED, not decorative: without it FastAPI infers a
    # response field from the `-> None` return annotation and asserts at import time
    # ("Status code 204 must not have a response body"), crash-looping the pod on
    # startup. Same idiom as triggers.py:200 / datasets.py:181.
    response_model=None,
)
async def delete_webhook_client(
    trigger_id: uuid.UUID,
    client_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a client permanently. Its secret is unrecoverable — re-registering
    mints a new one."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="webhook_clients registration is retired. Use POST /api/v1/teams/{team}/applications to create a reusable application, then POST /api/v1/artifacts/{artifact_type}/{artifact_id}/grants with role='invoker' to authorize it — see docs/design/todo/webhook-application-identity.md.",
    )
    client = await _get_client(trigger_id, client_id, db)
    await db.delete(client)
    await db.commit()
    logger.info("deleted webhook client '%s' on trigger %s", client_id, trigger_id)

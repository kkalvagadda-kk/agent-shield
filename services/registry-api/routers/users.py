"""User directory — the minimum identity needed to grant something to a person.

  GET /api/v1/users/directory

Why this exists (R2, 2026-08-06). The artifact grant form
(`studio/src/components/shared/ArtifactGrantsList.tsx`) is rendered on the agent
Settings tab and the workflow triggers panel — surfaces a *contributor* reaches. It
populated its "grant to user" picker from `GET /api/v1/admin/users`, which R2 restricts
to platform-admin. Without a replacement, a contributor holding `agent-admin` on their
own agent could still create the grant the model entitles them to (§2: agent-admin may
delegate agent-admin/approver/invoker on that artifact) but would face an empty picker
— a capability broken silently by an authorization change, which is the failure mode
CLAUDE.md DoD rule 5 exists to prevent.

The tradeoff, stated rather than buried (Decision 43): ANY authenticated user can
enumerate usernames here. That is inherent to a name picker — you cannot grant to a
person you cannot name. What this endpoint deliberately does NOT return is everything
that made `/admin/users` sensitive: no email, no `enabled` flag, no team, no global
role, no Keycloak internal state. It is strictly less than every role could already
read before R2, not more.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth_middleware import require_user
from keycloak_client import list_users as kc_list

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class DirectoryEntry(BaseModel):
    """`sub` is the Keycloak user id — the same value `artifact_role_grants.grantee_id`
    stores for a `user` grantee, so the picker's option value needs no translation."""

    sub: str
    username: str
    display_name: str


@router.get("/directory", response_model=list[DirectoryEntry])
async def user_directory(
    claims: dict = Depends(require_user),  # noqa: ARG001 — the gate, not an input
) -> list[DirectoryEntry]:
    """Name + id for every user, for grant pickers. Any authenticated role.

    Disabled accounts are filtered out: granting a role to an account that cannot log
    in produces a grant row that never takes effect and an audit trail that reads as
    though someone has access.
    """
    kc_users = await kc_list()
    out: list[DirectoryEntry] = []
    for u in kc_users:
        if not u.get("enabled", True):
            continue
        username = u.get("username") or ""
        full = " ".join(x for x in (u.get("firstName"), u.get("lastName")) if x).strip()
        out.append(
            DirectoryEntry(
                sub=u["id"],
                username=username,
                display_name=full or username,
            )
        )
    out.sort(key=lambda e: e.username)
    return out

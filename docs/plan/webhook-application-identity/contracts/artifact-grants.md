# Contract — Generic Artifact Grants

`services/registry-api/routers/artifact_grants.py`. First live consumer of `rbac.can_delegate_role` / the `artifact_role_grants` table's delegation rule (design doc §3, §11.1) — works identically for `user`, `team`, and `application` grantees. Router prefix `/api/v1/artifacts`, path params carry `artifact_type`/`artifact_id` (this repo's existing convention — see `composite_workflows.py`'s `/{workflow_id}/triggers` — does not bake path params into `APIRouter(prefix=...)`).

## Security invariants (restated per this repo's `rbac-design.md` §3 + Decision 25/30)

- Every mutating call requires `require_user` (real Keycloak JWT — no `X-User-Sub` bypass).
- `POST`/`DELETE` are gated by `rbac.can_delegate_role(db, caller_sub, artifact_id, target_role)`: `platform-admin` may grant/revoke any of the three roles on any artifact; an `agent-admin` on the specific artifact may grant/revoke `agent-admin`, `approver`, or `invoker` **within that artifact's scope only**; anyone else gets `403`.
- Revocation uses the same `can_delegate_role` check evaluated against the **role being revoked** (read off the target grant row first, not the caller's intent) — an agent-admin cannot use a revoke call to affect a role they would not be allowed to grant.
- `GET` is unauthenticated-read, matching this repo's existing convention for grant/trigger listing endpoints (`list_triggers`, `list_webhook_clients` are both ungated reads) — no secret material is ever present in a grant row.
- Soft-delete only (`revoked_at`), no cascade — identical semantics to Decision 25's existing `agent-admin`/`approver` revocation.

---

## `POST /api/v1/artifacts/{artifact_type}/{artifact_id}/grants`

Grant a role to a user, team, or application.

**Path params:** `artifact_type` (`"agent" | "workflow"`), `artifact_id` (UUID).

**Request body** (`ArtifactRoleGrantCreate`):
```json
{
  "grantee_type": "application",
  "grantee_id": "b6c1e2a4-9e3f-4c1a-9b0a-7f2d8e1c4a5b",
  "role": "invoker"
}
```
`grantee_type ∈ {"user","team","application"}`, `role ∈ {"agent-admin","approver","invoker"}` — both are Pydantic `pattern`-constrained, so an out-of-set value never reaches the DB layer (T-ARG-007 is satisfied at the API, not just the CHECK constraint).

**201 response** (`ArtifactRoleGrantResponse`):
```json
{
  "id": "1a2b3c4d-...",
  "artifact_type": "agent",
  "artifact_id": "9f8e7d6c-...",
  "role": "invoker",
  "grantee_type": "application",
  "grantee_id": "b6c1e2a4-9e3f-4c1a-9b0a-7f2d8e1c4a5b",
  "granted_by": "a1b2c3d4-user-sub",
  "granted_at": "2026-07-19T18:04:00Z",
  "revoked_at": null,
  "grantee_label": "billing-service"
}
```
`grantee_label` is resolved server-side from `applications.name` for `grantee_type="application"` grants only (a bare application UUID is meaningless to a human; a team name or a user sub is already directly displayable, matching this codebase's existing convention of showing raw subs elsewhere — e.g. `WebhookClientResponse.created_by`, `AgentTriggerResponse.armed_by`). `grantee_label` is `null` for `user`/`team` grantees.

**Errors:**
| Status | Cause |
|---|---|
| `401` | No/invalid bearer token |
| `403` | Caller is not `platform-admin` and does not hold `agent-admin` on this artifact (`can_delegate_role` → `False`) |
| `404` | `{artifact_type}/{artifact_id}` does not resolve to a real agent/workflow |
| `422` | `artifact_type` not in `{agent,workflow}`, or `grantee_type`/`role` outside their enums (FastAPI/Pydantic validation) |
| `400` | `grantee_id` does not resolve — unknown user sub (no `user_team_assignments` row), unknown team name, or an application UUID that does not exist (T-ARG-008: never a silent no-op insert) |
| `409` | An active (non-revoked) grant already exists for this exact `(artifact_id, role, grantee_type, grantee_id)` tuple — `uq_arg_active_grant` violation caught and translated (T-ARG-009) |

---

## `GET /api/v1/artifacts/{artifact_type}/{artifact_id}/grants`

List active (non-revoked) grants on one artifact.

**200 response:** `ArtifactRoleGrantResponse[]`, newest-first (`ORDER BY granted_at DESC`). Includes all three roles and all three grantee types mixed together — the Studio grants-list UI (design doc §9.2) filters/groups client-side if it wants a per-role breakdown; the API returns the full active set for the artifact in one call.

**Errors:** `404` if the artifact does not exist. No auth dependency (matches existing read-endpoint convention).

---

## `DELETE /api/v1/artifacts/{artifact_type}/{artifact_id}/grants/{grant_id}`

Revoke one grant (soft-delete — sets `revoked_at = now()`).

**204**, empty body (`response_model=None`, same idiom `webhook_clients.py`/`triggers.py` already use for 204s — omitting it crashes the pod at import time per those files' own comments).

**Errors:**
| Status | Cause |
|---|---|
| `401` | No/invalid bearer token |
| `404` | `grant_id` does not exist on this artifact, or is already revoked (T-ARG-006 — a second `DELETE` on the same grant is `404`, not a silent no-op `204`) |
| `403` | Caller lacks `can_delegate_role` for the **target grant's own role** (read from the row before the authority check — see security invariants above) |

**Effect (T-ARG-006):** the very next `has_artifact_role`/gateway-side grant check for this `(grantee_type, grantee_id, role, artifact_id)` returns `False`/denies — no cache anywhere in this path, live read on every check, identical posture to every other revocation in this codebase.

---

## Worked example — the full Suite A path (T-ARG-001 → T-ARG-006)

```
# T-ARG-001 — agent-admin grants agent-admin to another user
POST /api/v1/artifacts/agent/{agent_id}/grants
Authorization: Bearer <agent-admin's token>
{ "grantee_type": "user", "grantee_id": "<other-user-sub>", "role": "agent-admin" }
→ 201

# T-ARG-002 — agent-admin grants approver to a team; a member of that team then
# passes has_artifact_role for that role (verified via a DIRECT rbac.has_artifact_role
# call in the suite driver, not a second HTTP round trip — has_artifact_role's
# existing (user_sub, team) signature is unchanged by this design, see research.md §4)
POST /api/v1/artifacts/agent/{agent_id}/grants
{ "grantee_type": "team", "grantee_id": "payments", "role": "approver" }
→ 201

# T-ARG-003 — agent-admin grants invoker to an application they own
POST /api/v1/artifacts/agent/{agent_id}/grants
{ "grantee_type": "application", "grantee_id": "<application_id>", "role": "invoker" }
→ 201

# T-ARG-004 — a contributor with no scoped role on the artifact attempts any grant
POST /api/v1/artifacts/agent/{agent_id}/grants   (as a plain contributor)
{ "grantee_type": "user", "grantee_id": "<some-sub>", "role": "approver" }
→ 403

# T-ARG-005 — platform-admin grants a role on an artifact they hold no agent-admin on
POST /api/v1/artifacts/agent/{agent_id}/grants   (as platform-admin, no prior grant)
{ "grantee_type": "user", "grantee_id": "<some-sub>", "role": "agent-admin" }
→ 201

# T-ARG-006 — revoke, then re-check
DELETE /api/v1/artifacts/agent/{agent_id}/grants/{grant_id_from_001}
→ 204
GET /api/v1/artifacts/agent/{agent_id}/grants
→ 200, grant_id_from_001 absent from the list
```

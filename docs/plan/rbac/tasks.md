# RBAC Implementation Tasks

**Plan**: `docs/plan/rbac/plan.md`  
**Design doc**: `docs/design/rbac-design.md`  
**Date**: 2026-07-06

Legend: `[ ]` pending, `[x]` done, `[~]` in progress, `[-]` skipped

---

## Phase 1: Data Layer (Migration + ORM Model)

**Depends on**: nothing  
**Blocked by**: nothing

- [ ] **T-1.1** Create migration `0032_rbac_artifact_role_grants.py` `[M]`
  - File: `services/registry-api/alembic/versions/0032_rbac_artifact_role_grants.py` (new)
  - Part A: `CREATE TABLE IF NOT EXISTS artifact_role_grants` with columns (`id UUID PK`, `artifact_type`, `artifact_id`, `role`, `grantee_type`, `grantee_id`, `granted_by`, `granted_at`, `revoked_at`) + CHECK constraints for `artifact_type IN ('agent','workflow')`, `role IN ('agent-admin','approver')`, `grantee_type IN ('user','team')`
  - Part A indexes: `idx_arg_lookup` on `(artifact_id, grantee_type, grantee_id, role, revoked_at)`, `idx_arg_grantee` on `(grantee_type, grantee_id, revoked_at)`, unique partial on `(artifact_id, role, grantee_type, grantee_id) WHERE revoked_at IS NULL`
  - Part B: `UPDATE user_team_assignments SET role = 'platform-admin' WHERE role = 'admin'`, `SET role = 'contributor' WHERE role = 'operator'`, `ALTER COLUMN role SET DEFAULT 'contributor'`
  - Use raw SQL via `op.execute()` with `IF NOT EXISTS` guards (project pattern)
  - `down_revision = "0031"`, `revision = "0032"`

- [ ] **T-1.2** Add `ArtifactRoleGrant` ORM model to `models.py` `[S]`
  - File: `services/registry-api/models.py`
  - Use `Mapped[]`/`mapped_column()` pattern matching existing models
  - Add `CheckConstraint` for `artifact_type`, `role`, `grantee_type`
  - Add to `__all__` list at bottom of file
  - Verify: `python3 -c "import ast; ast.parse(open('services/registry-api/models.py').read())"`

**Phase 1 verification**: import routers + `sqlalchemy.orm.configure_mappers()` succeeds

---

## Phase 2: Permission Service (`rbac.py`)

**Depends on**: Phase 1  
**Blocked by**: T-1.1, T-1.2

- [ ] **T-2.1** Implement core helpers: `get_user_global_role()`, `get_user_team()` `[S]`
  - File: `services/registry-api/rbac.py` (new)
  - `get_user_global_role(db, user_sub) -> str | None`: query `user_team_assignments` via raw `text()` SQL (matching `me.py` line 29 pattern), normalize legacy values (`admin`→`platform-admin`, `operator`→`contributor`)
  - `get_user_team(db, user_sub) -> str | None`: same query, return `team_name`

- [ ] **T-2.2** Implement `has_artifact_role()` `[M]`
  - File: `services/registry-api/rbac.py`
  - `has_artifact_role(db, user_sub, artifact_id, role, user_team=None) -> bool`
  - Query `ArtifactRoleGrant` ORM model: `WHERE revoked_at IS NULL AND artifact_id = :id AND role = :role` AND (`grantee_type='user' AND grantee_id=user_sub` OR `grantee_type='team' AND grantee_id=user_team`)
  - Blocked by: T-2.1

- [ ] **T-2.3** Implement policy decision functions `[M]`
  - File: `services/registry-api/rbac.py`
  - `can_deploy_to_production(db, sub, artifact_id)` → platform-admin OR has_artifact_role(agent-admin)
  - `can_approve_hitl(db, sub, artifact_id)` → platform-admin OR has_artifact_role(approver)
  - `can_use_playground(db, sub)` → role in {platform-admin, contributor}
  - `can_create_agent(db, sub)` → role in {platform-admin, contributor}
  - `can_delegate_role(db, caller, artifact_id, target_role)` → platform-admin OR (agent-admin on artifact AND target_role in {agent-admin, approver})
  - Each calls `get_user_global_role()` + `get_user_team()` internally
  - Blocked by: T-2.1, T-2.2

- [ ] **T-2.4** Implement `require_global_role()` dependency factory `[M]`
  - File: `services/registry-api/rbac.py`
  - `require_global_role(*allowed_roles)` returns a FastAPI `Depends` closure
  - Inner async function: `Depends(require_user)` for JWT (401), `Depends(get_db)` for session, calls `get_user_global_role()`, raises `HTTPException(403, "insufficient_role")` if role not in allowed_roles, injects `_global_role` and `_team` into claims dict
  - Blocked by: T-2.1

**Phase 2 verification**: `python3 -c "from rbac import require_global_role, can_deploy_to_production"` succeeds

---

## Phase 3: Backend Endpoint Guards

**Depends on**: Phase 2  
**Blocked by**: T-2.3, T-2.4

**Backward compat strategy**: Admin routes use strict `require_global_role()`. Other routes use `require_user` + inline rbac checks. E2E suites will need JWT auth (Keycloak direct-grant pattern from `suite-14-consumer-chat.sh`).

- [ ] **T-3.1** Guard `admin.py` — `require_global_role("platform-admin")` on all endpoints `[M]`
  - File: `services/registry-api/routers/admin.py`
  - Add `claims: dict = Depends(require_global_role("platform-admin"))` to every endpoint (publish-requests approve/reject, grants CRUD, approval-authority CRUD)
  - Currently: NO auth at all on any endpoint
  - Blocked by: T-2.4

- [ ] **T-3.2** Guard `admin_users.py` — platform-admin only + update role values `[M]`
  - File: `services/registry-api/routers/admin_users.py`
  - Add `require_global_role("platform-admin")` to all endpoints (list, create, get, patch, delete, reset-password, teams-summary)
  - Update `PLATFORM_ROLES` set (line 109) to include `platform-admin`, `contributor`
  - Change default role from `"operator"` to `"contributor"` (line 49)
  - Blocked by: T-2.4

- [ ] **T-3.3** Guard agent create with `can_create_agent()` `[S]`
  - File: `services/registry-api/routers/agents.py`
  - Replace `get_optional_user` with `require_user` on `create_agent`
  - Add `can_create_agent()` check → 403 if false
  - Keep `X-User-Sub` as secondary fallback for internal service calls (scheduler/event-gateway)
  - Blocked by: T-2.3

- [ ] **T-3.4** Guard agent update/delete — platform-admin OR agent-admin `[M]`
  - File: `services/registry-api/routers/agents.py`
  - Add `require_user` to `update_agent` and `delete_agent`
  - Check: `platform-admin` OR `has_artifact_role(agent-admin)` on the agent
  - Resolve agent name → `agent.id` for scoped role check
  - Blocked by: T-2.3

- [ ] **T-3.5** Guard quarantine — platform-admin only `[S]`
  - File: `services/registry-api/routers/agents.py`
  - Add `require_global_role("platform-admin")` to `quarantine_agent` and `lift_quarantine`
  - Blocked by: T-2.4

- [ ] **T-3.6** Guard deploy endpoint — sandbox vs production authorization `[L]` **(HARDEST GUARD)**
  - File: `services/registry-api/routers/deployments.py`
  - Add `require_user` (endpoint currently has ZERO auth)
  - If `body.environment == "production"`: `can_deploy_to_production(db, caller, agent.id)` → 403 if false
  - If sandbox/staging: contributor+ check
  - Replace `X-User-Team` header with `claims["_team"]` from rbac
  - **Critical**: `PATCH /deployments/{id}` is on `global_deployments_router` (separate router) — stays unguarded for deploy-controller callback
  - Blocked by: T-2.3

- [ ] **T-3.7** Guard playground — `can_use_playground()` check `[S]`
  - File: `services/registry-api/routers/playground.py`
  - Replace `get_optional_user` with `require_user` on `POST /playground/runs`
  - Add `can_use_playground()` check → 403 for viewers
  - Blocked by: T-2.3

- [ ] **T-3.8** Guard tools/skills/triggers mutate — contributor+ check `[M]`
  - Files: `routers/tools.py`, `routers/skills.py`, `routers/triggers.py`
  - Add `require_user` + contributor+ check on create/update/delete endpoints
  - Currently: no auth on any of these
  - Blocked by: T-2.3

- [ ] **T-3.9** Guard workflow create/mutate — contributor+ or agent-admin `[M]`
  - File: `services/registry-api/routers/composite_workflows.py`
  - Add `require_user` + `can_create_agent()` on `create_workflow`
  - Add platform-admin OR agent-admin check on `update_workflow` / `archive_workflow`
  - Currently: uses `get_optional_user`
  - Blocked by: T-2.3

**Phase 3 verification**: For each guarded endpoint: no token → 401, viewer → 403 on mutate, contributor → 200 on create / 403 on admin, platform-admin → 200 on everything.

---

## Phase 4: Artifact-Scoped Roles (Auto-Grant, HITL Rewrite, New Router, /me)

**Depends on**: Phase 2 (rbac.py), Phase 3 (guards in place)

- [ ] **T-4.1** Add creator auto-grant to agent create `[S]`
  - File: `services/registry-api/routers/agents.py`
  - After `await db.refresh(agent)` (line 97) where `agent.id` is populated:
    ```python
    auto_grant = ArtifactRoleGrant(
        artifact_type="agent", artifact_id=agent.id,
        role="agent-admin", grantee_type="user",
        grantee_id=caller, granted_by="system:auto-grant",
    )
    db.add(auto_grant); await db.flush()
    ```
  - Import `ArtifactRoleGrant` from models
  - Blocked by: T-3.3

- [ ] **T-4.2** Add creator auto-grant to workflow create `[S]`
  - File: `services/registry-api/routers/composite_workflows.py`
  - Same pattern as T-4.1 after workflow is created and flushed
  - Blocked by: T-3.9

- [ ] **T-4.3** Create `artifact_roles.py` router + register in `main.py` `[L]`
  - File: `services/registry-api/routers/artifact_roles.py` (new)
  - File: `services/registry-api/main.py` (register router)
  - Prefix: `/api/v1/artifact-roles`
  - `GET /` — list grants (platform-admin sees all; others see own + artifacts they're agent-admin of). Query params: `artifact_id`, `artifact_type`, `grantee_id`
  - `POST /` — create grant. Guard: `can_delegate_role()`. Validate artifact exists. Unique constraint prevents duplicate active grants.
  - `DELETE /{id}` — soft-revoke: set `revoked_at = now()`. Guard: platform-admin OR agent-admin on artifact.
  - `GET /my-roles` — caller's own active grants (user direct + team)
  - Define Pydantic request/response schemas
  - Blocked by: T-2.3, T-2.4

- [ ] **T-4.4** Rewrite HITL list approvals — replace `approval_authority` with `artifact_role_grants` `[L]` **(HARDEST TASK)**
  - File: `services/registry-api/routers/approvals.py`
  - Current code (lines 187-205) queries `ApprovalAuthority` by `approver_user_id`/`approver_role` — **design doc §7.1 bug** (checks record exists but never verifies caller holds the role)
  - New logic for `GET /approvals/?context=production`:
    1. Get caller from JWT claims via `require_user` (replace `X-User-Sub` header)
    2. Get caller's global role + team via `rbac.get_user_global_role()` / `get_user_team()`
    3. If platform-admin → no filter, return all production approvals
    4. Query `artifact_role_grants` for `artifact_id` values where caller has `approver` (user or team grant)
    5. Filter `Approval` WHERE `agent_id IN (approved_artifact_ids)`
    6. Empty set → empty list
  - `approval_authority` table stays but is no longer read
  - Blocked by: T-2.2, T-2.3

- [ ] **T-4.5** Rewrite HITL decide approval — `can_approve_hitl()` check `[M]`
  - File: `services/registry-api/routers/approvals.py`
  - Replace `X-User-Sub` with JWT claims via `require_user`
  - Load approval → get `agent_id` → `can_approve_hitl(db, caller, agent_id)` → 403 if false
  - Remove references to `_ADMIN_ROLES` and `_has_authority_for_tool`
  - Keep existing optimistic-lock logic unchanged
  - Blocked by: T-4.4

- [ ] **T-4.6** Enrich `/me` endpoint with `artifact_roles` `[M]`
  - File: `services/registry-api/routers/me.py`
  - After existing team/role fetch, query `artifact_role_grants` for caller's `sub` (both user direct + team grants, `WHERE revoked_at IS NULL`)
  - Return `artifact_roles: [{artifact_id, artifact_type, role}]` in response
  - Normalize the `role` value (map legacy `admin`→`platform-admin`, `operator`→`contributor`)
  - Blocked by: T-2.2

**Phase 4 verification**:
- Create agent → query `artifact_role_grants` confirms `agent-admin` row
- `POST /artifact-roles` with agent-admin caller → 201
- `DELETE /artifact-roles/{id}` → `revoked_at` set
- `GET /approvals/?context=production` as approver → sees matching approvals
- `GET /approvals/?context=production` as non-approver → empty
- `GET /me` returns `artifact_roles` array

---

## Phase 5: Keycloak Realm Roles

**Depends on**: Phase 2 (rbac.py exists for role mapping)  
**Can run in parallel with**: Phase 3, Phase 4

- [ ] **T-5.1** Add realm role creation to `realm-init-job.yaml` `[M]`
  - File: `charts/agentshield/templates/realm-init-job.yaml`
  - After client creation, add idempotent `kcadm.sh` commands:
    - `kcadm.sh create roles -r agentshield -s name=platform-admin || true`
    - `kcadm.sh create roles -r agentshield -s name=contributor || true`
    - `kcadm.sh create roles -r agentshield -s name=viewer || true`
  - Assign `platform-admin` role to the `platform-admin` user
  - Assign `viewer` role to the `agent-reviewer` user
  - Blocked by: T-2.4

- [ ] **T-5.2** Update `keycloak_client.py` platform_roles set `[S]`
  - File: `services/registry-api/keycloak_client.py`
  - Expand `platform_roles` (line 162) to `{"admin", "operator", "viewer", "platform-admin", "contributor"}`
  - In `set_user_realm_role()`, map old→new when assigning: `admin`→`platform-admin`, `operator`→`contributor`
  - Blocked by: T-2.4

**Phase 5 verification**: `kcadm.sh get roles -r agentshield` shows `platform-admin`, `contributor`, `viewer`. Login as `platform-admin` user → JWT `realm_access.roles` includes `platform-admin`.

---

## Phase 6: Frontend Guards

**Depends on**: Phase 4 (needs enriched `/me` response for `artifactRoles`)

- [ ] **T-6.1** Add `isAtLeast()` + `artifactRoles` to AuthContext `[S]`
  - File: `studio/src/contexts/AuthContext.tsx`
  - Add to `AuthContextValue`: `isAtLeast: (minRole: 'viewer' | 'contributor' | 'platform-admin') => boolean` and `artifactRoles: {artifact_id: string; artifact_type: string; role: string}[]`
  - Implement with `ROLE_RANK` hierarchy: `{viewer: 0, contributor: 1, 'platform-admin': 2}`
  - Normalize legacy values: `admin`→`platform-admin`, `operator`→`contributor`
  - Blocked by: T-4.6

- [ ] **T-6.2** Update `MeResponse` type + boot flow for `artifact_roles` `[S]`
  - File: `studio/src/api/registryApi.ts` — add `artifact_roles` to `MeResponse` interface
  - File: `studio/src/main.tsx` — pass `me.artifact_roles` to `buildAuthValue()`
  - Update `buildAuthValue` signature to accept and store artifact roles
  - Blocked by: T-4.6

- [ ] **T-6.3** Create `RequireRole` component `[S]`
  - File: `studio/src/components/RequireRole.tsx` (new)
  - Props: `minRole`, `children`, optional `fallback` (defaults to `<Navigate to="/" replace />`)
  - Uses `useAuth().isAtLeast(minRole)` — if insufficient, render fallback; otherwise render children
  - Blocked by: T-6.1

- [ ] **T-6.4** Add route guards to `App.tsx` `[M]`
  - File: `studio/src/App.tsx`
  - Wrap `/admin/*` routes (lines 61-65) with `<RequireRole minRole="platform-admin">`
  - Wrap `/playground`, `/agents/new`, `/workflows/new` with `<RequireRole minRole="contributor">`
  - Read-only routes (catalog, deployments, agent list/detail) stay unguarded
  - Blocked by: T-6.3

- [ ] **T-6.5** Filter sidebar sections by role `[M]`
  - File: `studio/src/components/Sidebar.tsx`
  - Administration section (lines 223-228): visible only when `isAtLeast("platform-admin")`
  - Playground build/eval sections: visible only when `isAtLeast("contributor")`
  - Org section (Catalog, Approvals, Deployments): visible to all
  - `useAuth()` is already imported
  - Blocked by: T-6.1

- [ ] **T-6.6** Update role dropdown in `AdminAccessPage.tsx` `[S]`
  - File: `studio/src/pages/AdminAccessPage.tsx`
  - Change `ROLES` (line 112) from `["admin", "operator", "viewer"]` to `["platform-admin", "contributor", "viewer"]`
  - Update `ROLE_CHIP` color mappings (lines 115-119)
  - Change default in `CreateUserModal` from `"operator"` to `"contributor"`
  - Blocked by: T-6.1

**Phase 6 verification**:
- `cd studio && npm run typecheck` passes
- `cd studio && npm run test` passes (update affected component tests)
- Login as viewer → admin sidebar hidden, `/admin/access` URL redirects to `/`
- Login as contributor → playground visible, admin hidden
- Login as platform-admin → everything visible

---

## Phase 7: E2E Tests + Image Tags

**Depends on**: All previous phases

- [ ] **T-7.1** Write `suite-33-rbac.sh` E2E tests `[L]`
  - File: `scripts/e2e/suite-33-rbac.sh` (new)
  - Follow `kubectl exec` + Python/httpx pattern from existing suites
  - 15 test cases:
    - T-S33-001: `/me` returns normalized `contributor` (not `operator`)
    - T-S33-002: Create agent → `artifact_role_grants` has auto-grant `agent-admin` row
    - T-S33-003: Creator (agent-admin) deploys to production → 200
    - T-S33-004: Random contributor deploys to production → 403
    - T-S33-005: Contributor deploys to sandbox → 200
    - T-S33-006: Viewer `POST /playground/runs` → 403
    - T-S33-007: Contributor `POST /playground/runs` → 200
    - T-S33-008: Grant `approver` → user sees HITL items for that agent
    - T-S33-009: User without `approver` tries `PATCH /approvals/{id}` → 403
    - T-S33-010: `agent-admin` grants `approver` to another user → 201
    - T-S33-011: Contributor (no scoped role) tries to grant → 403
    - T-S33-012: Revoke grant → permission check returns false
    - T-S33-013: Contributor tries `GET /admin/users` → 403
    - T-S33-014: Grant `approver` to team → team member sees HITL
    - T-S33-015: Cleanup test artifacts
  - Blocked by: all Phase 1-6 tasks

- [ ] **T-7.2** Register suite-33 in `run-all.sh` `[S]`
  - File: `scripts/e2e/run-all.sh`
  - Add `suite-33-rbac.sh` to the test runner
  - Blocked by: T-7.1

- [ ] **T-7.3** Bump image tags — registry-api `0.2.64`, studio `0.1.48` `[S]`
  - `scripts/deploy-cpe2e.sh` line 53: `REGISTRY_API_TAG="0.2.63"` → `"0.2.64"`
  - `scripts/deploy-cpe2e.sh` line 56: `STUDIO_TAG="0.1.47"` → `"0.1.48"`
  - `charts/agentshield/values.yaml` line 517: `tag: "0.2.63"` → `"0.2.64"`
  - `charts/agentshield/values.yaml` line 821: `tag: "0.1.47"` → `"0.1.48"`
  - Update `deploy-cpe2e.sh` header comment with RBAC description
  - Blocked by: T-7.1

---

## Dependency Graph

```
T-1.1 ──┐
T-1.2 ──┤
        ├──→ T-2.1 ──→ T-2.2 ──→ T-2.3 ──┐
        │         └──→ T-2.4 ──┐           │
        │                      │           │
        │    ┌─────────────────┤           │
        │    │                 │           │
        │    ├──→ T-3.1        │           │
        │    ├──→ T-3.2        │           │
        │    ├──→ T-3.5        ├──→ T-3.3 ──→ T-4.1
        │    ├──→ T-5.1        ├──→ T-3.4
        │    └──→ T-5.2        ├──→ T-3.6
        │                      ├──→ T-3.7
        │                      ├──→ T-3.8
        │                      ├──→ T-3.9 ──→ T-4.2
        │                      │
        │                      ├──→ T-4.3
        │                      │
        │         T-2.2 ───────┼──→ T-4.4 ──→ T-4.5
        │                      │
        │         T-2.2 ───────┼──→ T-4.6 ──┐
        │                      │            │
        │                      │            ├──→ T-6.1 ──→ T-6.3 ──→ T-6.4
        │                      │            │       ├──→ T-6.5
        │                      │            │       └──→ T-6.6
        │                      │            └──→ T-6.2
        │                      │
        └──────────────────────┴──→ T-7.1 ──→ T-7.2
                                         └──→ T-7.3
```

## Summary

| Phase | # Tasks | Effort | Status |
|-------|---------|--------|--------|
| 1 — Data Layer | 2 | M | pending |
| 2 — rbac.py | 4 | L | pending |
| 3 — Endpoint Guards | 9 | XL | pending |
| 4 — Scoped Roles | 6 | XL | pending |
| 5 — Keycloak | 2 | M | pending |
| 6 — Frontend | 6 | L | pending |
| 7 — Tests + Tags | 3 | L | pending |
| **Total** | **32** | | |

## Files Created (5)

| File | Task |
|------|------|
| `services/registry-api/alembic/versions/0032_rbac_artifact_role_grants.py` | T-1.1 |
| `services/registry-api/rbac.py` | T-2.1 |
| `services/registry-api/routers/artifact_roles.py` | T-4.3 |
| `studio/src/components/RequireRole.tsx` | T-6.3 |
| `scripts/e2e/suite-33-rbac.sh` | T-7.1 |

## Files Modified (24)

| File | Tasks |
|------|-------|
| `services/registry-api/models.py` | T-1.2 |
| `services/registry-api/routers/admin.py` | T-3.1 |
| `services/registry-api/routers/admin_users.py` | T-3.2 |
| `services/registry-api/routers/agents.py` | T-3.3, T-3.4, T-3.5, T-4.1 |
| `services/registry-api/routers/deployments.py` | T-3.6 |
| `services/registry-api/routers/playground.py` | T-3.7 |
| `services/registry-api/routers/tools.py` | T-3.8 |
| `services/registry-api/routers/skills.py` | T-3.8 |
| `services/registry-api/routers/triggers.py` | T-3.8 |
| `services/registry-api/routers/composite_workflows.py` | T-3.9, T-4.2 |
| `services/registry-api/routers/approvals.py` | T-4.4, T-4.5 |
| `services/registry-api/routers/me.py` | T-4.6 |
| `services/registry-api/main.py` | T-4.3 |
| `services/registry-api/keycloak_client.py` | T-5.2 |
| `charts/agentshield/templates/realm-init-job.yaml` | T-5.1 |
| `studio/src/contexts/AuthContext.tsx` | T-6.1 |
| `studio/src/api/registryApi.ts` | T-6.2 |
| `studio/src/main.tsx` | T-6.2 |
| `studio/src/App.tsx` | T-6.4 |
| `studio/src/components/Sidebar.tsx` | T-6.5 |
| `studio/src/pages/AdminAccessPage.tsx` | T-6.6 |
| `scripts/deploy-cpe2e.sh` | T-7.3 |
| `charts/agentshield/values.yaml` | T-7.3 |
| `scripts/e2e/run-all.sh` | T-7.2 |

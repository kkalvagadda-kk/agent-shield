# AgentShield RBAC — Design Spec

**Status**: Draft  
**Date**: 2026-07-06  
**Author**: Karthik + Claude  
**Version**: 1.0.0  
**Decision**: `docs/decisions.md` §Decision 25  
**Related**: `docs/design/authorization-model-spec.md` (data-plane auth — agent machine identity, OPA, Istio)

---

## 1. Problem Statement

The platform has authentication (Keycloak OIDC + JWT) but no authorization. Every authenticated user can:
- Navigate to every Studio page, including admin panels
- Create, modify, and delete any agent or workflow
- Deploy to production without ownership checks
- See and decide on every HITL approval in every team's queue
- Use the playground without restriction

Three Keycloak realm roles exist (`admin`, `operator`, `viewer`) and are stored in `user_team_assignments.role`, but nothing in the backend or frontend reads them to gate access. The `hasRole()` function in Studio's `AuthContext` is defined but never called.

This spec introduces **two-tier RBAC**: global roles for platform-wide capabilities and artifact-scoped roles for per-agent/workflow authority.

### Relationship to authorization-model-spec.md

The existing authorization model spec covers the **data plane** — how agent pods prove their identity to OPA at runtime (SA tokens, Istio mTLS, OPA bundle policies). This spec covers the **control plane** — how human users prove they're allowed to perform actions in Studio and the Registry API. The two are complementary and independent.

---

## 2. Role Definitions

### 2.1 Global Roles

Stored in `user_team_assignments.role`. Mutually exclusive per user — each user has exactly one global role.

| Role | What it grants | What it blocks |
|------|---------------|----------------|
| **platform-admin** | Full platform access: manage users/teams, approve publish requests, configure approval authority, deploy to any environment, assign any artifact-scoped role on any artifact, use playground, see all HITL queues | Nothing |
| **contributor** | Create agents/workflows/tools/skills, develop in sandbox (playground, test runs), submit for publish, deploy to sandbox. Can deploy to production only if also `agent-admin` on the artifact. | Cannot manage users/teams, cannot access `/admin/*` routes, cannot deploy to production without `agent-admin` |
| **viewer** | Browse the catalog, view run history, view deployment status | Cannot create/modify/delete anything, cannot use the playground, cannot approve HITL, cannot deploy |

**Migration from current values**: `admin` → `platform-admin`, `operator` → `contributor`. The `viewer` value stays unchanged.

### 2.2 Artifact-Scoped Roles

Stored in `artifact_role_grants` (new table). A user or team can hold zero or more scoped roles across different artifacts.

| Role | Scope | What it grants |
|------|-------|---------------|
| **agent-admin** | Per agent or workflow | Suspend, resume, scale replicas, upgrade version, rollback, edit runtime config (env vars, LLM provider keys), delete deployment, deploy to production. Can grant `agent-admin` and `approver` to other users/teams within the same artifact scope. |
| **approver** | Per agent or workflow | Receives HITL approval requests for that agent/workflow in the production queue. Can approve or reject those requests. Approves all HITL for that agent regardless of which tool triggered it. |

**Multiple roles per user**: A user can be `contributor` globally + `agent-admin` on Agent X + `approver` on Agent Y. Roles are additive — having more roles never reduces access.

---

## 3. Grant Model

### 3.1 Polymorphic Grantee

A scoped role grant targets either a **user** or a **team**:

- `grantee_type = 'user'`, `grantee_id = <user_sub>` — individual grant
- `grantee_type = 'team'`, `grantee_id = <team_name>` — team-wide grant (all members of that team inherit the role)

When checking permissions, both are evaluated: a user has a role if they hold a direct grant OR if their team holds a grant.

### 3.2 Creator Auto-Grant

When a contributor creates an agent or workflow, the platform automatically inserts an `agent-admin` grant for the creator (`grantee_type = 'user'`, `granted_by = 'system:auto-grant'`). This ensures the creator can manage their artifact from day one without requiring a platform-admin to assign it.

### 3.3 Delegation Rules

| Caller | Can grant |
|--------|-----------|
| **platform-admin** | Any scoped role (`agent-admin`, `approver`) on any artifact, to any user or team |
| **agent-admin** (on artifact X) | `agent-admin` or `approver` on artifact X only, to any user or team |
| **contributor** (no scoped role on X) | Nothing on artifact X |
| **viewer** | Nothing |

### 3.4 Revocation

Grants are soft-deleted (`revoked_at` timestamp set). Revocation does **not** cascade — if User A grants `agent-admin` to User B, and User A's grant is later revoked, User B's grant survives. Cascade-revoke is deferred to a future improvement.

---

## 4. Data Model

### 4.1 Existing: `user_team_assignments` (migration 0013)

```
user_sub      VARCHAR(255)  PK
team_name     VARCHAR(255)  NOT NULL
role          VARCHAR(64)   NOT NULL  DEFAULT 'contributor'   -- was 'operator'
assigned_by   VARCHAR(255)  NULL
assigned_at   TIMESTAMPTZ   DEFAULT now()
```

**Change**: rename role values (`admin` → `platform-admin`, `operator` → `contributor`), update `server_default` to `'contributor'`.

No ORM model exists — accessed via raw SQL in `routers/me.py`, `routers/admin_users.py`, `routers/chat.py`.

### 4.2 New: `artifact_role_grants` (migration 0030)

```
id              UUID          PK  DEFAULT gen_random_uuid()
artifact_type   VARCHAR(32)   NOT NULL  CHECK IN ('agent','workflow')
artifact_id     UUID          NOT NULL
role            VARCHAR(32)   NOT NULL  CHECK IN ('agent-admin','approver')
grantee_type    VARCHAR(16)   NOT NULL  CHECK IN ('user','team')
grantee_id      TEXT          NOT NULL  -- user_sub or team_name
granted_by      TEXT          NOT NULL  -- user_sub of granter, or 'system:auto-grant'
granted_at      TIMESTAMPTZ   NOT NULL  DEFAULT now()
revoked_at      TIMESTAMPTZ   NULL      -- soft-delete
```

**Indexes**:
- `idx_arg_lookup` on `(artifact_id, grantee_type, grantee_id, role, revoked_at)` — primary permission check path
- `idx_arg_grantee` on `(grantee_type, grantee_id, revoked_at)` — "what roles does this user/team have?" query
- Unique partial constraint on `(artifact_id, role, grantee_type, grantee_id) WHERE revoked_at IS NULL` — prevents duplicate active grants

### 4.3 Relationship to Existing Tables

| Table | Purpose | Relationship to `artifact_role_grants` |
|-------|---------|---------------------------------------|
| `asset_grants` | **Visibility** — which teams can see/bind a published asset | Independent. Having visibility does not imply authority. A team can have an `asset_grant` to see Agent X in the catalog without anyone on the team having `agent-admin` or `approver` on it. |
| `approval_authority` | **HITL routing** — who can approve for a specific tool | **Deprecated** by `artifact_role_grants`. The `approver` scoped role replaces `approval_authority` records. The table is not dropped — it stays for historical records. New HITL routing reads from `artifact_role_grants` only. |
| `user_team_assignments` | **Global role** — one role per user | Complementary. Global role determines platform-wide capability; scoped roles add per-artifact authority. |

---

## 5. Permission Service: `rbac.py`

A new module at `services/registry-api/rbac.py` — the single source of truth for all permission checks. All routers import from here rather than implementing inline checks.

### 5.1 Core Helpers

```python
async def get_user_global_role(db, user_sub) -> str | None
```
Fetches from `user_team_assignments`. Normalizes legacy values (`admin` → `platform-admin`, `operator` → `contributor`).

```python
async def get_user_team(db, user_sub) -> str | None
```
Fetches team_name from `user_team_assignments`.

```python
async def has_artifact_role(db, user_sub, artifact_id, role, user_team=None) -> bool
```
Checks `artifact_role_grants` for an active grant (revoked_at IS NULL) matching either:
- `grantee_type = 'user'` AND `grantee_id = user_sub`, OR
- `grantee_type = 'team'` AND `grantee_id = user_team` (if user_team provided)

### 5.2 Policy Decision Functions

```python
async def can_deploy_to_production(db, user_sub, artifact_id) -> bool
```
Returns True if `platform-admin` OR `has_artifact_role(agent-admin)`.

```python
async def can_approve_hitl(db, user_sub, artifact_id) -> bool
```
Returns True if `platform-admin` OR `has_artifact_role(approver)`.

```python
async def can_use_playground(db, user_sub) -> bool
```
Returns True if global role is `platform-admin` or `contributor`.

```python
async def can_create_agent(db, user_sub) -> bool
```
Returns True if global role is `platform-admin` or `contributor`.

```python
async def can_delegate_role(db, caller_sub, artifact_id, target_role) -> bool
```
Returns True if `platform-admin` OR (`agent-admin` on the artifact AND target_role is `agent-admin` or `approver`).

### 5.3 FastAPI Dependency Factory

```python
def require_global_role(*allowed_roles) -> Depends
```
Returns a FastAPI dependency that:
1. Calls `require_user` to validate the JWT (401 if missing/invalid)
2. Looks up the caller's global role from `user_team_assignments`
3. Raises 403 if the role is not in `allowed_roles`
4. Injects `_global_role` and `_team` into the claims dict for downstream use

---

## 6. Endpoint Authorization Matrix

### 6.1 Admin Routes (platform-admin only)

| Endpoint | Guard |
|----------|-------|
| `GET /api/v1/admin/users` | `require_global_role("platform-admin")` |
| `POST /api/v1/admin/users` | `require_global_role("platform-admin")` |
| `GET /api/v1/admin/users/{id}` | `require_global_role("platform-admin")` |
| `PATCH /api/v1/admin/users/{id}` | `require_global_role("platform-admin")` |
| `DELETE /api/v1/admin/users/{id}` | `require_global_role("platform-admin")` |
| `POST /api/v1/admin/users/{id}/reset-password` | `require_global_role("platform-admin")` |
| `GET /api/v1/admin/teams-summary` | `require_global_role("platform-admin")` |
| `GET /api/v1/admin/grants` | `require_global_role("platform-admin")` |
| `POST /api/v1/admin/grants` | `require_global_role("platform-admin")` |
| `DELETE /api/v1/admin/grants/{id}` | `require_global_role("platform-admin")` |
| `GET /api/v1/admin/publish-requests` | `require_global_role("platform-admin")` |
| `POST /api/v1/admin/publish-requests/{id}/approve` | `require_global_role("platform-admin")` |
| `POST /api/v1/admin/publish-requests/{id}/reject` | `require_global_role("platform-admin")` |
| `GET /api/v1/admin/approval-authority` | `require_global_role("platform-admin")` |
| `POST /api/v1/admin/approval-authority` | `require_global_role("platform-admin")` |
| `DELETE /api/v1/admin/approval-authority/{id}` | `require_global_role("platform-admin")` |

### 6.2 Create/Mutate Routes (contributor+)

| Endpoint | Guard |
|----------|-------|
| `POST /api/v1/agents/` | `can_create_agent` (contributor or platform-admin) |
| `PUT /api/v1/agents/{name}` | `platform-admin` OR `agent-admin` on the agent |
| `DELETE /api/v1/agents/{name}` | `platform-admin` OR `agent-admin` on the agent |
| `POST /api/v1/agents/{name}/publish` | `contributor+` (must be creator or agent-admin) |
| `POST /api/v1/agents/{name}/quarantine` | `platform-admin` only |
| `POST /api/v1/tools/` | `contributor+` |
| `PUT /api/v1/tools/{name}` | `contributor+` |
| `DELETE /api/v1/tools/{name}` | `contributor+` |
| `POST /api/v1/skills/` | `contributor+` |
| `POST /api/v1/workflows/` | `can_create_agent` (contributor or platform-admin) |
| `PUT /api/v1/workflows/{id}` | `platform-admin` OR `agent-admin` on the workflow |
| `DELETE /api/v1/workflows/{id}` | `platform-admin` OR `agent-admin` on the workflow |
| `POST /api/v1/triggers/` | `contributor+` |

### 6.3 Deploy Routes

| Endpoint | Guard |
|----------|-------|
| `POST /api/v1/agents/{name}/deploy` (env=sandbox) | `contributor+` |
| `POST /api/v1/agents/{name}/deploy` (env=production) | `can_deploy_to_production` (platform-admin OR agent-admin) |
| `POST /api/v1/agents/{name}/rollback` | `platform-admin` OR `agent-admin` on the agent |
| `PATCH /api/v1/deployments/{id}` | Internal only (deploy-controller callback) |

### 6.4 Playground Routes

| Endpoint | Guard |
|----------|-------|
| `POST /api/v1/playground/runs` | `can_use_playground` (contributor or platform-admin) |
| `GET /api/v1/playground/runs/{id}/stream` | `contributor+` (same user who created the run) |
| `POST /api/v1/playground/runs/{id}/feedback` | `contributor+` |

### 6.5 HITL Approval Routes

| Endpoint | Guard |
|----------|-------|
| `GET /api/v1/approvals/` (context=production) | Filtered: only show approvals where caller has `approver` on the agent, or caller is `platform-admin` |
| `PATCH /api/v1/approvals/{id}` (context=production) | `can_approve_hitl` on the approval's agent |
| `POST /api/v1/approvals/{id}/reopen` | `platform-admin` only |
| Playground approvals | Self-approval by run owner (unchanged) |

### 6.6 Artifact Role Management (new router)

| Endpoint | Guard |
|----------|-------|
| `GET /api/v1/artifact-roles` | Any authenticated user (filtered to what they can see) |
| `POST /api/v1/artifact-roles` | `can_delegate_role` (platform-admin or agent-admin on the artifact) |
| `DELETE /api/v1/artifact-roles/{id}` | `platform-admin` or agent-admin on the artifact |
| `GET /api/v1/artifact-roles/my-roles` | Any authenticated user (returns own roles) |

### 6.7 Read-Only Routes (any authenticated user)

All GET endpoints not listed above are accessible to any authenticated user (viewer+):
- `GET /api/v1/agents/`, `GET /api/v1/agents/{name}`
- `GET /api/v1/tools/`, `GET /api/v1/skills/`
- `GET /api/v1/workflows/`, `GET /api/v1/workflows/{id}`
- `GET /api/v1/deployments/`
- `GET /api/v1/teams/`
- `GET /api/v1/me`
- `GET /api/v1/agents/{name}/runs`
- `GET /api/v1/agents/{name}/memory`

### 6.8 Internal/System Routes (no user auth)

| Endpoint | Access |
|----------|--------|
| `GET /api/v1/bundle` | OPA bundle server (no auth) |
| `POST /api/v1/internal/runs/start` | Scheduler/event-gateway (service identity) |
| `GET /healthz` | Kubernetes probes |
| `PATCH /api/v1/deployments/{id}` | Deploy-controller callback |

---

## 7. HITL Routing Rewrite

### 7.1 Current State (broken)

`routers/approvals.py` uses `approval_authority` to scope the HITL queue:
- Looks up which tools the caller is authorized to approve (by `approver_user_id`)
- Adds tools that have role-based authority records for `platform_admin` or `team_lead`
- **Bug**: checks that a role-based `approval_authority` record *exists* but never checks whether the caller actually holds that role

### 7.2 New State

Replace the `approval_authority`-based routing with `artifact_role_grants`:

**List approvals** (`GET /approvals/?context=production`):
1. Extract caller's `sub` from JWT claims (not the spoofable `X-User-Sub` header)
2. If caller is `platform-admin` → return all production approvals (no filter)
3. Otherwise, query `artifact_role_grants` for all `artifact_id` values where caller has `approver` (direct user grant or team grant)
4. Filter approvals to only those whose `agent_id` is in that set
5. If the set is empty → return empty list

**Decide approval** (`PATCH /approvals/{id}`):
1. Load the approval record
2. Resolve the approval's `agent_id` (may need to look up from `agent_name`)
3. Check `can_approve_hitl(db, caller, agent_id)` → 403 if false
4. Proceed with existing optimistic-lock logic

### 7.3 Approvals Table Change

The `approvals` table currently stores `agent_name` (string) but `artifact_role_grants` references `artifact_id` (UUID). The routing code needs to join through the `agents` table to resolve name → id. Alternatively, add `agent_id` to the approvals table (populated at creation time from the agent lookup).

---

## 8. Frontend Guards

### 8.1 AuthContext Enhancement

`studio/src/contexts/AuthContext.tsx` — add:

```typescript
interface AuthContextValue {
  // ... existing fields ...
  role: string | null;         // normalized: 'platform-admin' | 'contributor' | 'viewer'
  isAtLeast: (minRole: 'viewer' | 'contributor' | 'platform-admin') => boolean;
}
```

`isAtLeast` uses a hierarchy: `viewer (0) < contributor (1) < platform-admin (2)`.

### 8.2 Route Guards

`studio/src/App.tsx` — wrap protected routes:

```tsx
<Route path="/admin/*" element={<RequireRole minRole="platform-admin">...}>
<Route path="/playground" element={<RequireRole minRole="contributor">...}>
<Route path="/agents/create" element={<RequireRole minRole="contributor">...}>
```

`RequireRole` redirects to `/` (or shows a 403 page) if the user's role is below the minimum.

### 8.3 Sidebar Filtering

`studio/src/components/Sidebar.tsx` — conditionally render:
- **Administration section**: visible only to `platform-admin`
- **Playground/Build sections**: visible only to `contributor+`
- **Catalog, HITL inbox**: visible to all

### 8.4 Role Dropdown Update

`studio/src/pages/AdminAccessPage.tsx` — change the role options from `["admin", "operator", "viewer"]` to `["platform-admin", "contributor", "viewer"]` with updated display labels and color chips.

### 8.5 `/me` Endpoint Enrichment

`services/registry-api/routers/me.py` — return normalized role and artifact roles:

```json
{
  "sub": "abc-123",
  "email": "user@example.com",
  "preferred_username": "platform-admin",
  "team": "platform",
  "role": "platform-admin",
  "artifact_roles": [
    { "artifact_id": "uuid", "artifact_type": "agent", "role": "agent-admin" },
    { "artifact_id": "uuid", "artifact_type": "workflow", "role": "approver" }
  ]
}
```

---

## 9. Keycloak Realm Changes

### 9.1 New Realm Roles

The `realm-init-job.yaml` needs to create three realm roles: `platform-admin`, `contributor`, `viewer`. The old roles (`admin`, `operator`) are kept as aliases during transition.

### 9.2 Keycloak Client Update

`keycloak_client.py` `set_user_realm_role()` must recognize both old and new role names. The platform roles set expands: `{"admin", "operator", "viewer", "platform-admin", "contributor"}`. When assigning, map old names to new.

---

## 10. Migration Strategy

### 10.1 Database Migration (0030)

1. Create `artifact_role_grants` table with indexes and constraints
2. Rename role values: `UPDATE user_team_assignments SET role = 'platform-admin' WHERE role = 'admin'`; `UPDATE user_team_assignments SET role = 'contributor' WHERE role = 'operator'`
3. Update server_default from `'operator'` to `'contributor'`
4. Make migration idempotent (IF NOT EXISTS guards)

### 10.2 Approval Authority Transition

The `approval_authority` table is **not** automatically migrated to `artifact_role_grants` because the mapping is lossy — `approval_authority` points at **tools** (by name string) while `artifact_role_grants` points at **agents** (by UUID). Instead:

1. Keep `approval_authority` table intact (do not drop)
2. New HITL routing reads from `artifact_role_grants` only
3. `AdminApprovalAuthorityPage.tsx` gets a deprecation banner pointing to the artifact roles UI
4. Drop `approval_authority` in a future migration once platform-admins have reassigned approvers

### 10.3 Backward Compatibility

The `rbac.py` permission service maps old role names transparently (`admin` → `platform-admin`, `operator` → `contributor`). Any code path that reads `user_team_assignments.role` and gets an old value still works.

---

## 11. E2E Flow Walkthrough

### 11.1 Agent Lifecycle with RBAC

```
1. Alice (contributor) creates "fraud-detector" agent
   → system auto-grants Alice agent-admin on fraud-detector
   → Alice can edit, test in playground, deploy to sandbox

2. Alice develops in sandbox
   → playground access: ✓ (contributor)
   → sandbox deploy: ✓ (contributor)

3. Alice submits for publish
   → contributor can submit: ✓

4. Bob (platform-admin) reviews and approves publish request
   → publish_status → published

5. Bob deploys fraud-detector to production
   → can_deploy_to_production: ✓ (platform-admin)
   → OR Alice deploys: ✓ (agent-admin on fraud-detector)

6. Alice grants "approver" on fraud-detector to the "operations" team
   → can_delegate_role: ✓ (Alice is agent-admin)

7. Carol (contributor, operations team) sees HITL requests for fraud-detector
   → operations team has approver grant → Carol sees them
   → Dave (contributor, platform team, no grant) → does NOT see them

8. Alice suspends the production deployment
   → agent-admin can suspend: ✓

9. Eve (viewer) browses the catalog, sees fraud-detector
   → viewer can read: ✓
   → Eve tries playground: ✗ (403, viewer cannot use playground)
   → Eve tries deploy: ✗ (403, viewer cannot deploy)
```

### 11.2 Cross-Team Delegation

```
1. Alice (platform team, agent-admin on fraud-detector) grants agent-admin to "operations" team
2. All operations team members can now manage fraud-detector in production
3. Frank (operations) grants "approver" on fraud-detector to "compliance" team
   → Frank can delegate because he's agent-admin (via team grant)
4. Grace (compliance, viewer) sees HITL for fraud-detector
   → But Grace cannot use playground (viewer globally)
   → She can only approve/reject HITL items
```

---

## 12. E2E Test Plan

New suite: `scripts/e2e/suite-32-rbac.sh`

| ID | Test | What it proves |
|----|------|---------------|
| T-S32-001 | Create user with role=contributor, `GET /me` returns `contributor` | Role normalization works |
| T-S32-002 | Create agent → query `artifact_role_grants` confirms agent-admin row | Creator auto-grant |
| T-S32-003 | Creator deploys to production → 200 | agent-admin can deploy to production |
| T-S32-004 | Random contributor deploys to production → 403 | Non-admin blocked from production deploy |
| T-S32-005 | Contributor deploys to sandbox → 200 | Sandbox deploy unguarded for contributors |
| T-S32-006 | Viewer tries `POST /playground/runs` → 403 | Playground blocked for viewer |
| T-S32-007 | Contributor tries `POST /playground/runs` → 200 | Playground open for contributor |
| T-S32-008 | Grant approver to user, that user sees HITL items | Scoped HITL visibility |
| T-S32-009 | User without approver tries `PATCH /approvals/{id}` → 403 | HITL decide blocked |
| T-S32-010 | agent-admin grants approver to another user → 201 | Delegation works |
| T-S32-011 | Contributor (no scoped role) tries to grant → 403 | Delegation blocked without authority |
| T-S32-012 | Revoke grant → subsequent permission check returns false | Soft-delete works |
| T-S32-013 | Contributor tries `GET /admin/users` → 403 | Admin routes blocked |
| T-S32-014 | Grant approver to team, team member can see HITL | Team-level grants work |
| T-S32-015 | Cleanup test artifacts | Housekeeping |

---

## 13. Future Improvements (Deferred)

| Item | Description | Why deferred |
|------|-------------|-------------|
| **Tool-level approver granularity** | Scope `approver` to a specific `(agent, tool)` pair instead of the whole agent | Adds complexity; agent-level scope is sufficient for MVP |
| **Mandatory deploy gate** | Block production deploy until at least one agent-admin (beyond the creator) is assigned | Advisory is simpler; creator auto-grant handles the common case |
| **Revocation cascading** | Option to cascade-revoke all grants made by a revoked agent-admin | Orphan-keep is safer for now; cascade risks unintended access loss |
| **Role audit log** | Full history of grant/revoke events with timestamps and actors | `granted_by` + `granted_at` + `revoked_at` covers basics; append-only audit is future |
| **UI for artifact roles** | Dedicated Studio page to manage artifact-scoped grants per agent | Can be built after the API is stable; platform-admin can use API directly for now |
| **Approval authority deprecation** | Drop the `approval_authority` table after full migration | Needs data migration tooling and admin communication |

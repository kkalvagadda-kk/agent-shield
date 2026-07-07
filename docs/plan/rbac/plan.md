# RBAC Implementation Plan

**Design doc**: `docs/design/rbac-design.md`  
**Decision**: `docs/decisions.md` §Decision 25  
**Date**: 2026-07-06  
**Current state**: Zero RBAC implementation — authentication exists (JWT validation), authorization does not.

---

## Context

Every authenticated user can do everything: create/delete agents, deploy to production, approve HITL items across all teams, access admin panels. Three Keycloak realm roles (`admin`, `operator`, `viewer`) exist in `user_team_assignments` but nothing reads them. The frontend's `hasRole()` is defined but never called. This plan implements two-tier RBAC: global roles for platform-wide capability, artifact-scoped roles for per-agent/workflow authority.

---

## Concrete Numbers

| Item | Value |
|------|-------|
| Migration | `0032` (0031 is `agent_events_workflow_id`) |
| E2E suite | `suite-33-rbac.sh` (suite-32 is `schedule-payload`) |
| registry-api tag | `0.2.63` → `0.2.64` |
| studio tag | `0.1.47` → `0.1.48` |
| values.yaml registry-api | line 517 |
| values.yaml studio | line 821 |

---

## Phase 1: Data Layer (Migration + ORM Model)

### Goal
Create the `artifact_role_grants` table and rename legacy role values in `user_team_assignments`.

### Tasks

| ID | Task | Files | Size |
|----|------|-------|------|
| 1.1 | Create migration `0032_rbac_artifact_role_grants.py` | `services/registry-api/alembic/versions/0032_rbac_artifact_role_grants.py` (new) | M |
| 1.2 | Add `ArtifactRoleGrant` ORM model | `services/registry-api/models.py` | S |

### 1.1 — Migration 0032

Use raw SQL via `op.execute()` with `IF NOT EXISTS` guards (project pattern from 0029/0030/0031).

**Part A — Create `artifact_role_grants` table:**
```sql
CREATE TABLE IF NOT EXISTS artifact_role_grants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_type   VARCHAR(32)  NOT NULL CHECK (artifact_type IN ('agent','workflow')),
    artifact_id     UUID         NOT NULL,
    role            VARCHAR(32)  NOT NULL CHECK (role IN ('agent-admin','approver')),
    grantee_type    VARCHAR(16)  NOT NULL CHECK (grantee_type IN ('user','team')),
    grantee_id      TEXT         NOT NULL,
    granted_by      TEXT         NOT NULL,
    granted_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ
);
```

**Indexes:**
- `idx_arg_lookup` on `(artifact_id, grantee_type, grantee_id, role, revoked_at)` — permission check hot path
- `idx_arg_grantee` on `(grantee_type, grantee_id, revoked_at)` — "what roles does user X have?"
- Unique partial: `(artifact_id, role, grantee_type, grantee_id) WHERE revoked_at IS NULL` — prevents duplicate active grants

**Part B — Rename role values:**
```sql
UPDATE user_team_assignments SET role = 'platform-admin' WHERE role = 'admin';
UPDATE user_team_assignments SET role = 'contributor' WHERE role = 'operator';
ALTER TABLE user_team_assignments ALTER COLUMN role SET DEFAULT 'contributor';
```
These UPDATEs are idempotent (no-op if already renamed).

### 1.2 — ORM Model

Add `ArtifactRoleGrant` to `models.py` using the existing `Mapped[]`/`mapped_column()` pattern (alongside the 30+ existing models). Add `CheckConstraint` for `artifact_type`, `role`, `grantee_type`. Add to `__all__`.

### Verification
- `python3 -c "import ast; ast.parse(open('services/registry-api/models.py').read())"`
- Import routers + `sqlalchemy.orm.configure_mappers()` succeeds

---

## Phase 2: Permission Service (`rbac.py`)

### Goal
Single source of truth for all permission checks. All routers import from here — no inline authorization logic.

### Tasks

| ID | Task | Files | Size |
|----|------|-------|------|
| 2.1 | Core helpers: `get_user_global_role()`, `get_user_team()` | `services/registry-api/rbac.py` (new) | S |
| 2.2 | `has_artifact_role()` | same | M |
| 2.3 | Policy decision functions | same | M |
| 2.4 | `require_global_role()` dependency factory | same | M |

### 2.1 — Core Helpers

```python
async def get_user_global_role(db: AsyncSession, user_sub: str) -> str | None
```
Query `user_team_assignments` via raw `text()` (matching `me.py` line 29 and `chat.py` line 63 patterns). Normalize legacy values: `"admin"` → `"platform-admin"`, `"operator"` → `"contributor"`. Return `None` if no row.

```python
async def get_user_team(db: AsyncSession, user_sub: str) -> str | None
```
Same query, return `team_name`.

### 2.2 — `has_artifact_role()`

```python
async def has_artifact_role(db, user_sub, artifact_id, role, user_team=None) -> bool
```
Query `ArtifactRoleGrant` ORM model: `WHERE revoked_at IS NULL AND artifact_id = :id AND role = :role` AND (`grantee_type='user' AND grantee_id=user_sub` OR `grantee_type='team' AND grantee_id=user_team`).

### 2.3 — Policy Decision Functions

| Function | Logic |
|----------|-------|
| `can_deploy_to_production(db, sub, artifact_id)` | `platform-admin` OR `has_artifact_role(agent-admin)` |
| `can_approve_hitl(db, sub, artifact_id)` | `platform-admin` OR `has_artifact_role(approver)` |
| `can_use_playground(db, sub)` | role ∈ {`platform-admin`, `contributor`} |
| `can_create_agent(db, sub)` | role ∈ {`platform-admin`, `contributor`} |
| `can_delegate_role(db, caller, artifact_id, target_role)` | `platform-admin` OR (`agent-admin` on artifact AND `target_role` ∈ {`agent-admin`, `approver`}) |

Each function calls `get_user_global_role()` + `get_user_team()` internally so callers don't need to pre-fetch.

### 2.4 — `require_global_role()` Factory

```python
def require_global_role(*allowed_roles):
    async def _dep(
        claims: dict = Depends(require_user),
        db: AsyncSession = Depends(get_db),
    ) -> dict:
        role = await get_user_global_role(db, claims.get("sub"))
        if role not in allowed_roles:
            raise HTTPException(403, detail="insufficient_role")
        claims["_global_role"] = role
        claims["_team"] = await get_user_team(db, claims.get("sub"))
        return claims
    return _dep
```

Returns enriched claims dict. Routers that need only role gating use `claims: dict = Depends(require_global_role("platform-admin"))`. Routers that need artifact-scoped checks call `rbac.py` functions inline after the dependency.

### Verification
- `python3 -c "from rbac import require_global_role, can_deploy_to_production"` succeeds
- Syntax check passes

---

## Phase 3: Backend Endpoint Guards

### Goal
Add authorization to every router endpoint per the design doc's endpoint matrix (§6).

### Backward Compatibility Strategy

Many existing E2E suites call endpoints with `X-User-Sub` header and no JWT. Two approaches:

- **Admin routes** (`/api/v1/admin/*`): Strict — `require_global_role("platform-admin")`. These are never called by E2E suites without auth.
- **All other mutate/deploy/playground routes**: Use `require_user` (mandatory JWT). The E2E suites obtain a JWT via Keycloak direct-grant (`grant_type=password`) — this pattern already exists in `suite-14-consumer-chat.sh`. Suites that don't have a JWT will need a helper function added (Task 7.1).

### Tasks

| ID | Task | Files | Size | Guard |
|----|------|-------|------|-------|
| 3.1 | Guard admin routes | `routers/admin.py` | M | `require_global_role("platform-admin")` on all endpoints |
| 3.2 | Guard admin user routes | `routers/admin_users.py` | M | `require_global_role("platform-admin")` on all endpoints. Update `PLATFORM_ROLES` set to include new names. Change default role from `"operator"` to `"contributor"`. |
| 3.3 | Guard agent create | `routers/agents.py` | S | `require_user` + `can_create_agent()` check. Keep `X-User-Sub` as secondary fallback for internal service calls. |
| 3.4 | Guard agent update/delete | `routers/agents.py` | M | `require_user` + check: `platform-admin` OR `agent-admin` on the agent. Resolve agent → `agent.id` for scoped check. |
| 3.5 | Guard quarantine | `routers/agents.py` | S | `require_global_role("platform-admin")` |
| 3.6 | Guard deploy endpoint | `routers/deployments.py` | L | Add `require_user`. If `environment == "production"`: `can_deploy_to_production()` → 403 if false. If sandbox/staging: contributor+ check. **Hardest task** — endpoint currently has zero auth. The `PATCH /deployments/{id}` callback is on `global_deployments_router` (separate router), so it stays unguarded. |
| 3.7 | Guard playground | `routers/playground.py` | S | `require_user` + `can_use_playground()` on `POST /playground/runs` |
| 3.8 | Guard tools/skills/triggers mutate | `routers/tools.py`, `routers/skills.py`, `routers/triggers.py` | M | `require_user` + contributor+ check on create/update/delete |
| 3.9 | Guard workflow create/mutate | `routers/composite_workflows.py` | M | `require_user` + `can_create_agent()` on create; `platform-admin` OR `agent-admin` on update/delete |

### Key Risk: Deploy Endpoint (3.6)

The deploy endpoint (`POST /api/v1/agents/{name}/deploy` in `routers/deployments.py`) currently uses `X-User-Team` header with no JWT. Adding `require_user`:
- Will not affect `PATCH /deployments/{id}` (deploy-controller callback) — that's on the separate `global_deployments_router`
- The existing team-match check (`x_user_team` vs `agent.team`) should read team from `claims["_team"]` (via rbac) rather than the spoofable header
- E2E suites that call deploy will need JWT auth added

### Verification
For each guarded endpoint, verify:
1. No token → 401
2. Viewer token → 403 on mutate/deploy/playground
3. Contributor token → 200 on create, 403 on admin
4. Platform-admin token → 200 on everything

---

## Phase 4: Artifact-Scoped Roles (Auto-Grant, HITL Rewrite, New Router, /me)

### Goal
Build the artifact-scoped features: creator auto-grant, artifact roles CRUD, HITL routing rewrite, and `/me` enrichment.

### Tasks

| ID | Task | Files | Size |
|----|------|-------|------|
| 4.1 | Creator auto-grant (agents) | `routers/agents.py` | S |
| 4.2 | Creator auto-grant (workflows) | `routers/composite_workflows.py` | S |
| 4.3 | Artifact roles router | `routers/artifact_roles.py` (new), `main.py` | L |
| 4.4 | HITL list rewrite | `routers/approvals.py` | L |
| 4.5 | HITL decide rewrite | `routers/approvals.py` | M |
| 4.6 | `/me` enrichment | `routers/me.py` | M |

### 4.1 — Creator Auto-Grant

In `agents.py` `create_agent`, after `await db.refresh(agent)` (line 97) where `agent.id` is populated:

```python
from models import ArtifactRoleGrant
auto_grant = ArtifactRoleGrant(
    artifact_type="agent", artifact_id=agent.id,
    role="agent-admin", grantee_type="user",
    grantee_id=caller, granted_by="system:auto-grant",
)
db.add(auto_grant)
await db.flush()
```

Same pattern in `composite_workflows.py` for workflow creation.

### 4.3 — Artifact Roles Router

New file: `services/registry-api/routers/artifact_roles.py`

Prefix: `/api/v1/artifact-roles`

| Endpoint | Auth | Logic |
|----------|------|-------|
| `GET /` | Any authenticated | Filter: platform-admin sees all; others see own grants + grants on artifacts they're agent-admin of. Query params: `artifact_id`, `artifact_type`, `grantee_id` |
| `POST /` | `can_delegate_role()` | Create grant. Validate: artifact exists, no duplicate active grant (unique constraint handles this). |
| `DELETE /{id}` | platform-admin OR agent-admin on artifact | Soft-delete: set `revoked_at = now()` |
| `GET /my-roles` | Any authenticated | Return caller's own active grants (user direct + team) |

Register in `main.py` via `app.include_router(artifact_roles_router)`.

### 4.4 — HITL List Rewrite (Hardest Task)

Current code (`approvals.py` lines 187-205) queries `ApprovalAuthority` by `approver_user_id` and `approver_role` — **the design doc's §7.1 bug** (checks record exists but never verifies caller holds the role).

**New logic for `GET /approvals/?context=production`:**
1. Get caller from JWT claims via `require_user` (replace `X-User-Sub` header)
2. Get caller's global role and team via `rbac.get_user_global_role()` / `get_user_team()`
3. If `platform-admin` → no filter, return all production approvals
4. Query `artifact_role_grants` for `artifact_id` values where caller has `approver` (check both `grantee_type='user'` and `grantee_type='team'`)
5. Filter `Approval` query: `WHERE agent_id IN (approved_artifact_ids)`
6. Empty set → empty list

**Decide approval** (`PATCH /approvals/{id}`):
1. Load approval record → get `agent_id`
2. `can_approve_hitl(db, caller, agent_id)` → 403 if false
3. Proceed with existing optimistic-lock logic

### 4.6 — `/me` Enrichment

Add `artifact_roles` to the response:

```python
# After existing team/role fetch
art_rows = await db.execute(
    select(ArtifactRoleGrant).where(
        ArtifactRoleGrant.revoked_at.is_(None),
        or_(
            and_(ArtifactRoleGrant.grantee_type == "user", ArtifactRoleGrant.grantee_id == sub),
            and_(ArtifactRoleGrant.grantee_type == "team", ArtifactRoleGrant.grantee_id == team),
        )
    )
)
artifact_roles = [
    {"artifact_id": str(r.artifact_id), "artifact_type": r.artifact_type, "role": r.role}
    for r in art_rows.scalars()
]
```

Normalize the `role` value in the response (map legacy `admin`→`platform-admin`, `operator`→`contributor`).

### Verification
- Create agent → query `artifact_role_grants` confirms `agent-admin` row
- `POST /artifact-roles` with agent-admin caller → 201
- `DELETE /artifact-roles/{id}` → `revoked_at` set
- `GET /approvals/?context=production` as approver → sees matching approvals
- `GET /approvals/?context=production` as non-approver → empty
- `GET /me` returns `artifact_roles` array

---

## Phase 5: Keycloak Realm Roles

### Goal
Align the identity provider so Keycloak realm roles match the new taxonomy.

### Tasks

| ID | Task | Files | Size |
|----|------|-------|------|
| 5.1 | Create realm roles in init job | `charts/agentshield/templates/realm-init-job.yaml` | M |
| 5.2 | Update keycloak_client role set | `services/registry-api/keycloak_client.py` | S |

### 5.1 — Realm Init Job

After client creation, add idempotent `kcadm.sh` commands:

```bash
# Create realm roles (idempotent — kcadm create returns 409 if exists)
kcadm.sh create roles -r agentshield -s name=platform-admin || true
kcadm.sh create roles -r agentshield -s name=contributor || true
kcadm.sh create roles -r agentshield -s name=viewer || true

# Assign platform-admin role to the platform-admin user
PLATFORM_ADMIN_ID=$(kcadm.sh get users -r agentshield -q username=platform-admin --fields id --format csv --noquotes)
kcadm.sh add-roles -r agentshield --uusername platform-admin --rolename platform-admin
kcadm.sh add-roles -r agentshield --uusername agent-reviewer --rolename viewer
```

### 5.2 — Keycloak Client Update

In `keycloak_client.py` line 162, expand `platform_roles`:
```python
platform_roles = {"admin", "operator", "viewer", "platform-admin", "contributor"}
```

In `set_user_realm_role()`, map old names to new when assigning:
- `"admin"` → assign `platform-admin`
- `"operator"` → assign `contributor`

### Verification
- After deploy: `kcadm.sh get roles -r agentshield` shows `platform-admin`, `contributor`, `viewer`
- Login as `platform-admin` user → JWT `realm_access.roles` includes `platform-admin`

---

## Phase 6: Frontend Guards

### Goal
Hide UI elements users can't use and block navigation to unauthorized routes. The backend already rejects unauthorized requests — this is UX polish.

### Tasks

| ID | Task | Files | Size |
|----|------|-------|------|
| 6.1 | Add `isAtLeast()` + `artifactRoles` to AuthContext | `studio/src/contexts/AuthContext.tsx` | S |
| 6.2 | Update `MeResponse` + boot flow | `studio/src/api/registryApi.ts`, `studio/src/main.tsx` | S |
| 6.3 | Create `RequireRole` component | `studio/src/components/RequireRole.tsx` (new) | S |
| 6.4 | Add route guards to App.tsx | `studio/src/App.tsx` | M |
| 6.5 | Filter sidebar sections | `studio/src/components/Sidebar.tsx` | M |
| 6.6 | Update role dropdown in AdminAccessPage | `studio/src/pages/AdminAccessPage.tsx` | S |

### 6.1 — AuthContext Enhancement

Add to `AuthContextValue`:
```typescript
isAtLeast: (minRole: 'viewer' | 'contributor' | 'platform-admin') => boolean;
artifactRoles: { artifact_id: string; artifact_type: string; role: string }[];
```

Implementation:
```typescript
const ROLE_RANK: Record<string, number> = { viewer: 0, contributor: 1, 'platform-admin': 2 };
const normalize = (r: string) => r === 'admin' ? 'platform-admin' : r === 'operator' ? 'contributor' : r;
// isAtLeast: (ROLE_RANK[normalize(role)] ?? -1) >= ROLE_RANK[minRole]
```

### 6.3 — RequireRole Component

```tsx
function RequireRole({ minRole, children }: { minRole: string; children: ReactNode }) {
  const { isAtLeast } = useAuth();
  if (!isAtLeast(minRole)) return <Navigate to="/" replace />;
  return <>{children}</>;
}
```

### 6.4 — Route Guards in App.tsx

```tsx
<Route path="/admin/*" element={<RequireRole minRole="platform-admin">...</RequireRole>} />
<Route path="/playground" element={<RequireRole minRole="contributor">...</RequireRole>} />
<Route path="/agents/new" element={<RequireRole minRole="contributor">...</RequireRole>} />
```

### 6.5 — Sidebar Filtering

```tsx
// Administration section — only for platform-admin
{isAtLeast('platform-admin') && <Section label="Administration" items={ADMIN_ITEMS} />}
// Playground sections — only for contributor+
{isAtLeast('contributor') && <Section label="Playground" items={PLAYGROUND_BUILD} />}
```

### 6.6 — Role Dropdown Update

Change `ROLES` from `["admin", "operator", "viewer"]` to `["platform-admin", "contributor", "viewer"]`. Update `ROLE_CHIP` colors. Change default in CreateUserModal from `"operator"` to `"contributor"`.

### Verification
- `cd studio && npm run typecheck` passes
- `cd studio && npm run test` passes (update any affected component tests)
- Log in as viewer → admin sidebar hidden, `/admin/access` URL redirects to `/`
- Log in as contributor → playground visible, admin hidden
- Log in as platform-admin → everything visible

---

## Phase 7: E2E Tests + Image Tags

### Goal
Prove everything works end-to-end. Bump image tags for deployment.

### Tasks

| ID | Task | Files | Size |
|----|------|-------|------|
| 7.1 | Write `suite-33-rbac.sh` | `scripts/e2e/suite-33-rbac.sh` (new) | L |
| 7.2 | Register in run-all.sh | `scripts/e2e/run-all.sh` | S |
| 7.3 | Bump image tags | `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml` | S |

### 7.1 — E2E Suite

Follow the `kubectl exec` + Python/httpx pattern from existing suites. Tests from design doc §12:

| Test ID | What it proves |
|---------|---------------|
| T-S33-001 | `/me` returns normalized `contributor` (not `operator`) |
| T-S33-002 | Create agent → `artifact_role_grants` has auto-grant `agent-admin` row |
| T-S33-003 | Creator (agent-admin) deploys to production → 200 |
| T-S33-004 | Random contributor deploys to production → 403 |
| T-S33-005 | Contributor deploys to sandbox → 200 |
| T-S33-006 | Viewer `POST /playground/runs` → 403 |
| T-S33-007 | Contributor `POST /playground/runs` → 200 |
| T-S33-008 | Grant `approver` → user sees HITL items for that agent |
| T-S33-009 | User without `approver` tries `PATCH /approvals/{id}` → 403 |
| T-S33-010 | `agent-admin` grants `approver` to another user → 201 |
| T-S33-011 | Contributor (no scoped role) tries to grant → 403 |
| T-S33-012 | Revoke grant → permission check returns false |
| T-S33-013 | Contributor tries `GET /admin/users` → 403 |
| T-S33-014 | Grant `approver` to team → team member sees HITL |
| T-S33-015 | Cleanup test artifacts |

### 7.3 — Image Tags

| File | Line | Old | New |
|------|------|-----|-----|
| `scripts/deploy-cpe2e.sh` | 53 | `REGISTRY_API_TAG="0.2.63"` | `"0.2.64"` |
| `scripts/deploy-cpe2e.sh` | 56 | `STUDIO_TAG="0.1.47"` | `"0.1.48"` |
| `charts/agentshield/values.yaml` | 517 | `tag: "0.2.63"` | `"0.2.64"` |
| `charts/agentshield/values.yaml` | 821 | `tag: "0.1.47"` | `"0.1.48"` |

---

## Phase Sequencing & Dependencies

```
Phase 1 (Data Layer)
  ↓
Phase 2 (rbac.py)
  ↓
Phase 3 (Endpoint Guards)  ←─ can run in parallel with ──→  Phase 5 (Keycloak)
  ↓
Phase 4 (Auto-Grant, HITL, Artifact Roles, /me)
  ↓
Phase 6 (Frontend Guards)  ←─ depends on Phase 4 (/me enrichment)
  ↓
Phase 7 (E2E Tests + Image Tags)
```

Phases 3 and 5 can run in parallel after Phase 2. Phase 4 depends on Phase 3 (guards must be in place before auto-grant inserts are meaningful). Phase 6 depends on Phase 4 (needs the enriched `/me` response for `artifactRoles`). Phase 7 is always last.

---

## Effort Summary

| Phase | Description | Effort |
|-------|-------------|--------|
| 1 | Migration + ORM model | **M** |
| 2 | `rbac.py` permission service | **L** |
| 3 | Guard all endpoints (9 tasks) | **XL** |
| 4 | Auto-grant + HITL rewrite + artifact router + /me | **XL** |
| 5 | Keycloak realm roles | **M** |
| 6 | Frontend guards | **L** |
| 7 | E2E tests + image tags | **L** |

**Total**: ~35 tasks across 7 phases.

---

## Key Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Adding `require_user` to currently-unguarded endpoints breaks E2E suites | High — all 32 existing suites may fail | E2E suites already have a JWT-acquisition pattern (`suite-14-consumer-chat.sh` uses Keycloak direct-grant). Add a shared helper `get_jwt()` function to the e2e common preamble. |
| HITL routing paradigm shift (tool-scoped → agent-scoped) | Medium — changes who sees which approvals | The `approval_authority` table is NOT dropped. Log both old and new routing results during testing to catch discrepancies. |
| Deploy endpoint has zero auth today | Medium — adding auth could break CI/CD flows | The `PATCH /deployments/{id}` callback is on `global_deployments_router` (separate router instance) — verified it is not affected. |
| Migration renames role values | Low — could break queries using old values | `rbac.py` normalizes legacy values transparently. Frontend maps `admin`→`platform-admin`, `operator`→`contributor` in `isAtLeast()`. |

---

## Files Created (New)

| File | Phase |
|------|-------|
| `services/registry-api/alembic/versions/0032_rbac_artifact_role_grants.py` | 1 |
| `services/registry-api/rbac.py` | 2 |
| `services/registry-api/routers/artifact_roles.py` | 4 |
| `studio/src/components/RequireRole.tsx` | 6 |
| `scripts/e2e/suite-33-rbac.sh` | 7 |

## Files Modified

| File | Phase | What Changes |
|------|-------|-------------|
| `services/registry-api/models.py` | 1 | Add `ArtifactRoleGrant` model |
| `services/registry-api/routers/admin.py` | 3 | Add `require_global_role("platform-admin")` to all endpoints |
| `services/registry-api/routers/admin_users.py` | 3 | Add auth guards, update role set, change default `"operator"`→`"contributor"` |
| `services/registry-api/routers/agents.py` | 3+4 | Add auth guards + creator auto-grant |
| `services/registry-api/routers/deployments.py` | 3 | Add `require_user` + production deploy check |
| `services/registry-api/routers/playground.py` | 3 | Add playground permission check |
| `services/registry-api/routers/tools.py` | 3 | Add contributor+ guards |
| `services/registry-api/routers/skills.py` | 3 | Add contributor+ guards |
| `services/registry-api/routers/triggers.py` | 3 | Add contributor+ guards |
| `services/registry-api/routers/composite_workflows.py` | 3+4 | Add auth guards + creator auto-grant |
| `services/registry-api/routers/approvals.py` | 4 | HITL routing rewrite |
| `services/registry-api/routers/me.py` | 4 | Add `artifact_roles` to response |
| `services/registry-api/main.py` | 4 | Register `artifact_roles_router` |
| `charts/agentshield/templates/realm-init-job.yaml` | 5 | Create realm roles |
| `services/registry-api/keycloak_client.py` | 5 | Expand role set + map old→new |
| `studio/src/contexts/AuthContext.tsx` | 6 | Add `isAtLeast()`, `artifactRoles` |
| `studio/src/api/registryApi.ts` | 6 | Add `artifact_roles` to `MeResponse` |
| `studio/src/main.tsx` | 6 | Pass artifact_roles to buildAuthValue |
| `studio/src/App.tsx` | 6 | Route guards |
| `studio/src/components/Sidebar.tsx` | 6 | Role-based section filtering |
| `studio/src/pages/AdminAccessPage.tsx` | 6 | Update role names + colors |
| `scripts/deploy-cpe2e.sh` | 7 | Bump tags |
| `charts/agentshield/values.yaml` | 7 | Bump tags |
| `scripts/e2e/run-all.sh` | 7 | Register suite-33 |

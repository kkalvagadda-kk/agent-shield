# Implementation Plan — Webhook Application Identity & Invoker Grants

**Source spec:** `docs/design/todo/webhook-application-identity.md` (§Decision 30, `docs/decisions.md`). **Grounding decisions:** `research.md` in this directory — read it alongside this plan; every deviation from the design doc's literal text is justified there, not invented here. **Supporting artifacts:** `data-model.md` (full migration DDL + ORM model), `contracts/artifact-grants.md`, `contracts/applications.md`, `contracts/gateway-verification.md`, `quickstart.md`.

---

## Goal

Turn a webhook-sending application from a per-trigger, unreusable `webhook_clients` secret into a first-class, team-owned, RBAC-governed principal: one secret per real sending system, reusable across every artifact an `agent-admin` grants it `invoker` on, revocable per-artifact or killed everywhere in one action — and, as a side effect of building the grant endpoint this requires, deliver the **first working implementation** of Decision 25's still-unbuilt delegation API (`artifact_role_grants` write path) for all three grantee kinds (`user`, `team`, `application`), not just applications.

## Architecture

```
┌─────────────┐   POST /teams/{team}/applications        ┌──────────────────┐
│   Studio    │──────────────────────────────────────────▶│                  │
│ (Priya:     │   POST /artifacts/{type}/{id}/grants       │   registry-api   │
│  create app)│──────────────────────────────────────────▶│                  │
│ (Amit:      │                                             │  applications    │
│  grant      │   GET  /artifacts/{type}/{id}/grants        │  artifact_role_  │
│  invoker)   │◀──────────────────────────────────────────│  grants (widened)│
└─────────────┘                                             └──────────────────┘
                                                                     │ (read-only,
                                                                     │  no shared
                                                                     │  package)
┌──────────────┐  POST /hooks/{agent}/{token}                       ▼
│ billing-     │  X-Client-Id / X-Timestamp / X-Signature   ┌──────────────────┐
│ service      │────────────────────────────────────────────▶│  event-gateway   │
│ (external    │                                             │  webhook_auth.py │
│  sender)     │◀────────────────────────────────────────── │  _verify_client_ │
└──────────────┘   uniform 202 / uniform 401                │  signed (rewired)│
                                                              └──────────────────┘
```

Two services change: **registry-api** (new table, widened constraints, one new generic delegation router reused by three grantee kinds, one new team-scoped applications router, soft-enforced authorization on the pre-existing trigger-management endpoints) and **event-gateway** (the single function that resolves a `client_signed` webhook's credential, rewired to read `applications` + `artifact_role_grants` instead of `webhook_clients` — no other gateway code changes, per its own module docstring's invariants). **Studio** gets a new team-scoped Applications page and a grant-picker UI replacing the old inline `ClientPanel` on both the agent and workflow trigger cards (closing today's agent-only parity gap). `deploy-controller`, `declarative-runner`, `python-executor`, `safety-orchestrator`, `eval-runner`, `scheduler` are untouched.

## Tech Stack

No new dependencies. Backend: FastAPI + SQLAlchemy 2.0 async ORM (registry-api) / raw `psycopg2` (event-gateway, unchanged — no shared package with registry-api, confirmed in `research.md` §5). Secrets: `cryptography.Fernet` via the existing `crypto.py`/`AGENTSHIELD_ENCRYPTION_KEY`. Frontend: React + TanStack Query + Tailwind, same patterns as every existing Studio page (`CredentialsPage.tsx` is the closest analog and is reused as the structural template — see `research.md` §11). Migrations: Alembic, applied automatically by the existing `alembic-migrate` init container.

## Constitution Check (this repo's `CLAUDE.md` — Definition of Done)

| # | Requirement | Status | How this plan satisfies it |
|---|---|---|---|
| 1 | Real user journey via Playwright/manual step | **PASS** | `artifact-grants.spec.ts` (task 22) drives the actual grant flow through Settings; `webhook-applications.spec.ts` (task 23) drives create-app → grant → reload from a real logged-in session. |
| 2 | Save → reload → assert survived | **PASS** | Both new specs explicitly reload and re-assert (tasks 22, 23); `applications.spec` and `artifact-grants.spec` cases are written save→reload→assert per design doc §11.3, not just save→assert. |
| 3 | No orphan code | **PASS, with one documented exception** | Every new exported function/endpoint/schema has a live caller wired in the SAME task set (traced in "Key Interfaces" below). The one function the design doc's own pseudocode implies (`rbac.can_invoke`) is deliberately **not added** to `rbac.py` because grounding found no live caller for it there (`research.md` §5) — its logic is realized instead in `event-gateway/webhook_auth.py`'s own port functions, which DO have a live caller (`_verify_client_signed`). Not adding a function that would have no caller is what keeps this PASS, not a gap. |
| 4 | Vertical slices, not horizontal layers | **PASS** | Task order follows the design doc's own §5→§11 structure exactly (schema → rbac → gateway → API → UX → tests), and within that, backend-and-caller are wired in adjacent tasks (e.g. task 5 grants router ships together with task 7's router registration, not stranded). |
| 5 | Honest gap ledger | **PASS** | Task 27 updates `docs/testing/manual-ui-e2e-test-plan.md`'s Known Gaps with every deferred/debt item from design doc §12, PLUS the two items this grounding pass itself introduced (`ENFORCE_TRIGGER_MGMT` soft-enforcement, `webhook_clients` write-410 retirement) — both tagged **not-yet-wired (debt)**, matching this repo's existing convention for the identical `require_global_role` pattern. |
| 6 | Reason from running product, not design doc | **PASS** | `research.md` documents 11 places this plan diverged from the design doc's literal text after reading the actual code (migration numbers, suite numbers, `has_artifact_role`'s real signature, the 16-suite blast radius, `webhook_clients` 410 retirement, the two Open Questions, the Studio surface choice). |
| 7 | Bug-fix regression-test-first discipline | **N/A** | This is new-feature work, not a bug fix — no pre-existing broken behavior is being patched. Where this plan DOES change existing behavior (soft-enforcing `can_manage_artifact` on trigger CRUD, 410-ing `webhook_clients` writes), task 21 updates the one test that would otherwise silently coexist with the new behavior incorrectly (`suite-76`), and task 26 runs the full impacted-suite sweep before declaring done. |

### Post-Implementation Checklist

- **E2E tests**: `suite-82-artifact-grants.sh` (T-ARG-001…010) and `suite-83-webhook-applications.sh` (T-SYY-001…010), registered in `run-all.sh` immediately after Suite 81 (tasks 18–20).
- **Image version bumps**: `REGISTRY_API_TAG` 0.2.210→0.2.211, `EVENT_GATEWAY_TAG` 0.1.3→0.1.4, `STUDIO_TAG` 0.1.158→0.1.159, mirrored in both `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml` in the same task (task 25).
- **Experience docs**: `docs/experience/playground.md` is **not touched** — this feature does not modify any of the files CLAUDE.md lists as triggering that requirement (`PlaygroundPage.tsx`, `ChatPane.tsx`, `routers/playground.py`, `sdk/streaming.py`, etc. — none are touched by this design). Recorded here explicitly per the "no silent skip" rule rather than left unmentioned.
- **Frontend tests**: Vitest updates in task 17; `bash scripts/studio-e2e.sh` gate covers tasks 22–24.
- **Verification**: `python3 -c "import ast; ast.parse(...)"` per Python file touched; `sqlalchemy.orm.configure_mappers()` after the new `Application` ORM model lands (task 1); `cd studio && npm run typecheck` after every frontend task; full regression sweep in task 26.
- **Migrations**: 0069, 0070 — sequential after 0068 (confirmed free, `research.md` §1), idempotent (`IF [NOT] EXISTS` / `ON CONFLICT DO NOTHING` guards throughout, see `data-model.md`).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `services/registry-api/alembic/versions/0069_applications_and_invoker_grants.py` | new | `applications` table DDL + widen `ck_arg_grantee_type`/`ck_arg_role` |
| `services/registry-api/alembic/versions/0070_backfill_webhook_clients_to_applications.py` | new | Data migration: `webhook_clients` → `applications` + `invoker` grants |
| `services/registry-api/models.py` | modify | New `Application` ORM model (mirrors `WebhookClient`) |
| `services/registry-api/rbac.py` | modify | Widen `can_delegate_role`; add `can_create_application`, `can_manage_artifact`, `ENFORCE_TRIGGER_MGMT` flag |
| `services/registry-api/schemas.py` | modify | New: `ArtifactRoleGrantCreate/Response`; `ApplicationCreate/CreatedResponse/Response/Update/RotateSecretResponse` |
| `services/registry-api/routers/artifact_grants.py` | new | Generic `POST/GET/DELETE /api/v1/artifacts/{type}/{id}/grants` |
| `services/registry-api/routers/applications.py` | new | `/api/v1/teams/{team}/applications` CRUD + rotate-secret |
| `services/registry-api/routers/triggers.py` | modify | Wire `can_manage_artifact` into create/update/delete/rotate-token (soft-enforced) |
| `services/registry-api/routers/composite_workflows.py` | modify | Same wiring for the workflow-trigger endpoints (§751–862 region) |
| `services/registry-api/routers/webhook_clients.py` | modify | POST/PATCH/DELETE → `410 Gone`; GET unchanged |
| `services/registry-api/main.py` | modify | Register `artifact_grants_router`, `applications_router` |
| `services/event-gateway/webhook_auth.py` | modify | `_TRIGGER_SQL` gains `artifact_id`/`team_name`; new `lookup_application`/`has_active_invoker_grant`; `_verify_client_signed` rewired; `lookup_webhook_client` deleted |
| `studio/src/api/registryApi.ts` | modify | New `Application`/`ApplicationCreated`/`ArtifactRoleGrant` types + client functions |
| `studio/src/components/shared/InvokeAccessPanel.tsx` | new | Grant-picker UI for `invoker`, shared by agent + workflow trigger cards |
| `studio/src/components/shared/ArtifactGrantsList.tsx` | new | Lists all active grants (`agent-admin`/`approver`/`invoker`) on one artifact, shared by both surfaces |
| `studio/src/components/agent-detail/SettingsTab.tsx` | modify | Delete `ClientPanel`; render the two new shared components |
| `studio/src/components/workflow/WorkflowTriggersPanel.tsx` | modify | Render the same two shared components (parity addition) |
| `studio/src/pages/ApplicationsPage.tsx` | new | Team-scoped applications CRUD page (modeled on `CredentialsPage.tsx`) |
| `studio/src/pages/AgentDetailPage.tsx` | modify | Pass `agent.id`/`agent.team` into `SettingsTab` |
| `studio/src/pages/WorkflowBuilderPage.tsx` | modify | Pass workflow id/team into `WorkflowTriggersPanel` |
| `studio/src/components/Sidebar.tsx` | modify | New "Applications" entry under Settings section |
| `studio/src/App.tsx` | modify | New `/applications` route |
| `studio/src/components/agent-detail/SettingsTab.test.tsx` | modify (or new) | Cover `InvokeAccessPanel`/`ArtifactGrantsList` states |
| `studio/src/pages/ApplicationsPage.test.tsx` | new | Vitest for the new page |
| `studio/src/components/workflow/WorkflowTriggersPanel.test.tsx` | modify | Cover the new parity addition |
| `scripts/e2e/suite-82-artifact-grants.sh` | new | Suite A — T-ARG-001…010 |
| `scripts/e2e/suite-83-webhook-applications.sh` | new | Suite B — T-SYY-001…010 |
| `scripts/e2e/suite-76-webhook-client-signing.sh` | modify | Registration retargeted to applications+grants; new T-S76-010 |
| `scripts/e2e/run-all.sh` | modify | Register Suite 82, Suite 83 |
| `studio/e2e/artifact-grants.spec.ts` | new | Playwright — human-grantee delegation UI |
| `studio/e2e/webhook-applications.spec.ts` | new | Playwright — create-app + grant + reload |
| `studio/e2e/webhook-clients.spec.ts` | delete | Retired — asserts against the removed `ClientPanel` UI |
| `scripts/deploy-cpe2e.sh` | modify | Tag bumps + header entry |
| `charts/agentshield/values.yaml` | modify | Mirror tag bumps (registry-api ~L648, studio ~L992, event-gateway ~L768) |
| `docs/testing/manual-ui-e2e-test-plan.md` | modify | New "Known gaps" section |
| `docs/design/todo/webhook-application-identity.md` | modify | Status header: Draft → Implemented |

---

## Key Interfaces

### `rbac.py` (extended)

```python
async def can_delegate_role(db: AsyncSession, caller_sub: str, artifact_id: uuid.UUID, target_role: str) -> bool
    # target_role now accepted: "agent-admin" | "approver" | "invoker" (was missing "invoker")

async def can_create_application(db: AsyncSession, user_sub: str, team_name: str) -> bool
    # NEW. platform-admin bypass OR (contributor+ AND get_user_team(db, user_sub) == team_name)

async def can_manage_artifact(db: AsyncSession, user_sub: str, artifact_id: uuid.UUID) -> bool
    # NEW. platform-admin bypass OR has_artifact_role(db, user_sub, artifact_id, "agent-admin", team)
    # Body-identical to can_deploy_to_production but under its own name — see research.md §4.

ENFORCE_TRIGGER_MGMT: bool = False
    # NEW module-level flag, same shape as require_global_role's own ENFORCE — see research.md §5.
```

`has_artifact_role`, `can_deploy_to_production`, `can_approve_hitl`, `can_use_playground`, `can_create_agent`, `grant_creator_admin`, `get_user_artifact_roles`, `require_global_role` — **unchanged, byte-for-byte**.

### `routers/artifact_grants.py` (new)

```python
router = APIRouter(prefix="/api/v1/artifacts", tags=["artifact-grants"])

async def _resolve_artifact(db: AsyncSession, artifact_type: str, artifact_id: uuid.UUID) -> None
    # 422 if artifact_type not in {"agent","workflow"}; 404 if the row doesn't exist.

async def _grantee_exists(db: AsyncSession, grantee_type: str, grantee_id: str) -> bool
    # user -> user_team_assignments row; team -> teams.name row; application -> applications.id row.

@router.post("/{artifact_type}/{artifact_id}/grants", response_model=ArtifactRoleGrantResponse, status_code=201)
async def create_grant(artifact_type: str, artifact_id: uuid.UUID, body: ArtifactRoleGrantCreate,
                        claims: dict = Depends(require_user), db: AsyncSession = Depends(get_db)) -> ArtifactRoleGrantResponse

@router.get("/{artifact_type}/{artifact_id}/grants", response_model=list[ArtifactRoleGrantResponse])
async def list_grants(artifact_type: str, artifact_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[ArtifactRoleGrantResponse]

@router.delete("/{artifact_type}/{artifact_id}/grants/{grant_id}", status_code=204, response_model=None)
async def revoke_grant(artifact_type: str, artifact_id: uuid.UUID, grant_id: uuid.UUID,
                        claims: dict = Depends(require_user), db: AsyncSession = Depends(get_db)) -> None
```

### `routers/applications.py` (new)

```python
router = APIRouter(prefix="/api/v1/teams/{team}/applications", tags=["applications"])
_SECRET_PREFIX = "whsec_"   # reused verbatim from webhook_clients.py

@router.post("", response_model=ApplicationCreatedResponse, status_code=201)
async def create_application(team: str, body: ApplicationCreate,
                              claims: dict = Depends(require_user), db: AsyncSession = Depends(get_db)) -> ApplicationCreatedResponse

@router.get("", response_model=list[ApplicationResponse])
async def list_applications(team: str, claims: dict = Depends(require_user), db: AsyncSession = Depends(get_db)) -> list[Application]

@router.post("/{application_id}/rotate-secret", response_model=ApplicationRotateSecretResponse)
async def rotate_secret(team: str, application_id: uuid.UUID,
                         claims: dict = Depends(require_user), db: AsyncSession = Depends(get_db)) -> ApplicationRotateSecretResponse

@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application(team: str, application_id: uuid.UUID, body: ApplicationUpdate,
                              claims: dict = Depends(require_user), db: AsyncSession = Depends(get_db)) -> Application

@router.delete("/{application_id}", status_code=204, response_model=None)
async def delete_application(team: str, application_id: uuid.UUID,
                              claims: dict = Depends(require_user), db: AsyncSession = Depends(get_db)) -> None
```

### `event-gateway/webhook_auth.py` (rewired — see `contracts/gateway-verification.md` for the full function body)

```python
def lookup_application(team_name: str, name: str) -> dict | None                                    # NEW
def has_active_invoker_grant(artifact_type: str, artifact_id: str, application_id: str) -> bool      # NEW
def _verify_client_signed(trigger: dict, headers, raw_body: bytes) -> WebhookAuthResult               # REWIRED
# lookup_webhook_client(trigger_id, client_id) -> dict | None                                          # DELETED (no caller after rewiring)
```

### `studio/src/api/registryApi.ts` (new exports)

```typescript
export interface Application {
  id: string; team_name: string; name: string; enabled: boolean;
  created_by: string; created_at: string; rotated_at: string | null;
}
export interface ApplicationCreated { id: string; name: string; secret: string; created_at: string; }
export interface ApplicationRotateSecretResponse { id: string; secret: string; rotated_at: string; }

export const createApplication = async (team: string, body: { name: string }): Promise<ApplicationCreated> => ...
export const listApplications = async (team: string): Promise<Application[]> => ...
export const rotateApplicationSecret = async (team: string, id: string): Promise<ApplicationRotateSecretResponse> => ...
export const setApplicationEnabled = async (team: string, id: string, enabled: boolean): Promise<Application> => ...
export const deleteApplication = async (team: string, id: string): Promise<void> => ...

export interface ArtifactRoleGrant {
  id: string; artifact_type: 'agent' | 'workflow'; artifact_id: string;
  role: 'agent-admin' | 'approver' | 'invoker';
  grantee_type: 'user' | 'team' | 'application'; grantee_id: string;
  granted_by: string; granted_at: string; revoked_at: string | null;
  grantee_label: string | null;
}
export const createGrant = async (
  artifactType: 'agent' | 'workflow', artifactId: string,
  body: { grantee_type: string; grantee_id: string; role: string }
): Promise<ArtifactRoleGrant> => ...
export const listGrants = async (artifactType: 'agent' | 'workflow', artifactId: string): Promise<ArtifactRoleGrant[]> => ...
export const revokeGrant = async (artifactType: 'agent' | 'workflow', artifactId: string, grantId: string): Promise<void> => ...
```

---

## Tasks

### Task 1 — Migration 0069: `applications` table + widened `artifact_role_grants` constraints [P with Task 3, Task 4]

**Files to create/modify:**
- `services/registry-api/alembic/versions/0069_applications_and_invoker_grants.py` (new) — full DDL in `data-model.md`.
- `services/registry-api/models.py` (modify) — add `Application` ORM class immediately after `WebhookClient` (currently ends at line 1806), exact field list in `data-model.md`.

**Interface contract:** `Application(Base)` with `id, team_name, name, secret_encrypted, enabled, created_by, created_at, rotated_at`; `UniqueConstraint("team_name", "name", name="uq_applications_team_name")`.

**Acceptance criteria:**
- `alembic upgrade head` (in-pod) leaves `alembic current` at `0069`.
- `SELECT conname FROM pg_constraint WHERE conname IN ('ck_arg_role','ck_arg_grantee_type')` shows both constraints with the widened value lists (query the constraint definition via `pg_get_constraintdef`).
- Re-running `alembic upgrade head` twice is a no-op (idempotency guard: the `DO $$ ... $$` blocks check `pg_constraint` before dropping).
- `python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0069_applications_and_invoker_grants.py').read())"` and the same for `models.py`.
- `python3 -c "from main import app; import sqlalchemy.orm as o; o.configure_mappers()"` (run in-pod) succeeds — confirms the new `Application` mapper configures cleanly alongside every existing model.

**Dependencies:** none.

**Test cases:** none yet (exercised by T-ARG-003/T-SYY-001 later, tasks 18–19).

**Verification command:**
```bash
kubectl exec -n agentshield-platform "$API_POD" -- alembic upgrade head && kubectl exec -n agentshield-platform "$API_POD" -- alembic current
```

---

### Task 2 — Migration 0070: backfill `webhook_clients` → `applications` + `invoker` grants

**Files to create/modify:**
- `services/registry-api/alembic/versions/0070_backfill_webhook_clients_to_applications.py` (new) — full SQL in `data-model.md`.

**Interface contract:** two `op.execute()` statements (Pass 1: `applications` insert with `DISTINCT ON (team_name, client_name)`; Pass 2: `artifact_role_grants` insert joined back through the now-current `applications` table), both `ON CONFLICT ... DO NOTHING`, both idempotent on re-run.

**Acceptance criteria:**
- Row-count check from `quickstart.md` §2 passes: `backfilled_invoker_grants <= webhook_clients count`, equal unless a `(team, client_id)` pair was registered on more than one trigger under the same team.
- Re-running `alembic upgrade head` after it has already reached `0070` is a no-op (both passes are `ON CONFLICT DO NOTHING`).
- `downgrade()` removes only rows tagged `granted_by='system:backfill-0070'` / `created_by='system:backfill-0070'` — a grant a human created through the new API after this migration ran is untouched (verify by creating one manually, then downgrading, then confirming it survives).
- `python3 -c "import ast; ast.parse(...)"` on the file.

**Dependencies:** Task 1 (needs the widened constraints and the `applications` table to exist).

**Test cases:** T-SYY-009 (task 19) — "migrated `webhook_clients` row produces an equivalent `applications` + `invoker` grant pair; old signature still verifies post-cutover" — is the live proof of this migration's correctness, run against a `webhook_clients` row seeded BEFORE this migration executes (the suite seeds via direct DB insert pre-migration in a fixture-specific team, then asserts the backfilled row post-migration).

**Verification command:**
```bash
kubectl exec -n agentshield-platform "$API_POD" -- alembic upgrade head
# then the row-count query from quickstart.md §2
```

---

### Task 3 — `rbac.py`: widen `can_delegate_role`, add `can_create_application`, `can_manage_artifact`, `ENFORCE_TRIGGER_MGMT` [P with Task 1, Task 4]

**Files to modify:** `services/registry-api/rbac.py`.

**Interface contract:** exact signatures under "Key Interfaces" above. `can_delegate_role`'s only edit is the `target_role not in (...)` tuple. The two new functions and the new flag are appended after `can_delegate_role`, before the `grant_creator_admin` section comment.

**Acceptance criteria:**
- `can_delegate_role(db, sub, artifact_id, "invoker")` returns `True` for an `agent-admin` on `artifact_id`, `False` for a plain contributor with no scoped role, `True` for `platform-admin` regardless of scoped role.
- `can_create_application(db, sub, "payments")` returns `True` only when `get_user_team(db, sub) == "payments"` and global role ≥ contributor, or when global role is `platform-admin` (any team).
- `can_manage_artifact(db, sub, artifact_id)` returns identically to `can_deploy_to_production(db, sub, artifact_id)` for every input (same body, different name — verified by a direct equality check in the suite, not just independently re-derived).
- `ENFORCE_TRIGGER_MGMT` defaults `False`; a direct import + monkeypatch to `True` (in-process, not over HTTP — see task 8) causes the gated trigger endpoints to 403 a non-agent-admin caller.
- `python3 -c "import ast; ast.parse(open('services/registry-api/rbac.py').read())"`.

**Dependencies:** none (does not require the `applications` table to exist — see `research.md` §4).

**Test cases:** T-ARG-001 through T-ARG-006 (task 18) exercise `can_delegate_role`'s widened tuple end-to-end; T-SYY-001/002 (task 19) exercise `can_create_application`. `can_manage_artifact`'s soft-enforcement path is exercised directly (in-pod, not over HTTP) as part of task 8's acceptance criteria.

**Verification command:**
```bash
kubectl exec -n agentshield-platform "$API_POD" -- python3 -c "
import asyncio
from db import AsyncSessionLocal
import rbac
async def check():
    async with AsyncSessionLocal() as db:
        print(await rbac.can_delegate_role(db, 'nonexistent-sub', __import__('uuid').uuid4(), 'invoker'))
asyncio.run(check())
"
```

---

### Task 4 — `schemas.py`: new Pydantic models for grants + applications [P with Task 1, Task 3]

**Files to modify:** `services/registry-api/schemas.py`.

**Interface contract:**
```python
class ArtifactRoleGrantCreate(BaseModel):
    grantee_type: str = Field(..., pattern="^(user|team|application)$")
    grantee_id: str = Field(..., min_length=1)
    role: str = Field(..., pattern="^(agent-admin|approver|invoker)$")

class ArtifactRoleGrantResponse(BaseModel):
    id: uuid.UUID; artifact_type: str; artifact_id: uuid.UUID; role: str
    grantee_type: str; grantee_id: str; granted_by: str
    granted_at: datetime; revoked_at: datetime | None = None
    grantee_label: str | None = None

class ApplicationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)

class ApplicationCreatedResponse(BaseModel):
    id: uuid.UUID; name: str; secret: str; created_at: datetime

class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID; team_name: str; name: str; enabled: bool
    created_by: str; created_at: datetime; rotated_at: datetime | None = None

class ApplicationUpdate(BaseModel):
    enabled: bool

class ApplicationRotateSecretResponse(BaseModel):
    id: uuid.UUID; secret: str; rotated_at: datetime
```

Insert immediately after the existing `WebhookClientUpdate` class (currently the last webhook-related schema, right before the `ErrorResponse` section).

**Acceptance criteria:** every field matches its router's exact usage in tasks 5/6 (no drift between what a router returns and what the schema declares — verified by FastAPI's own startup-time response-model validation, which crash-loops the pod on a mismatch, same as this repo's existing `response_model=None` 204 convention already guards against). `python3 -c "import ast; ast.parse(...)"`.

**Dependencies:** none.

**Test cases:** exercised indirectly by every case in tasks 18/19 (every request/response in those suites round-trips through these schemas).

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/schemas.py').read())"
```

---

### Task 5 — `routers/artifact_grants.py`: generic delegation endpoint [P with Task 6]

**Files to create:** `services/registry-api/routers/artifact_grants.py`.

**Interface contract:** see "Key Interfaces" above and `contracts/artifact-grants.md` for the full request/response/error contract, including the exact `_resolve_artifact`/`_grantee_exists` helper bodies.

**Acceptance criteria (mapped to Suite A test IDs — task 18 is where these actually run):**
- T-ARG-001: `user`-grantee create → `201`, row exists.
- T-ARG-002: `team`-grantee create → `201`; a team member subsequently passes `has_artifact_role` (verified via a direct in-pod call to the UNCHANGED `rbac.has_artifact_role`, not a second HTTP round trip).
- T-ARG-003: `application`-grantee create → `201`.
- T-ARG-004: contributor with no scoped role → `403`.
- T-ARG-005: platform-admin with no prior scoped role on the artifact → `201`.
- T-ARG-006: `DELETE` → `204`; subsequent `GET` list excludes it; subsequent `has_artifact_role`/`has_active_invoker_grant` (as applicable) returns `False`.
- T-ARG-007: invalid `role` value → `422` (Pydantic pattern, never reaches the DB).
- T-ARG-008: unresolvable `grantee_id` for each of the three `grantee_type`s → `400`.
- T-ARG-009: duplicate `(artifact, role, grantee)` → `409` (via `IntegrityError` on `uq_arg_active_grant`, caught exactly like `webhook_clients.py::create_webhook_client` catches its own unique-constraint violation).

**Dependencies:** Task 3 (`can_delegate_role`), Task 4 (schemas).

**Verification command:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/artifact_grants.py').read())"`; full behavioral verification is Task 18.

---

### Task 6 — `routers/applications.py`: team-scoped CRUD + rotate-secret [P with Task 5]

**Files to create:** `services/registry-api/routers/applications.py`.

**Interface contract:** see "Key Interfaces" above and `contracts/applications.md`.

**Acceptance criteria (mapped to Suite B — task 19):**
- T-SYY-001: create → `201`, zero grants exist for the new application.
- Rotate-secret → `200`, new secret differs from the original, `rotated_at` updates.
- Kill switch (`PATCH {enabled:false}`) → `200`, `enabled=false` in the response.
- Delete → `204`; a subsequent `GET .../grants` on any artifact this application held `invoker` on no longer lists it (cascade — explicit `DELETE FROM artifact_role_grants WHERE grantee_type='application' AND grantee_id=:id` in the same transaction as the `applications` row delete).
- Duplicate `(team, name)` → `409`.
- Non-contributor / wrong-team caller on any write endpoint → `403` (T-SYY-003's underlying mechanism — the create-application half is exercised here, the grant-invoker half in Task 5/18).

**Dependencies:** Task 1 (`Application` model), Task 3 (`can_create_application`), Task 4 (schemas).

**Verification command:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/applications.py').read())"`; full behavioral verification is Task 19.

---

### Task 7 — `main.py`: register the two new routers

**Files to modify:** `services/registry-api/main.py`.

**Interface contract:** add `from routers.artifact_grants import router as artifact_grants_router` and `from routers.applications import router as applications_router` alongside the existing router imports (~line 70, next to `triggers_router`/`webhook_clients_router`); add `app.include_router(artifact_grants_router)` and `app.include_router(applications_router)` alongside the existing `app.include_router(triggers_router)` / `app.include_router(webhook_clients_router)` calls (~lines 184–187).

**Acceptance criteria:** `GET /openapi.json` (in-pod, `curl http://localhost:8000/openapi.json`) lists all 8 new paths (3 grants + 5 applications); pod starts cleanly (no import-time crash — the same class of failure the `response_model=None` comments elsewhere in this codebase warn about).

**Dependencies:** Task 5, Task 6.

**Verification command:**
```bash
kubectl exec -n agentshield-platform "$API_POD" -- curl -s http://localhost:8000/openapi.json | python3 -c "
import sys, json
paths = json.load(sys.stdin)['paths']
assert any('/artifacts/{artifact_type}/{artifact_id}/grants' in p for p in paths)
assert any('/teams/{team}/applications' in p for p in paths)
print('OK')
"
```

---

### Task 8 — `routers/triggers.py`: soft-enforce `can_manage_artifact` [P with Task 9, Task 10]

**Files to modify:** `services/registry-api/routers/triggers.py`.

**Interface contract:** `create_trigger`, `update_trigger`, `delete_trigger`, `rotate_token` each gain a `claims: dict = Depends(require_user)` parameter and, as the first line of the handler body (after resolving the agent), a call:
```python
if rbac.ENFORCE_TRIGGER_MGMT and not await rbac.can_manage_artifact(db, claims["sub"], agent.id):
    raise HTTPException(status_code=403, detail="agent-admin required to manage triggers on this agent")
elif not await rbac.can_manage_artifact(db, claims["sub"], agent.id):
    logger.warning("trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)",
                    claims["sub"], agent.id)
```
(mirrors `require_global_role`'s own log-then-permit shape exactly — see `rbac.py`'s existing `_check` closure).

**Acceptance criteria:**
- With `ENFORCE_TRIGGER_MGMT=False` (default): every one of the 16 pre-existing bash suites + `webhook-clients.spec.ts` + `webhook-public-url.spec.ts` that call these four endpoints continue to pass unmodified (task 26's regression sweep proves this) — a caller with no `agent-admin` grant still gets `201`/`200`/`204`, with a warning logged server-side.
- Direct in-pod monkeypatch of `rbac.ENFORCE_TRIGGER_MGMT = True` (importing the router module fresh in a one-off `python3 -c` process, NOT against the live uvicorn worker, which cannot be monkeypatched from outside its own process) followed by a direct call to the route function with a non-agent-admin's claims raises `HTTPException(403)`.
- `require_user` (not `get_optional_user`) is now required on these four endpoints — a request with no bearer token at all gets `401` regardless of `ENFORCE_TRIGGER_MGMT` (401 is always hard; only the 403 policy decision is soft — see `research.md` §5 for why this split is safe: none of the 16 existing suites/2 specs send NO identifying header at all, they send `X-User-Sub` only, which `require_user` ignores — **this is the one part of this task that DOES change existing behavior**, since `X-User-Sub`-only callers currently reach `get_optional_user`/no-Depends-at-all successfully and will now need SOME bearer token. Verify against the actual suites in task 26 before merging; if any of the 16 sends zero Authorization header at all on these specific 4 endpoints, task 26 must add a real bearer token to that one call site even under soft enforcement, since `require_user`'s 401 is not soft.**

**Dependencies:** Task 3.

**Verification command:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/triggers.py').read())"`; task 26 for the full regression sweep.

---

### Task 9 — `routers/composite_workflows.py`: same soft-enforcement for workflow triggers [P with Task 8, Task 10]

**Files to modify:** `services/registry-api/routers/composite_workflows.py` (the trigger CRUD block at lines ~747–862: `create_workflow_trigger`, `update_workflow_trigger`, plus `delete`/`rotate-token` if present in that same region — confirm exact endpoint set by reading the block before editing, per this repo's "read before modify" rule).

**Interface contract:** identical shape to Task 8, using the workflow's `id` (not `agent.id`) as `artifact_id`.

**Acceptance criteria:** identical to Task 8, scoped to the workflow-trigger endpoints. `suite-34-workflow-triggers.sh` (a pre-existing suite that calls these endpoints via `X-User-Sub` only) continues to pass unmodified under the default `ENFORCE_TRIGGER_MGMT=False`.

**Dependencies:** Task 3.

**Verification command:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/composite_workflows.py').read())"`.

---

### Task 10 — `routers/webhook_clients.py`: retire write endpoints (410 Gone) [P with Task 8, Task 9]

**Files to modify:** `services/registry-api/routers/webhook_clients.py`.

**Interface contract:** `create_webhook_client`, `update_webhook_client`, `delete_webhook_client` handler bodies replaced with:
```python
raise HTTPException(
    status_code=status.HTTP_410_GONE,
    detail="webhook_clients registration is retired. Use POST /api/v1/teams/{team}/applications "
           "to create a reusable application, then POST /api/v1/artifacts/{artifact_type}/{artifact_id}"
           "/grants with role='invoker' to authorize it — see docs/design/todo/webhook-application-identity.md.",
)
```
placed as the FIRST line of each handler (before any DB access — this is a pure deprecation stub, not a partial-behavior change). `list_webhook_clients` (GET) is **untouched**. Update the module's header docstring to note the retirement and point at the replacement (mirrors how this file's own header already documents its security invariants in detail — the retirement note belongs in the same place).

**Acceptance criteria:**
- `POST/PATCH/DELETE /api/v1/triggers/{id}/clients...` → `410`, response body contains the redirect message.
- `GET /api/v1/triggers/{id}/clients` → unchanged `200` behavior (still lists any pre-existing rows).
- This task ships in the **same deploy** as Task 11 (gateway cutover) — never separately, since a live 201 from this endpoint after Task 11 ships but before this task ships would be the exact silent-dead-end trap `research.md` §6 identifies.

**Dependencies:** none directly, but must land in the same release as Task 11 — sequence them adjacently in the actual commit/PR even though they are file-disjoint.

**Test cases:** new T-S76-010 (task 21) asserts the `410`.

**Verification command:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/webhook_clients.py').read())"`.

---

### Task 11 — `event-gateway/webhook_auth.py`: gateway cutover [P with Tasks 5–10]

**Files to modify:** `services/event-gateway/webhook_auth.py`.

**Interface contract:** full function bodies in `contracts/gateway-verification.md`. Summary: `_TRIGGER_SQL` gains two columns (`a.id::text`/`w.id::text` as `artifact_id`, `a.team`/`w.team` as `team_name`); `lookup_triggers` injects `"artifact_type": kind` into each row dict; new `lookup_application(team_name, name)` and `has_active_invoker_grant(artifact_type, artifact_id, application_id)` port functions (raw `psycopg2`, no import from `registry-api`); `_verify_client_signed` rewired to the 5-step order in `contracts/gateway-verification.md` (app lookup → grant check → enabled check → freshness → HMAC); `lookup_webhook_client` **deleted**.

**Acceptance criteria:**
- Every invariant in `contracts/gateway-verification.md`'s "What does NOT change" section holds — verified by suite-83's T-SYY-005/006 byte-identity assertion (extends suite-76 T-S76-003's existing pattern to the two new failure reasons).
- `grep -c "def verify_webhook_auth" services/event-gateway/webhook_auth.py` still returns `1`, and `grep -c "lookup_webhook_client"` returns `0` (T-S76-000-style parity guard — no second copy, no dead reference).
- `python3 -c "import ast; ast.parse(open('services/event-gateway/webhook_auth.py').read())"`.

**Dependencies:** Task 1, Task 2 (needs `applications`/widened `artifact_role_grants` to exist for the new SQL to be meaningful — code-wise this file has no import dependency on registry-api, but must not deploy before the schema does).

**Test cases:** T-SYY-004, T-SYY-005, T-SYY-006, T-SYY-007, T-SYY-008, T-SYY-009 (task 19); T-S76-000…009 retargeted (task 21).

**Verification command:**
```bash
grep -c "def verify_webhook_auth" services/event-gateway/webhook_auth.py   # expect 1
grep -c "lookup_webhook_client" services/event-gateway/webhook_auth.py     # expect 0
```

---

### Task 12 — `studio/src/api/registryApi.ts`: new client functions + types [P with backend tasks]

**Files to modify:** `studio/src/api/registryApi.ts`.

**Interface contract:** exact signatures under "Key Interfaces" above. Insert the `Application*` block immediately after the existing `WebhookClient`/webhook-client functions section (currently ends ~line 1392, right before `RotateTokenResponse`); insert the `ArtifactRoleGrant*` block immediately after that.

**Acceptance criteria:** `cd studio && npm run typecheck` passes; every function matches its router's actual request/response shape from tasks 5/6 exactly (path template, body shape, response shape) — cross-checked against `contracts/artifact-grants.md`/`contracts/applications.md` line-for-line.

**Dependencies:** Task 4 (schema shapes must be locked before the TS types are written) — file-disjoint from all backend tasks, so codeable in parallel once the contract is fixed (the contract files already exist as of this plan; this task does not need to wait for tasks 5/6 to be *merged*, only for the shapes to be *decided*, which they already are).

**Verification command:** `cd studio && npm run typecheck`

---

### Task 13 — Shared `InvokeAccessPanel`/`ArtifactGrantsList` + wire into `SettingsTab.tsx`

**Files to create/modify:**
- `studio/src/components/shared/InvokeAccessPanel.tsx` (new)
- `studio/src/components/shared/ArtifactGrantsList.tsx` (new)
- `studio/src/components/agent-detail/SettingsTab.tsx` (modify)

**Interface contract:**
- Both new components are artifact-type-agnostic from the start — built once, here, for both the agent and workflow surfaces (Task 14 only *consumes* them; it creates neither), closing the exact "two parallel paths" pattern this codebase's own `webhook_clients.py` header warns against (`docs/bugs/side-effecting-lost-on-declarative-runner-path.md`).
```typescript
// studio/src/components/shared/InvokeAccessPanel.tsx
interface InvokeAccessPanelProps {
  artifactType: 'agent' | 'workflow';
  artifactId: string;
  artifactTeam: string;
}
export default function InvokeAccessPanel(props: InvokeAccessPanelProps): JSX.Element
```
  - Fetches `listApplications(artifactTeam)` (task 12) for picker options and `listGrants(artifactType, artifactId)` (task 12) filtered to `role === 'invoker'` for the current list.
  - "Grant access" button opens a picker (application name dropdown, sourced from `listApplications`); confirming shows the explicit unattended-execution acknowledgment text from design doc §9.4 step 3, then calls `createGrant(artifactType, artifactId, { grantee_type: 'application', grantee_id, role: 'invoker' })`.
  - Empty state (team has zero applications) shows the exact copy from design doc §9.8: "No applications registered for your team yet." with a link to `/applications`.
  - Each granted row shows an "application disabled" badge when the corresponding `Application.enabled === false` (cross-referenced from the same `listApplications` fetch, not a second lookup) — per design doc §9.8, not silently hidden.
  - Revoke button calls `revokeGrant(artifactType, artifactId, grant.id)`.
```typescript
// studio/src/components/shared/ArtifactGrantsList.tsx
interface ArtifactGrantsListProps {
  artifactType: 'agent' | 'workflow';
  artifactId: string;
}
export default function ArtifactGrantsList(props: ArtifactGrantsListProps): JSX.Element
```
  - Lists ALL active grants (`agent-admin`, `approver`, `invoker` alike) via `listGrants(artifactType, artifactId)`, each row showing `role`, `grantee_type`, `grantee_label ?? grantee_id`, a revoke button (`revokeGrant`). This is also where a human `agent-admin`/`approver` grant is managed now that the delegation endpoint exists (design doc §9.2) — not `invoker`-only.
- `SettingsTab.tsx` changes: gains two new required props `agentId: string` and `agentTeam: string` (alongside existing `agentName`/`memoryEnabled`) — needed because the two shared components above operate on the artifact's UUID and owning team, neither of which the current `agentName`-only prop set carries. **Delete** the `ClientPanel` function (lines 339–491) and its call site inside `WebhookRow` (line 331); render `<InvokeAccessPanel artifactType="agent" artifactId={agentId} artifactTeam={agentTeam} />` in `ClientPanel`'s old slot inside `WebhookRow`, and `<ArtifactGrantsList artifactType="agent" artifactId={agentId} />` once near the top of `SettingsTab`'s return (above the Webhook Triggers card, matching design doc §9.2's "same Settings surface, above or alongside the trigger card").

**Acceptance criteria:**
- Empty-team-applications state renders the exact copy above.
- Granting flips the trigger card's existing `auth_mode` badge (already present — `authMode === "client_signed"` styling at line 297) from `token` to `client_signed` on the very next `listTriggers` refetch (no new badge code needed — the existing badge already reads `trigger.auth_mode`, which the backend already flips on first `invoker` grant per `contracts/gateway-verification.md`/design doc §9.4 step 4).
- `cd studio && npm run typecheck` passes.

**Dependencies:** Task 12.

**Verification command:** `cd studio && npm run typecheck && npm run test -- SettingsTab`

---

### Task 14 — Wire the shared components into `WorkflowTriggersPanel.tsx`

**Files to modify:** `studio/src/components/workflow/WorkflowTriggersPanel.tsx`.

**Interface contract:**
- `WorkflowTriggersPanel` gains a new required prop `workflowTeam: string` (alongside existing `workflowId`/`workflowName`/`onClose`).
- Import `InvokeAccessPanel`/`ArtifactGrantsList` from `studio/src/components/shared/` (Task 13 — this task only consumes them, it creates nothing new under `shared/`).
- Render `<ArtifactGrantsList artifactType="workflow" artifactId={workflowId} />` near the top of the modal body, and `<InvokeAccessPanel artifactType="workflow" artifactId={workflowId} artifactTeam={workflowTeam} />` inside each `WebhookRow`'s slot (mirroring `SettingsTab`'s placement from Task 13).

**Acceptance criteria:** identical to Task 13's, scoped to the workflow surface — this is the parity gap the design doc explicitly calls out (§9.2: "New (parity gap closed — today this panel has no client/application UI at all)"). `cd studio && npm run typecheck` passes.

**Dependencies:** Task 12, **Task 13** (this task imports the shared components Task 13 creates — not parallel-safe with Task 13, despite both being frontend-only).

**Verification command:** `cd studio && npm run typecheck && npm run test -- WorkflowTriggersPanel`

---

### Task 15 — `ApplicationsPage.tsx` (new) + Sidebar + route

**Files to create/modify:**
- `studio/src/pages/ApplicationsPage.tsx` (new) — modeled directly on `studio/src/pages/CredentialsPage.tsx` (research.md §11): team-scoped list (defaulting to the logged-in user's own `useAuth().team`), create form (name only), reveal-once secret box on create/rotate (same copy-and-warn pattern as `CredentialsPage`'s own secret handling and `ClientPanel`'s former secret box), enable/disable toggle, delete with confirmation.
- `studio/src/components/Sidebar.tsx` (modify) — add `{ label: "Applications", to: "/applications", icon: KeyRound }` (or a distinct icon — `Boxes`/`AppWindow` from `lucide-react` to avoid visual duplication with the existing "Credentials" `KeyRound` entry) to the same Settings-section array that already holds `Models`/`Credentials` (~line 86–87).
- `studio/src/App.tsx` (modify) — add `<Route path="/applications" element={<ApplicationsPage />} />` alongside the existing `/credentials` route (~line 82), with **no** `RequireRole` wrapper (matches `/credentials`'s own unwrapped route — the real gate is server-side `can_create_application`, not a client-side role hide, per this repo's established pattern of gating writes server-side while keeping the page itself reachable to any authenticated contributor).

**Acceptance criteria:**
- Creating an application shows the secret exactly once; navigating away and back never re-shows it (matches design doc §9.3 step 3).
- The page lists only the current user's own team's applications by default (per `research.md` §11's CredentialsPage-analog pattern — `useAuth().team` drives the initial `listApplications` call).
- `cd studio && npm run typecheck` passes.

**Dependencies:** Task 12.

**Verification command:** `cd studio && npm run typecheck`

---

### Task 16 — Wire the new props into `AgentDetailPage.tsx` / `WorkflowBuilderPage.tsx`

**Files to modify:**
- `studio/src/pages/AgentDetailPage.tsx` — the `<SettingsTab agentName={agent.name} memoryEnabled={agent.memory_enabled} />` call site (line 203) gains `agentId={agent.id} agentTeam={agent.team}` (both fields already present on the `Agent` object in scope at that line — no new fetch needed).
- `studio/src/pages/WorkflowBuilderPage.tsx` — the `<WorkflowTriggersPanel workflowId={compositeWorkflowId} workflowName={compositeWorkflowName ?? 'workflow'} onClose={...} />` call site (line 912) gains `workflowTeam={currentTeam || authTeam || ''}` (both variables already in scope at that line, used identically for `AddAgentModal`'s own `team` prop a few lines above).

**Acceptance criteria:** `cd studio && npm run typecheck` passes with the new required props satisfied at both call sites (a missing prop is a compile error, not a runtime surprise — this is the actual proof the wiring isn't orphaned).

**Dependencies:** Task 13, Task 14.

**Verification command:** `cd studio && npm run typecheck`

---

### Task 17 — Vitest coverage for the new/changed components

**Files to modify/create:**
- `studio/src/components/agent-detail/SettingsTab.test.tsx` (modify if it exists, else new) — cover: `InvokeAccessPanel` empty state, grant-and-list, revoke, "application disabled" badge; `ArtifactGrantsList` render with mixed roles. Mock `registryApi` per this repo's `vi.mock('../api/registryApi')` convention, render via `renderWithProviders`.
- `studio/src/pages/ApplicationsPage.test.tsx` (new) — cover: create shows secret once, list renders without a secret field, enable/disable toggle, delete confirmation.
- `studio/src/components/workflow/WorkflowTriggersPanel.test.tsx` (modify) — cover the same `InvokeAccessPanel`/`ArtifactGrantsList` states as `SettingsTab.test.tsx`, proving the SHARED component (Task 14) renders correctly under the `workflow` artifact type too — this is what makes Task 14's parity claim testable, not just visually plausible.

**Acceptance criteria:** `cd studio && npm run test` green, including all new/modified specs above.

**Dependencies:** Tasks 13, 14, 15, 16.

**Verification command:** `cd studio && npm run test`

---

### Task 18 — Suite A: `scripts/e2e/suite-82-artifact-grants.sh` (T-ARG-001…010)

**Files to create:** `scripts/e2e/suite-82-artifact-grants.sh`.

**Interface contract:** bash + in-pod Python driver, `kubectl exec` into the registry-api pod, `httpx` against `http://localhost:8000/api/v1`, following `suite-78-conversations.sh`'s exact pattern for obtaining real Keycloak tokens (`grant_type=password` against `agentshield-studio`). Since only `platform-admin`/`agent-reviewer` are pre-seeded, this suite creates its OWN scoped test users via `POST /api/v1/admin/users` (as `platform-admin`) for: an agent-admin persona (auto-granted via `grant_creator_admin` by creating the fixture agent as that user), a plain-contributor-no-scoped-role persona, and a second team-member persona for T-ARG-002. Each created user's Keycloak `requiredActions` is cleared and its password set non-temporary immediately after creation — the exact recipe in `quickstart.md` §3 (load-bearing; without it, `grant_type=password` 400s and the suite would falsely SKIP rather than FAIL its persona-dependent cases).

**Test cases (exact IDs from the design doc — do not renumber):**

| ID | Assertion |
|---|---|
| T-ARG-001 | agent-admin grants `agent-admin` to another user → `201`, row exists in `artifact_role_grants` |
| T-ARG-002 | agent-admin grants `approver` to a team → `201`; a member of that team's `has_artifact_role` check (direct in-pod call to `rbac.has_artifact_role`) returns `True` |
| T-ARG-003 | agent-admin grants `invoker` to an application they own → `201` |
| T-ARG-004 | contributor with no scoped role attempts any grant → `403` |
| T-ARG-005 | platform-admin grants a role on an artifact with no prior `agent-admin` grant → `201` |
| T-ARG-006 | `DELETE .../grants/{id}` → `204`; subsequent `has_artifact_role` → `False`; subsequent `GET .../grants` excludes it |
| T-ARG-007 | grant a role outside `{agent-admin, approver, invoker}` → `422` |
| T-ARG-008 | grant to an unresolvable `grantee_id` (unknown user sub / unknown team / unknown application id) → `400` |
| T-ARG-009 | grant the same `(artifact, role, grantee)` twice → `409` on the second attempt |
| T-ARG-010 | cleanup: delete every fixture agent/user/application/grant this suite created |

**Dependencies:** Tasks 5, 7 (endpoint must be registered), Task 3 (policy functions), Task 1 (schema).

**Verification command:** `bash scripts/e2e/suite-82-artifact-grants.sh`

---

### Task 19 — Suite B: `scripts/e2e/suite-83-webhook-applications.sh` (T-SYY-001…010)

**Files to create:** `scripts/e2e/suite-83-webhook-applications.sh`.

**Interface contract:** same in-pod driver pattern as Task 18; additionally uses the `sign_webhook` AST-extraction technique `suite-76` already established (extract the real function from `services/event-gateway/webhook_auth.py` at runtime rather than hand-copying it — "the signer is not a copy," same rationale `suite-76`'s own header documents) so the suite can never silently drift from the product's actual signing behavior.

**Test cases (exact IDs from the design doc):**

| ID | Assertion |
|---|---|
| T-SYY-001 | Create application under a team → row exists, zero grants |
| T-SYY-002 | agent-admin grants `invoker` to an application on their agent → `201`; subsequent `GET .../triggers` shows `auth_mode: "client_signed"` |
| T-SYY-003 | Contributor (not agent-admin on that artifact) attempts to grant `invoker` → `403` |
| T-SYY-004 | Signed webhook from a granted application → `202`, a real `agent_events` row committed with `status='matched'`, `client_id` = the application's name |
| T-SYY-005 | Signed webhook from a *revoked-grant* application → uniform `401` |
| T-SYY-006 | Signed webhook from a *disabled* application → uniform `401`, response body byte-identical to T-SYY-005's |
| T-SYY-007 | Rotate secret → old secret's signature now fails (`401`), new secret's succeeds (`202`) |
| T-SYY-008 | Same application granted `invoker` on two different agents → revoking one grant leaves the other working |
| T-SYY-009 | A `webhook_clients` row seeded BEFORE migration 0070 runs produces an equivalent `applications` + `invoker` grant pair after it runs; the OLD secret still verifies through the NEW gateway path post-cutover |
| T-SYY-010 | Cleanup: delete every fixture agent/application/grant this suite created |

**Dependencies:** Task 18 (Suite A validates the delegation endpoint this suite's grant steps reuse — run order matters, not just file existence), Task 6, Task 7, Task 11 (gateway cutover).

**Verification command:** `bash scripts/e2e/suite-83-webhook-applications.sh`

---

### Task 20 — Register both suites in `run-all.sh`

**Files to modify:** `scripts/e2e/run-all.sh`.

**Interface contract:** append immediately after the existing `run_suite "Suite 81: Deploy-time tool-access auto-grant" "suite-81-deploy-tool-autograt.sh"` line:
```bash
run_suite "Suite 82: Artifact Delegation Foundation (grants API)"  "suite-82-artifact-grants.sh"
run_suite "Suite 83: Webhook Applications (invoker grants)"       "suite-83-webhook-applications.sh"
```

**Acceptance criteria:** `bash scripts/e2e/run-all.sh` runs Suite 82 before Suite 83, both reported in the final pass/fail tally.

**Dependencies:** Tasks 18, 19.

**Verification command:** `grep -A1 "Suite 81" scripts/e2e/run-all.sh` shows the two new lines immediately after.

---

### Task 21 — Update `suite-76-webhook-client-signing.sh` for the cutover

**Files to modify:** `scripts/e2e/suite-76-webhook-client-signing.sh`.

**Interface contract:** every existing setup step that currently calls `POST /api/v1/triggers/{id}/clients` (the WS-4 registration endpoint) is replaced with: `POST /api/v1/teams/{team}/applications` (create) followed by `POST /api/v1/artifacts/{agent|workflow}/{id}/grants` with `{grantee_type: "application", grantee_id, role: "invoker"}` (grant) — using a REAL bearer token (the `platform-admin` password-grant pattern this file can borrow from `suite-78`, since the new grants/applications endpoints are hard-enforced, not soft). Every existing assertion (T-S76-000 through T-S76-009 — the HMAC/freshness/uniform-401/enable-disable/upgrade-on-first-registration behaviors) is preserved in spirit, retargeted to fire on the new setup mechanism (e.g. T-S76-009's "upgrade to `client_signed`" now fires on the first `invoker` GRANT, not the first client registration). **New** T-S76-010: `POST /api/v1/triggers/{id}/clients` (the now-retired endpoint) returns `410`, response body contains the redirect message from Task 10.

**Acceptance criteria:** `bash scripts/e2e/suite-76-webhook-client-signing.sh` is green with the SAME 10 claims it made before (0 through 9) still true, plus the new 10th. This is the concrete proof that the gateway cutover (Task 11) didn't quietly break WS-4's own acceptance gate — see `research.md` §6 for why this rewrite, not a passive skip, is required here.

**Dependencies:** Task 10 (410 retirement), Task 11 (gateway cutover), Task 19 (establishes the exact application-create + invoker-grant setup pattern this task reuses).

**Verification command:** `bash scripts/e2e/suite-76-webhook-client-signing.sh`

---

### Task 22 — Playwright: `artifact-grants.spec.ts`

**Files to create:** `studio/e2e/artifact-grants.spec.ts`.

**Interface contract:** real Keycloak login via `global-setup.ts` (not a raw `pwRequest` bypass — this spec exercises the hard-enforced new endpoint, so it needs a real session). Flow: open an agent's Settings, use `ArtifactGrantsList`'s grant UI (Task 13) to grant `agent-admin` or `approver` to a user or team, assert the grant appears in the list via `page.waitForResponse` on `POST .../grants`, **reload the page**, assert the grant survived (save→reload→assert — DoD rule #2). This is the first Playwright coverage of delegation at all, human-grantee or not (design doc §11.3).

**Acceptance criteria:** the spec fails against pre-Task-13 code (no `ArtifactGrantsList` UI exists to click) and passes once Task 13 ships — confirms it's testing the real wiring, not a stub.

**Dependencies:** Task 13, Task 15 (test user setup may reuse `/applications` for fixtures), Task 7 (endpoint live).

**Verification command:** `cd studio && npx playwright test artifact-grants.spec.ts`

---

### Task 23 — Playwright: `webhook-applications.spec.ts`

**Files to create:** `studio/e2e/webhook-applications.spec.ts`.

**Interface contract:** real Keycloak session, two `test()` cases in the same file (design doc §11.3 requires covering both the agent surface AND the workflow parity gap; one spec file, two flows, since both reuse the identical `webhook-applications` fixture-creation setup):
1. **Agent flow:** `/applications` page → create an application → assert secret shown once → navigate to an agent's Settings → grant it `invoker` via `InvokeAccessPanel` (Task 13) → assert it appears in the trigger card's Invoke Access list → **reload** → assert the grant survived and the secret is NOT re-displayed anywhere (structural guarantee from the response-model shape, not just UI behavior).
2. **Workflow flow (closes the design doc §11.3 parity requirement — "add a workflow-trigger-specific spec to cover the new parity: granting invoker from `WorkflowTriggersPanel.tsx`"):** same application (or a second one) → open a workflow's builder → open **Workflow Triggers** → grant it `invoker` via the same shared `InvokeAccessPanel` now mounted in `WorkflowTriggersPanel.tsx` (Task 14) → assert it appears → **reload** the builder page → assert the grant survived.

**Acceptance criteria:** save→reload→assert on the application creation AND on each of the two grants (agent-scoped and workflow-scoped), per DoD rule #2 applying independently to each write surface.

**Dependencies:** Task 13, Task 14, Task 15.

**Verification command:** `cd studio && npx playwright test webhook-applications.spec.ts`

---

### Task 24 — Retire `webhook-clients.spec.ts`

**Files to delete:** `studio/e2e/webhook-clients.spec.ts`.

**Rationale:** the UI it drives (`ClientPanel`, deleted in Task 13) no longer exists — its assertions would fail on a missing element, not on a real regression, the moment Task 13 ships. Per design doc §11.3: "retire it alongside `webhook_clients.py`... don't leave it asserting against a dead code path." `webhook-applications.spec.ts` (Task 23) is its full replacement (create-app + grant + reload, the same shape of proof `webhook-clients.spec.ts` provided for the old mechanism).

**Acceptance criteria:** `git rm studio/e2e/webhook-clients.spec.ts`; `webhook-public-url.spec.ts` is confirmed **unaffected** and left in place (it does not touch `webhook_clients.py` or `ClientPanel` at all — it only tests trigger-URL correctness and the token-mode gateway path, verified in `research.md` §5).

**Dependencies:** Task 13, Task 23 (the replacement must exist before the original is removed, so coverage is never zero even transiently in the same commit).

**Verification command:** `git status studio/e2e/webhook-clients.spec.ts` shows deleted; `grep -c "ClientPanel" studio/src/components/agent-detail/SettingsTab.tsx` returns `0`.

---

### Task 25 — Image tag bumps

**Files to modify:** `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`.

**Interface contract:**
- `scripts/deploy-cpe2e.sh`: `REGISTRY_API_TAG="0.2.210"` → `"0.2.211"`; `STUDIO_TAG="0.1.158"` → `"0.1.159"`; `EVENT_GATEWAY_TAG="0.1.3"` → `"0.1.4"`. Add a new header comment entry (matching the existing changelog-style comment block at the top of the file) summarizing this feature in one paragraph, per this file's own established convention.
- `charts/agentshield/values.yaml`: mirror all three tags at their respective `tag:` lines (registry-api ~L648, studio ~L992, event-gateway ~L768 — the **top-level** pin only; the sub-chart pin is deliberately left alone per the pre-existing, documented disagreement in `docs/testing/manual-ui-e2e-test-plan.md`'s gap ledger — "the sub-chart is shadowed by a stale packaged `.tgz`, so a sub-chart edit silently no-ops and the top-level pin wins").

**Acceptance criteria:** `grep 'tag: "0.2.211"' charts/agentshield/values.yaml`, `grep 'tag: "0.1.159"' charts/agentshield/values.yaml`, `grep 'tag: "0.1.4"' charts/agentshield/values.yaml` all match; `grep REGISTRY_API_TAG scripts/deploy-cpe2e.sh` shows `0.2.211`.

**Dependencies:** every code-change task (1–3, 5–14 for registry-api/studio; 11 for event-gateway) — this is the last code task, run once all changes for each service are final.

**Verification command:** `bash scripts/deploy-cpe2e.sh` (full rebuild+redeploy at the new tags).

---

### Task 26 — Regression sweep

**Not a file-change task** — a verification gate, run after Tasks 1–25.

**Blast radius mapped** (per this repo's mandatory regression-testing rule):
- **Trigger CRUD auth (Tasks 8, 9):** all 16 bash suites (`suite-19`, `21`, `22`, `26`, `27`, `28`, `31`, `32`, `33`, `34`, `66`, `70`, `71`, `75-eval-v2-scheduled`, `76-webhook-client-signing` [now Task 21's rewritten version], `77-eval-v2-webhook`) that call `POST/PATCH/DELETE .../triggers...`, plus `webhook-public-url.spec.ts`.
- **`webhook_clients` retirement (Task 10) + gateway cutover (Task 11):** `suite-76` (Task 21), `suite-77-eval-v2-webhook.sh` (uses webhook triggers — confirm it does not ALSO register via `webhook_clients`; if it does, apply the same Task-21-style retarget).
- **New shared components (Task 14):** `WorkflowTriggersPanel.test.tsx`, any existing `workflow-builder.spec.ts` case that opens the triggers panel.
- **Studio typecheck/build:** every frontend task (12–17).

**Run:**
```bash
bash scripts/e2e/run-all.sh                       # full backend sweep, incl. the 16 potentially-impacted suites + Suites 82/83
bash scripts/studio-e2e.sh                         # full Playwright sweep, incl. workflow-builder.spec.ts, scheduled-overview.spec.ts, webhook-public-url.spec.ts
cd studio && npm run test && npm run typecheck     # Vitest + TS
```

**Acceptance criteria:** zero new failures in any suite/spec not already covered by Tasks 18/19/21/22/23. Any suite that fails and is NOT one of this plan's own new/modified suites is a genuine regression — root-cause it per this repo's bug-fixing discipline (trace root cause, check whether Tasks 8/9's `require_user` addition is the cause per the flagged risk in Task 8's acceptance criteria, fix, re-run) before proceeding to Task 27.

**Dependencies:** Tasks 1–25.

---

### Task 27 — Gap ledger + design doc status update

**Files to modify:**
- `docs/testing/manual-ui-e2e-test-plan.md` — insert a new `## Known gaps (webhook application identity — Decision 30) — <ship date>` section immediately after the file's header (before the existing "Workflow deployment Conversations tab" entry, matching the file's newest-first ordering), listing:
  - **not-yet-wired (debt):** Bounded unattended-approval fallback policy (design doc §12) — `invoker` grants are functionally live without it; an application-triggered run hitting a HITL checkpoint sits `awaiting_approval` with no faster notification than the existing failure-alert path. **Must land before this is relied on for anything production-critical** (verbatim severity from the design doc — this is the one item that is not "nice to have later").
  - **not-yet-wired (debt):** `rbac.ENFORCE_TRIGGER_MGMT = False` — trigger/webhook-client-adjacent CRUD calls the real `can_manage_artifact` policy check and logs a warning on every would-deny, but does not yet 403. Flipping it to `True` requires migrating the 16 e2e suites + `webhook-public-url.spec.ts` enumerated in Task 26 to real bearer tokens first (`research.md` §5).
  - **deferred (intentional):** Cross-team application reuse; split manage-user vs. manage-application RBAC capability; tool-level `invoker` granularity finer than whole-artifact; role audit log beyond `granted_by`/`granted_at`/`revoked_at` — all four carried verbatim from design doc §12, unchanged by this implementation pass.
  - **deferred (intentional):** `webhook_clients` table + router drop — kept for one release per §10 step 5; POST/PATCH/DELETE already 410 (Task 10), GET still live; the table drop itself is a follow-up migration, not part of this change.
  - **pre-existing debt, inherited, not introduced here:** `rbac.py`'s `require_global_role` `ENFORCE = False` (admin-route gating, orthogonal to this design); `can_deploy_to_production`/`can_approve_hitl` remain uncalled by the production-deploy and HITL-decide endpoints (§11.1 of the design doc proves the underlying `has_artifact_role`/`can_delegate_role` machinery works via THIS design's new grants endpoint, but that's a different code path from these two functions specifically).
- `docs/design/todo/webhook-application-identity.md` — update the `**Status**: Draft (design only — nothing in this doc is implemented yet)` header line to `**Status**: Implemented (registry-api 0.2.211 / event-gateway 0.1.4 / studio 0.1.159 — see docs/testing/manual-ui-e2e-test-plan.md for verification)` (the exact tags Task 25 bumps to; if a later change re-bumps any of the three before this task lands, update the line to whatever the actual shipped tags are at merge time — never leave a stale version number in a status header), mirroring `rbac-design.md`'s own precedent (`**Status**: Partially Implemented (foundation shipped, enforcement pending)`) of keeping a design doc's status line truthful once code lands, per this repo's "reason from the running product" rule.

**Acceptance criteria:** every deferred/debt item from design doc §12 appears in the new gap-ledger section, tagged correctly; the design doc's status header no longer reads "nothing in this doc is implemented yet."

**Dependencies:** Tasks 1–26 (this is the final task — it describes what actually shipped, so it must run last).

**Verification command:** `grep -A3 "Known gaps (webhook application identity" docs/testing/manual-ui-e2e-test-plan.md`

# RBAC R0 + R1 — Platform-Owned User Identity & Router Authentication

**Goal:** Make "a user with no `user_team_assignments` row" unrepresentable — platform code creates the sole admin, the column stops inventing one, and the resolver refuses one — and close the unauthenticated routers to anonymous callers.

**Architecture.** Bootstrap lives in registry-api's `lifespan` because it is the only component holding both Keycloak admin credentials and the DB; it is single-flighted across the 2 replicas with `pg_try_advisory_lock` on a dedicated connection (the `mcp_health.py:190-199` pattern), identifies the admin by **username** so realm recreation self-heals, and is **non-fatal** — failure holds `/ready` red and retries rather than crash-looping a pod whose Keycloak dependency is routinely late. `rbac.get_user_global_role` becomes the single resolution path and raises `NoPlatformRole` on a missing row (mapped to 403 by one app-level exception handler); an *unrecognized* value keeps rank 0 because `user_team_assignments.role` is a union of `{global role} ∪ {reviewer scope}` (Decision 42). The trade-off accepted: R1 protects only routes with no in-cluster machine caller — four route groups stay open, each named in the gap ledger with its caller's file:line, because closing them requires service identity that identity propagation owns.

**Explicitly out of scope:** R2 (flipping `rbac.py:205 ENFORCE`, wiring `require_global_role`), R3 (deploy guards), R4 (trigger management), R5 (role-vocabulary unification, `approval_authority` → `approver`), identity propagation (migrations 0080–0082, suite-99+), and OPA. R0 is R2's prerequisite, not its start.

**Tech stack:** Python 3.11 / FastAPI / SQLAlchemy 2 async / Alembic / httpx; Postgres 15 behind PgBouncer; Keycloak 26 Admin REST; Helm 3; bash + `kubectl exec` e2e; Playwright for the one UI journey.

---

## Constitution Check (CLAUDE.md)

| DoD rule | Verdict | Justification |
|---|---|---|
| 1. Real user journey, not just an endpoint | **PASS** | T20 adds `studio/e2e/admin-access-roles.spec.ts` case "bootstrap yields a working Admin menu" — logs in as `platform-admin` through real Keycloak and asserts the sidebar Admin section (`Sidebar.tsx:391-398`, gated on `isAtLeast("platform-admin")` ⇐ `/me.role` ⇐ the bootstrap row) renders. That is Story 1's literal acceptance and the 2026-07-20 symptom. |
| 2. Save → reload → assert | **PASS** | Restart-idempotence is the persistence round-trip here: T-S97-002 reads the row, restarts registry-api, re-reads and asserts `user_sub` + `assigned_at` are unchanged. The existing `admin-access-roles.spec.ts` "assigning consumer persists across a reload" already covers the write surface FR-8 touches. |
| 3. No orphan code | **PASS** | `ensure_platform_admin` ← `main.py` lifespan (T5); `bootstrap_state` ← `/ready` (T5); `NoPlatformRole` ← `rbac.get_user_global_role` raises it and `main.create_app` handles it (T10); `audit_identity` ← `GET /api/v1/admin/identity-audit` (T8) ← suite-97 T-S97-012 + quickstart. `list_users(username=…)` ← `bootstrap_admin` (T2/T4). Every new symbol grepped in T22. |
| 4. Vertical slices | **PASS** | Slice order is Story-shaped: FR-7 (stop the litter) → bootstrap end-to-end (Keycloak user → realm role → row → `/me` → Admin menu) → then FR-4/FR-5 → then FR-11. No "all models then all endpoints". |
| 5. Honest gap ledger | **PASS** | T22 writes G-R0-1..5 (from the spec) plus **new** G-R0-6, G-R1-1..6 into `docs/testing/manual-ui-e2e-test-plan.md` under "Known gaps". |
| 6. Reason from the running product | **PASS** | Every file/line in this plan was read from the repo, not the design doc — including three facts the spec has wrong or missing (suite-53 insert is line **50** not 44; `suite-48:49` is an unlisted fourth role-omitting insert; four routers have machine callers). |
| 7. Bug fixes reproduce first | **PASS** | T21 requires T-S97-004 (realm-recreation re-pin) to be written and demonstrated **RED against current code** before T4/T5 land — the 2026-07-20 regression. T11 repairs `suite-96` T-S96-002, which would otherwise stay green-then-error through a real contract change. |
| 8. Document every bug + debugging session | **PASS** | T22 writes `docs/bugs/platform-admin-role-stranded-on-realm-recreation.md` (Found 2026-07-20 / Fixed `<REGISTRY_API_TAG>`; Symptom = Admin menu vanishes; Root cause = the assignment is pinned to a stored `sub` and nothing in the install writes it; Fix = bootstrap looks up by username, class-fix because it removes the stored-`sub` coupling) cross-linked to T-S97-004. |

| Post-Impl checklist item | Verdict | Justification |
|---|---|---|
| 1. E2E tests + manifest registration | **PASS** | T21 creates `scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh` and registers `api\|governance,rbac\|suite-97-…`; verified with `bash scripts/run-tests.sh --audit`. |
| 2. Image version bumps (both files) | **PASS** | T22 bumps `scripts/deploy-cpe2e.sh:369` and mirrors `charts/agentshield/values.yaml:744` in the same change. |
| 3. Experience docs | **PASS (N/A)** | No file in the `docs/experience/playground.md` trigger list is touched. `routers/playground_approvals.py` gains a router-level dependency only — no new SSE event, panel, endpoint, or error state; recorded in Complexity Tracking. |
| 4. Frontend tests (Vitest + Playwright) | **PASS** | No Studio `src/` change ⇒ no Vitest delta. Playwright: T20. |
| 5. Verification (typecheck / python syntax / regression sweep / migrations) | **PASS** | No TS source change (spec-only) ⇒ `npm run typecheck` still run in T20. `python3 -c "import ast; ast.parse(...)"` on every touched `.py` + mapper configure check in T9/T10. Regression sweep enumerated per task. Migration 0079 is idempotent and data-preserving. |

---

## Complexity Tracking

| Item | Deviation | Justification |
|---|---|---|
| Four route groups stay unauthenticated after FR-11 (G-R1-1..5) | FR-11 says "the ten routers" | Applying `require_user` to them breaks `deploy-controller` reconciliation, `declarative-runner` run recording and `eval-runner` step writes — all verified callers with no credential. Alternatives (TokenReview, Keycloak service accounts, a shared secret) are identity propagation, explicitly out of scope. Mitigated by T-S97-011, a canary that asserts the exemption set is *exactly* these routes, so a new unauthenticated route fails the suite. |
| Post-Impl item 3 marked N/A | Checklist says MUST update `docs/experience/playground.md` | The trigger list is file-based; none of those files change. `playground_approvals.py` is not in it, and its behaviour for authenticated callers is byte-identical. |
| `UserCreate.role` default stays `"operator"` | A legacy value survives | FR-4 requires the role be *stated*, not canonical; `_normalize_role` maps `operator → contributor` on read. Changing the default is a behaviour change outside FR-1..12 and would alter `suite-82`'s fixture semantics. Recorded as G-R0-6. |

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `services/registry-api/bootstrap_admin.py` | **CREATE** | The invariant "the platform has exactly one auto-created admin, and it has a role row"; owns the bootstrap sequence, its advisory-lock key, and the process-local readiness flag. |
| `services/registry-api/alembic/versions/0079_drop_user_team_assignments_role_default.py` | **CREATE** | Drop `user_team_assignments.role`'s `server_default='operator'`; column stays `NOT NULL`. |
| `services/registry-api/main.py` | MODIFY | Invoke bootstrap from `lifespan` (after DB warm-up, `:126`); gate `/ready` (`:341`) on bootstrap success; register the `NoPlatformRole → 403` exception handler in `create_app` (`:189`). |
| `services/registry-api/rbac.py` | MODIFY | Add `NoPlatformRole`; `get_user_global_role` (`:50-56`) raises on a missing row; `_normalize_role` (`:40-43`) no longer invents; document the union at `ROLE_HIERARCHY` (`:26`). |
| `services/registry-api/routers/me.py` | MODIFY | Drop the `_normalize_role` import (`:26`) and the second resolution path (`:38-45`); call `rbac.get_user_global_role`. |
| `services/registry-api/routers/admin_users.py` | MODIFY | `_upsert_team` (`:91-106`) loses its internal commit; `create_user` (`:150-181`) becomes atomic with compensating `kc_delete`; `patch_user` (`:196-230`) owns its commit; add `audit_identity` on `teams_router`. |
| `services/registry-api/keycloak_client.py` | MODIFY | `list_users` gains exact-username lookup; `create_user` sets `emailVerified`. |
| `services/registry-api/config.py` | MODIFY | Add `platform_admin_*` bootstrap settings after the MCP block (`:99`). |
| `services/registry-api/routers/schedules.py` | MODIFY | Comment `:155-164` recording that the deny-by-default branch is now unreachable and why it is kept. |
| `services/registry-api/routers/deployments.py` | MODIFY | `require_user` on `router` (all 4 routes) + 3 of 5 `global_deployments_router` routes. |
| `services/registry-api/routers/versions.py` | MODIFY | `require_user` on `router` (4 routes); `versions_global_router` exempt (G-R1-3). |
| `services/registry-api/routers/auth_configs.py` | MODIFY | `require_user` on 5 of 6 routes; `/{config_id}/secret-ref` exempt (G-R1-4). |
| `services/registry-api/routers/agent_tools.py` | MODIFY | `require_user` on POST + DELETE; `GET /{name}/tools` exempt (G-R1-5). |
| `services/registry-api/routers/agent_runs.py` | MODIFY | **Comment only** — the whole router is exempt (G-R1-1) with all five caller sites named. |
| `services/registry-api/routers/workflows.py` | MODIFY | Router-level `dependencies=[Depends(require_user)]`. |
| `services/registry-api/routers/teams.py` | MODIFY | Router-level `dependencies=[Depends(require_user)]`. |
| `services/registry-api/routers/llm_providers.py` | MODIFY | Router-level `dependencies=[Depends(require_user)]`. |
| `services/registry-api/routers/admin.py` | MODIFY | Router-level `dependencies=[Depends(require_user)]`. |
| `services/registry-api/routers/playground_approvals.py` | MODIFY | Router-level `dependencies=[Depends(require_user)]`. |
| `charts/agentshield/templates/realm-init-job.yaml` | MODIFY | Delete both user-creation blocks and their two `secretKeyRef` env vars; keep realm + 4 clients. |
| `charts/agentshield/charts/registry-api/templates/deployment.yaml` | MODIFY | Add `PLATFORM_ADMIN_PASSWORD` from `keycloak-user-passwords` to the **main container** env (after `KEYCLOAK_ADMIN_PASSWORD`, `:135-139`). |
| `charts/agentshield/values.yaml` | MODIFY | Mirror the new `registry-api.image.tag` at `:744`. |
| `scripts/deploy-cpe2e.sh` | MODIFY | Bump `REGISTRY_API_TAG` (`:369`) + comment header; demote the `seed-platform-admin-role.sh` call (`:830`) to a commented repair hint. |
| `scripts/seed-platform-admin-role.sh` | MODIFY | Header rewritten: manual repair tool, not the mechanism (G-R0-5). |
| `scripts/e2e/lib/e2e-auth.sh` | MODIFY | Add `e2e_ensure_reviewer` — one definition, four callers. |
| `scripts/e2e/suite-53-cost-tracking.sh` | MODIFY | State a role on the insert at `:50`. |
| `scripts/e2e/suite-48-feedback-dashboard.sh` | MODIFY | State a role on the insert at `:49`. |
| `scripts/e2e/suite-71-scheduled-e2e.sh` | MODIFY | Annotate `:325`'s `agent:reviewer` as a deliberate reviewer scope (Decision 42). |
| `scripts/e2e/suite-76-preferences.sh` | MODIFY | Create `agent-reviewer` via the admin API before `:62`. |
| `scripts/e2e/suite-78-conversations.sh` | MODIFY | Same, before `:182`. |
| `scripts/e2e/suite-82-artifact-grants.sh` | MODIFY | Same, before `:96`. |
| `scripts/e2e/suite-83-webhook-applications.sh` | MODIFY | Same, before `:112`. |
| `scripts/e2e/suite-96-schedules-endpoint.sh` | MODIFY | Rewrite T-S96-002 for the post-FR-5 contract (`:171-193`). |
| 28 further `scripts/e2e/suite-*.sh` (enumerated in T15–T19) | MODIFY | Attach a real Bearer to calls that hit newly-protected routes. |
| `scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh` | **CREATE** | 12 cases: bootstrap, race, re-pin, 403, NOT NULL, 401 matrix, exemption canary, audit. |
| `scripts/test-manifest.txt` | MODIFY | Register suite-97 after `:147`. |
| `studio/e2e/admin-access-roles.spec.ts` | MODIFY | Add the Admin-menu journey case (DoD 1). |
| `docs/testing/manual-ui-e2e-test-plan.md` | MODIFY | Gap ledger G-R0-1..6, G-R1-1..6. |
| `docs/bugs/platform-admin-role-stranded-on-realm-recreation.md` | **CREATE** | DoD-8 postmortem for the 2026-07-20 incident. |
| `docs/design/rbac-and-artifact-authorization.md` | MODIFY | §1.4 annotated with the FR-11 exemptions; §5 R0/R1 marked SHIPPED. |
| `docs/design/rbac-r0-r1-spec.md` | MODIFY | FR-11 restated to match the verified exemption set. |
| `docs/decisions.md` | MODIFY | Append the FR-11 exemption consequence under Decision 40's consequences. |

---

## Key Interfaces (contracts — match exactly)

`services/registry-api/bootstrap_admin.py`:
```python
BOOTSTRAP_LOCK_KEY: int          # 4611686019521751996
ADMIN_USERNAME: str              # "platform-admin"
ADMIN_EMAIL: str                 # "platform-admin@agentshield.local"  — PINNED, Langfuse
ADMIN_FIRST_NAME: str            # "Platform"
ADMIN_LAST_NAME: str             # "Admin"
ADMIN_ROLE: str                  # "platform-admin"
ADMIN_TEAM: str                  # "platform"

class BootstrapState:
    ok: bool
    last_error: str | None
    last_attempt_at: datetime | None
    admin_sub: str | None
    attempts: int

bootstrap_state: BootstrapState                       # module singleton, read by /ready

async def ensure_platform_admin() -> bool: ...
    # Idempotent, single-flighted. True  = row is pinned to the live sub (or a peer holds
    #                                      the lock — nothing to do this attempt).
    #                                False = attempt failed; bootstrap_state.last_error set.
    # NEVER raises. Mutates bootstrap_state.

async def bootstrap_admin_loop(interval_seconds: int = 30) -> None: ...
    # Retry until bootstrap_state.ok, then return. Cancelled on shutdown.
```

`services/registry-api/keycloak_client.py`:
```python
async def list_users(max: int = 500, username: str | None = None,
                     exact: bool = True) -> list[dict]: ...
    # username -> Keycloak `username`/`exact` query params. Default call unchanged.
```

`services/registry-api/rbac.py`:
```python
class NoPlatformRole(Exception):
    def __init__(self, user_sub: str) -> None: ...
    user_sub: str
    ERROR_CODE: str = "no_platform_role"

def _normalize_role(raw: str) -> str: ...                       # raw is no longer optional
async def get_user_global_role(db: AsyncSession, user_sub: str) -> str: ...   # raises NoPlatformRole
```

`services/registry-api/routers/admin_users.py`:
```python
async def _upsert_team(db, user_sub: str, team_name: str, role: str,
                       assigned_by: str | None) -> None: ...   # NO commit — caller owns it

class IdentityAuditResponse(BaseModel):
    checked_at: str
    keycloak_user_count: int
    assignment_row_count: int
    orphan_users: list[OrphanUser]     # KC user, no row
    stale_rows: list[StaleRow]         # row, no KC user
    matched_count: int

class OrphanUser(BaseModel):
    kc_id: str; username: str; email: str | None
class StaleRow(BaseModel):
    user_sub: str; team_name: str; role: str; assigned_by: str | None; assigned_at: str | None

@teams_router.get("/identity-audit", response_model=IdentityAuditResponse)
async def audit_identity(db: AsyncSession = Depends(get_db)) -> IdentityAuditResponse: ...
```

`services/registry-api/config.py` additions:
```python
platform_admin_bootstrap_enabled: bool = True
platform_admin_password: str = ""          # env PLATFORM_ADMIN_PASSWORD
platform_admin_bootstrap_retry_seconds: int = 30
```

`scripts/e2e/lib/e2e-auth.sh`:
```bash
E2E_REVIEWER_USER="${E2E_REVIEWER_USER:-agent-reviewer}"
E2E_REVIEWER_PASS="${E2E_REVIEWER_PASS:-Reviewer2024}"
e2e_ensure_reviewer <namespace> <pod> [container]   # idempotent; exit 1 with a named cause
```

---

## Tasks

### T1 — Stop the suites regenerating role-less rows (FR-7). MUST land before T9.

**Files:**
- Modify `scripts/e2e/suite-53-cost-tracking.sh:50` — the insert is `INSERT INTO user_team_assignments (user_sub, team_name) VALUES (:s,:t) ON CONFLICT DO NOTHING`. Change to `(user_sub, team_name, role, assigned_by) VALUES (:s,:t,'contributor','suite-53')`. Add: `# role is STATED, not defaulted — migration 0079 removed the column default (FR-4).`
- Modify `scripts/e2e/suite-48-feedback-dashboard.sh:49` — identical shape, identical fix, `assigned_by='suite-48'`. **This site is not in the spec; it is the third producer.**
- Modify `scripts/e2e/suite-71-scheduled-e2e.sh:325` — do **not** change the value. Add above it:
  ```
  # 'agent:reviewer' is a REVIEWER SCOPE, not a global role (Decision 42 / V-5).
  # approvals.py:48 _DEFAULT_REVIEWER_SCOPE matches this literal via _caller_roles (:266).
  # ROLE_HIERARCHY.get(...,0)==0 for it is load-bearing. Do not "normalize" it. The FR-12
  # audit reports it as a matched row, never as litter.
  ```

**Interface contract:** none (bash).

**Acceptance criteria (FR-7):**
- `grep -rn "INSERT INTO user_team_assignments" scripts/ services/` returns **zero** sites lacking a `role` column.
- Suites 48, 53, 71 pass.
- `GET /api/v1/admin/identity-audit` after a full run of all three reports `orphan_users == []` and `stale_rows == []`.

**Dependencies:** none. **First task.**

**Test cases:**
- `suite-53` passes with a stated role
- `suite-48` passes with a stated role
- `suite-71` T-S71-002b still asserts its 403 keyed on `agent:reviewer`

**Verification command:**
```bash
bash scripts/e2e/suite-53-cost-tracking.sh && bash scripts/e2e/suite-48-feedback-dashboard.sh && bash scripts/e2e/suite-71-scheduled-e2e.sh
```

---

### T2 — Keycloak client: exact-username lookup + a loginable created user (FR-3, FR-10 enabler)

**Files:** Modify `services/registry-api/keycloak_client.py:52-61` and `:75-104`.

**Interface contract:**
```python
async def list_users(max: int = 500, username: str | None = None,
                     exact: bool = True) -> list[dict]: ...
```
When `username` is not None add `"username": username, "exact": "true" if exact else "false"` to `params`. Existing caller `routers/admin_users.py:140` (`kc_list()`) is unaffected.

`create_user`: add `"emailVerified": True` to the payload dict at `:85-95`, with this comment:
```
# emailVerified is REQUIRED for a direct-grant login. Keycloak's declarative user profile
# (VERIFY_PROFILE) refuses "Account is not fully set up" without it — the same reason
# charts/agentshield/templates/realm-init-job.yaml sets it via kcadm. This platform has no
# email-verification flow, so a created user could never clear it.
```

**Acceptance criteria (FR-3):**
- `await list_users(username="platform-admin")` returns exactly the 1 matching user on a realm holding 3+ users
- `list_users()` still returns all
- A user created via `POST /api/v1/admin/users` then `POST /{kc_id}/reset-password {"temporary": false}` can obtain a token via the `password` grant on client `agentshield-studio`

**Dependencies:** none. **[P]** with T1, T3.

**Test cases:** covered by T21 T-S97-001 and T13's four suites.

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/keycloak_client.py').read())"
```

---

### T3 — Bootstrap settings (FR-1)

**Files:** Modify `services/registry-api/config.py` — insert after `mcp_health_max_backoff_cycles` (`:99`), before the MCP list_changed block.

**Interface contract:**
```python
    # ------------------------------------------------------------------ #
    # platform-admin bootstrap (Decision 40, phase R0)                     #
    # ------------------------------------------------------------------ #
    # registry-api code — not the Helm chart — creates the sole auto-created
    # user. Disable ONLY for a deploy that provisions the admin some other way;
    # with it off, a fresh install has no admin and no Admin menu.
    platform_admin_bootstrap_enabled: bool = True
    # From the pre-existing keycloak-user-passwords Secret, key `platform-admin`.
    # Empty -> bootstrap fails loudly (/ready red) rather than minting an
    # unknown-password admin.
    platform_admin_password: str = ""
    # Retry cadence while Keycloak is not yet up. /ready stays red until success.
    platform_admin_bootstrap_retry_seconds: int = 30
```

**Acceptance criteria (FR-1):**
- `from config import settings; settings.platform_admin_bootstrap_enabled is True`
- `PLATFORM_ADMIN_PASSWORD=x` in the environment surfaces as `settings.platform_admin_password == "x"` (pydantic-settings is case-insensitive, matching `keycloak_url`/`KEYCLOAK_URL`)

**Dependencies:** none. **[P]** with T1, T2.

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/config.py').read())"
```

---

### T4 — `bootstrap_admin.py` (FR-1, FR-2, FR-3)

**Files:** **Create** `services/registry-api/bootstrap_admin.py`.

Module docstring must state: what invariant it owns; that the lookup is by **username** so realm recreation self-heals; that the email is **pinned** for Langfuse project membership (cite `charts/agentshield/values.yaml:509 LANGFUSE_INIT_USER_EMAIL` and `docs/bugs/langfuse-trace-access-sso-and-membership.md`); and that failure is non-fatal.

**Interface contract:**
```python
import zlib
# 63-bit positive advisory-lock key. Same idiom and rationale as
# mcp_health._SWEEP_LOCK_KEY (mcp_health.py:39-45): crc32 & 0x7FFFFFFF | (1<<62),
# so it can never collide with the scheduler's 31-bit fire locks
# (services/scheduler/ha.py) nor with the health sweep. Value: 4611686019521751996
# (health sweep is 4611686020559757442 — distinct).
BOOTSTRAP_LOCK_KEY = (zlib.crc32(b"platform-admin-bootstrap") & 0x7FFFFFFF) | (1 << 62)
ADMIN_USERNAME = "platform-admin"
ADMIN_EMAIL = "platform-admin@agentshield.local"
ADMIN_FIRST_NAME = "Platform"
ADMIN_LAST_NAME = "Admin"
ADMIN_ROLE = "platform-admin"
ADMIN_TEAM = "platform"   # hard-coded per spec OQ-3 option (a); matches current data

@dataclass
class BootstrapState:
    ok: bool = False
    last_error: str | None = None
    last_attempt_at: datetime | None = None
    admin_sub: str | None = None
    attempts: int = 0

bootstrap_state = BootstrapState()

async def ensure_platform_admin() -> bool: ...
async def bootstrap_admin_loop(interval_seconds: int = 30) -> None: ...
```

**`ensure_platform_admin()` algorithm, in order:**
1. If `not settings.platform_admin_bootstrap_enabled`: set `bootstrap_state.ok = True`, log INFO `"bootstrap: disabled by config — /ready will not gate on it"`, return `True`.
2. `bootstrap_state.attempts += 1`; `bootstrap_state.last_attempt_at = now(UTC)`.
3. If `not settings.platform_admin_password`: set `last_error="PLATFORM_ADMIN_PASSWORD is empty"`, log ERROR, return `False`.
4. `async with engine.connect() as lock_conn:` — `SELECT pg_try_advisory_lock(:k)`; if falsy → `await lock_conn.rollback()`, log INFO `"bootstrap: another replica holds the lock — skipping"`, return `True` (the peer owns it; do not fail this replica). Wrap the whole body in `try/finally` with `SELECT pg_advisory_unlock(:k)` in the `finally`, exactly as `mcp_health.py:190-236`.
5. `users = await kc.list_users(username=ADMIN_USERNAME, exact=True)`; `existing = next((u for u in users if u.get("username") == ADMIN_USERNAME), None)` — the second filter is defensive: Keycloak's `exact` is honoured but the response is still a list.
6. If `existing is None`: `kc_id = await kc.create_user(username=ADMIN_USERNAME, email=ADMIN_EMAIL, first_name=ADMIN_FIRST_NAME, last_name=ADMIN_LAST_NAME, temp_password=settings.platform_admin_password)`; then `await kc.reset_password(kc_id, settings.platform_admin_password, temporary=False)`; `created = True`. Else `kc_id = existing["id"]; created = False`.
7. **Always** `await kc.update_user(kc_id, email=ADMIN_EMAIL, emailVerified=True, firstName=ADMIN_FIRST_NAME, lastName=ADMIN_LAST_NAME, enabled=True, requiredActions=[])`. This is the reconcile step — it re-pins the Langfuse-critical email and clears the `UPDATE_PASSWORD` action `create_user` sets, so the admin can complete a browser login and a `password` grant. Deliberately runs on every attempt, not just on create, so a hand-edited admin self-heals. **Does not** reset the password on an existing user — an operator rotation must survive a restart.
8. `await kc.set_user_realm_role(kc_id, ADMIN_ROLE)` — **not** wrapped in a bare `except: pass`. Let it raise into step 11's handler. A missing realm-role *object* is not an error — `set_user_realm_role` (`keycloak_client.py:183`) silently skips when the name is absent from `role_map`, which is today's state (G-R0-4).
9. Upsert the row on a fresh `AsyncSessionLocal()` session (not `lock_conn`):
   ```sql
   INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_by, assigned_at)
   VALUES (:sub, :team, :role, 'system:bootstrap', now())
   ON CONFLICT (user_sub) DO UPDATE
      SET team_name = EXCLUDED.team_name,
          role = EXCLUDED.role,
          assigned_by = EXCLUDED.assigned_by,
          assigned_at = now()
    WHERE user_team_assignments.team_name IS DISTINCT FROM EXCLUDED.team_name
       OR user_team_assignments.role      IS DISTINCT FROM EXCLUDED.role
   ```
   then `await session.commit()`. The `WHERE` makes a no-op restart a genuine no-op — `assigned_at` only moves when something actually changed, which is what Story 1 scenario 2 asserts.
10. On success: `bootstrap_state.ok = True`, `admin_sub = kc_id`, `last_error = None`; log INFO `"bootstrap: platform-admin pinned sub=%s team=%s role=%s created=%s"`. Return `True`.
11. `except Exception as exc:` — `bootstrap_state.ok = False`, `last_error = f"{type(exc).__name__}: {exc}"`, log ERROR `"bootstrap: FAILED (attempt %d): %s"` with `exc_info=True`, return `False`. **Never re-raise** — a Keycloak outage must not crash-loop the pod.

`bootstrap_admin_loop(interval_seconds)`: `while not bootstrap_state.ok:` → `await ensure_platform_admin()`; if still not ok, `await asyncio.sleep(interval_seconds)`. Return when ok. Catch `asyncio.CancelledError` and return.

**Acceptance criteria:**
- FR-1: on an empty cluster, after startup, Keycloak holds `platform-admin` **and** `user_team_assignments` holds `(sub, 'platform', 'platform-admin', 'system:bootstrap')`
- FR-2: with `replicaCount: 2`, exactly one row and exactly one Keycloak user exist; the losing replica logs `"another replica holds the lock"` and returns `True`
- FR-3: after deleting the Keycloak `platform-admin` user and restarting, the row's `user_sub` equals the **new** `id`
- NFR: a restart with the admin already correct leaves `assigned_at` byte-identical
- Availability: with Keycloak scaled to 0, the process stays up, `/ready` is 503, and the log carries a per-attempt ERROR

**Dependencies:** T2 (`list_users(username=…)`), T3 (settings).

**Test cases:** T-S97-001, -002, -003, -004, -005.

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/bootstrap_admin.py').read())"
# in-pod:
python3 -c "import bootstrap_admin; print(bootstrap_admin.BOOTSTRAP_LOCK_KEY)"   # 4611686019521751996
```

---

### T5 — Wire bootstrap into `lifespan` and gate `/ready` (FR-1)

**Files:** Modify `services/registry-api/main.py`.

**Interface contract:**
After the MCP-health task block (`:137-140`), before `yield`:
```python
    # platform-admin bootstrap (Decision 40, phase R0). Runs in the BACKGROUND and is
    # NON-FATAL: Keycloak is routinely not ready when registry-api starts, and
    # crash-looping a fresh install is worse than degrading. /ready stays red until it
    # succeeds. Single-flighted across replicas by a Postgres advisory lock.
    from bootstrap_admin import bootstrap_admin_loop
    bootstrap_task = _asyncio.create_task(
        bootstrap_admin_loop(settings.platform_admin_bootstrap_retry_seconds)
    )
```
In shutdown (after the `mcp_health_task` block, `:151-156`), cancel + await `bootstrap_task` with the same `except (_asyncio.CancelledError, Exception): pass` shape.

Replace `ready()` (`:341-350`) so that after the `SELECT 1` succeeds it checks bootstrap:
```python
        from bootstrap_admin import bootstrap_state
        if not bootstrap_state.ok:
            # RED UNTIL SUCCESS (spec OQ-1, resolved 2026-08-04). The platform is not
            # live; a loud stall beats a quiet half-working state, and a pod whose
            # admin does not exist should not take traffic.
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "bootstrapping",
                    "detail": bootstrap_state.last_error or "platform-admin bootstrap has not completed",
                    "attempts": str(bootstrap_state.attempts)}
        return {"status": "ready"}
```
Keep the existing DB-failure branch verbatim.

**Acceptance criteria (FR-1, NFR-Availability):**
- `/health` is 200 throughout
- `/ready` is 503 with `status="bootstrapping"` until the bootstrap succeeds, then 200 `{"status":"ready"}`
- The pod never restarts due to bootstrap failure (`kubectl get pod -o jsonpath='{.status.containerStatuses[0].restartCount}'` unchanged)
- Warm-start bootstrap adds < 2s (measured from the two INFO log timestamps)

**Dependencies:** T4.

**Test cases:** T-S97-006 (`/ready` shape), T-S97-005 (non-fatal).

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/main.py').read())"
```

---

### T6 — Chart: the platform creates users, not Helm (FR-9)

**Files:**
- Modify `charts/agentshield/templates/realm-init-job.yaml`:
  - Delete the `platform-admin` user block (`==> Creating user 'platform-admin'` through `fi`) and the `agent-reviewer` user block.
  - Delete the now-unused env vars `PLATFORM_ADMIN_PASSWORD` (`:106-110`) and `REVIEWER_PASSWORD` (`:111-115`).
  - Update the header comment block (`:5-16`): remove the two `• User :` lines and the `keycloak-user-passwords` "Secrets expected" lines; add:
    ```
    • Users    : NONE. Decision 40 — users are created by platform code, never by
                 the chart. registry-api's lifespan bootstraps platform-admin
                 (services/registry-api/bootstrap_admin.py); agent-reviewer is
                 created by the four e2e suites that use it, via POST /api/v1/admin/users.
    ```
  - Keep the realm and all four client blocks (`registry-api`, `envoy-gateway`, `agentshield-studio`, `langfuse`) unchanged.
- Modify `charts/agentshield/charts/registry-api/templates/deployment.yaml` — in the **main `registry-api` container** env list only (after `KEYCLOAK_ADMIN_PASSWORD`, `:135-139`), add:
  ```yaml
            # platform-admin bootstrap (Decision 40). The Secret moved here from the
            # realm-init Job, which no longer creates users. Missing/empty -> the
            # bootstrap fails loudly and /ready stays red.
            - name: PLATFORM_ADMIN_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: keycloak-user-passwords
                  key: platform-admin
  ```
  Do **not** add it to the `alembic-migrate` init container.
- Modify `scripts/seed-platform-admin-role.sh:1-22` header — retitle "MANUAL REPAIR TOOL — not part of the install", state that `bootstrap_admin.ensure_platform_admin` is now the mechanism, and that this script exists for the case where an operator must re-pin without a restart (G-R0-5). Keep the script body unchanged.
- Modify `scripts/deploy-cpe2e.sh:829-831` — replace the unconditional `bash scripts/seed-platform-admin-role.sh || echo ...` invocation with:
  ```bash
  # platform-admin's role row is written by registry-api's lifespan bootstrap
  # (bootstrap_admin.py, Decision 40) — /ready is red until it succeeds, so a green
  # rollout already proves it. scripts/seed-platform-admin-role.sh is retained as a
  # MANUAL repair tool (G-R0-5); run it by hand only if the audit reports a stale row:
  #   kubectl exec ... curl -s localhost:8000/api/v1/admin/identity-audit
  ```
  Leave the `keycloak-user-passwords` Secret creation (`:587-591`) exactly as is — both keys are still needed.

**Acceptance criteria (FR-9):**
- `helm template` output for `realm-init-job.yaml` contains `kcadm.sh create clients` four times and `kcadm.sh create users` **zero** times
- A fresh `bash scripts/deploy-cpe2e.sh` yields a working Admin menu with no `seed-*` invocation (SC-1)

**Dependencies:** T4, T5 (the chart must not stop creating the admin before code starts creating it).

**Test cases:** T-S97-001 + T20.

**Verification command:**
```bash
helm template agentshield charts/agentshield --namespace agentshield-platform | grep -c "kcadm.sh create users"   # 0
helm template agentshield charts/agentshield --namespace agentshield-platform | grep -c "PLATFORM_ADMIN_PASSWORD" # 1
```

---

### T7 — `POST /api/v1/admin/users` becomes atomic (FR-8)

**Files:** Modify `services/registry-api/routers/admin_users.py`.

**Interface contract:**
```python
async def _upsert_team(db, user_sub: str, team_name: str, role: str,
                       assigned_by: str | None) -> None: ...   # NO commit
```
Delete `await db.commit()` (`:106`). Add to the docstring: *Does NOT commit — the CALLER owns the transaction boundary. That is what makes create_user atomic (a compensating kc_delete needs the failure to be visible before the response). An explicit boundary, not a `commit: bool` flag sniffed per call site (Decision 41).*

`create_user` (`:150-181`): after `kc_create` succeeds, wrap the rest:
```python
    assigned_by = caller.get("preferred_username", "admin") if caller else "admin"
    try:
        # Realm-role failure is FATAL, not swallowed. A user whose Keycloak role and
        # DB row disagree is exactly the half-created state R0 exists to remove.
        await set_user_realm_role(kc_id, body.role)
        await _upsert_team(db, kc_id, body.team, body.role, assigned_by=assigned_by)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        try:
            await kc_delete(kc_id)          # compensate: no orphan Keycloak user
        except Exception as cleanup_exc:
            logger.error("create_user: compensating kc_delete FAILED for %s: %s — "
                         "ORPHAN Keycloak user, see GET /api/v1/admin/identity-audit",
                         kc_id, cleanup_exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"User creation rolled back: {type(exc).__name__}: {exc}",
        )
```
Keep the trailing `kc_get` + `_kc_to_response` block (`:175-181`) unchanged. Add `import logging` / `logger = logging.getLogger(__name__)` at module level (the file has none today).

`patch_user` (`:217-223`): after `await _upsert_team(...)` add `await db.commit()`. Leave the `set_user_realm_role` `except Exception: pass` in `patch_user` unchanged — patch is not the half-creation path, and changing it is outside FR-8.

**Acceptance criteria (FR-8 / Story 2):**
1. A valid request leaves the Keycloak user, its realm role, and the row all present
2. Forcing the row write to fail leaves **no** Keycloak user and returns 5xx
3. `set_user_realm_role` failure is treated as failure
4. `grep -n "await db.commit()" services/registry-api/routers/admin_users.py` shows commits in `create_user`, `patch_user`, `delete_user` — and none inside `_upsert_team`

**Dependencies:** none on other tasks; must precede T13.

**Test cases:** T-S97-007, T-S97-008.

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/routers/admin_users.py').read())"
```

---

### T8 — Read-only identity audit surface (FR-12)

**Files:** Modify `services/registry-api/routers/admin_users.py` — add the four models from *Key Interfaces* and `audit_identity` on **`teams_router`** (prefix `/api/v1/admin`), placed after `teams_summary`.

Mounted on `teams_router`, not `router`, deliberately: `router` declares `GET /{kc_id}` (`:184`), and a literal `/audit` sibling would depend on declaration order to avoid shadowing. `/api/v1/admin/identity-audit` cannot collide.

**Interface contract:**
```python
@teams_router.get("/identity-audit", response_model=IdentityAuditResponse)
async def audit_identity(db: AsyncSession = Depends(get_db)) -> IdentityAuditResponse: ...
```
Body: `kc_users = await kc_list()`; `team_map = await _team_map(db)`; `kc_ids = {u["id"] for u in kc_users}`; `orphan_users` = every `u` with `u["id"] not in team_map`; `stale_rows` = every `sub, info` in `team_map` with `sub not in kc_ids`; `matched_count = len(kc_ids & set(team_map))`. `checked_at = datetime.now(timezone.utc).isoformat()`. On Keycloak failure raise `HTTPException(502, "Keycloak unreachable: …")` — an audit that silently reports zero orphans because it could not read Keycloak is worse than one that fails.

Docstring must state: **READ-ONLY. Reports, never deletes** (spec OQ-2 resolved to option (a)). A `stale_row` is litter, not a security hole — nobody can authenticate as a dead `sub` — but it means "row count" ≠ "admin count" (G-R0-3). A row holding a reviewer scope such as `agent:reviewer` whose `sub` is a live Keycloak user is **matched**, not litter (Decision 42).

**Acceptance criteria (FR-12):**
- Reproduces the V-6 table on demand
- On the current cluster after T1 + a full e2e pass, `orphan_users == []` and `stale_rows == []` (SC-3)
- Deleting a Keycloak user without its row makes it appear in `stale_rows` on the next call

**Dependencies:** none. **[P]** with T7.

**Test cases:** T-S97-012.

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/routers/admin_users.py').read())"
curl -s localhost:8000/api/v1/admin/identity-audit | python3 -m json.tool
```

---

### T9 — Migration 0079: drop the column default (FR-4). MUST land after T1.

**Files:** **Create** `services/registry-api/alembic/versions/0079_drop_user_team_assignments_role_default.py`.

**Interface contract:**
```python
"""Drop user_team_assignments.role's server_default.

Revision ID: 0079
Revises: 0078
Create Date: 2026-08-04

WHY
---
Migration 0013 created the column with server_default="operator". 0044 migrated the
DATA operator -> contributor and 0075 finished viewer -> consumer, but NEITHER touched
the default — so every insert that omitted `role` silently re-introduced the exact
legacy value those two migrations existed to remove. Decision 41, producer 2.

The column stays NOT NULL. After this migration an insert that omits `role` fails with
a NOT NULL violation instead of inventing one. Every in-repo inserter was fixed first
(FR-7, ordered before this migration on purpose):
    services/registry-api/routers/admin_users.py:96   states it
    scripts/e2e/suite-53-cost-tracking.sh:50          fixed
    scripts/e2e/suite-48-feedback-dashboard.sh:49     fixed
    scripts/e2e/suite-71-scheduled-e2e.sh:325         already states it (reviewer scope)

Idempotent: ALTER COLUMN ... DROP DEFAULT is a no-op when no default exists.
Data-preserving: touches no row.
"""
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE user_team_assignments ALTER COLUMN role DROP DEFAULT")


def downgrade() -> None:
    op.execute("ALTER TABLE user_team_assignments ALTER COLUMN role SET DEFAULT 'operator'")
```

**Acceptance criteria (FR-4 / Story 3 scenario 2):**
- After upgrade, `SELECT column_default FROM information_schema.columns WHERE table_name='user_team_assignments' AND column_name='role'` returns `NULL`
- `INSERT INTO user_team_assignments (user_sub, team_name) VALUES ('x','platform')` raises `NotNullViolation`
- `is_nullable` is still `NO`; row count unchanged
- Running `alembic upgrade head` twice is clean

**Dependencies:** **T1** (hard ordering — otherwise the next e2e run inserts rows this rejects).

**Test cases:** T-S97-009.

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0079_drop_user_team_assignments_role_default.py').read())"
# in-pod: alembic upgrade head && alembic current   -> 0079 (head)
```

---

### T10 — One resolution path; a missing row raises (FR-5, FR-6)

**Files:** Modify `services/registry-api/rbac.py`, `services/registry-api/routers/me.py`, `services/registry-api/main.py`, `services/registry-api/routers/schedules.py`.

**Interface contract:**

`rbac.py` — after `PLATFORM_ROLES` (`:37`):
```python
class NoPlatformRole(Exception):
    """The subject has no user_team_assignments row.

    Decision 40/41: users are created by the platform, never auto-provisioned from
    the IdP, so a missing row is DATA CORRUPTION, not a kind of user. Raised rather
    than resolved to an invented role. main.create_app maps this to 403 with the
    stable code below.
    """
    ERROR_CODE = "no_platform_role"

    def __init__(self, user_sub: str) -> None:
        self.user_sub = user_sub
        super().__init__(f"no platform role row for sub '{user_sub}'")
```

`_normalize_role` (`:40-43`) becomes:
```python
def _normalize_role(raw: str) -> str:
    """Map a legacy spelling onto its canonical name. Never invents.

    An UNRECOGNIZED value is returned VERBATIM and keeps today's behaviour
    (ROLE_HIERARCHY.get(role, 0) == 0). That is deliberate and load-bearing, not a
    gap: user_team_assignments.role is a union of {global role} u {reviewer scope}
    (Decision 42 / V-5). routers/approvals.py:48 defines
    _DEFAULT_REVIEWER_SCOPE = "agent:reviewer" and _caller_roles (:266) matches it
    against this same column. Rank 0 for a scope literal is the ONLY thing stopping a
    reviewer-scope holder from being read as a contributor. Do not "fix" it here —
    the split lands in R5 (G-R0-1).
    """
    return _LEGACY_MAP.get(raw, raw)
```

`get_user_global_role` (`:50-56`): after `r = row.scalar_one_or_none()` insert:
```python
    if r is None:
        logger.warning(
            "rbac: sub '%s' has NO user_team_assignments row — refusing (%s). "
            "Users are platform-created; a missing row is corruption, not a default "
            "(Decision 40/41). Check GET /api/v1/admin/identity-audit.",
            user_sub, NoPlatformRole.ERROR_CODE,
        )
        raise NoPlatformRole(user_sub)
    return _normalize_role(r)
```

`routers/me.py`: `:26` → `from rbac import get_user_artifact_roles, get_user_global_role`. Replace `normalized_role = _normalize_role(raw_role)` with `normalized_role = await get_user_global_role(db, sub)`. Add: *# ONE resolution path. me.py used to import _normalize_role directly — two independent answers to "what role is this" is exactly how approvals._ADMIN_ROLES diverged (docs/bugs/production-hitl-decide-403-authority.md). Decision 41.*

`main.py` — inside `create_app`, immediately after the CORS middleware (`:183`):
```python
    # A subject with no user_team_assignments row is refused, loudly, in ONE place
    # (Decision 40/41). Stable machine-readable code so Studio and the suites can
    # assert on it without string-matching a sentence.
    from rbac import NoPlatformRole
    from fastapi.responses import JSONResponse

    @app.exception_handler(NoPlatformRole)
    async def _no_platform_role_handler(request: Request, exc: NoPlatformRole):
        logger.error("403 %s: sub=%s path=%s", NoPlatformRole.ERROR_CODE,
                     exc.user_sub, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": f"No platform role assigned to '{exc.user_sub}'. "
                          "Users are created by a platform administrator.",
                "error_code": NoPlatformRole.ERROR_CODE,
                "sub": exc.user_sub,
            },
        )
```

`routers/schedules.py:155-164` — leave the code, add above the `if not all_teams and not team:` guard:
```python
    # NOTE (R0/FR-5): this branch is now UNREACHABLE. get_user_global_role above raises
    # NoPlatformRole -> 403 for a sub with no row, and team_name is NOT NULL, so
    # "no team" and "no row" are the same condition. Kept as defence in depth: if FR-5
    # is ever reverted, this is still the Decision-33 `else` that stops an unfiltered
    # read. suite-96 T-S96-002 asserts the NEW contract (refusal), not the old empty list.
```

**Acceptance criteria:**
- FR-5 / Story 3.1: `GET /api/v1/me` with a valid JWT for a sub with no row → **403**, body `error_code == "no_platform_role"`, and a log line naming the sub. `grep -n "_normalize_role" services/registry-api/routers/me.py` → no match
- FR-6 / Story 3.3: a row with `role='agent:reviewer'` → `get_user_global_role` returns `'agent:reviewer'`, `ROLE_HIERARCHY.get(...,0) == 0`, **no exception**
- Legacy mapping unchanged: `admin→platform-admin`, `operator→contributor`, `viewer→consumer`
- Known behaviour changes (assert, do not fix): `routers/triggers.py:64` and `routers/composite_workflows.py:774,865,903,937` now 403 a row-less caller instead of logging PERMITTED; `routers/artifact_grants.py:153,331` and `routers/applications.py:95` still return **403** (status unchanged, detail changed) — `suite-82` T-ARG-004 and `suite-83` T-SYY-003 stay green

**Dependencies:** T9 (land the migration first so the DB cannot re-mint a row that masks the change).

**Test cases:** T-S97-009, T-S97-010; T11 repairs suite-96.

**Verification command:**
```bash
for f in rbac.py routers/me.py main.py routers/schedules.py; do python3 -c "import ast; ast.parse(open('services/registry-api/$f').read())"; done
# in-pod:
python3 -c "import main; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('mappers ok')"
```

---

### T11 — Repair `suite-96` T-S96-002 for the post-FR-5 contract

**Files:** Modify `scripts/e2e/suite-96-schedules-endpoint.sh:171-193` (the T-S96-002 block) and its header description at `:23-27`.

**Interface contract:**
```python
        from routers.schedules import list_schedules
        from rbac import NoPlatformRole
        teamless_sub = f"s96-noteam-{uuid.uuid4()}"
        async with AsyncSessionLocal() as s:
            assigned = (await s.execute(text(
                "SELECT count(*) FROM user_team_assignments WHERE user_sub = :u"),
                {"u": teamless_sub})).scalar()
            refused = False; returned = None
            try:
                returned = await list_schedules(trigger_type="schedule",
                                                claims={"sub": teamless_sub}, db=s)
            except NoPlatformRole:
                refused = True
        record("T-S96-002 DENY BY DEFAULT: a caller with NO role row is REFUSED, never given the table",
               assigned == 0 and refused and returned is None,
               f"team_rows_for_caller={assigned} (want 0) refused={refused} (want True) "
               f"returned={returned!r} (want None) — the platform has {len(body)} schedules, "
               f"so an unfiltered read would return them all")
```

Header comment must say: the contract changed in R0 — a caller with no `user_team_assignments` row is now refused with `NoPlatformRole` (403 over HTTP) *before* the team filter runs, because `team_name` is `NOT NULL` so "no team" and "no row" are the same condition. The security property under test is unchanged and still non-vacuous: an unfiltered read must never happen. The old assertion (`denied == []`) would now raise, and leaving it would be a test that stayed green through a real contract change.

**Acceptance criteria:** `bash scripts/e2e/suite-96-schedules-endpoint.sh` → all cases PASS, and T-S96-002's evidence line still names the platform's schedule count.

**Dependencies:** T10.

**Verification command:**
```bash
bash scripts/run-tests.sh --layer api --group rbac
```

---

### T12 — `e2e_ensure_reviewer` — one definition, four callers (FR-10)

**Files:** Modify `scripts/e2e/lib/e2e-auth.sh`.

**Interface contract:**
```bash
E2E_REVIEWER_USER="${E2E_REVIEWER_USER:-agent-reviewer}"
E2E_REVIEWER_PASS="${E2E_REVIEWER_PASS:-Reviewer2024}"

# e2e_ensure_reviewer <namespace> <pod> [container]
# Idempotently ensure the `agent-reviewer` Keycloak user exists with password
# $E2E_REVIEWER_PASS and a stated role row, via the REAL API:
#   POST   /api/v1/admin/users                         (creates KC user + assignment row)
#   POST   /api/v1/admin/users/{kc_id}/reset-password  {"temporary": false}
# The reset is REQUIRED: keycloak_client.create_user sets a temporary password and
# requiredActions=["UPDATE_PASSWORD"], and Keycloak refuses a `password` grant for
# such a user with "Account is not fully set up". A 409 from the POST means the user
# already exists — that is success, and the function then re-resolves its kc_id from
# GET /api/v1/admin/users and re-asserts the password so a rotated credential self-heals.
# FAILS LOUD: aborts the suite with a message naming the cause, never leaves the
# caller to discover it 20 minutes later as an unexplained 401.
e2e_ensure_reviewer() { ... }
```
Implementation runs one `kubectl exec ... python3 -` in-pod against `http://localhost:8000`, authenticating with a `platform-admin` token obtained via `e2e_token` (so the helper survives R2's `require_global_role` on `/admin/*`). Create body: `{"username": "$E2E_REVIEWER_USER", "email": "agent-reviewer@agentshield.local", "first_name": "Agent", "last_name": "Reviewer", "temp_password": "$E2E_REVIEWER_PASS", "team": "platform", "role": "contributor"}`. After the reset it **verifies** by performing the `password` grant itself and aborts with `FATAL: agent-reviewer exists but cannot obtain a token …` if it fails.

**Acceptance criteria (FR-10):**
- Calling it twice in a row is a no-op the second time
- After it returns, a `password` grant for `agent-reviewer`/`Reviewer2024` on client `agentshield-studio` succeeds
- A `user_team_assignments` row exists for its sub with `role='contributor'`

**Dependencies:** T7 (atomic create), T2 (`emailVerified`).

**Verification command:**
```bash
bash -n scripts/e2e/lib/e2e-auth.sh
```

---

### T13 — The four reviewer suites create their own persona (FR-10)

**Files** (each: `source lib/e2e-auth.sh`, then `e2e_ensure_reviewer "$NAMESPACE" "$API_POD"` **before** the driver runs):
- `scripts/e2e/suite-76-preferences.sh` — insert after the `API_POD` guard (`:29`), before the `kubectl exec` at `:35`. `:62`'s `get_token(c, "agent-reviewer", "Reviewer2024")` then resolves.
- `scripts/e2e/suite-78-conversations.sh` — before the driver that reaches `:182`.
- `scripts/e2e/suite-82-artifact-grants.sh` — before `:95`. Keep `:101`'s `PATCH /api/v1/admin/users/{RSUB}` (it re-pins team `platform`); change its `"role": "operator"` to `"role": "contributor"` and comment `# stated, canonical; the 403 persona is defined by lacking an ARTIFACT role, not a global one`.
- `scripts/e2e/suite-83-webhook-applications.sh` — before `:111`.

**Acceptance criteria (FR-10):** all four suites pass against a chart that no longer creates `agent-reviewer`, on a namespace where the user does not pre-exist (verify by deleting the Keycloak user first, or on a fresh install).

**Dependencies:** T12, T6.

**Verification command:**
```bash
bash scripts/e2e/suite-76-preferences.sh && bash scripts/e2e/suite-78-conversations.sh && bash scripts/e2e/suite-82-artifact-grants.sh && bash scripts/e2e/suite-83-webhook-applications.sh
```

---

### T14 — Router authentication with named exemptions (FR-11)

**Files:** all under `services/registry-api/routers/`. Import in each: `from auth_middleware import require_user` and `from fastapi import Depends`.

*Fully protected — router-level* `dependencies=[Depends(require_user)]` on the `APIRouter(...)` constructor:

| File | Line | Router |
|---|---|---|
| `workflows.py` | 39 | `/api/v1/agent-graphs` |
| `teams.py` | 30 | `/api/v1/teams` |
| `llm_providers.py` | 39 | `/api/v1/llm-providers` |
| `admin.py` | 55 | `/api/v1/admin` |
| `playground_approvals.py` | 23 | `/api/v1/playground` (its only route is `GET /approvals`) |
| `deployments.py` | 195 | `router` — `/{name}/deploy`, `/{name}/rollback`, `GET /{name}/deployments`, `PATCH /{name}/deployments/{id}` |
| `versions.py` | 29 | `router` — the four `/{name}/versions*` routes |

*Partially protected — per-endpoint* `dependencies=[Depends(require_user)]` in the decorator:

| File | Protect | Exempt (+ verified caller) |
|---|---|---|
| `deployments.py` | `:267 GET /workflows`, `:327 GET /{id}/stats`, `:377 GET /{id}/runs` | `:209 GET /`, `:241 PATCH /{deployment_id}` — `services/deploy-controller/main.py:54,70,122,176` (**G-R1-2**) |
| `versions.py` | — | `:319 GET /{version_id}` on `versions_global_router` — `services/deploy-controller/main.py:33` (**G-R1-3**) |
| `auth_configs.py` | `:55, :96, :125, :222, :258` | `:140 GET /{config_id}/secret-ref` — `services/deploy-controller/tool_secrets.py:45` (**G-R1-4**) |
| `agent_tools.py` | `:54 POST /{name}/tools`, `:94 DELETE /{name}/tools/{tool_id}` | `:126 GET /{name}/tools` — `services/deploy-controller/tool_secrets.py:36`, `services/declarative-runner/workflow_executor.py:171` (**G-R1-5**) |
| `agent_runs.py` | — (entire router exempt) | all 7 routes — `services/declarative-runner/main.py:410,437,148`, `checkpoint.py:27`, `orchestrator.py:35`, `services/eval-runner/main.py:1254` (**G-R1-1**) |

Every exempt route gets a comment in this exact shape (no bare `# TODO`):
```python
# UNAUTHENTICATED BY NECESSITY (R1, G-R1-N). In-cluster machine caller with no user
# JWT: <service>/<file>:<line>. Closing this needs a service identity that
# docs/design/identity-propagation-architecture.md owns (migrations 0080-0082); doing
# it here would break control-plane reconciliation. Same posture as routers/internal.py:
# cluster-internal, NetworkPolicy-trusted. suite-97 T-S97-011 pins this exemption set.
```
Also add a module-level note at the top of `agent_runs.py` recording that the whole router is exempt and why.

**Acceptance criteria (FR-11 / Story 4):**
1. Every protected route returns **401** with no `Authorization` header
2. With a valid Studio JWT the response is byte-identical to before — no role logic added anywhere
3. `deploy-controller`, `declarative-runner` and `eval-runner` continue to function (verified by the deploy/execution/eval groups going green)

**Dependencies:** T15–T19 must ship in the **same image/commit** — a router change deployed without the suite changes turns ~28 suites red at setup, exactly as commit `76b3570` did to fifteen (see `scripts/e2e/lib/e2e-auth.sh:9-22`).

**Test cases:** T-S97-011 (401 matrix + exemption canary).

**Verification command:**
```bash
for f in workflows teams llm_providers admin playground_approvals deployments versions auth_configs agent_tools agent_runs; do python3 -c "import ast; ast.parse(open('services/registry-api/routers/$f.py').read())"; done
```

---

### T15–T19 — Attach a real Bearer to e2e calls that hit newly-protected routes (FR-11 blast radius)

**Recipe, identical for every suite:**
1. After the `API_POD` resolution, add `source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"` then `e2e_set_token "$NAMESPACE" "$API_POD"` (call bare — **not** in a command substitution; `e2e-auth.sh:110-121` explains why). For detached-driver suites, also `e2e_install_pyauth "$NAMESPACE" "$API_POD"` and use `from e2e_auth import BearerAuth, mint` inside the driver, so the token refreshes past its 300s life.
2. Add `"Authorization": "Bearer ${E2E_TOKEN}"` to the header dict — **do not** remove existing `X-User-Sub`/`X-User-Team` headers; they are audit stamps and several suites assert on them.
3. Re-run the suite.

| Task | Group(s) | Suites |
|---|---|---|
| **T15** | deploy, agent | `suite-2-lifecycle.sh`(verify), `suite-38-deployment-overview.sh`, `suite-39-deployment-lifecycle.sh`, `suite-41-version-delete.sh`, `suite-44-version-management.sh`, `suite-50-version-dedup.sh`, `suite-67-deployment-gc-and-drift.sh`, `suite-46-chat-deployment-pinning.sh`, `suite-47-deployment-chat-tracing.sh` |
| **T16** | eval | `suite-17-eval-gate.sh`, `suite-61-eval-mode-plumbing.sh`, `suite-72-eval-v2-durable.sh`, `suite-73-eval-v2-workflow.sh`, `suite-74-eval-v2-side-effects.sh`, `suite-80-eval-v2-regression.sh` |
| **T17** | workflow, execution | `suite-40-workflow-deploy.sh`, `suite-58-workflow-live-run.sh`, `suite-64-production-workflow-golden-path.sh`, `suite-68-daemon-no-input.sh` |
| **T18** | governance, hitl, tools | `suite-5-hitl-authority.sh`, `suite-7-machine-identity.sh`, `suite-15-artifact-isolation.sh`, `suite-18-opa-governance.sh`, `suite-51-credential-validation.sh`, `suite-81-deploy-tool-autograt.sh`, `suite-84-mcp-tools.sh`, `suite-45-hitl-e2e.sh`(verify), `suite-65-production-hitl-console.sh` |
| **T19** | chat, knowledge, agent | `suite-8-playground.sh`, `suite-14-consumer-chat.sh`(verify), `suite-16-create-agent.sh`(verify), `suite-6-asset-lifecycle.sh`, `suite-77-knowledge-rag.sh`, `suite-80-agent-knowledge-binding.sh` |

"(verify)" = the suite already fetches a Bearer for *some* calls; confirm it is attached to the calls that hit the surfaces in the matrix, and add it where it is not.

Also re-verify, without editing unless a gap is found: `43, 66, 70, 71, 75-eval-v2-scheduled, 77-eval-v2-webhook, 95, 96` plus `75-context-storage, 79-workflow-hitl, 82, 83, 86, 87, 88, 89, 94`.

**Acceptance criteria (FR-11 scenario 2):** every suite in the task's group passes with behaviour unchanged; no suite is skipped or weakened to go green.

**Dependencies:** T14 (same commit). T15–T19 are **[P]** with each other.

**Verification command:**
```bash
bash scripts/run-tests.sh --layer api --group <group>
```

---

### T20 — Playwright: the Admin menu is present after a bootstrap-only install (DoD rule 1)

**Files:** Modify `studio/e2e/admin-access-roles.spec.ts` — append:
```ts
test("bootstrap gives platform-admin a role row, so the Admin menu renders (R0 / Decision 40)", async ({ page }) => {
  // The 2026-07-20 symptom was structural: the assignment row was pinned to a dead
  // `sub`, /me answered role=null, Sidebar.tsx:392 `isAtLeast("platform-admin")` was
  // false, and the Admin section silently vanished. Nothing in the install wrote that
  // row; scripts/seed-platform-admin-role.sh patched it after the fact. It is now
  // written by registry-api's lifespan bootstrap. This asserts the whole chain from
  // the browser: real Keycloak login -> /me -> sidebar.
  const me = page.waitForResponse(
    (r) => r.url().includes("/api/v1/me") && r.request().method() === "GET",
    { timeout: 30_000 },
  );
  await page.goto(BASE_URL);
  const body = await (await me).json();
  expect(body.role, `/me role: ${JSON.stringify(body)}`).toBe("platform-admin");
  expect(body.team).toBe("platform");
  await expect(page.getByRole("button", { name: /^Admin$/ })).toBeVisible({ timeout: 20_000 });
});
```
If `CollapsibleSection` renders its label as something other than a `button`, match the rendered element — read `studio/src/components/Sidebar.tsx` `CollapsibleSection` before writing the locator.

**Acceptance criteria:** passes against a cluster deployed with `scripts/deploy-cpe2e.sh` **without** any `seed-*` invocation. Fails (role null / no Admin section) if the bootstrap is disabled. No manifest change — `browser|governance,rbac|e2e/admin-access-roles.spec.ts` is already registered (`test-manifest.txt:151`).

**Dependencies:** T5, T6, T10.

**Verification command:**
```bash
cd studio && npm run typecheck && cd .. && bash scripts/studio-e2e.sh
```

---

### T21 — `suite-97` + manifest registration

**Files:** **Create** `scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh` (executable, `set -euo pipefail`, `source lib/e2e-auth.sh`, `e2e_require_token` + `e2e_install_pyauth`, detached in-pod driver → result file, following `suite-96`'s structure at `:56-75`). Modify `scripts/test-manifest.txt` — add after line 147:
```
api|governance,rbac|suite-97-rbac-bootstrap-and-router-auth.sh|R0/R1 — platform-admin bootstrap (username lookup, advisory-lock single-flight, realm-recreation re-pin), missing-row 403, role NOT NULL, and the ten-router 401 matrix with its named exemptions
```

**Test cases:**
- **T-S97-001** — Keycloak holds exactly one `platform-admin` and `user_team_assignments` holds one row for its `id` with `team_name='platform'`, `role='platform-admin'`, `assigned_by='system:bootstrap'`. *(FR-1, Story 1.1)*
- **T-S97-002** — restart idempotence: read `assigned_at`; `kubectl rollout restart`; wait for `/ready` 200; re-read — same `user_sub`, same `assigned_at`, still exactly one Keycloak user. *(FR-1, Story 1.2)*
- **T-S97-003** — replica race: with `replicaCount: 2`, `SELECT count(*) … WHERE role='platform-admin' AND assigned_by='system:bootstrap'` is 1, `list_users(username='platform-admin')` length is 1, and at least one pod's log carries `"another replica holds the lock"`. *(FR-2, Story 1.3)*
- **T-S97-004 — MUST FAIL AGAINST CURRENT CODE.** Realm-recreation re-pin. Record `sub_before`. Delete the Keycloak `platform-admin` user through the Admin API, restart registry-api, wait for `/ready` 200. Assert: a `platform-admin` user exists again with a **new** `id != sub_before`; the row is on the **new** id with `role='platform-admin'`; and a token for `platform-admin` calling `GET /api/v1/me` returns `role == "platform-admin"`. **Write and demonstrate RED before T4/T5 land** (DoD rule 7). Restore by letting the bootstrap re-create the user; delete the stale row via the audit + a manual `DELETE`. *(FR-3, Story 1.4, SC-2)*
- **T-S97-005** — non-fatal: scale Keycloak to 0; restart registry-api; assert over 90s that `restartCount` does not increase, `/health` stays 200, `/ready` is 503 `status="bootstrapping"`; scale Keycloak back; assert `/ready` reaches 200 within 3 retry intervals. *(NFR-Availability)*
- **T-S97-006** — `/ready` body shape: 200 → `{"status":"ready"}`; bootstrapping → 503 with `status`, `detail`, `attempts`.
- **T-S97-007** — `POST /api/v1/admin/users` happy path is atomic: create a throwaway user; assert Keycloak user + realm roles + row all exist; delete it. *(FR-8, Story 2.1)*
- **T-S97-008** — compensation: call `create_user` in-pod with a DB session whose `commit` is monkeypatched to raise; assert `HTTPException` with `status_code == 502` **and** `list_users(username=<throwaway>)` is empty afterwards. *(FR-8, Story 2.2/2.3)*
- **T-S97-009** — `INSERT INTO user_team_assignments (user_sub, team_name) VALUES (:s,'platform')` raises NOT NULL, and `information_schema.columns.column_default` for `role` is NULL. *(FR-4, Story 3.2)*
- **T-S97-010** — missing-row refusal and the scope exception: (a) `rbac.get_user_global_role` for a random sub with no row → `NoPlatformRole`; (b) over HTTP, `GET /api/v1/me` with a token for a Keycloak user with no row → 403, `error_code == "no_platform_role"`, sub echoed; (c) insert a row with `role='agent:reviewer'`, assert `get_user_global_role` returns it with **no** exception and `ROLE_HIERARCHY.get(role,0) == 0`; clean up. *(FR-5, FR-6, Story 3.1/3.3)*
- **T-S97-011** — the 401 matrix **and** the exemption canary. In-pod, `from main import app`; walk `app.routes`; for every route whose endpoint module is one of the ten, compute whether `require_user` is in its flattened dependencies. Assert the partition equals an expected mapping written literally in the suite (the two tables in T14). Then over HTTP with no `Authorization`, assert 401 for one representative route per protected group and that the exempt routes still answer. *(FR-11, Story 4.1/4.2, SC-4)*
- **T-S97-012** — `GET /api/v1/admin/identity-audit` returns 200 with all seven fields; `matched_count + len(orphan_users) == keycloak_user_count`; `matched_count + len(stale_rows) == assignment_row_count`; on a clean cluster both lists are empty. *(FR-12, SC-3)*

**Acceptance criteria:** `bash scripts/run-tests.sh --audit` prints "Manifest audit clean"; suite-97 passes; T-S97-004 is documented as having been red first.

**Dependencies:** T1–T14, T20.

**Verification command:**
```bash
bash scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh && bash scripts/run-tests.sh --audit
```

---

### T22 — Ship: image tags, docs, gap ledger, orphan sweep

**Files:**
- Modify `scripts/deploy-cpe2e.sh:369` — `REGISTRY_API_TAG="0.2.259"` and prepend to the comment chain a summary of R0/R1 ending with `MUST match charts/agentshield/values.yaml.`
- Modify `charts/agentshield/values.yaml:744` — `tag: "0.2.259"` (mirror exactly).
- **Create** `docs/bugs/platform-admin-role-stranded-on-realm-recreation.md` — Found 2026-07-20 / Fixed `0.2.259`; **Symptom**: the Studio Admin menu silently disappears after a realm recreation, `/me` returns `role: "contributor"`; **Root cause**: the assignment row was pinned to a `sub` captured at seed time, and nothing in the install wrote it at all — the design flaw is coupling a durable row to an identifier the IdP is free to reissue; **Fix**: the platform creates the admin and looks it up by **username** each start, so re-pinning falls out for free. Cross-link T-S97-004.
- Modify `docs/testing/manual-ui-e2e-test-plan.md` "Known gaps":
  - *deferred (intentional)*: **G-R0-1** role/scope union (owner R5) · **G-R0-2** `approval_authority` remains the live HITL mechanism (R5) · **G-R0-4** no Keycloak realm-role objects for the three global roles · **G-R0-5** `seed-platform-admin-role.sh` retained as a repair tool · **G-R0-6** `UserCreate.role` still defaults to the legacy `"operator"`
  - *not-yet-wired (debt)*: **G-R0-3** a stale row survives realm recreation or a hand-deleted admin · **G-R1-1** `routers/agent_runs.py` entirely unauthenticated (`declarative-runner` ×4, `eval-runner` ×1) · **G-R1-2** `GET /api/v1/deployments/` + `PATCH /api/v1/deployments/{id}` · **G-R1-3** `GET /api/v1/versions/{id}` · **G-R1-4** `GET /api/v1/auth-configs/{id}/secret-ref` · **G-R1-5** `GET /api/v1/agents/{name}/tools`. All five owned by identity propagation; pinned by T-S97-011 · **G-R1-6** `sdk/agentshield_sdk/cli.py:194,207` sends no token, so `agentshield deploy` now 401s
- Modify `docs/design/rbac-and-artifact-authorization.md` — §1.4 gains an "R1 outcome" column; §5's R0 and R1 paragraphs get a `**SHIPPED 2026-08-04 (0.2.259)**` prefix and the exemption caveat.
- Modify `docs/design/rbac-r0-r1-spec.md` — FR-11 restated to the verified exemption set.
- Modify `docs/decisions.md` — under Decision 40's "Consequences", append the FR-11 exemption note.

**Orphan sweep (DoD rule 3), all must return ≥1 non-definition hit:**
```bash
grep -rn "ensure_platform_admin"  services/registry-api/ --include=*.py
grep -rn "bootstrap_admin_loop"   services/registry-api/ --include=*.py
grep -rn "bootstrap_state"        services/registry-api/ --include=*.py
grep -rn "NoPlatformRole"         services/registry-api/ --include=*.py
grep -rn "identity-audit"         services/registry-api/ scripts/ docs/
grep -rn "e2e_ensure_reviewer"    scripts/e2e/
```

**Acceptance criteria:** both tags identical; every new symbol has a caller; the gap ledger names every deferred item.

**Dependencies:** all prior tasks.

**Verification command:**
```bash
grep -n 'REGISTRY_API_TAG=' scripts/deploy-cpe2e.sh | head -1
sed -n '744p' charts/agentshield/values.yaml
```

---

## Execution Notes

```
T1 ─────────────────────────────► T9 ──► T10 ──► T11
T2 ─┐                                       │
T3 ─┼──► T4 ──► T5 ──► T6 ──► T13           │
T7 ─┴──► T12 ─┘         │                   │
T8                      └──► T20 ◄──────────┘
T14 ──► {T15 T16 T17 T18 T19}  (same commit as T14)
all ──► T21 ──► T22
```

- **T1 strictly before T9** — the spec's Migration Path step 2. Reversing it means the next e2e run inserts rows the new constraint rejects.
- **T14 and T15–T19 ship together.** A deploy carrying the router change without the suite changes reproduces commit `76b3570`'s fifteen-dark-suites failure at ~28× scale.
- **T10 after T9** — with the default still in place, a row can be re-minted between the code change and the migration, masking the refusal.
- **[P] parallel-safe**: {T1, T2, T3}; {T7, T8}; {T15, T16, T17, T18, T19}.
- Full-run gate before T22:
  ```bash
  bash scripts/run-tests.sh --layer api --group rbac,governance,deploy,agent,eval,workflow,execution,hitl,chat,tools,knowledge
  bash scripts/studio-e2e.sh
  ```

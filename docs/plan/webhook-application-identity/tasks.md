# Tasks — Webhook Application Identity & Invoker Grants

**Source plan:** `./plan.md` (27 coarser tasks — decomposed here to file-level granularity, same order, same decisions). **Grounding:** `./research.md` (11 decisions — respected verbatim below; none silently overridden). **Data model:** `./data-model.md`. **Contracts:** `./contracts/artifact-grants.md`, `./contracts/applications.md`, `./contracts/gateway-verification.md`. **Spec:** `/Users/kalyankalvagadda/code/agent-shield/docs/design/todo/webhook-application-identity.md` (§9 UX flows, §11 test IDs, §12 gap ledger — all carried forward verbatim). **Constitution:** `/Users/kalyankalvagadda/code/agent-shield/CLAUDE.md`.

**Total tasks:** 46 (34 implementation + 12 checkpoint)
**Phases:** 15 (11 implementation + 4 checkpoint gates)
**Parallel opportunities:** noted inline with `[P]`
**Checkpoint phases:** CP1 (after Phase 3 — backend schema + delegation + applications API), CP2 (after Phase 5 — gateway cutover + trigger soft-auth), CP3 (after Phase 8 — full Studio UX), CP4 (inside Phase 12, between the tag bump and the gap-ledger task — final regression at the official shipped tags)

> **Deploy model note:** this repo has no local dev server — everything runs in-cluster via `bash scripts/deploy-cpe2e.sh` + Helm (`charts/agentshield/charts/registry-api/templates/deployment.yaml`'s `alembic-migrate` init container applies migrations automatically on every pod restart). Checkpoint deploy scripts assume a `docker-desktop` k8s cluster (namespace `agentshield-platform`) per the existing quickstart. **A checkpoint's deploy script is a verification gate, not the single official ship-deploy** — the ONE canonical, plan-matching image-tag bump (registry-api `0.2.210→0.2.211`, event-gateway `0.1.3→0.1.4`, studio `0.1.158→0.1.159`, confirmed still free against the live repo today) is tracked as its own task (T033) inside the Polish phase, exactly where plan.md places it and exactly at plan.md's exact numbers — never invented, never re-bumped. CP1/CP2/CP3 verify in-progress code by rebuilding+redeploying under whatever tag is current at that point in implementation (ordinary iterative dev-loop practice, not itself a tracked version-bump task); CP4 is the one checkpoint that runs against the **final, officially bumped** tags.

---

## Conventions

- **Task line:** `- [ ] [T0NN] [P] <description> — \`path/to/file\``, followed by **Do / Acceptance / Deps / Verify** bullets.
- **Checkpoint line:** `- [ ] [CPNx] <description> — \`scripts/<name>.sh\``, no `[P]` — checkpoints always run sequentially, and always after every task in their gate has landed.
- **`[P]`** = parallel-safe: files disjoint from sibling `[P]` tasks in the same phase, and dependencies already satisfied.
- **Granularity:** each task touches at most 1–3 closely related files. Plan.md tasks that bundled a migration+model, or a router+2 sibling test files, or 3 unrelated frontend files, are split below into their own T-numbers; the split is **only** a granularity change — no scope, ordering, or decision from plan.md/research.md is altered. Where a plan.md task's shape changed materially (not just split), it's flagged in-line with **[GRANULARITY SPLIT]**.
- **Every plan.md task (1–27) maps to at least one T### below** — see the mapping table at the end.

---

## Phase summary

| Phase | Tasks | Purpose | Gate |
|---|---|---|---|
| **2 — Foundational** | T001–T005 | Migration 0069/0070, `Application` ORM model, `rbac.py` extensions, new Pydantic schemas | — |
| **3 — Delegation + Applications APIs** | T006–T008 | Generic `artifact_role_grants` grants router, team-scoped `applications` router, router registration | **CP1** |
| **4 — Gateway Cutover** | T009–T011 | `event-gateway` reads `applications`/`artifact_role_grants` instead of `webhook_clients`; old registration endpoint retired (410) | — |
| **5 — Trigger Management Soft-Auth** | T012–T013 | `can_manage_artifact` wired into trigger/workflow-trigger CRUD, soft-enforced via `ENFORCE_TRIGGER_MGMT` | **CP2** |
| **6 — Studio API Client Layer** | T014–T015 | `Application*`/`ArtifactRoleGrant*` types + fetch functions | — |
| **7 — Shared Grant/Invoke Components** | T016–T018 | `InvokeAccessPanel`, `ArtifactGrantsList`, wired into `SettingsTab.tsx` (agent surface, `ClientPanel` retired) | — |
| **8 — Workflow Parity + Applications Page + Nav** | T019–T022 | Same components on `WorkflowTriggersPanel.tsx`, new `ApplicationsPage.tsx`, Sidebar/route, prop wiring | **CP3** |
| **9 — Vitest Coverage** | T023–T025 | Component tests for all new/changed Studio surfaces | — |
| **10 — Backend E2E Suites** | T026–T029 | Suite 82 (T-ARG-001…010), Suite 83 (T-SYY-001…010), `run-all.sh` registration, Suite 76 cutover rewrite | — |
| **11 — Playwright Specs** | T030–T032 | `artifact-grants.spec.ts`, `webhook-applications.spec.ts`, retire `webhook-clients.spec.ts` | — |
| **12 — Polish & Cross-Cutting** | T033 → **CP4** → T034 | Official tag bump → final regression at shipped tags → gap ledger + design-doc status update | **CP4** |

---

## Phase 2 — Foundational (schema, rbac, schemas)

- [ ] [T001] [P] Migration 0069 — `applications` table + widen `artifact_role_grants` CHECK constraints — `services/registry-api/alembic/versions/0069_applications_and_invoker_grants.py`
  - **Do:** new file, `revision="0069"`, `down_revision="0068"` (confirmed free — `0068_knowledge_base_rag.py` is head as of today). Full DDL from `data-model.md` §Migration 0069: `CREATE TABLE IF NOT EXISTS applications (id UUID PK default gen_random_uuid(), team_name VARCHAR(255), name VARCHAR(128), secret_encrypted TEXT, enabled BOOLEAN default true, created_by VARCHAR(255), created_at TIMESTAMPTZ default now(), rotated_at TIMESTAMPTZ NULL, UNIQUE(team_name, name) AS uq_applications_team_name)`, `CREATE INDEX IF NOT EXISTS idx_applications_team`; then two idempotent `DO $$ ... $$` blocks that `DROP CONSTRAINT IF EXISTS`/`ADD CONSTRAINT` on `ck_arg_grantee_type` (widen to `'user','team','application'`) and `ck_arg_role` (widen to `'agent-admin','approver','invoker'`), guarded by `pg_constraint` lookups exactly as shown in `data-model.md`. `downgrade()` re-narrows both CHECKs then drops the index/table.
  - **Acceptance:** `alembic upgrade head` (in-pod) reaches `0069`; re-running is a no-op; `SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname IN ('ck_arg_role','ck_arg_grantee_type')` shows the widened lists.
  - **Deps:** none.
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0069_applications_and_invoker_grants.py').read())"`

- [ ] [T002] [P] `Application` ORM model — `services/registry-api/models.py`
  - **Do:** insert immediately after `class WebhookClient(Base):` (confirmed today: starts at line 1765), before the `agent_events` section comment. Exact class body from `data-model.md`: `__tablename__ = "applications"`, `UniqueConstraint("team_name","name", name="uq_applications_team_name")`, fields `id/team_name/name/secret_encrypted/enabled/created_by/created_at/rotated_at` with the same `_UUID`/`_TSTZ`/`_NOW`/`_GEN_UUID` helpers `WebhookClient` already uses. No `relationship()` to `artifact_role_grants` (polymorphic `grantee_id`, raw-SQL-only table per `research.md` §3 — do not add an ORM model for `artifact_role_grants` itself).
  - **Acceptance:** `python3 -c "from main import app; import sqlalchemy.orm as o; o.configure_mappers()"` (in-pod) succeeds with `Application` alongside every existing model.
  - **Deps:** none (file-disjoint from T001; functionally needs the T001 table to exist only at runtime, not at code-write time).
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/models.py').read())"`

- [ ] [T003] Migration 0070 — backfill `webhook_clients` → `applications` + `invoker` grants — `services/registry-api/alembic/versions/0070_backfill_webhook_clients_to_applications.py`
  - **Do:** `revision="0070"`, `down_revision="0069"`. Two `op.execute()` passes verbatim from `data-model.md`: Pass 1 inserts one `applications` row per distinct `(team_name, client_name)` via `DISTINCT ON` (earliest `created_at` wins within a team), `ON CONFLICT (team_name, name) DO NOTHING`, `created_by` defaults to `'system:backfill-0070'` when null. Pass 2 inserts one `artifact_role_grants` row (`role='invoker'`, `grantee_type='application'`, `granted_by='system:backfill-0070'`) per webhook_clients row's resolved artifact, joined back through the now-current `applications` table (not a `RETURNING` set — safe on partial re-run), `ON CONFLICT (artifact_id, role, grantee_type, grantee_id) WHERE revoked_at IS NULL DO NOTHING` (matches `uq_arg_active_grant`'s exact partial-index shape). `downgrade()` deletes ONLY rows tagged `granted_by='system:backfill-0070'` / `created_by='system:backfill-0070'` — never a grant/application a human created through the new API.
  - **Acceptance:** row-count check from `quickstart.md` §2 passes (`backfilled_invoker_grants <= webhook_clients count`); re-running `alembic upgrade head` after reaching 0070 is a no-op; downgrade leaves a manually-created post-migration grant untouched (verify by creating one, downgrading, confirming survival).
  - **Deps:** T001 (needs the widened constraints + `applications` table to exist).
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0070_backfill_webhook_clients_to_applications.py').read())"`

- [ ] [T004] [P] `rbac.py` — widen `can_delegate_role`, add `can_create_application`, `can_manage_artifact`, `ENFORCE_TRIGGER_MGMT` — `services/registry-api/rbac.py`
  - **Do:** (1) `can_delegate_role`'s `target_role not in (...)` tuple widens from `("agent-admin","approver")` to also accept `"invoker"` — everything else in the function body unchanged. (2) Append, after `can_delegate_role`, before the `grant_creator_admin` section comment: `async def can_create_application(db, user_sub, team_name) -> bool` — `True` for platform-admin, else `True` only if global role ≥ contributor **and** `get_user_team(db, user_sub) == team_name` (stricter than `can_create_agent`, which has no team check at all — deliberate, per `research.md` §4). `async def can_manage_artifact(db, user_sub, artifact_id) -> bool` — body-identical to `can_deploy_to_production` (platform-admin bypass OR `has_artifact_role(agent-admin)`), under its own name because it gates a different action (trigger/webhook CRUD, not production deploy). (3) `ENFORCE_TRIGGER_MGMT: bool = False` module-level flag, same shape as the existing `ENFORCE = False` under `require_global_role` (confirmed at line 169 today).
  - **Preserve:** `has_artifact_role`, `can_deploy_to_production`, `can_approve_hitl`, `can_use_playground`, `can_create_agent`, `grant_creator_admin`, `get_user_artifact_roles`, `require_global_role` — **byte-for-byte unchanged**. Do not reshape `has_artifact_role`'s signature to a `grantee_type=`/`grantee_id=` form (the design doc's §6 pseudocode implies this, but it's explicitly out of scope — `research.md` §4).
  - **Acceptance:** `can_delegate_role(db, sub, artifact_id, "invoker")` → `True` for that artifact's agent-admin, `False` for a plain contributor, `True` for platform-admin regardless. `can_create_application` gate matches the team-membership rule above. `can_manage_artifact` returns identically to `can_deploy_to_production` for every input (direct equality check, not independent re-derivation). `ENFORCE_TRIGGER_MGMT` defaults `False`.
  - **Deps:** none (does not require the `applications` table — `research.md` §4).
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/rbac.py').read())"`

- [ ] [T005] [P] `schemas.py` — new Pydantic models for grants + applications — `services/registry-api/schemas.py`
  - **Do:** insert immediately after `WebhookClientUpdate` (the last webhook-related schema, right before `ErrorResponse`). Exact classes from plan.md "Key Interfaces": `ArtifactRoleGrantCreate` (`grantee_type` pattern `^(user|team|application)$`, `grantee_id` min_length 1, `role` pattern `^(agent-admin|approver|invoker)$`), `ArtifactRoleGrantResponse` (`id, artifact_type, artifact_id, role, grantee_type, grantee_id, granted_by, granted_at, revoked_at: datetime|None=None, grantee_label: str|None=None`), `ApplicationCreate` (`name`, 1–128 chars), `ApplicationCreatedResponse` (`id, name, secret, created_at`), `ApplicationResponse` (`from_attributes=True`; `id, team_name, name, enabled, created_by, created_at, rotated_at: datetime|None=None` — **no `secret` field, ever**), `ApplicationUpdate` (`enabled: bool`), `ApplicationRotateSecretResponse` (`id, secret, rotated_at`).
  - **Acceptance:** every field matches the router usage in T006/T007 exactly (FastAPI's own startup-time response-model validation crash-loops the pod on drift — same guard this repo already relies on for `response_model=None` 204s).
  - **Deps:** none.
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/schemas.py').read())"`

---

## Phase 3 — Generic Delegation API + Team Applications API

- [ ] [T006] [P] `routers/artifact_grants.py` — generic `POST/GET/DELETE /api/v1/artifacts/{artifact_type}/{artifact_id}/grants` — `services/registry-api/routers/artifact_grants.py`
  - **Do:** new file. `router = APIRouter(prefix="/api/v1/artifacts", tags=["artifact-grants"])`. Helpers `_resolve_artifact(db, artifact_type, artifact_id)` (422 if `artifact_type` not in `{"agent","workflow"}`; 404 if the row doesn't exist) and `_grantee_exists(db, grantee_type, grantee_id)` (user → `user_team_assignments`; team → `teams.name`; application → `applications.id`). `create_grant` (`require_user`, `can_delegate_role` gate, `IntegrityError` on `uq_arg_active_grant` → 409, unresolvable `grantee_id` → 400). `list_grants` (unauthenticated read, newest-first, `grantee_label` resolved from `applications.name` for application grantees only). `revoke_grant` (`require_user`, soft-delete `revoked_at=now()`, re-check `can_delegate_role` against the **target grant's own role**, not the caller's intent; a second `DELETE` on an already-revoked grant → 404). Raw SQL via `sqlalchemy.text()` against `artifact_role_grants` (no ORM model — per `research.md` §3). Full request/response/error contract: `contracts/artifact-grants.md`.
  - **Acceptance (behavioral proof deferred to T026):** T-ARG-001…009's shapes match this contract exactly.
  - **Deps:** T004 (`can_delegate_role`), T005 (schemas).
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/artifact_grants.py').read())"`

- [ ] [T007] [P] `routers/applications.py` — team-scoped applications CRUD + rotate-secret — `services/registry-api/routers/applications.py`
  - **Do:** new file. `router = APIRouter(prefix="/api/v1/teams/{team}/applications", tags=["applications"])`, `_SECRET_PREFIX = "whsec_"` (reused verbatim from `webhook_clients.py` — do not invent a new prefix, per `research.md` §10). `create_application` (`require_user` + `can_create_application`, secret Fernet-encrypted via `crypto.py`, `201` returns the ONLY shape carrying `secret`, `409` on `(team,name)` collision via `IntegrityError`, same pattern `webhook_clients.py::create_webhook_client` already uses). `list_applications` (`require_user` only, no team-membership check, never a `secret` field). `rotate_secret` (`require_user` + `can_create_application`, new secret returned once, `rotated_at` updated). `update_application` (`PATCH {enabled}` — kill switch). `delete_application` (`require_user` + `can_create_application`, hard delete, **same transaction** issues `DELETE FROM artifact_role_grants WHERE grantee_type='application' AND grantee_id=:id` — explicit app-code cascade since `grantee_id` is polymorphic `TEXT`, no DB FK possible). Full contract: `contracts/applications.md`.
  - **Acceptance (behavioral proof deferred to T027):** T-SYY-001's shape matches; kill switch / rotate / delete-cascades-grants match the contract's worked example.
  - **Deps:** T002 (`Application` model), T004 (`can_create_application`), T005 (schemas).
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/applications.py').read())"`

- [ ] [T008] `main.py` — register `artifact_grants_router` + `applications_router` — `services/registry-api/main.py`
  - **Do:** add `from routers.artifact_grants import router as artifact_grants_router` and `from routers.applications import router as applications_router` next to the existing `triggers_router`/`webhook_clients_router` imports (~line 70); add `app.include_router(artifact_grants_router)` and `app.include_router(applications_router)` next to the existing `include_router` calls for those two (~lines 184–187).
  - **Acceptance:** `GET /openapi.json` (in-pod) lists all 8 new paths (3 grants + 5 applications); pod starts cleanly.
  - **Deps:** T006, T007.
  - **Verify:** `curl -s http://localhost:8000/openapi.json | python3 -c "import sys,json; p=json.load(sys.stdin)['paths']; assert any('/artifacts/{artifact_type}/{artifact_id}/grants' in k for k in p); assert any('/teams/{team}/applications' in k for k in p); print('OK')"` (in-pod)

---

## Checkpoint 1 — Backend Schema + Delegation Foundation
_Gate: Phases 2–3 (T001–T008) must be complete. Run before starting Phase 4._
_What you prove: `applications` table + widened `artifact_role_grants` constraints are live; the generic grants endpoint and the applications endpoint both work end-to-end via real HTTP, including the RBAC gate and the 409/400/403 error paths — the first-ever live proof that `artifact_role_grants` delegation works at all (design doc §3)._

- [ ] [CP1a] Deploy script: build+push registry-api at a fresh dev tag, `helm upgrade`, wait for rollout — `scripts/deploy-cp1.sh`
  - Bumps a checkpoint-local dev tag for `registry-api` only (does NOT touch the tracked `REGISTRY_API_TAG` in `deploy-cpe2e.sh`/`values.yaml` — that's T033's job, once, at the plan's exact number), runs the equivalent of `bash scripts/deploy-cpe2e.sh` scoped to registry-api, then `kubectl -n agentshield-platform rollout status deploy/agentshield-registry-api --timeout=180s`.

- [ ] [CP1b] Infrastructure smoke test: pod health + migration head + schema shape — `scripts/smoke-test-cp1-infra.sh`
  - Asserts: `kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api` shows `Running`, no `CrashLoopBackOff`; `kubectl exec … -- alembic current` prints `0070 (head)`; in-pod `psql`/python check that `pg_get_constraintdef` for `ck_arg_role`/`ck_arg_grantee_type` contains `invoker`/`application`; `curl -s http://localhost:8000/openapi.json | jq '.paths | keys' | grep -c "artifacts\|applications"` ≥ 2 (in-pod curl, since the service isn't externally routed yet at this checkpoint).

- [ ] [CP1c] Behaviour smoke test: grant + application happy path + one failure case each — `scripts/smoke-test-cp1-behaviour.sh`
  - Gets a real platform-admin bearer token (`grant_type=password`, `platform-admin`/`PlatformAdmin2024`, `agentshield-studio` client, per quickstart.md §5a). `curl -X POST .../teams/platform/applications -d '{"name":"cp1-smoke-app"}'` → assert `201` and `secret` starts with `whsec_`. `curl -X POST .../artifacts/agent/{seed_agent_id}/grants -d '{"grantee_type":"application","grantee_id":"<id>","role":"invoker"}'` → assert `201`. Failure case: repeat the same grant POST → assert `409`. Failure case: `curl -X POST .../artifacts/agent/{id}/grants -d '{"grantee_type":"user","grantee_id":"nonexistent","role":"agent-admin"}'` → assert `400`.

> **To run:** `bash scripts/deploy-cp1.sh` → wait for pods → `bash scripts/smoke-test-cp1-infra.sh && bash scripts/smoke-test-cp1-behaviour.sh`
> **Pass criteria:** all assertions exit 0, no pod in `CrashLoopBackOff`, `alembic current` is `0070`.

---

## Phase 4 — Gateway Cutover

- [ ] [T009] `event-gateway/webhook_auth.py` — widen `_TRIGGER_SQL` + new port functions `lookup_application`/`has_active_invoker_grant` — `services/event-gateway/webhook_auth.py`
  - **Do:** `_TRIGGER_SQL` gains 2 selected columns per kind (`a.id::text, a.team` for `"agent"`; `w.id::text, w.team` for `"workflow"` — full before/after in `contracts/gateway-verification.md`). `lookup_triggers(kind, name)` injects `"artifact_type": kind` into each returned dict alongside the new `artifact_id`/`team_name` keys (resulting dict: `{id, token_hash, filter_conditions, auth_mode, workflow_id, artifact_id, team_name, artifact_type}`). New `def lookup_application(team_name, name) -> dict | None` (`SELECT id::text, secret_encrypted, enabled FROM applications WHERE team_name=%s AND name=%s`, NOT filtered on `enabled` — that's a separate explicit step so "unknown app" and "disabled app" log distinct reasons). New `def has_active_invoker_grant(artifact_type, artifact_id, application_id) -> bool` (`SELECT 1 FROM artifact_role_grants WHERE artifact_type=%s AND artifact_id=%s AND role='invoker' AND grantee_type='application' AND grantee_id=%s AND revoked_at IS NULL LIMIT 1`). Both are raw `psycopg2` ports — **no import from `registry-api`** (confirmed: separate `requirements.txt`, separate deployments — `research.md` §5, `contracts/gateway-verification.md` invariant 5).
  - **Acceptance:** the two new functions exist with exactly this signature/shape; `_TRIGGER_SQL`'s column count matches the 7-column "after" shape in `contracts/gateway-verification.md`.
  - **Deps:** T001, T003 (needs `applications`/widened `artifact_role_grants` to exist for the SQL to be meaningful at runtime — no code-level import dependency).
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/event-gateway/webhook_auth.py').read())"`

- [ ] [T010] `event-gateway/webhook_auth.py` — rewire `_verify_client_signed`, delete `lookup_webhook_client` — `services/event-gateway/webhook_auth.py`
  - **Do:** rewrite `_verify_client_signed(trigger, headers, raw_body)` to the exact 5-step order in `contracts/gateway-verification.md` (verbatim, do not reorder): (1) `lookup_application(trigger["team_name"], client_id)` → deny if `None`; (2) `has_active_invoker_grant(...)` → deny if `False`; (3) `app["enabled"]` → deny if `False`; (4) `_fresh(timestamp)` → deny if stale/missing; (5) decrypt secret, `hmac.compare_digest` HMAC check → deny on mismatch. Every deny path returns the existing `_DENY` sentinel via `_deny(reason, **ctx)` — no new response shape, byte-identical uniform-401 body across ALL reasons (T-SYY-005/006's byte-identity assertion depends on this). Delete `lookup_webhook_client` entirely (no caller remains after this rewire).
  - **Acceptance:** `grep -c "def verify_webhook_auth" services/event-gateway/webhook_auth.py` == `1`; `grep -c "lookup_webhook_client"` == `0`; every "What does NOT change" invariant in `contracts/gateway-verification.md` holds (fail-closed, uniform 401, constant-time compare, live read no cache, no cross-service import).
  - **Deps:** T009 (same file, sequential — not `[P]` with it).
  - **Verify:** `grep -c "def verify_webhook_auth" services/event-gateway/webhook_auth.py; grep -c "lookup_webhook_client" services/event-gateway/webhook_auth.py` (expect `1` then `0`)

- [ ] [T011] `routers/webhook_clients.py` — retire write endpoints (410 Gone) — `services/registry-api/routers/webhook_clients.py`
  - **Do:** `create_webhook_client`, `update_webhook_client`, `delete_webhook_client` handler bodies become the FIRST line of the function (before any DB access): `raise HTTPException(status_code=status.HTTP_410_GONE, detail="webhook_clients registration is retired. Use POST /api/v1/teams/{team}/applications to create a reusable application, then POST /api/v1/artifacts/{artifact_type}/{artifact_id}/grants with role='invoker' to authorize it — see docs/design/todo/webhook-application-identity.md.")`. `list_webhook_clients` (GET) is **untouched**. Update the module header docstring to note the retirement + point at the replacement, mirroring how the file's own header already documents its security invariants.
  - **Acceptance:** `POST/PATCH/DELETE /api/v1/triggers/{id}/clients...` → `410` with the redirect message; `GET .../clients` unchanged `200`. **This task must ship in the SAME deploy as T010** (CP2 gates both together) — never separately, since a live 201 from this endpoint after T010 ships but before this ships would be the exact silent-dead-end trap `research.md` §6 identifies.
  - **Deps:** none directly, but sequenced adjacent to T010 (same CP2 gate).
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/webhook_clients.py').read())"`

---

## Phase 5 — Trigger Management Soft-Auth

- [ ] [T012] [P] `routers/triggers.py` — soft-enforce `can_manage_artifact` on trigger CRUD — `services/registry-api/routers/triggers.py`
  - **Do:** `create_trigger`, `update_trigger`, `delete_trigger`, `rotate_token` each gain `claims: dict = Depends(require_user)` and, as the first line after resolving the agent: `if rbac.ENFORCE_TRIGGER_MGMT and not await rbac.can_manage_artifact(db, claims["sub"], agent.id): raise HTTPException(403, "agent-admin required to manage triggers on this agent")` `elif not await rbac.can_manage_artifact(...): logger.warning("trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)", claims["sub"], agent.id)` — mirrors `require_global_role`'s existing log-then-permit shape exactly.
  - **Acceptance:** with `ENFORCE_TRIGGER_MGMT=False` (default), a caller with no `agent-admin` grant still gets `201`/`200`/`204` with a server-side warning logged — the 16 pre-existing bash suites + 2 Playwright specs enumerated in `research.md` §5 keep passing unmodified (proven at CP2/CP4, not here). **Behavior-changing part:** `require_user` (not `get_optional_user`) is now hard-required — a request with NO bearer token at all gets `401` regardless of the flag (401 is always hard; only the 403 policy decision is soft). None of the 16 suites/2 specs send zero Authorization header on these 4 endpoints (they send `X-User-Sub` only, ignored by `require_user`, so they were never authenticated in the first place and remain unauthenticated-but-permitted) — verify this claim holds at CP4's regression sweep; if any call site is found sending literally no identifying header, that ONE call site needs a real bearer token added even under soft enforcement (`require_user`'s 401 is not soft).
  - **Deps:** T004 (`can_manage_artifact`, `ENFORCE_TRIGGER_MGMT`).
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/triggers.py').read())"`

- [ ] [T013] [P] `routers/composite_workflows.py` — same soft-enforcement for workflow triggers — `services/registry-api/routers/composite_workflows.py`
  - **Do:** identical shape to T012, applied to the workflow-trigger CRUD block (`create_workflow_trigger` L755, `update_workflow_trigger` L839, `delete_workflow_trigger` L870, `rotate_workflow_trigger_token` L894 — confirmed today), using the workflow's own `id` as `artifact_id` (not `agent.id`).
  - **Acceptance:** identical to T012's, scoped to workflow-trigger endpoints; `suite-34-workflow-triggers.sh` (calls these via `X-User-Sub` only) continues to pass unmodified under the default `ENFORCE_TRIGGER_MGMT=False`.
  - **Deps:** T004.
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/composite_workflows.py').read())"`

---

## Checkpoint 2 — Gateway Cutover + Trigger Soft-Auth
_Gate: Phases 4–5 (T009–T013) must be complete. Run before starting Phase 6._
_What you prove: a real signed webhook resolves through `applications`+`artifact_role_grants` end-to-end (happy path, revoked-grant, disabled-app — byte-identical uniform 401s), the retired `webhook_clients` write endpoints 410, and trigger CRUD still works unauthenticated (soft-enforcement, warning-only) — the exact "ships together" requirement from `research.md` §6._

- [ ] [CP2a] Deploy script: build+push registry-api + event-gateway at fresh dev tags, `helm upgrade`, wait for rollout — `scripts/deploy-cp2.sh`
  - Same dev-tag discipline as CP1a, now for BOTH `registry-api` and `event-gateway`; `kubectl -n agentshield-platform rollout status deploy/agentshield-registry-api --timeout=180s && kubectl -n agentshield-platform rollout status deploy/agentshield-event-gateway --timeout=180s`.

- [ ] [CP2b] Infrastructure smoke test: both pods healthy, gateway reachable, 410 live — `scripts/smoke-test-cp2-infra.sh`
  - Asserts both pods `Running`; `curl -sk -X POST https://agentshield.127.0.0.1.nip.io:8443/api/v1/triggers/{seed_trigger_id}/clients -H "Authorization: Bearer $TOKEN" -d '{}'` → `410` with body containing `"webhook_clients registration is retired"`; `curl -sk https://agentshield.127.0.0.1.nip.io:8443/hooks/{seed_agent}/{bad-token}` → `401` (gateway reachable at all).

- [ ] [CP2c] Behaviour smoke test: signed-webhook happy path + revoked-grant + disabled-app (byte-identical 401s) — `scripts/smoke-test-cp2-behaviour.sh`
  - Creates an application + grants it `invoker` on a seed agent's webhook trigger (reusing CP1's flow). Signs and POSTs to `/hooks/{agent}/{token}` with the real HMAC recipe from `quickstart.md` §5d → assert `202`. `DELETE .../grants/{id}` (revoke) → re-POST same signed request → assert `401`, capture body. `PATCH .../applications/{id} {"enabled":false}` on a second still-granted application → re-POST → assert `401`, capture body → `diff` the two response bodies, assert byte-identical (T-SYY-005/006's core claim). Assert `agent_events` row exists with `status='matched'` for the happy path (in-pod `psql`).

> **To run:** `bash scripts/deploy-cp2.sh` → wait for pods → `bash scripts/smoke-test-cp2-infra.sh && bash scripts/smoke-test-cp2-behaviour.sh`
> **Pass criteria:** all assertions exit 0, no pod in `CrashLoopBackOff`, the two 401 bodies are byte-identical.

---

## Phase 6 — Studio API Client Layer

- [X] [T014] [P] `Application`/`ApplicationCreated`/rotate-secret types + client functions — `studio/src/api/registryApi.ts`
  - **Do:** insert immediately after the existing `WebhookClient`/webhook-client functions section (WebhookClient interfaces at ~L1341–1410 today). Exact shapes from plan.md "Key Interfaces": `interface Application { id, team_name, name, enabled, created_by, created_at, rotated_at: string | null }`, `interface ApplicationCreated { id, name, secret, created_at }`, `interface ApplicationRotateSecretResponse { id, secret, rotated_at }`; `createApplication(team, {name})`, `listApplications(team)`, `rotateApplicationSecret(team, id)`, `setApplicationEnabled(team, id, enabled)`, `deleteApplication(team, id)` — path/body/response shapes cross-checked line-for-line against `contracts/applications.md`.
  - **Acceptance:** `cd studio && npm run typecheck` passes; every function's request/response shape matches T007's router exactly.
  - **Deps:** T005 (schema shapes locked) — file-disjoint from all backend tasks.
  - **Verify:** `cd studio && npm run typecheck`

- [X] [T015] `ArtifactRoleGrant` types + client functions — `studio/src/api/registryApi.ts`
  - **Do:** insert immediately after T014's block. `interface ArtifactRoleGrant { id, artifact_type: 'agent'|'workflow', artifact_id, role: 'agent-admin'|'approver'|'invoker', grantee_type: 'user'|'team'|'application', grantee_id, granted_by, granted_at, revoked_at: string|null, grantee_label: string|null }`; `createGrant(artifactType, artifactId, {grantee_type, grantee_id, role})`, `listGrants(artifactType, artifactId)`, `revokeGrant(artifactType, artifactId, grantId)` — cross-checked against `contracts/artifact-grants.md`.
  - **Acceptance:** `cd studio && npm run typecheck` passes; shapes match T006's router exactly.
  - **Deps:** T005, T014 (same file — sequential, not `[P]` with it).
  - **Verify:** `cd studio && npm run typecheck`

---

## Phase 7 — Shared Grant/Invoke Components (Agent Surface)

- [X] [T016] [P] `InvokeAccessPanel.tsx` (new) — application invoker grant-picker, artifact-type-agnostic from the start — `studio/src/components/shared/InvokeAccessPanel.tsx`
  - **Do:** `InvokeAccessPanelProps { artifactType: 'agent'|'workflow'; artifactId: string; artifactTeam: string }`. Fetches `listApplications(artifactTeam)` for picker options and `listGrants(artifactType, artifactId)` filtered to `role === 'invoker'`. "Grant access" button → picker (application dropdown) → confirm shows the exact design-doc §9.4 step 3 unattended-execution acknowledgment text ("… will be able to trigger runs on … without a human present. Approval-gated steps … may stall if nobody is watching.") → `createGrant(artifactType, artifactId, {grantee_type:'application', grantee_id, role:'invoker'})`. Empty state (zero team applications) shows the exact design-doc §9.8 copy: "No applications registered for your team yet." + link to `/applications`. Each granted row shows an "application disabled" badge when `Application.enabled === false` (cross-referenced from the same `listApplications` fetch — no second lookup). Revoke button → `revokeGrant`. Built here **once**, generic — Task T019 only *consumes* it for the workflow surface, never re-creates it (closes the "two parallel paths" pattern this codebase has already been burned by).
  - **Acceptance:** empty-state copy exact; granting an application flips the trigger card's existing `auth_mode` badge (`token`→`client_signed`) on the next `listTriggers` refetch — no new badge code needed, the badge already reads `trigger.auth_mode`.
  - **Deps:** T014, T015.
  - **Verify:** `cd studio && npm run typecheck`

- [X] [T017] [P] `ArtifactGrantsList.tsx` (new) — full grants list (all 3 roles, all 3 grantee types), artifact-type-agnostic — `studio/src/components/shared/ArtifactGrantsList.tsx`
  - **Do:** `ArtifactGrantsListProps { artifactType: 'agent'|'workflow'; artifactId: string }`. Lists ALL active grants via `listGrants(artifactType, artifactId)` — `agent-admin`/`approver`/`invoker` mixed, each row showing `role`, `grantee_type`, `grantee_label ?? grantee_id`, a revoke button (`revokeGrant`). This is also where a human `agent-admin`/`approver` grant is now managed (design doc §9.2) — not `invoker`-only.
  - **Acceptance:** renders mixed-role rows correctly; revoke removes a row on the next refetch.
  - **Deps:** T014, T015 (file-disjoint from T016 — parallel-safe with it).
  - **Verify:** `cd studio && npm run typecheck`

- [X] [T018] Wire `InvokeAccessPanel`/`ArtifactGrantsList` into `SettingsTab.tsx`; delete `ClientPanel` — `studio/src/components/agent-detail/SettingsTab.tsx`
  - **Do:** `SettingsTab` gains two new required props `agentId: string`, `agentTeam: string` (alongside existing `agentName`/`memoryEnabled`). **Delete** the `ClientPanel` function (confirmed today: starts at line 339) and its call site inside `WebhookRow`; render `<InvokeAccessPanel artifactType="agent" artifactId={agentId} artifactTeam={agentTeam} />` in `ClientPanel`'s old slot inside `WebhookRow`, and `<ArtifactGrantsList artifactType="agent" artifactId={agentId} />` once near the top of `SettingsTab`'s return, above the Webhook Triggers card (design doc §9.2 placement).
  - **Acceptance:** empty-applications state renders correctly; granting flips the existing `auth_mode` badge; `cd studio && npm run typecheck` passes.
  - **Deps:** T016, T017.
  - **Verify:** `cd studio && npm run typecheck`

---

## Phase 8 — Workflow Parity + Applications Page + Nav

- [X] [T019] Wire the shared components into `WorkflowTriggersPanel.tsx` (closes the agent-only parity gap) — `studio/src/components/workflow/WorkflowTriggersPanel.tsx`
  - **Do:** `WorkflowTriggersPanel` gains a new required prop `workflowTeam: string` (alongside existing `workflowId`/`workflowName`/`onClose`). Import `InvokeAccessPanel`/`ArtifactGrantsList` from `studio/src/components/shared/` — this task only **consumes** them, creates nothing new under `shared/`. Render `<ArtifactGrantsList artifactType="workflow" artifactId={workflowId} />` near the top of the modal body, `<InvokeAccessPanel artifactType="workflow" artifactId={workflowId} artifactTeam={workflowTeam} />` inside each `WebhookRow`'s slot (mirrors `SettingsTab`'s T018 placement).
  - **Acceptance:** identical to T018's acceptance, scoped to the workflow surface — this is the exact parity gap design doc §9.2 calls out ("today this panel has no client/application UI at all").
  - **Deps:** T016, T017, **T018** (imports the shared components T018 first wires up the pattern for — not parallel-safe with T018, despite disjoint files).
  - **Verify:** `cd studio && npm run typecheck`

- [X] [T020] [P] `ApplicationsPage.tsx` (new) — team-scoped applications CRUD page — `studio/src/pages/ApplicationsPage.tsx`
  - **Do:** modeled directly on `studio/src/pages/CredentialsPage.tsx` (`research.md` §11 — the correct contributor-writable, team-scoped, reveal-once-secret precedent; NOT the platform-admin-only `AdminAccessPage.tsx`). Team-scoped list defaulting to `useAuth().team`, create form (name only), reveal-once secret box on create/rotate (same copy-and-warn pattern as `CredentialsPage`/former `ClientPanel`), enable/disable toggle, delete with confirmation.
  - **Acceptance:** creating shows the secret exactly once; navigating away and back never re-shows it (design doc §9.3 step 3); lists only the current user's own team by default.
  - **Deps:** T014.
  - **Verify:** `cd studio && npm run typecheck`

- [X] [T021] Sidebar entry + `/applications` route — `studio/src/components/Sidebar.tsx`, `studio/src/App.tsx`
  - **Do:** **Sidebar** — add `{ label: "Applications", to: "/applications", icon: Boxes }` (or another `lucide-react` icon distinct from the existing `KeyRound` "Credentials" entry) to the same Settings-section array that already holds `Models`/`Credentials`. **App** — add `<Route path="/applications" element={<ApplicationsPage />} />` alongside the existing `/credentials` route, with **no** `RequireRole` wrapper (matches `/credentials`'s own unwrapped route — the real gate is server-side `can_create_application`, not a client-side hide, per this repo's established pattern).
  - **Acceptance:** `Applications` appears in the Settings nav section; `/applications` renders `ApplicationsPage`; `cd studio && npm run typecheck` passes.
  - **Deps:** T020.
  - **Verify:** `grep -n '"/applications"' studio/src/App.tsx; grep -n "Applications" studio/src/components/Sidebar.tsx`

- [X] [T022] Wire new required props into `AgentDetailPage.tsx` / `WorkflowBuilderPage.tsx` call sites — `studio/src/pages/AgentDetailPage.tsx`, `studio/src/pages/WorkflowBuilderPage.tsx`
  - **Do:** `AgentDetailPage` — the `<SettingsTab agentName={agent.name} memoryEnabled={agent.memory_enabled} />` call site gains `agentId={agent.id} agentTeam={agent.team}` (both already present on the in-scope `Agent` object — no new fetch). `WorkflowBuilderPage` — the `<WorkflowTriggersPanel workflowId={compositeWorkflowId} workflowName={compositeWorkflowName ?? 'workflow'} onClose={...} />` call site (confirmed today at line 912) gains `workflowTeam={currentTeam || authTeam || ''}` (both variables already in scope, used identically a few lines above for `AddAgentModal`'s own `team` prop).
  - **Acceptance:** `cd studio && npm run typecheck` passes — a missing required prop is a compile error, the concrete proof this wiring isn't orphaned.
  - **Deps:** T018, T019.
  - **Verify:** `cd studio && npm run typecheck`

---

## Checkpoint 3 — Studio UX Complete
_Gate: Phases 6–8 (T014–T022) must be complete. Run before starting Phase 9._
_What you prove: the Studio build compiles clean with every new prop/type/component wired to a live caller, and the backend flow the UI depends on (create app → grant invoker → auth_mode flips) still works end-to-end via curl — a proxy for the real-browser proof Playwright supplies later in Phase 11._

- [ ] [CP3a] Deploy script: build+push studio (+ registry-api/event-gateway if not already at CP2's tags) at fresh dev tags, `helm upgrade`, wait for rollout — `scripts/deploy-cp3.sh`
  - `kubectl -n agentshield-platform rollout status deploy/agentshield-studio --timeout=180s`. Studio's Docker build runs `tsc && vite build` — a TypeScript error fails the build here, this IS the type gate for T014–T022.

- [ ] [CP3b] Infrastructure smoke test: studio pod healthy, `/applications` route serves — `scripts/smoke-test-cp3-infra.sh`
  - Asserts studio pod `Running`; `curl -sk -o /dev/null -w "%{http_code}" https://agentshield.127.0.0.1.nip.io:8443/applications` → `200` (SPA shell serves for any client route); `curl -sk -o /dev/null -w "%{http_code}" https://agentshield.127.0.0.1.nip.io:8443` → `200`.

- [ ] [CP3c] Behaviour smoke test: full create-app → grant-invoker → auth_mode-flip flow via curl (UI-flow proxy) — `scripts/smoke-test-cp3-behaviour.sh`
  - Reuses `quickstart.md` §5 steps b–c verbatim: create an application under a real team, grant it `invoker` on a seed agent's trigger, then `GET .../triggers` and assert `auth_mode == "client_signed"` on that trigger (design doc §9.4 step 4 — this is exactly what `InvokeAccessPanel`'s acceptance criteria in T016/T018 rely on the backend already doing).

> **To run:** `bash scripts/deploy-cp3.sh` → wait for pods → `bash scripts/smoke-test-cp3-infra.sh && bash scripts/smoke-test-cp3-behaviour.sh`
> **Pass criteria:** all assertions exit 0, no pod in `CrashLoopBackOff`, `auth_mode` flips to `client_signed`.

---

## Phase 9 — Vitest Coverage

- [X] [T023] [P] Vitest — `InvokeAccessPanel`/`ArtifactGrantsList` states on the agent surface — `studio/src/components/agent-detail/SettingsTab.test.tsx`
  - **Do:** mock `registryApi` per `vi.mock('../api/registryApi')`, render via `renderWithProviders`. Cover: `InvokeAccessPanel` empty state, grant-and-list, revoke, "application disabled" badge; `ArtifactGrantsList` render with mixed roles (`agent-admin`/`approver`/`invoker` together).
  - **Acceptance:** `cd studio && npm run test -- SettingsTab` green.
  - **Deps:** T018.
  - **Verify:** `cd studio && npm run test -- SettingsTab`

- [X] [T024] [P] Vitest — `ApplicationsPage` — `studio/src/pages/ApplicationsPage.test.tsx`
  - **Do:** cover: create shows secret once, list renders without a secret field, enable/disable toggle, delete confirmation.
  - **Acceptance:** `cd studio && npm run test -- ApplicationsPage` green.
  - **Deps:** T020.
  - **Verify:** `cd studio && npm run test -- ApplicationsPage`

- [X] [T025] [P] Vitest — `WorkflowTriggersPanel` parity (same states as T023, proving the SHARED component works under `workflow` too) — `studio/src/components/workflow/WorkflowTriggersPanel.test.tsx`
  - **Do:** cover the same `InvokeAccessPanel`/`ArtifactGrantsList` states as T023, mounted with `artifactType="workflow"` — this is what makes T019's parity claim testable, not just visually plausible.
  - **Acceptance:** `cd studio && npm run test -- WorkflowTriggersPanel` green.
  - **Deps:** T019.
  - **Verify:** `cd studio && npm run test -- WorkflowTriggersPanel`

---

## Phase 10 — Backend E2E Suites

- [X] [T026] Suite A — `scripts/e2e/suite-82-artifact-grants.sh` (T-ARG-001…010) — `scripts/e2e/suite-82-artifact-grants.sh`
  - **Do:** bash + in-pod Python/`httpx` driver against `http://localhost:8000/api/v1`, following `suite-78-conversations.sh`'s pattern for real Keycloak tokens (`grant_type=password`, `agentshield-studio` client). Creates its OWN scoped test users via `POST /api/v1/admin/users` (as `platform-admin`) for the agent-admin / plain-contributor / second-team-member personas — since only `platform-admin`/`agent-reviewer` are pre-seeded — and clears `requiredActions`/sets a non-temporary password immediately after creation, per the exact `quickstart.md` §3 recipe (load-bearing: skipping either PUT makes `grant_type=password` 400 and the suite falsely SKIP instead of FAIL).
  - **Test cases (verbatim IDs — do not renumber):**

    | ID | Assertion |
    |---|---|
    | T-ARG-001 | agent-admin grants `agent-admin` to another user → `201`, row exists |
    | T-ARG-002 | agent-admin grants `approver` to a team → `201`; a team member's direct `rbac.has_artifact_role` call (in-pod, not a second HTTP round trip) returns `True` |
    | T-ARG-003 | agent-admin grants `invoker` to an application they own → `201` |
    | T-ARG-004 | contributor with no scoped role attempts any grant → `403` |
    | T-ARG-005 | platform-admin grants a role on an artifact with no prior `agent-admin` grant → `201` |
    | T-ARG-006 | `DELETE .../grants/{id}` → `204`; subsequent `has_artifact_role` → `False`; subsequent `GET .../grants` excludes it |
    | T-ARG-007 | grant a role outside `{agent-admin, approver, invoker}` → `422` |
    | T-ARG-008 | grant to an unresolvable `grantee_id` (unknown user sub / team / application id) → `400` |
    | T-ARG-009 | grant the same `(artifact, role, grantee)` twice → `409` on the second attempt |
    | T-ARG-010 | cleanup: delete every fixture agent/user/application/grant this suite created |
  - **Acceptance:** `bash scripts/e2e/suite-82-artifact-grants.sh` prints all `RESULT … PASS`, exit 0.
  - **Deps:** T006, T008 (endpoint registered), T004, T005.
  - **Verify:** `bash scripts/e2e/suite-82-artifact-grants.sh`

- [X] [T027] Suite B — `scripts/e2e/suite-83-webhook-applications.sh` (T-SYY-001…010) — `scripts/e2e/suite-83-webhook-applications.sh`
  - **Do:** same in-pod driver pattern as T026; additionally uses the `sign_webhook` AST-extraction technique `suite-76` already established (extract the real signer function from `services/event-gateway/webhook_auth.py` at runtime rather than hand-copying it, so the suite can never silently drift from the product's actual signing behavior).
  - **Test cases (verbatim IDs):**

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
  - **Acceptance:** `bash scripts/e2e/suite-83-webhook-applications.sh` all-PASS, exit 0.
  - **Deps:** T026 (Suite A validates the delegation endpoint Suite B's grant steps reuse — run order matters), T007, T008, T010 (gateway cutover).
  - **Verify:** `bash scripts/e2e/suite-83-webhook-applications.sh`

- [X] [T028] Register Suite 82 + Suite 83 in `run-all.sh` — `scripts/e2e/run-all.sh`
  - **Do:** append immediately after `run_suite "Suite 81: Deploy-time tool-access auto-grant" "suite-81-deploy-tool-autograt.sh"` (confirmed today at line 138):
    ```bash
    run_suite "Suite 82: Artifact Delegation Foundation (grants API)"  "suite-82-artifact-grants.sh"
    run_suite "Suite 83: Webhook Applications (invoker grants)"       "suite-83-webhook-applications.sh"
    ```
  - **Acceptance:** `bash scripts/e2e/run-all.sh` runs Suite 82 before Suite 83, both in the final pass/fail tally.
  - **Deps:** T026, T027.
  - **Verify:** `grep -A2 "Suite 81" scripts/e2e/run-all.sh`

- [X] [T029] Update `suite-76-webhook-client-signing.sh` for the cutover — `scripts/e2e/suite-76-webhook-client-signing.sh`
  - **Do:** every setup step currently calling `POST /api/v1/triggers/{id}/clients` is replaced with `POST /api/v1/teams/{team}/applications` (create) → `POST /api/v1/artifacts/{agent|workflow}/{id}/grants` `{grantee_type:"application", grantee_id, role:"invoker"}` (grant), using a REAL bearer token (`platform-admin` password-grant pattern, borrowed from `suite-78` — the new endpoints are hard-enforced). Every existing assertion (T-S76-000…009) preserved in spirit, retargeted to fire on the new setup mechanism (e.g. T-S76-009's "upgrade to `client_signed`" now fires on the first `invoker` GRANT, not the first client registration). **New** T-S76-010: `POST /api/v1/triggers/{id}/clients` → `410`, body contains the redirect message from T011.
  - **Acceptance:** `bash scripts/e2e/suite-76-webhook-client-signing.sh` green with the SAME 10 original claims (0–9) still true, plus T-S76-010 — the concrete proof the gateway cutover (T010) didn't quietly break WS-4's own acceptance gate.
  - **Deps:** T011 (410 retirement), T010 (gateway cutover), T027 (establishes the exact application-create + invoker-grant setup pattern this task reuses).
  - **Verify:** `bash scripts/e2e/suite-76-webhook-client-signing.sh`

---

## Phase 11 — Playwright Specs

- [ ] [T030] [P] Playwright — `artifact-grants.spec.ts` (human-grantee delegation UI) — `studio/e2e/artifact-grants.spec.ts`
  - **Do:** real Keycloak login via `global-setup.ts` (not a raw `pwRequest` bypass — this exercises the hard-enforced new endpoint). Flow: open an agent's Settings → use `ArtifactGrantsList`'s grant UI (T017) to grant `agent-admin` or `approver` to a user or team → assert the grant appears via `page.waitForResponse` on `POST .../grants` → **reload the page** → assert the grant survived (save→reload→assert, DoD rule #2). First-ever Playwright coverage of delegation at all.
  - **Acceptance:** fails against pre-T018 code (no `ArtifactGrantsList` UI to click), passes once T018 ships.
  - **Deps:** T018, T020 (test-user fixtures may reuse `/applications`), T008 (endpoint live).
  - **Verify:** `cd studio && npx playwright test artifact-grants.spec.ts`

- [ ] [T031] [P] Playwright — `webhook-applications.spec.ts` (agent flow + workflow parity flow) — `studio/e2e/webhook-applications.spec.ts`
  - **Do:** real Keycloak session, two `test()` cases sharing the same fixture-creation setup: (1) **Agent flow** — `/applications` → create → assert secret shown once → agent Settings → grant `invoker` via `InvokeAccessPanel` (T016) → assert it appears in the trigger card's Invoke Access list → **reload** → assert the grant survived and the secret is NOT re-displayed anywhere (structural guarantee — `ApplicationResponse` has no `secret` field). (2) **Workflow flow (parity)** — same/second application → workflow builder → Workflow Triggers → grant `invoker` via the same shared `InvokeAccessPanel` now mounted in `WorkflowTriggersPanel.tsx` (T019) → assert it appears → **reload** the builder page → assert survived.
  - **Acceptance:** save→reload→assert on application creation AND on each of the two grants independently (DoD rule #2 applied per write surface).
  - **Deps:** T018, T019, T020.
  - **Verify:** `cd studio && npx playwright test webhook-applications.spec.ts`

- [ ] [T032] Retire `webhook-clients.spec.ts` — `studio/e2e/webhook-clients.spec.ts` (delete)
  - **Do:** `git rm studio/e2e/webhook-clients.spec.ts` — the UI it drove (`ClientPanel`, deleted in T018) no longer exists; its assertions would fail on a missing element, not a real regression. `webhook-applications.spec.ts` (T031) is its full replacement. Confirm `webhook-public-url.spec.ts` is **unaffected** and left in place (tests trigger-URL correctness + the token-mode gateway path only — `research.md` §5).
  - **Acceptance:** `git status studio/e2e/webhook-clients.spec.ts` shows deleted; `grep -c "ClientPanel" studio/src/components/agent-detail/SettingsTab.tsx` returns `0`.
  - **Deps:** T018, T031 (the replacement must exist before the original is removed — never a zero-coverage window even transiently in the same commit).
  - **Verify:** `git status studio/e2e/webhook-clients.spec.ts; grep -c "ClientPanel" studio/src/components/agent-detail/SettingsTab.tsx`

---

## Phase 12 — Polish & Cross-Cutting

- [X] [T033] Official image tag bumps — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
  - **Do:** `deploy-cpe2e.sh`: `REGISTRY_API_TAG "0.2.210"→"0.2.211"` (L351), `STUDIO_TAG "0.1.158"→"0.1.159"` (L390), `EVENT_GATEWAY_TAG "0.1.3"→"0.1.4"` (L395) — all confirmed still-current/free against the live repo as of today. Add a new header changelog-comment entry summarizing this feature (matches the file's own established convention). `values.yaml`: mirror all three at their **top-level** `tag:` lines (registry-api L648, studio L992, event-gateway L768 — confirmed today; the event-gateway **sub-chart** pin at L150 is deliberately left alone — it's shadowed by a stale packaged `.tgz`, a pre-existing, documented condition per `docs/testing/manual-ui-e2e-test-plan.md`'s gap ledger, not something this task should "fix").
  - **Acceptance:** `grep 'tag: "0.2.211"' charts/agentshield/values.yaml`, `grep 'tag: "0.1.159"' charts/agentshield/values.yaml`, `grep 'tag: "0.1.4"' charts/agentshield/values.yaml` all match; `grep REGISTRY_API_TAG scripts/deploy-cpe2e.sh` shows `0.2.211`.
  - **Deps:** T001–T032 (every code-change task — this is the last code task; run once all changes are final).
  - **Verify:** `grep -c "0.2.211\|0.1.159\|0.1.4\"" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml`

---

## Checkpoint 4 — Final Regression at the Official Tags
_Gate: T033 (tag bump) complete, and Phases 9–11 (T023–T032) complete. Run before T034 — this IS plan.md's Task 26 regression sweep, promoted to a checkpoint._
_What you prove: the FULL, officially-tagged stack (`registry-api:0.2.211` / `event-gateway:0.1.4` / `studio:0.1.159`) deploys clean and every impacted suite/spec — new and pre-existing — is green. Blast radius per `plan.md` Task 26: the 16 bash suites + `webhook-public-url.spec.ts` that call trigger CRUD (T012/T013's soft-auth), `suite-76`+`suite-77-eval-v2-webhook` (webhook_clients retirement / gateway cutover), `WorkflowTriggersPanel.test.tsx`/`workflow-builder.spec.ts` (new shared components), full Studio typecheck/build (every frontend task)._

- [ ] [CP4a] Deploy script: `bash scripts/deploy-cpe2e.sh` full rebuild+redeploy at the now-official tags — `scripts/deploy-cp4.sh`
  - Wraps `bash scripts/deploy-cpe2e.sh` (uses the tags T033 just set — the one and only deploy in this whole plan that uses the tracked, official version numbers); `kubectl -n agentshield-platform rollout status` for all three deployments.

- [ ] [CP4b] Infrastructure smoke test: all 3 pods on the correct tag, migration head, openapi paths — `scripts/smoke-test-cp4-infra.sh`
  - Asserts `kubectl get deploy/agentshield-registry-api -o jsonpath='{.spec.template.spec.containers[0].image}'` contains `0.2.211` (same for event-gateway `0.1.4`, studio `0.1.159`); `alembic current` → `0070 (head)`; all pods `Running`.

- [ ] [CP4c] Behaviour/regression smoke test: full impacted-suite sweep — `scripts/smoke-test-cp4-behaviour.sh`
  - Runs, and asserts exit 0 on each: `bash scripts/e2e/run-all.sh` (full backend sweep — includes the 16 potentially-impacted suites + Suites 82/83 + rewritten Suite 76); `bash scripts/studio-e2e.sh` (full Playwright sweep — includes `workflow-builder.spec.ts`, `scheduled-overview.spec.ts`, `webhook-public-url.spec.ts`, plus the 3 new/changed specs); `cd studio && npm run test && npm run typecheck`. Any failure outside T026/T027/T029/T030/T031's own new/modified suites is a genuine regression — root-cause it per this repo's bug-fixing discipline (check whether T012's `require_user` addition is the cause, per the flagged risk in T012) before this checkpoint can pass.

> **To run:** `bash scripts/deploy-cp4.sh` → wait for pods → `bash scripts/smoke-test-cp4-infra.sh && bash scripts/smoke-test-cp4-behaviour.sh`
> **Pass criteria:** all assertions exit 0, no pod in `CrashLoopBackOff`, zero new failures across `run-all.sh` + `studio-e2e.sh` + Vitest + typecheck.

- [ ] [T034] Gap ledger + design-doc status update — `docs/testing/manual-ui-e2e-test-plan.md`, `docs/design/todo/webhook-application-identity.md`
  - **Do:** in `manual-ui-e2e-test-plan.md`, insert a new `## Known gaps (webhook application identity — Decision 30) — <ship date>` section right after the header (before the existing newest-first entries), listing exactly:
    - **not-yet-wired (debt):** bounded unattended-approval fallback policy (design doc §12) — `invoker` grants are functionally live without it; an application-triggered run hitting a HITL checkpoint sits `awaiting_approval` with no faster notification than the existing failure-alert path. **Must land before this is relied on for anything production-critical.**
    - **not-yet-wired (debt):** `rbac.ENFORCE_TRIGGER_MGMT = False` — the real `can_manage_artifact` check runs and logs a warning on every would-deny, but does not yet 403. Flipping it requires migrating the 16 e2e suites + `webhook-public-url.spec.ts` to real bearer tokens first (`research.md` §5).
    - **deferred (intentional):** cross-team application reuse; split manage-user vs. manage-application RBAC capability; tool-level `invoker` granularity finer than whole-artifact; role audit log beyond `granted_by`/`granted_at`/`revoked_at` — all four carried verbatim from design doc §12.
    - **deferred (intentional):** `webhook_clients` table + router drop — kept read for one release per §10 step 5; POST/PATCH/DELETE already 410 (T011), GET still live; the table drop itself is a follow-up migration.
    - **pre-existing debt, inherited, not introduced here:** `require_global_role`'s `ENFORCE=False` (orthogonal, admin-route gating only); `can_deploy_to_production`/`can_approve_hitl` remain uncalled by the production-deploy and HITL-decide endpoints (this design's grants endpoint proves the underlying `has_artifact_role`/`can_delegate_role` machinery works, but that's a different code path).

    In `docs/design/todo/webhook-application-identity.md`, update the `**Status**: Draft (design only — nothing in this doc is implemented yet)` header line to `**Status**: Implemented (registry-api 0.2.211 / event-gateway 0.1.4 / studio 0.1.159 — see docs/testing/manual-ui-e2e-test-plan.md for verification)` (or whatever tags actually shipped at merge time, if T033 was re-bumped after this task was drafted — never leave a stale version number).
  - **Acceptance:** every deferred/debt item from design doc §12 appears, correctly tagged; the design doc's status header no longer reads "nothing in this doc is implemented yet."
  - **Deps:** CP4 (this task describes what actually shipped — must run last).
  - **Verify:** `grep -A3 "Known gaps (webhook application identity" docs/testing/manual-ui-e2e-test-plan.md`

---

## Mapping — plan.md Task → tasks.md T###/CP#

| plan.md Task | tasks.md | Note |
|---|---|---|
| 1 | T001, T002 | split: migration DDL / ORM model |
| 2 | T003 | |
| 3 | T004 | |
| 4 | T005 | |
| 5 | T006 | |
| 6 | T007 | |
| 7 | T008 | |
| 8 | T012 | |
| 9 | T013 | |
| 10 | T011 | |
| 11 | T009, T010 | split: new port fns / rewire+delete |
| 12 | T014, T015 | split: Application* / ArtifactRoleGrant* |
| 13 | T016, T017, T018 | split: 2 new components / wire into SettingsTab |
| 14 | T019 | |
| 15 | T020, T021 | split: ApplicationsPage / Sidebar+App route |
| 16 | T022 | |
| 17 | T023, T024, T025 | split by target component |
| 18 | T026 | |
| 19 | T027 | |
| 20 | T028 | |
| 21 | T029 | |
| 22 | T030 | |
| 23 | T031 | |
| 24 | T032 | |
| 25 | T033 | positioned in Polish per the task-generation brief; CP1a/CP2a/CP3a use their own untracked dev-loop tags to enable earlier checkpoint deploys — see the "Deploy model note" at the top |
| 26 | CP4 (CP4a/b/c) | promoted from a bare verification gate to a formal checkpoint, per the task-generation brief's mandatory-checkpoint rule |
| 27 | T034 | |

---

## Dependency graph (backend spine)

```
T001 ─┬─► T003 ─────────────────────────────────────┐
T002 ─┤                                              │
T004 ─┼─► T006 ─┐                                    │
T005 ─┴─► T007 ─┴─► T008 ─► CP1 ─┬─► T009 ─► T010 ─┐  │
                                  ├─► T011 ─────────┼──┼─► CP2 ─┬─► T014 ─► T015 ─► T016,T017 ─► T018 ─► T019 ──┐
                                  └─► T012, T013 ───┘  │        │                                              │
                                                        └────────┘                                    T020 ─► T021
                                                                                                          │      │
                                                                                          T018,T019 ─► T022 ◄────┘
                                                                                                          │
                                                                                                        CP3 ─┬─► T023,T024,T025 (Vitest)
                                                                                                             ├─► T026 ─► T027 ─► T028
                                                                                                             │            └─► T029
                                                                                                             └─► T030,T031 ─► T032
                                                                                                                              │
                                                                                                                          T033 ─► CP4 ─► T034
```

## MVP critical path (suggested first target)

**Thinnest vertical slice proving the whole design works (one grantee kind, one artifact, real traffic):** `T001→T002→T004→T005→T006→T008→CP1→T009→T010→T011→CP2` — this alone proves the hardest, highest-risk part: a real signed webhook resolving through `applications`+`artifact_role_grants` instead of `webhook_clients`, with byte-identical uniform-401 failure modes. **Target CP2 as the first milestone** — everything past it (Studio UX, Vitest, Playwright, the two bash suites, polish) is surface area on top of an already-proven backend, and can proceed in any reasonable order after CP2 is green (T012/T013 trigger soft-auth is independent of CP2's own gate and could run in parallel with Phase 6+ Studio work if desired, since both only depend on T004).

**Full path to ship:** CP2 → Phase 6-8 (Studio) → CP3 → Phase 9-11 (tests/specs) → T033 → CP4 → T034.

## Parallel batches (`[P]` = disjoint files, deps met)

- **Batch A (Phase 2 kickoff):** T001 ‖ T002 ‖ T004 ‖ T005 — four disjoint files, all deps-free.
- **Batch B (Phase 3):** T006 ‖ T007 — disjoint new router files.
- **Batch C (Phase 4/5, post-CP1):** T009→T010→T011 (same-file/coupled chain) ‖ T012 ‖ T013 (disjoint router files, both only depend on T004).
- **Batch D (Phase 7, post-Phase 6):** T016 ‖ T017 — disjoint new component files.
- **Batch E (Phase 8):** T020 ‖ (T019, once T018 lands) — disjoint files.
- **Batch F (Phase 9):** T023 ‖ T024 ‖ T025 — three disjoint test files.
- **Batch G (Phase 11):** T030 ‖ T031 — disjoint spec files (T032 waits on T031).

---

## Known gaps carried into this plan (see T034 for the full ledger written into the repo)

- **Bounded unattended-approval fallback policy** — not-yet-wired (debt), the one item design doc §12 marks as must-land-before-production-critical-reliance. Not addressed by this task list (explicitly out of scope per design doc §3 non-goals).
- **`ENFORCE_TRIGGER_MGMT` stays `False` after this plan ships** — not-yet-wired (debt). T012/T013 wire the real check and log every would-deny; flipping to hard-enforce is future work gated on migrating 16 suites + 2 specs off `X-User-Sub`-only auth.
- **`can_deploy_to_production`/`can_approve_hitl` remain uncalled** — pre-existing, not addressed here (design doc §3 non-goal, confirmed still true by `research.md`).

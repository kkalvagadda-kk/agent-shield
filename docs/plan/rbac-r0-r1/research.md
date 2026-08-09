# RBAC R0 + R1 — Research & Decisions

Every rationale below cites `file:line` read from the running repo, not from a design doc.

---

## D1 — Bootstrap lives in registry-api's `lifespan`

**Decision:** the platform-admin bootstrap runs as a background task started from registry-api's `lifespan` hook.

**Rationale:** it is the only process holding both `KEYCLOAK_ADMIN_PASSWORD` (`charts/agentshield/charts/registry-api/templates/deployment.yaml:135-139`) and a DB session factory (`db.py:46`), and it already runs two background tasks from `lifespan` (`main.py:131,140`).

**Rejected:** an init container or Job — needs a second copy of both credentials and cannot re-run on realm recreation. Helm — that is the mechanism being removed (Decision 40).

---

## D2 — `pg_try_advisory_lock` on a dedicated connection

**Decision:** reuse the existing single-flight pattern with a new lock key.

**Rationale:** the pattern exists verbatim at `mcp_health.py:190-199` with a documented reason — the lock is session-scoped, so acquire and unlock must ride the same connection, and holding one open transaction pins the PgBouncer server backend through transaction pooling. Key derivation copies `mcp_health.py:39-45`'s `crc32 & 0x7FFFFFFF | (1<<62)`, which is itself keyed off `services/scheduler/ha.py`'s 31-bit fire locks so the spaces cannot collide. Computed values: bootstrap `4611686019521751996`, health sweep `4611686020559757442`.

**Rejected:** leader election / K8s Lease — new infra for a once-per-start operation.

---

## D3 — Lookup by username, never a stored `sub`

**Decision:** the bootstrap finds the admin by Keycloak username on every attempt.

**Rationale:** `scripts/seed-platform-admin-role.sh:12-17` documents the exact failure of the alternative — *"when the Keycloak realm is later recreated the platform-admin gets a NEW `sub`, the old assignment strands on the dead `sub`, and the Admin menu silently vanishes (Observed 2026-07-20)"*. Username is stable across realm recreation; `sub` is not. Self-healing is then a property of the design, not a script someone remembers to run.

**Rejected:** storing the `sub` in config or a table — strands identically.

---

## D4 — Extend `list_users` with `username`/`exact` rather than filtering a 500-row page

**Decision:** add optional `username` + `exact` params to `keycloak_client.list_users`.

**Rationale:** `list_users` (`:52-61`) caps at 500. Client-side filtering would, past that cap, make the bootstrap fail to find an existing admin and attempt a create, which Keycloak rejects with 409 — an infinite red-`/ready` loop. Two added query params are backward compatible; the only existing caller is `admin_users.py:140` (`kc_list()`), which passes nothing.

**Rejected:** filtering `list_users()` in the bootstrap (latent cliff at 500 users). A separate `get_user_by_username` function (a second lookup path when one already exists).

---

## D5 — The bootstrap reconciles profile fields on *every* attempt, and sets the password only on create

**Decision:** step 7 always calls `update_user(email, emailVerified=True, firstName, lastName, enabled=True, requiredActions=[])`; the password is set only when the user is created.

**Rationale — load-bearing.** `keycloak_client.create_user` (`:91-95`) writes `"temporary": True` and `requiredActions: ["UPDATE_PASSWORD"]` and never sets `emailVerified`. `charts/agentshield/templates/realm-init-job.yaml:249-253` carries the measured consequence in a comment: *"email/emailVerified/firstName/lastName are REQUIRED: Keycloak's declarative user profile (VERIFY_PROFILE action) rejects a direct-grant login with 'Account is not fully set up' when these profile fields are missing, even with requiredActions=[]"*. `scripts/e2e/suite-96-schedules-endpoint.sh:174-178` independently records hitting that same wall.

If the bootstrap used `create_user` unmodified, `scripts/e2e/lib/e2e-auth.sh:64-79`'s password grant would fail for every suite, and `studio/e2e/global-setup.ts:31-35` would land on a password-reset interstitial instead of the app.

Reconciling on every attempt (not just create) makes a hand-edited admin self-heal. *Not* resetting the password on an existing user means an operator rotation survives a restart.

**Rejected:** adding `temporary`/`required_actions` parameters to `create_user` — more surface than needed, and `update_user` already does partial PUTs (proven by `admin_users.py:206-210` sending only `enabled`/`firstName`/`lastName`).

---

## D6 — `emailVerified: True` in `create_user` itself

**Decision:** every user created through the platform API gets `emailVerified: true`.

**Rationale:** the platform has no email-verification flow anywhere — no Keycloak SMTP is configured in the chart — so a user created through `POST /api/v1/admin/users` could never clear `VERIFY_PROFILE`, making FR-10's fixture (and every admin-created user) unable to log in. The chart already set it for exactly this reason.

**Assumption the spec did not state:** the realm's `verifyEmail` stays false (the realm is created with only `enabled` + `displayName`, `realm-init-job.yaml:136-140`), so this is about profile completeness, not an email round-trip.

---

## D7 — Bootstrap failure is non-fatal; `/ready` red until success

**Decision:** failure holds `/ready` at 503 and retries; it never aborts startup.

**Rationale:** spec OQ-1, resolved 2026-08-04. The `wait-for-keycloak` init container (`deployment.yaml:35-46`) waits only for the **master** realm to answer — the `agentshield` realm is created later by a `post-install` Helm hook at weight 10, so registry-api is routinely running before the realm exists. Aborting startup deadlocks a fresh install.

**Rejected:** ready-after-N-attempts with a degraded flag — trades a visible failure for an invisible one, and buys availability nobody is consuming (the platform is not live).

---

## D8 — Compensating `kc_delete`, not a two-phase commit

**Decision:** `kc_create → set_user_realm_role → _upsert_team → commit`, and on any failure after `kc_create`, roll back the DB and delete the Keycloak user.

**Rationale:** Keycloak has no transaction to enlist. This ordering makes Keycloak the resource that can be undone and Postgres the one that decides. `get_db` (`db.py:70-79`) commits *after* the handler returns — too late to compensate — which is precisely why the commit must move into the handler, and therefore why `_upsert_team` must stop committing.

**Rejected:** a `commit: bool` flag on `_upsert_team` — the priority-fallthrough pattern CLAUDE.md forbids, and Decision 41 names it explicitly.

---

## D9 — An unrecognized role keeps rank 0

**Decision:** only a *missing row* raises. An unrecognized *value* is returned verbatim and ranks 0, exactly as today.

**Rationale:** `routers/approvals.py:48` sets `_DEFAULT_REVIEWER_SCOPE = "agent:reviewer"` and `_caller_roles` (`:266-280`) reads it out of `user_team_assignments.role`. `_caller_roles` does **not** go through `get_user_global_role`, so FR-5's exception does not touch daemon-approval routing — but treating unknown values as corruption would have. `scripts/e2e/suite-71-scheduled-e2e.sh:325` writes that literal and T-S71-002b asserts a 403 keyed on it.

**Rejected:** a `CHECK` constraint on `role` — drags R5's HITL authority rewrite into R0 (Decision 42 option B).

---

## D10 — FR-11 exempts four route groups

**Decision:** `require_user` is applied to every route on the ten routers *except* five route groups with verified in-cluster machine callers.

**Evidence (all read from the repo):**

| Caller | File:line | Route |
|---|---|---|
| `deploy-controller` | `main.py:33` | `GET /api/v1/versions/{id}` |
| `deploy-controller` | `main.py:54` | `PATCH /api/v1/deployments/{id}` |
| `deploy-controller` | `main.py:70,122,176` | `GET /api/v1/deployments/` |
| `deploy-controller` | `tool_secrets.py:36` | `GET /api/v1/agents/{name}/tools` |
| `deploy-controller` | `tool_secrets.py:45` | `GET /api/v1/auth-configs/{id}/secret-ref` |
| `declarative-runner` | `main.py:410,437,148`, `checkpoint.py:27`, `orchestrator.py:35` | `/api/v1/agent-runs*` |
| `declarative-runner` | `workflow_executor.py:171` | `GET /api/v1/agents/{name}/tools` |
| `eval-runner` | `main.py:1254` | `POST /api/v1/agent-runs/{id}/steps` |

None sends an `Authorization` header — a grep for `headers|Authorization|Bearer` across `deploy-controller/main.py` returns nothing, and `eval-runner`'s `_EVAL_HEADERS` (`main.py:148`) is `{"X-User-Sub": "eval-runner"}`, which `scripts/e2e/lib/e2e-auth.sh:36-38` documents as an audit stamp, never an authentication.

The exemption matches the platform's existing documented posture for service traffic (`routers/internal_mcp.py:1-18`: *"cluster-internal, NetworkPolicy-trusted (unauthenticated)"*).

**Rejected:** TokenReview on inbound SA tokens (new RBAC + changes to three services). Keycloak service accounts per service (Decision 29's territory). A loopback exemption — CLAUDE.md rule 7 forbids weakening a control to silence a symptom, and it would make the guard trivially bypassable from any pod.

---

## D11 — A canary test, not a comment, holds the exemption set

**Decision:** T-S97-011 walks `app.routes` and asserts the protected/exempt partition programmatically.

**Rationale:** `docs/design/rbac-and-artifact-authorization.md:75-86` (§1.3) is a list of policy functions that were built and never called — the doc's own explanation is that the orphan check "was never run". A prose exemption list decays the same way. A canary makes adding an unauthenticated route to one of the ten fail a test rather than a review.

---

## D12 — `e2e_ensure_reviewer` in the shared lib, not four copies

**Decision:** one helper in `scripts/e2e/lib/e2e-auth.sh`, called by the four suites.

**Rationale:** `scripts/e2e/lib/e2e-auth.sh:24-32` argues this case for itself — *"fifteen copies of one decision is precisely the pattern `routers/webhook_clients.py` and `agent_endpoints.py` both carry postmortems about"*. Four copies of a fixture that must stay in step with `POST /admin/users`' contract is the same shape.

---

## D13 — `suite-96` T-S96-002 is rewritten, not deleted

**Decision:** rewrite the case to assert refusal; keep its evidence line.

**Rationale:** Decision 39 records five correct tests invalidated by a contract change and the instructive one — T-S95-004 — which would have passed *vacuously*. `schedules.py:157`'s `else` becomes unreachable after FR-5 (`team_name` is `NOT NULL` per migration `0013:20`, so "no team" ⟺ "no row"), so an unchanged assertion would either error or, if softened, prove nothing.

---

## D14 — Team hard-coded to `platform`

**Decision:** `ADMIN_TEAM = "platform"`, not a chart value.

**Rationale:** spec OQ-3 option (a). Matches current data and every e2e fixture (`suite-53`, `suite-71`, `suite-82` all use `'platform'`). Making it a chart value adds surface for a value nothing varies.

**Assumption:** `user_team_assignments.team_name` has no FK (migration `0013` declares none), so the row does not require a `teams` row to exist first.

---

## Assumptions the spec did not specify

| # | Assumption | Basis |
|---|---|---|
| a | `pydantic-settings` case-insensitivity maps `PLATFORM_ADMIN_PASSWORD` → `settings.platform_admin_password` | matches how `KEYCLOAK_URL` already works |
| b | `PUT /admin/realms/{r}/users/{id}` merges a partial `UserRepresentation` | relied on by `update_user`, already exercised by `admin_users.patch_user` |
| c | `PUT .../reset-password` with `temporary: false` clears `UPDATE_PASSWORD` | the plan does not depend on it — step 7 sets `requiredActions=[]` explicitly afterwards |
| d | `set_user_realm_role` silently no-ops when the role object is absent | `keycloak_client.py:183` `if role_name in role_map`; today's state per G-R0-4, so step 8 raising on *transport* failure does not make a missing realm-role object fatal |
| e | The `keycloak-user-passwords` Secret keeps its `agent-reviewer` key even though the chart stops reading it | `scripts/deploy-cpe2e.sh:587-591` and `scripts/deploy-eks.sh:271` both create it; `Reviewer2024` remains the suites' shared constant |

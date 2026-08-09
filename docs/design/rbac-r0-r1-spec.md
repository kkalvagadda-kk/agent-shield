# RBAC R0 + R1 — Platform-Owned User Identity & Router Authentication

**Status**: SHIPPED — R0 `0.2.260`, R1 `0.2.261` (2026-08-05). Verified state below is as-designed unless a gap says otherwise.
**Date**: 2026-08-04
**Author**: Kalyan + Claude
**Version**: 1.0.0
**Parent**: [`rbac-and-artifact-authorization.md`](rbac-and-artifact-authorization.md) — this spec implements its Phases **R0** (new) and **R1**
**Scope note**: this is a *scoped* spec. `docs/spec.md` remains the platform architecture document; its `## Authorization Model` section carries a pointer here.

---

## Problem Statement

RBAC is built and wired in this platform, then switched off. Before enforcement can be turned on
(Phase R2), one thing must be true that is not true today: **a user without a role must be
impossible, not merely unusual.** Today `rbac._normalize_role(None)` invents `contributor` for any
caller with no `user_team_assignments` row, the `role` column re-invents the legacy value
`operator` for any insert that omits it, and ten routers accept requests with no authentication at
all. R0 removes the invented states; R1 closes the unauthenticated doors. Neither turns
enforcement on — that is R2, and it is safe only after these land.

---

## Decisions this spec encodes

**There is no auto-provisioning.** Users are created by the platform, never from the IdP. Keycloak
is an implementation detail. **`platform-admin` is the only auto-created user, and platform code
creates it — not the Helm chart.** A missing `user_team_assignments` row is therefore not a *kind
of user*; it is data corruption, and it must fail loud.

---

## Verified state — measured on `test-cluster-964-10086`, 2026-08-04

Every claim below was read from running code or the live cluster, not from a design doc.

### V-1 — Three independent producers of an invented role

| # | Producer | Mechanism | Evidence |
|---|---|---|---|
| 1 | Python, missing row | `_normalize_role(None) → "contributor"` | `rbac.py:41-43` |
| 2 | **Postgres, role omitted** | `role` column `server_default="operator"` | `alembic/versions/0013_user_team_assignments.py` |
| 3 | Python, unknown value | `ROLE_HIERARCHY.get(role, 0)` → rank 0, silently | `rbac.py:118,122,142` |

Producer 2 was not in the original R0 scope and is the more interesting one. Migration `0044`
migrated the data `operator → contributor`; `0075` finished the job for `viewer → consumer`.
**Neither touched the column default**, so every insert that omits `role` re-introduces the exact
legacy value those migrations existed to remove. The live cluster proves it: `agent-reviewer`
carries `role='operator'`, and `suite-53-cost-tracking.sh:44` inserts
`(user_sub, team_name)` with no role at all.

### V-2 — Nothing in the install seeds an assignment row

`charts/agentshield/templates/realm-init-job.yaml` creates the Keycloak users `platform-admin` and
`agent-reviewer` with `kcadm.sh` and writes **no** row. `scripts/seed-platform-admin-role.sh`
exists solely to patch this after the fact; its own header says *"Nothing else in the install seeds
that row."* It covers `platform-admin` only. `agent-reviewer`'s row on the live cluster was created
by a **test fixture** (`suite-82`'s `T-ARG-FIXTURE-000b`), not by the platform.

Consequence recorded honestly: when the realm is recreated, the admin gets a new `sub`, the old row
strands, and the Studio Admin menu silently disappears. Observed 2026-07-20.

### V-3 — `POST /api/v1/admin/users` is not atomic

`admin_users.py:157` calls `kc_create(...)`; `:168` calls `_upsert_team(...)`, which runs its own
`await db.commit()`. No spanning transaction, no compensating delete. A DB failure after Keycloak
succeeds leaves an orphan user — precisely the state R0 exists to eliminate.
`set_user_realm_role` failure is swallowed by `except Exception: pass`.

### V-4 — Ten routers have no authentication at all

`deployments.py`, `agent_runs.py`, `workflows.py`, `auth_configs.py`, `versions.py`, `teams.py`,
`llm_providers.py`, `agent_tools.py`, `admin.py`, `playground_approvals.py` take only
`Depends(get_db)`. registry-api installs no global auth middleware. `auth_configs` and
`llm_providers` are credential-bearing. Full matrix: parent doc §1.4.

### V-5 — `user_team_assignments.role` holds two different kinds of value

Not corruption — **design**. `approvals.py:48` sets `_DEFAULT_REVIEWER_SCOPE = "agent:reviewer"`,
and `_caller_roles` (`:266`) matches it against `user_team_assignments.role`. WS-2 T011 uses that
column as a namespace for **reviewer scopes** alongside the three global roles. So the column is a
union of `{global role} ∪ {reviewer scope}`, and `ROLE_HIERARCHY.get(role, 0) == 0` for
`agent:reviewer` is not a bug — it is the only thing preventing a reviewer-scope holder from being
treated as a contributor.

This is the Decision-32 lesson (one field, two meanings) sitting in the RBAC foundation. It bounds
what R0 may safely do — see FR-6.

### V-7 — Four of the ten routers have in-cluster machine callers that send no JWT

Found during planning, 2026-08-04, by reading the calling services rather than the routers. This
**corrects FR-11's original wording** ("router-level `require_user` on the ten routers"), which
would have broken control-plane reconciliation on deploy.

| Caller | File:line | Route | Gap |
|---|---|---|---|
| `deploy-controller` | `main.py:33` | `GET /api/v1/versions/{id}` | G-R1-3 |
| `deploy-controller` | `main.py:54` | `PATCH /api/v1/deployments/{id}` | G-R1-2 |
| `deploy-controller` | `main.py:70,122,176` | `GET /api/v1/deployments/` | G-R1-2 |
| `deploy-controller` | `tool_secrets.py:36` | `GET /api/v1/agents/{name}/tools` | G-R1-5 |
| `deploy-controller` | `tool_secrets.py:45` | `GET /api/v1/auth-configs/{id}/secret-ref` | G-R1-4 |
| `declarative-runner` | `main.py:410,437,148`, `checkpoint.py:27`, `orchestrator.py:35` | `/api/v1/agent-runs*` | G-R1-1 |
| `declarative-runner` | `workflow_executor.py:171` | `GET /api/v1/agents/{name}/tools` | G-R1-5 |
| `eval-runner` | `main.py:1254` | `POST /api/v1/agent-runs/{id}/steps` | G-R1-1 |

**None sends an `Authorization` header.** A grep for `headers|Authorization|Bearer` across
`deploy-controller/main.py` returns nothing; `eval-runner`'s `_EVAL_HEADERS` (`main.py:148`) is
`{"X-User-Sub": "eval-runner"}`, which `scripts/e2e/lib/e2e-auth.sh:36-38` documents as an audit
stamp, never an authentication.

Giving them credentials is **service identity**, owned by
`identity-propagation-architecture.md` (migrations `0080–0082`) and out of scope here. So these
five route groups stay unauthenticated, each named individually in the gap ledger, and a canary
test (`suite-97` T-S97-011) walks `app.routes` to assert the exemption set is exactly this — so a
*new* unauthenticated route on one of the ten fails a test rather than a review.

### V-6 — Cluster state after the one-time cleanup

Audit run 2026-08-04 found 6 Keycloak users / 5 rows: 3 orphan users (`probe-7f01b0`,
`probe2-9b802f`, `s96-nobody-f56546`) and 2 stale rows (`s53-user-fd3a092a` — not a UUID;
`58833c93-…` holding the reviewer scope `agent:reviewer`). **All five were e2e test litter; no real
user lacked a role.** They were deleted. The table is now 3 users / 3 rows, 1:1.

The litter regenerates: `suite-53:49` and `suite-71:325` recreate their rows on every run. Cleaning
is necessary, not sufficient — FR-7 fixes the producers.

---

## User Scenarios & Testing

### User Story 1 — A fresh install has a working admin, with no manual step (Priority: P1)

An operator runs `scripts/deploy-cpe2e.sh` against an empty cluster. When registry-api reports
ready, `platform-admin` can log into Studio and see the Admin menu — without anyone running a seed
script.

**Why this priority**: it is the invariant everything else rests on. If the platform cannot
guarantee its own admin, no enforcement decision downstream is trustworthy.

**Independent Test**: fresh namespace, `helm install`, log in. No `seed-*` invocation.

**Acceptance Scenarios**:
1. **Given** an empty cluster, **When** registry-api starts, **Then** Keycloak holds a
   `platform-admin` user **and** `user_team_assignments` holds a row for its `sub` with
   `role='platform-admin'`.
2. **Given** a running platform, **When** registry-api restarts, **Then** the bootstrap is a no-op
   — no duplicate user, no changed `assigned_at` beyond the upsert.
3. **Given** two registry-api replicas starting simultaneously, **When** both run bootstrap,
   **Then** exactly one row exists and exactly one Keycloak user was created.
4. **Given** the Keycloak realm is recreated (admin gets a new `sub`), **When** registry-api
   restarts, **Then** the row re-pins onto the live `sub` and the Admin menu is present after login.
   *This is the regression test for the 2026-07-20 incident and MUST fail against current code.*

---

### User Story 2 — A half-created user cannot exist (Priority: P1)

A platform-admin creates a user through Studio. If any step fails, no partial user remains.

**Why this priority**: this is the other producer of the state R0 exists to remove. Fixing the
bootstrap while leaving the API able to mint orphans would be theatre.

**Independent Test**: force the DB write to fail, assert Keycloak has no leftover user.

**Acceptance Scenarios**:
1. **Given** a valid request, **When** `POST /api/v1/admin/users` succeeds, **Then** the Keycloak
   user, its realm role, and its assignment row all exist.
2. **Given** a request where the DB write fails, **When** creation is attempted, **Then** the
   Keycloak user is deleted and the API returns 5xx — no orphan.
3. **Given** a request where `set_user_realm_role` fails, **When** creation is attempted, **Then**
   it is treated as a failure, not swallowed.

---

### User Story 3 — An unknown caller is refused, loudly (Priority: P1)

A request arrives bearing a valid JWT whose `sub` has no assignment row. The platform refuses it
and says so in the log, instead of quietly treating the caller as a contributor.

**Why this priority**: it is what makes R2's flag flip meaningful. Without it, enforcement gates on
a role the platform invented.

**Independent Test**: mint a token for a `sub` with no row; call any role-resolving endpoint.

**Acceptance Scenarios**:
1. **Given** a `sub` with no row, **When** `GET /api/v1/me` is called, **Then** 403 with a stable
   error code and a log line naming the `sub`.
2. **Given** an insert that omits `role`, **When** it runs, **Then** it fails — the column no longer
   supplies `operator`.
3. **Given** a row holding a reviewer scope (`agent:reviewer`), **When** global role is resolved,
   **Then** behaviour is **unchanged** from today (rank 0), because V-5 makes that value legitimate.

---

### User Story 4 — Anonymous requests reach nothing (Priority: P1)

An unauthenticated request to any of the ten routers is refused before it touches the database.

**Independent Test**: `curl` each route with no `Authorization` header.

**Acceptance Scenarios**:
1. **Given** no credentials, **When** any route on the ten routers is called, **Then** 401.
2. **Given** a valid Studio JWT, **When** the same route is called, **Then** behaviour is byte
   identical to before R1 — this is authentication only, no role logic.

---

### Edge Cases

- **Keycloak not ready when registry-api starts.** Expected on a fresh install. Bootstrap must not
  crash-loop the pod; it retries and holds `/ready` red until it succeeds.
- **Two replicas race the bootstrap.** Advisory lock; the loser returns immediately.
- **Realm recreated while the platform runs.** Detected on next start only. Between recreation and
  restart, the admin is an orphan and — after FR-5 — is refused. Accepted: recreating a realm under
  a running platform is an operator action with an operator-visible consequence.
- **The admin's Keycloak user is deleted by hand.** Next restart recreates it with a new `sub`; the
  stale row survives as litter. See G-R0-3.
- **An insert omits `role` after FR-4.** Fails with a NOT NULL violation rather than inventing one.
  `suite-53` is the known caller; FR-7 fixes it.

---

## Requirements

### Functional Requirements

| ID | Priority | Requirement | Acceptance Criteria |
|----|----------|-------------|-------------------|
| **FR-1** | P1 | Platform code, invoked from registry-api's `lifespan`, creates the `platform-admin` Keycloak user and its assignment row as one unit on first init | Story 1 scenarios 1–2 |
| **FR-2** | P1 | The bootstrap is single-flighted across replicas using a Postgres advisory lock, reusing the pattern at `mcp_health.py:172-193` | Story 1 scenario 3 |
| **FR-3** | P1 | The bootstrap identifies the admin by **username**, so a recreated realm self-heals onto the new `sub` | Story 1 scenario 4 |
| **FR-4** | P1 | `user_team_assignments.role` loses its `server_default`; every insert states a role | Story 3 scenario 2; migration `0079` |
| **FR-5** | P1 | `get_user_global_role` raises on a missing row instead of returning an invented role; an exception handler maps it to 403 with a stable code. `me.py` stops importing `_normalize_role` directly — one resolution path, not two | Story 3 scenario 1 |
| **FR-6** | P1 | An **unrecognized** role value keeps today's behaviour (rank 0, no exception). The role/scope union (V-5) is documented, not "fixed", in R0 | Story 3 scenario 3 |
| **FR-7** | P1 | `suite-53` states a role on insert; `suite-71`'s reviewer-scope row is annotated as a deliberate scope, not corruption | both suites green, audit re-run finds zero litter |
| **FR-8** | P1 | `POST /api/v1/admin/users` becomes atomic: `_upsert_team` no longer commits internally (callers own the transaction boundary), and a compensating `kc_delete` runs if the row write fails | Story 2 scenarios 1–3 |
| **FR-9** | P1 | `realm-init-job.yaml` stops creating users; it keeps realm + client setup. `seed-platform-admin-role.sh` is demoted to a manual repair tool | fresh install passes Story 1 with no seed script |
| **FR-10** | P1 | `suite-76`, `suite-78`, `suite-82`, `suite-83` create `agent-reviewer` themselves via `POST /api/v1/admin/users` | four suites green against a chart that no longer creates it |
| **FR-11** ✅ | P1 | `require_user` on the ten routers in V-4 — **except five route groups with verified in-cluster machine callers** (see V-7). Router-level where the whole router is protectable; per-endpoint where it is not | Story 4 scenarios 1–2, plus a canary asserting the exemption set is exactly V-7's |
| **FR-12** | P2 | A read-only audit surface reports Keycloak users with no row, and rows with no Keycloak user | reproduces the V-6 table on demand |

### Non-Functional Requirements

| Attribute | Target | How Achieved |
|-----------|--------|-------------|
| Startup latency | Bootstrap adds < 2s to a warm start | One Keycloak lookup + one upsert; skipped entirely by replicas that lose the lock |
| Availability | A Keycloak outage must not crash-loop registry-api | Bootstrap failure is non-fatal to the process; `/ready` goes red and the attempt retries |
| Security | No credential path widens | registry-api already holds `KEYCLOAK_ADMIN_PASSWORD`; the only addition is the admin's initial password from the existing `keycloak-user-passwords` Secret |
| Observability | An admin who cannot log in has exactly one place to look | Bootstrap logs outcome at INFO, failure at ERROR with the `sub`; 403s from FR-5 name the `sub` |
| Idempotence | Re-running is a no-op | Keycloak lookup-by-username + `ON CONFLICT (user_sub) DO UPDATE` |

### Integration Points

| System | Direction | Protocol | Purpose |
|--------|-----------|----------|---------|
| Keycloak Admin API | Outbound | REST, master-realm admin token | Create/lookup the admin user, set its realm role |
| Postgres | Outbound | SQLAlchemy async | Assignment row; advisory lock |
| Langfuse | Indirect | — | **Constraint only**: trace access is authorized by project membership keyed on email |
| Helm chart | Config | values/Secret | `keycloak-user-passwords` moves from the realm-init Job to registry-api |

### Key Entities

| Entity | Description | Key Attributes | Relationships |
|--------|-------------|---------------|---------------|
| `user_team_assignments` | One global role (or reviewer scope, V-5) per subject | `user_sub` (PK, = Keycloak user id = JWT `sub`), `team_name`, `role` | 1:1 with a Keycloak user, *by convention* — which is exactly what R0 makes enforceable |
| Keycloak user | The authentication record | `id` (→ `user_sub`), `username`, `email` | created only by platform code or `POST /admin/users` |

---

## Architecture

### System Diagram

```
                          registry-api pod (×2 replicas)
   ┌──────────────────────────────────────────────────────────────┐
   │  lifespan (main.py:104)                                      │
   │      │                                                       │
   │      ├─► bootstrap_admin.ensure_platform_admin()   ── NEW    │
   │      │        │                                              │
   │      │        ├── pg_try_advisory_lock(BOOTSTRAP_KEY)        │
   │      │        │     (dedicated conn; pattern from            │
   │      │        │      mcp_health.py:172-193)                  │
   │      │        │        └── not acquired → return             │
   │      │        │                                              │
   │      │        ├── kc: find user by USERNAME 'platform-admin' │
   │      │        │     absent → create (email PINNED)           │
   │      │        ├── kc: set_user_realm_role(sub,               │
   │      │        │                 'platform-admin')            │
   │      │        └── db: upsert (sub, team, 'platform-admin')   │
   │      │                                                       │
   │      └─► /ready ── red while bootstrap has not succeeded     │
   │                                                              │
   │  rbac.get_user_global_role()  ── the ONE resolution path     │
   │      no row → raise NoPlatformRole → 403 + log               │
   │                                                              │
   │  10 routers  ── dependencies=[Depends(require_user)]         │
   └──────────────────────────────────────────────────────────────┘

   charts/…/realm-init-job.yaml → realm + clients ONLY (user blocks deleted)
   scripts/seed-platform-admin-role.sh → manual repair tool, not the mechanism
```

### Components

| Component | Responsibility | Owns | Depends On |
|-----------|---------------|------|------------|
| `bootstrap_admin.py` *(new)* | The invariant "the platform has exactly one auto-created admin, and it has a role row" | The bootstrap sequence + its advisory lock key | `keycloak_client`, `db` |
| `rbac.get_user_global_role` | The **single** global-role resolution path | Missing-row and unknown-value policy | `db` |
| `admin_users.create_user` | Atomic user creation | The transaction boundary + compensation | `keycloak_client`, `rbac._upsert_team` |
| `realm-init-job.yaml` | Realm + client setup | Infrastructure only | — |

### Data Flow — bootstrap

1. `lifespan` startup, after the DB pool warm-up (`main.py:118`)
2. `pg_try_advisory_lock(BOOTSTRAP_LOCK_KEY)` on a dedicated connection → not acquired → return
3. Keycloak: look up `platform-admin` **by username**
4. Absent → `kc_create(username='platform-admin', email='platform-admin@agentshield.local', temp_password=<from Secret>)`
5. `set_user_realm_role(sub, 'platform-admin')` — failure is fatal to the bootstrap, not swallowed
6. `INSERT … ON CONFLICT (user_sub) DO UPDATE` with `role='platform-admin'`
7. Mark ready; release the lock

### Key Decisions

| Decision | Choice | Rationale | Alternatives rejected |
|----------|--------|-----------|----------------------|
| Where bootstrap lives | registry-api `lifespan` | Only component holding both Keycloak admin creds and the DB | Init container / Job — needs a second copy of both; Helm — that is the thing being removed |
| Cross-replica safety | Reuse `pg_try_advisory_lock` | The pattern already exists in this service; a second mechanism is a second thing to get wrong | Leader election, K8s Lease — new infra for a once-per-start operation |
| Keycloak lookup key | **Username** | Makes realm-recreation self-healing fall out for free | Storing the `sub` — strands exactly as the current seed script does |
| `_upsert_team` commit | Remove the internal commit; callers own the boundary | The only way FR-8 is possible. An explicit boundary, not a `commit: bool` flag sniffed per call site | Passing a flag — the priority-fallthrough pattern CLAUDE.md forbids |
| Missing role row | `get_user_global_role` raises; handler → 403 | Collapses two resolution paths into one. Two independent answers to "what role is this" is how `_ADMIN_ROLES` diverged | Returning `None` — pushes the decision to every call site |
| Unknown role value | **Unchanged** (rank 0) | V-5: those values are legitimate reviewer scopes | Treating them as corruption — would break daemon-approval routing by design |
| Bootstrap failure | Non-fatal to the process, `/ready` red, retry | Keycloak is routinely not ready when registry-api starts; crash-looping is worse than degrading | Abort startup — deadlocks a fresh install |
| Column default | Drop `server_default` | It re-introduces the legacy value `0044`/`0075` removed | Changing the default to `consumer` — still an invented role, just a quieter one |

---

## Constraints

- **The admin's email MUST remain `platform-admin@agentshield.local`.** Langfuse authorizes trace
  access by project membership keyed on email and account-links SSO on a match
  (`values.yaml` `LANGFUSE_INIT_USER_EMAIL`). A different address silently breaks admin trace
  access — see `docs/bugs/langfuse-trace-access-sso-and-membership.md`.
- Migration `0079`; e2e suites `97` (R1 + bootstrap) and `98` reserved for R2/R3. Identity
  propagation owns `0080–0082` / `suite-99+`. Do not re-allocate without updating all three docs.
- registry-api image bump in `scripts/deploy-cpe2e.sh` **and** mirrored in
  `charts/agentshield/values.yaml` (~L503), same commit.
- Migrations must be idempotent and data-preserving.

---

## Success Criteria

- **SC-1**: A fresh install yields a working Admin menu with zero manual seed steps.
- **SC-2**: `suite-97` fails against current code on the realm-recreation case, and passes after.
- **SC-3**: The V-6 audit re-run after a full e2e pass reports **zero** orphan users and **zero**
  stale rows — proving FR-7 stopped the regeneration, not just the symptom.
- **SC-4**: No route on the ten routers answers an unauthenticated request.
- **SC-5**: No new exported symbol is orphaned (DoD rule 3) — `ensure_platform_admin`,
  `NoPlatformRole`, and the audit surface each have a live caller in the same change.

---

## Risks & Mitigations

Likelihoods assume the platform is **not live** (see Assumptions). Impact is measured in red suites
and developer time, not user outage.

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| R1 breaks e2e suites that call the ten routers without a token | **High** | Suites fail after deploy | Sweep `scripts/e2e/` for callers before flipping; fix in the same change. **This is the bulk of R1's real work** and it is unchanged by the platform not being live |
| FR-5's 403 catches a caller nobody predicted | **Low** | A dev flow starts refusing | The V-6 audit found zero real orphans; FR-12 makes it repeatable pre-deploy; no live users to lock out |
| Bootstrap ↔ Keycloak startup race on a fresh install | Medium | `/ready` red for a while | Non-fatal + retry; expected on first install |
| Dropping `server_default` breaks an unknown inserter | Medium | 500 on an insert path | `grep` every `INSERT INTO user_team_assignments` first — currently `admin_users.py:96`, `suite-53:49`, `suite-71:325`. All three are in-repo, so the set is knowable |
| The role/scope union stays live between R0 and R5 | High | Confusing to the next reader | Stated out loud in the parent doc's gap ledger (G-R0-1) rather than papered over |

---

## Assumptions

- **The platform is in active development and is NOT live.** Confirmed 2026-08-04. The only
  consumers of these APIs are the e2e suites and Studio in dev. This raises the acceptable risk on
  every behaviour-changing requirement here (FR-4, FR-5, FR-11) — a wrong call costs a red suite,
  not a user outage — and it is why forward-fix is preferred over elaborate rollback. Revisit every
  "Likelihood" in the risk table if that changes.
- JWT `sub` equals the Keycloak user id — relied on by `_upsert_team(db, kc_id, …)` today.
- Only one Keycloak realm (`agentshield`) matters; the master realm is administrative only.
- The team for the bootstrap admin is `platform`, matching current data.
- Studio sends a JWT on every call to the ten routers (verified: it does).

---

## Out of Scope

- **R2** — flipping `ENFORCE` and wiring `require_global_role`. R0 is its prerequisite, not its start.
- **R3/R4/R5** — deploy guards, trigger management, the role-vocabulary unification, and the
  `approval_authority` → `approver` rewrite.
- **Splitting the role/scope union** (V-5). Deliberately deferred to R5, where reviewer scopes have
  a natural home on `artifact_role_grants`.
- Identity propagation and OPA. Separate docs, separate migration ranges.

---

## Migration Path

1. **Clean first** — done 2026-08-04. Cluster is 3 users / 3 rows, 1:1.
2. **FR-7** — stop the suites regenerating litter. Must precede FR-4, or the next e2e run
   reintroduces rows that FR-4 will then reject.
3. **FR-1/2/3/8/9/10** — bootstrap + atomicity + chart. No behaviour change for existing callers.
4. **FR-4/5** — the enforcing pieces. Re-run the FR-12 audit immediately before.
5. **FR-11** — R1's router sweep.
6. **Rollback**: FR-4 and FR-5 are the only user-visible steps. FR-4 reverses by restoring the
   `server_default`; FR-5 by restoring the `None → "contributor"` branch. Both are single-commit
   reverts with no data migration.

---

## Gap Ledger

Carry into `docs/testing/manual-ui-e2e-test-plan.md`.

**not-yet-wired (debt)**
- **G-R0-1** `user_team_assignments.role` holds both global roles and reviewer scopes (V-5).
  Documented, not fixed. Owner: R5.
- **G-R0-2** `approval_authority` remains the live HITL mechanism. Owner: R5.
- **G-R0-3** A stale row survives realm recreation or a hand-deleted admin. Harmless (nobody can
  authenticate as it) but it means "row count" ≠ "admin count". Reaping needs a Keycloak existence
  check; FR-12 reports it, nothing removes it automatically.

**deferred (intentional)**
- **G-R0-4** No Keycloak realm-role objects are created for the three global roles; backend
  normalization makes this non-blocking until R2.
- **G-R0-5** `seed-platform-admin-role.sh` is retained as a repair tool rather than deleted.

---

## Open Questions for Reviewers

| # | Question | Context | Options considered | Blocked decision |
|---|----------|---------|-------------------|-----------------|
| 1 | ~~Should bootstrap failure hold `/ready` red indefinitely, or go ready after N attempts?~~ **RESOLVED 2026-08-04: red until success.** | Keycloak is routinely late on a fresh install, so red-forever can look like a deploy hang. But the platform is not live — there is no traffic that a degraded-ready pod would serve, and in development a loud stall beats a quiet half-working state | (a) red until success ✅ (b) ready after N with a metric — rejected: trades a visible failure for an invisible one, and buys availability nobody is consuming | — |
| 2 | Should the bootstrap reap rows whose `sub` no longer exists in Keycloak? | G-R0-3. It is litter, not a security hole, and reaping means the bootstrap deletes data | (a) report only (FR-12) (b) reap under the same lock | Whether FR-12 stays read-only |
| 3 | Is `platform` the right team for the bootstrap admin on a fresh install, or should it be configurable? | Current data uses `platform`; hard-coding is simpler but assumes a team name | (a) hard-code (b) chart value | Chart surface area |

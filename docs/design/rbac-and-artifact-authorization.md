# RBAC & Artifact Authorization — Design + State of Record

**Status:** CONSOLIDATED — this is the single source of truth for control-plane authorization
(who may do what in Studio and the Registry API). Supersedes the four documents listed in §8.
**Date:** 2026-08-02.
**Verified against:** registry-api on `main` @ `b73989f`, live EKS `test-cluster-964-10086`,
migrations through `0078`, suites through `suite-96`.
**Decisions:** `docs/decisions.md` §25 (two-tier RBAC), §30 (webhook application identity), §33
(deny-by-default reads).

> **Every status claim in §1 is code-verified with a `file:line`, not carried over from a
> predecessor doc.** Three of the four superseded docs had status lines that no longer matched
> the repo — see §8. Do not update this section from memory or from another doc; re-check the
> code.

---

## 0. Boundary — what this doc owns, and what it does not

Three authorization layers exist. They are independent, they fail differently, and conflating
them is why the gaps below went unnoticed.

| Layer | Question it answers | Principal | Owned by |
|---|---|---|---|
| **RBAC** (this doc) | May this *person* create / deploy / delete / approve on this *artifact*? | a human, via Keycloak JWT `sub` | `rbac.py`, `artifact_role_grants`, `user_team_assignments` |
| **Identity propagation** | *Whose authority* does this running agent carry, at every hop? | a human OR a verified service | `identity-propagation-architecture.md` |
| **OPA authorization** | May this *agent pod* call this *tool*, right now? | the agent's K8s ServiceAccount | `opa-authorization-contract.md` |

Concretely: RBAC decides whether Alice may press Deploy. Identity decides whether the run that
follows still says "Alice" three hops later. OPA decides whether the agent in that run may call
`issue_refund`. A hole in any one is invisible to the other two.

**Not in scope here:** run initiation auth on `/api/v1/internal/*` (identity doc, Drop point 7);
tool risk → allow/deny (OPA contract §4); agent machine identity / SA tokens (OPA contract §1);
`asset_grants` **visibility** (a separate, pre-existing feature — visibility never implies
authority, per Decision 25).

---

## 1. Verified state — 2026-08-02

**Headline: RBAC is built and wired, then switched off.** The model, the table, the helper
module, the creator auto-grant, the delegation API, and the frontend guards all shipped. Two
hard-coded `False` flags and a set of never-wired call sites mean that, for the platform's
highest-consequence actions, **no user is ever denied anything**.

### 1.1 Shipped AND enforcing (real 403s)

| Component | Evidence |
|---|---|
| `artifact_role_grants` table — polymorphic grantee, soft-delete, both indexes | migration `0044`; widened to `role='invoker'` + `grantee_type='application'` by `0070` |
| Role vocabulary normalized `admin→platform-admin`, `operator→contributor`, `viewer→consumer` | `rbac.py:26-43`; migrations `0044`, `0075` |
| Creator auto-grant (`agent-admin` to artifact creator) | `rbac.py:151`; called `routers/agents.py:120`, `routers/composite_workflows.py:237` |
| Delegation API — grant/revoke, all three grantee kinds | `routers/artifact_grants.py:153,331` → `can_delegate_role` (**403s for real**) |
| Application creation gated to own-team contributors | `routers/applications.py:95` → `can_create_application` (**403s for real**) |
| `/me` returns normalized role + `artifact_roles` | `routers/me.py:47` |
| Studio: role hierarchy, `isAtLeast`, `RequireRole` on all 5 admin routes | `contexts/AuthContext.tsx:5-57`, `App.tsx:106-110` |
| Studio: artifact-grant + invoke-access UI | `components/shared/ArtifactGrantsList.tsx`, `InvokeAccessPanel.tsx` |
| Decision 30 end-to-end (applications, invoker grants, `auth_mode` flip) | migrations `0070`/`0071`; `suite-83`; flip bug fixed in `0.2.222` |

### 1.2 Shipped, wired, and DELIBERATELY DISABLED — the two flags

This is the core finding. Both flags were introduced with a stated unblock condition. **Both
conditions have since been met. Neither flag was flipped.**

| Flag | Where | Call sites | Effect today | Stated unblock condition | Condition status |
|---|---|---|---|---|---|
| `ENFORCE = False` | `rbac.py:205` (inside `require_global_role`) | **zero** — the factory is never used by any router | Nothing. The dependency is an orphan (`grep require_global_role services/registry-api` → only its own definition) | "once frontend guards + role rename are deployed" (`rbac.py:203`) | **MET** — guards at `App.tsx:106-110`, rename in `0044`+`0075` |
| `ENFORCE_TRIGGER_MGMT = False` | `rbac.py:192` | **8** — `triggers.py:65,233,271,312`, `composite_workflows.py:775,866,904,938` | Every call computes `can_manage_artifact`, **discards the answer**, and logs `"… — PERMITTED (ENFORCE_TRIGGER_MGMT=False)"` | "once frontend guards for trigger CRUD land" (`rbac.py:191`) | needs confirming per-surface |

So `can_manage_artifact` runs 8 times per relevant request and authorizes nothing. Trigger and
workflow-trigger management — create, update, delete, rotate-token — is permit-all for any
authenticated user.

### 1.3 Built but never called — orphaned policy functions

DoD rule 3 violations. Each is a working, tested-by-nothing function with no caller
(`grep` across `services/registry-api`, 2026-08-02):

| Function | `rbac.py` | Intended guard (design §6) | Callers |
|---|---|---|---|
| `can_deploy_to_production` | :92 | production deploy — **the single highest-consequence action on the platform** | **0** |
| `can_approve_hitl` | :108 | HITL decide in production | **0** |
| `can_use_playground` | :116 | playground access for consumers | **0** |
| `can_create_agent` | :121 | agent/workflow create | **0** |
| `require_global_role` | :199 | all 16 `/admin/*` routes | **0** |

### 1.4 Routers with no authorization AND no authentication check

Only `Depends(get_db)` — no `require_user`, no `get_optional_user`, no router-level
`dependencies=`, and registry-api installs no global auth middleware (`main.py`). Verified
per-file 2026-08-02.

> **R1 outcome (2026-08-05, `0.2.261`)** added as the last column. `require_user` now covers
> **47** of these routes; **12** stay open because in-cluster machine callers reach them with no
> `Authorization` header (V-7 of [`rbac-r0-r1-spec.md`](rbac-r0-r1-spec.md) lists all eight call
> sites). The exempt set is pinned by `suite-97` **T-S97-011**, which walks `app.routes` — so a
> route added without auth fails a test, not a review.

| Router | Endpoints | Notable exposure | R1 outcome |
|---|---|---|---|
| `deployments.py` | 9 | **deploy to production**, rollback, delete deployment | 7 protected · **2 exempt** — `GET /` + `PATCH /{id}` (deploy-controller) **G-R1-2** |
| `agent_runs.py` | 7 | full run history | **0 protected · 7 exempt** — whole router (declarative-runner ×5, eval-runner) **G-R1-1** |
| `workflows.py` | 7 | agent-graph CRUD | ✅ all 7 protected |
| `auth_configs.py` | 6 | **tool credential configuration** | 5 protected · **1 exempt** — `GET /{id}/secret-ref` **G-R1-4** |
| `versions.py` | 5 | version create / publish | 4 protected · **1 exempt** — `GET /{version_id}` **G-R1-3** |
| `teams.py` | 5 | team listing | ✅ all 5 protected |
| `llm_providers.py` | 5 | **LLM provider keys** | ✅ all 5 protected |
| `agent_tools.py` | 3 | tool binding | 2 protected · **1 exempt** — `GET /{name}/tools` **G-R1-5** |
| `admin.py` | 11 | **the admin surface itself**: grants, publish-requests, approval-authority | ✅ all 11 protected |
| `playground_approvals.py` | 1 | playground approval decide | ✅ protected |

`bundle.py`, `internal.py`, `internal_mcp.py`, `events.py`, `catalog.py` are also unauthenticated
but are **intentionally** service-facing — except `internal.py`, which is a real hole owned by
the identity doc (Drop point 7), not by this one.

**Reachability, stated honestly:** these are cluster-internal today — the gateway NLB carries
`aws-load-balancer-scheme=internal`. That bounds blast radius; it is not a control, and it is the
same caveat recorded in `docs/bugs/internal-run-door-has-no-authentication.md`.

### 1.5 A second, competing role vocabulary

`routers/approvals.py:41` defines its own admin set:

```python
_ADMIN_ROLES = {"platform-admin", "platform_admin", "team_lead"}
```

`team_lead` exists in **no** migration, **no** Keycloak realm role, and **not** in
`rbac.ROLE_HIERARCHY`. The underscore spelling `platform_admin` is a legacy variant that
`rbac._LEGACY_MAP` does not carry either. This set is checked independently of `rbac.py`, so the
platform has two answers to "is this caller an admin" that can diverge — and did:
`docs/bugs/production-hitl-decide-403-authority.md` is exactly that divergence, where the
hyphen/underscore mismatch made the admin branch unreachable.

HITL authority still runs on the older `approval_authority` table
(`approvals.py:241-257,392-399`), which Decision 25 marked deprecated in favour of the `approver`
scoped role. That rewrite (design §7) is not done.

### 1.6 Test coverage — foundation only, zero negative tests

`suite-42-rbac.sh` has 7 cases: table exists, agent auto-grant, workflow auto-grant, `/me` shape,
role normalization, auto-grant idempotency, no legacy `viewer` rows. **All positive.** Not one
asserts a 403.

The original design's test plan specified the negative cases — T-S32-003/004 (production deploy
allowed/denied), 006 (consumer blocked from playground), 009 (HITL decide blocked), 011
(delegation blocked), 013 (admin route blocked). None exist, because none of those guards do.

`suite-82` (delegation) and `suite-83` (applications) **do** assert 403s — they cover the two
paths that actually enforce (§1.1). That asymmetry is the whole story: where enforcement shipped,
negative tests shipped with it.

---

## 2. The model (consolidated, unchanged in substance)

### 2.1 Global roles — one per user, in `user_team_assignments.role`

| Role | Grants | Blocks |
|---|---|---|
| `platform-admin` | everything: users/teams, publish approval, approval authority, deploy anywhere, grant any scoped role, all HITL queues | nothing |
| `contributor` | create agents/workflows/tools/skills, sandbox deploy, playground, submit for publish. Production deploy only if also `agent-admin` on the artifact | `/admin/*`, user/team management, unscoped production deploy |
| `consumer` | browse catalog, view runs, view deployment status | all mutation, playground, HITL approval, deploy |

Legacy values normalize on read (`rbac._normalize_role`): `admin→platform-admin`,
`operator→contributor`, `viewer→consumer`.

> **Decided 2026-08-04 (Decisions 40–42, phase R0).** There is **no auto-provisioning** — users are
> created by the platform, never from the IdP, and `platform-admin` is the only auto-created user.
> A subject with **no row is therefore corruption, not a kind of user**, and is refused rather than
> resolved to an invented role. The old fail-open `NULL → contributor` (`rbac.py:41-43`) goes away,
> and so does the `role` column's `server_default="operator"`, which silently re-introduced the
> value migrations `0044`/`0075` removed.
>
> **An *unrecognized* value is not corruption and keeps today's behaviour.**
> `user_team_assignments.role` is a union of `{global role} ∪ {reviewer scope}` — `approvals.py:48`
> defines `_DEFAULT_REVIEWER_SCOPE = "agent:reviewer"` and `_caller_roles` (`:266`) matches it
> against this column. So `ROLE_HIERARCHY.get(role, 0) == 0` for a scope literal is **load-bearing,
> not a bug**: it is the only thing stopping a reviewer-scope holder from being read as a
> contributor. The split lands in R5 (G-R0-1), not R0.
>
> Design: [`rbac-r0-r1-spec.md`](rbac-r0-r1-spec.md).

### 2.2 Artifact-scoped roles — in `artifact_role_grants`

| Role | Scope | Grants |
|---|---|---|
| `agent-admin` | one agent or workflow | suspend/resume/scale/upgrade/rollback, edit runtime config, delete deployment, **deploy to production**, delegate `agent-admin`/`approver`/`invoker` within that artifact |
| `approver` | one agent or workflow | receives + decides that artifact's production HITL requests |
| `invoker` | one agent or workflow | (Decision 30) an **application** may invoke it via signed webhook; first such grant flips the trigger's `auth_mode` to `client_signed` |

Grantee is polymorphic: `user` (by `sub`), `team` (by name, all members inherit), or
`application` (Decision 30). A permission check evaluates direct and team grants together
(`rbac.has_artifact_role`, `:75-85`).

Roles are additive — more grants never reduce access. Revocation is a soft-delete (`revoked_at`)
and does **not** cascade: revoking a granter leaves grants they made intact.

### 2.3 Creator auto-grant

Creating an agent or workflow inserts an `agent-admin` grant for the creator,
`granted_by='system:auto-grant'`, `ON CONFLICT DO NOTHING`. Creator `"system"` is skipped
(`rbac.py:155`).

### 2.4 What `artifact_role_grants` is NOT

| Table | Purpose | Relationship |
|---|---|---|
| `asset_grants` | **visibility** — which teams see/bind a published asset | independent; visibility ≠ authority |
| `approval_authority` | **HITL routing**, per-tool | superseded in design by the `approver` role; **still the live mechanism** (§1.5) |
| `user_team_assignments` | global role, one per user | complementary |

---

## 3. Endpoint authorization matrix — target vs today

`✅` enforcing · `⚠️` wired but disabled · `❌` no guard · `🔓` no auth at all

| Endpoint | Target guard | Today | Evidence |
|---|---|---|---|
| `GET/POST/PATCH/DELETE /admin/*` (16 routes) | `require_global_role("platform-admin")` | ✅ | R2 — router-level on `admin.py` + `admin_users.py` |
| `GET /admin/teams-summary` (org census) | `require_global_role("platform-admin")` | ✅ | R2 — split from the self-scoped read (Decision 43) |
| `GET /me/team` (self-scoped team + grants) | authenticated, any role | ✅ | R2 — `me.py`; backs the sidebar for every role |
| `GET /users/directory` (name + sub only) | authenticated, any role | ✅ | R2 — `users.py`; backs the artifact grant picker |
| `POST /agents/` | `can_create_agent` | ✅ | R2 — first caller of `can_create_agent`; the `X-User-Sub` identity fallback is deleted. (This row previously read ❌; it was 🔓 — `get_optional_user` meant ANONYMOUS creation with caller-supplied `created_by` **and** a caller-chosen `agent-admin` auto-grant. Postmortem: `docs/bugs/anonymous-agent-creation-with-forged-attribution.md`.) |
| `PUT/DELETE /agents/{name}` | platform-admin OR `agent-admin` | ❌ | |
| `POST /agents/{name}/quarantine` | platform-admin | ❌ | |
| `POST /agents/{name}/deploy` env=sandbox | contributor+ | 🔓 | `deployments.py` |
| `POST /agents/{name}/deploy` **env=production** | `can_deploy_to_production` | 🔓 | `deployments.py` — **orphan helper, §1.3** |
| `POST /agents/{name}/rollback` | platform-admin OR `agent-admin` | 🔓 | |
| `POST/PUT/DELETE /versions/*` | contributor+ / `agent-admin` | 🔓 | `versions.py` |
| `POST /workflows/`, `PUT/DELETE /workflows/{id}` | `can_create_agent` / `agent-admin` | 🔓 | `workflows.py` (agent-graphs) |
| `POST /composite-workflows/` | contributor+ | ❌ (auto-grant only) | `composite_workflows.py:237` |
| Trigger CRUD + rotate-token (agent) | `can_manage_artifact` | ⚠️ | `triggers.py:64-68,232,270,311` |
| Trigger CRUD (workflow) | `can_manage_artifact` | ⚠️ | `composite_workflows.py:774,865,903,937` |
| `POST /playground/runs` | `can_use_playground` | ❌ | orphan helper |
| `GET /approvals/` context=production | filter by `approver` grant | ❌ — filters by `approval_authority` | `approvals.py:241` |
| `PATCH /approvals/{id}` production | `can_approve_hitl` | ❌ — `_ADMIN_ROLES` + per-tool authority | `approvals.py:392-399` |
| `POST /artifacts/{type}/{id}/grants` | `can_delegate_role` | ✅ | `artifact_grants.py:153` |
| `DELETE …/grants/{id}` | `can_delegate_role` on target grant's role | ✅ | `artifact_grants.py:331` |
| `POST /applications/` | `can_create_application` | ✅ | `applications.py:95` |
| `GET /schedules` | deny-by-default, team-scoped | ✅ | `schedules.py` (R7) |
| `GET /triggers` (list) | team-scoped | ❌ | R7's known sub-gap |
| `GET /playground/eval-runs`, `/datasets` | authenticated + team-scoped | ✅ | fixed in `0.2.234` (Decision 33) |
| `GET /auth-configs/*`, `/llm-providers/*` | contributor+ | 🔓 | credential-bearing |

---

## 4. Gap ledger

Consolidated from the four superseded docs plus the bug record. Tagged per CLAUDE.md.

**not-yet-wired (debt) — security-relevant**
- ~~G-1 `require_global_role` orphaned + `ENFORCE=False`; all 16 admin routes unguarded.~~ **CLOSED
  by R2 (`0.2.263`)** — the flag is deleted rather than flipped, and the factory is wired onto both
  admin routers. Measured on `0.2.262` before the fix: `e2e-consumer` → `GET /api/v1/admin/users`
  → **200**. *(§1.2, §1.3)*
- G-2 Production deploy has no authorization check; `can_deploy_to_production` orphaned. *(§1.3)*
- G-3 Trigger/webhook management permit-all via `ENFORCE_TRIGGER_MGMT=False`, 8 sites. *(§1.2)*
- G-4 `auth_configs.py` (6) + `llm_providers.py` (5) expose credential configuration with no auth. *(§1.4)*
- G-5 `admin.py`, `deployments.py`, `versions.py`, `workflows.py`, `agent_runs.py`, `teams.py`, `agent_tools.py`, `playground_approvals.py` — no auth dependency. *(§1.4)*
- G-6 Two role vocabularies; `team_lead` exists nowhere else. Caused `production-hitl-decide-403-authority`. *(§1.5)*
- G-7 `list_triggers` has no auth — R7's explicitly noted, still-open sub-gap. *(`schedule-lifecycle-and-operations.md` R7)*
- G-8 `suite-42` has zero negative tests; the design's six 403 cases are unwritten. *(§1.6)* —
  **partly closed:** `suite-98` (10 cases) and `e2e/rbac-role-journeys.spec.ts` (9 cases) now carry the
  403 assertions for R2's surface. `suite-42` itself is still positive-only.
- G-R2-1 `/api/v1/users/directory` lets ANY authenticated user enumerate usernames. Deliberate and
  bounded (Decision 43): a name picker cannot work otherwise, and it carries no email/role/team/
  enabled field — strictly less than every role could read before R2. `suite-98` T-S98-009 pins the
  absent fields so it cannot grow back into `/admin/users`. **deferred (intentional)**.
- G-R3-2 **`start_deployment_chat` has no access check at all.** `chat.py:801`
  (`POST /{name}/deployments/{dep_id}/chat`) resolves `caller_team` and never compares it to
  `agent.team`, never calls `_has_grant`. Its sibling `start_chat` (`:550`) enforces both. Studio
  routes to the UNGUARDED one (`App.tsx:84`) from a fleet row. Two doors to one capability, one
  guarded — the same shape as `webhook_clients.py`/`agent_endpoints.py` and
  `approvals._ADMIN_ROLES`. **not-yet-wired (debt), suspected not proven:** the 200 observed was
  same-team, so the own-team fast path would have allowed it anyway. *(2026-08-07)*
- G-R3-3 **`asset_grants` is called visibility in §2 and used as authority in `chat.py:585`.**
  §2 says "visibility ≠ authority", but `_has_grant` on that table is THE gate for cross-team
  invoke. Either the doc is wrong or the code trusts a visibility record as an authorization
  decision. Resolving it determines whether the invoke gate replaces `_has_grant` or sits beside
  it. Owned by R5 (one role vocabulary). *(2026-08-07)*
- G-R3-4 **Tool authorization resolves on the agent's team, not the caller's** — so sharing an
  agent escalates tool access. **Decision 45** resolves; lands in identity P2. *(2026-08-07)*
- G-R3-5 **`owner_team` is never set at tool creation**, so a Studio-created tool is usable by every
  team (65 of ~173 rows). **Decision 46** resolves; ships ahead of identity P1. *(2026-08-07)*
- G-R2-4 `agents.py` is **1 protected / 11 exempt** on the deployed `0.2.263` (measured
  with T-S97-011's own algorithm). R2 closed `POST /agents/`; the eleven still-open routes
  include `PATCH /agents/{name}`, `DELETE /agents/{name}` and `POST
  /agents/{name}/quarantine` — unauthenticated **mutations**. R3's scope. Now pinned in
  the canary (`"agents": (1, 11)`) so the count is a test, not a memory. *(§3)*
- G-R2-2 `main.tsx` swallows a `/me` failure (`console.warn`) and renders with `role = null`, which
  `isAtLeast` treats as `consumer`. With R2 live, a transient `/me` failure silently demotes a
  platform-admin's UI to a consumer's — Admin nav gone, `/admin/*` deep links redirected — with no
  message. Denying is the safe direction, but doing it silently is not. **not-yet-wired (debt)**.
- G-R0-1 `user_team_assignments.role` holds **both** global roles and reviewer scopes (WS-2 T011,
  `approvals.py:48,266`). Documented in R0, split in R5 where scopes move to `artifact_role_grants`.
  Until then an unrecognized value must NOT be treated as corruption. *(Decision 42)*
- G-R0-2 Nothing in the install seeds an assignment row; `seed-platform-admin-role.sh` patches it
  afterwards and covers only `platform-admin`. Closed by R0. *(Decision 40)*
- G-R0-3 A stale row survives realm recreation or a hand-deleted admin — harmless (nobody can
  authenticate as it) but "row count" ≠ "admin count". R0's audit surface reports it; nothing reaps
  it automatically. *(open question 2 in `rbac-r0-r1-spec.md`)*
- G-R0-4 `suite-53:49` inserts an assignment row with **no role** and `suite-71:325` inserts a
  reviewer scope; both regenerate table litter on every run. Fixed in R0 (`FR-7`) — cleaning the
  cluster without this is a one-time illusion.

**deferred (intentional)**
- G-9 `approval_authority` table not dropped — historical records retained.
- G-10 Revocation does not cascade (orphan-keep is the safer default).
- G-11 Tool-level `approver` granularity — agent-level scope is the MVP.
- G-12 Keycloak realm roles not created by `realm-init-job.yaml`; backend normalization makes this non-blocking, so it stays LOW until enforcement flips.

**environment / not this layer**
- G-13 `workflow-playground-run-rbac-403` — registry-api's *K8s* ServiceAccount lacks RBAC in the `agentshield-playground` namespace. Kubernetes RBAC, not platform RBAC. Filed here only because the name collides; owner is the deploy/chart lane. Still open.

---

## 5. Implementation plan

Sequenced so each phase is independently shippable and reversible, and so the two highest-risk
holes close first. **Number allocation across the three authorization docs** (latest on disk:
migration `0078`, `suite-96`): RBAC takes `0079` + `suite-97/98`; identity propagation takes
`0080–0082` + `suite-99+`; OPA needs no migration. Do not re-allocate without updating all three.

**Phase R0 — ✅ SHIPPED 2026-08-05 (registry-api `0.2.260`).** Make "a user with no role row" unrepresentable (prerequisite for R2).
Decisions 40–42. Platform code, not the chart, creates the sole auto-created user `platform-admin`
on first init: registry-api `lifespan`, single-flighted across replicas with `pg_try_advisory_lock`
(reusing the pattern at `mcp_health.py:172-193`), admin identified by *username* so realm
recreation self-heals, email pinned to `platform-admin@agentshield.local` for Langfuse membership.
`realm-init-job.yaml` drops both user blocks; `seed-platform-admin-role.sh` becomes a repair tool;
`agent-reviewer` moves to the four suites that use it (`76/78/82/83`) via `POST /admin/users`.
`POST /api/v1/admin/users` becomes atomic — `_upsert_team` loses its internal commit so callers own
the boundary, with a compensating `kc_delete` on failure. `get_user_global_role` raises instead of
inventing a role, and migration `0079` drops the `role` column's `server_default`. *Test:*
`suite-97` — fresh install has an admin with a row and no seed script; replica race yields one row;
realm recreation re-pins (**this case must fail against current code first**); orphan `sub` → 403.
Design: [`rbac-r0-r1-spec.md`](rbac-r0-r1-spec.md). Run **with or before R1** — they touch the same
four e2e suites.

**Phase R1 — ✅ SHIPPED 2026-08-05 (registry-api `0.2.261`).** Close the unauthenticated routers
(no behaviour change for legitimate users).

> **Caveat, stated because a green tick would hide it:** R1 protects **47** routes and leaves **12**
> exempt. Five route groups keep accepting anonymous requests because in-cluster machine callers
> reach them with no `Authorization` header — verified at eight call sites (`deploy-controller` sends
> no auth headers at all; `eval-runner`'s `_EVAL_HEADERS` is an audit stamp). They are G-R1-1…5, each
> commented in code with its caller's `file:line`, and `suite-97` **T-S97-011** walks `app.routes` to
> pin the partition so a new unauthenticated route fails a test rather than a review. Closing them
> needs the service identity owned by `identity-propagation-architecture.md` (migrations `0080–0082`).
> **R1 is authentication only** — no role check, no team scope, no new 403. That is R2.

Add `require_user` to the 10 routers in §1.4. Pure authentication, no role logic, so no
legitimate Studio call changes — Studio already sends the JWT. *Test:* `suite-97` — every route
in §1.4, anonymous → 401. This is the cheapest large risk reduction available and it blocks
nothing.

**Phase R2 — ✅ SHIPPED 2026-08-06 (registry-api `0.2.263` / studio `0.1.184`).** Turn on
global-role enforcement. `require_global_role` now enforces — the `ENFORCE = False`
closure-local was **deleted, not flipped**: a permanently-true flag is dead config that reads as
a switch someone may flip back, and being closure-local it was invisible to grep. Wired onto
`admin.py` + `admin_users.py`, which had zero call sites, so the flag alone would have changed
nothing. `can_create_agent` got its first caller on `POST /agents/`, and that handler's
`X-User-Sub` identity fallback was deleted (an anonymous caller could create an agent and
attribute it to anyone).

> **R2 was NOT "a flag flip plus 16 decorators", and this section said it was.** Checking the
> browser before wiring — the step whose omission shipped the blank page — found `GET
> /admin/teams-summary` had two unrelated readers: the Access Control census, and the **Sidebar +
> My Agents page that every role sees**. Gating it as written would have silently emptied
> "Shared With Me" platform-wide; leaving it authenticated-only would have kept handing a
> `consumer` the entire org's membership map. The same check found the artifact grant picker on
> the agent Settings tab reading `GET /admin/users`, a panel a contributor reaches. **Decision 43**
> splits the questions: the census stays admin-only, `GET /api/v1/me/team` answers the self-scoped
> one, and `GET /api/v1/users/directory` (name + `sub`, nothing else) backs the picker. Both grant
> reads go through `team_assets.fetch_team_asset_grants` so they cannot drift.

*Test:* `suite-98` — written RED before R2 and now green, 10 cases. Half of them are the guards
against an over-broad fix: platform-admin still 200, anonymous still 401 (not 403), `/me/team` and
`/users/directory` still open to non-admins, contributor can still create an agent. **Plus
`e2e/rbac-role-journeys.spec.ts`** — see §6, R2 is UX-facing and this doc previously said it was
not. OQ-1 was resolved by R0 (Decision 40/41): there is no unknown-role default to decide, because
there is no such thing as a legitimate user without a row.

**Phase R3 — guard the deploy path.** Wire `can_deploy_to_production` into the production branch
of `deployments.py`; sandbox stays contributor+. Wire `can_create_agent` into agent/workflow POST
and `can_use_playground` into `POST /playground/runs`. *Test:* `suite-98` — the design's
T-S32-003/004/005/006/007, i.e. creator deploys to prod 200, unrelated contributor 403, sandbox
200, consumer playground 403.

**Phase R4 — trigger management.** Confirm the Studio trigger surfaces render per-role, then flip
`ENFORCE_TRIGGER_MGMT=True`. Add auth + team scope to `list_triggers` (G-7). *Test:* extend
`suite-96` (schedules) rather than a new suite — same surface.

**Phase R5 — one role vocabulary + HITL on scoped roles.** Delete `approvals._ADMIN_ROLES`;
route every admin check through `rbac.get_user_global_role`. Rewrite production approval listing
to filter on `approver` grants and decide via `can_approve_hitl`. Migration `0079` backfills
active `approval_authority` rows into `artifact_role_grants` as `approver` grants (idempotent,
data-preserving; the old table is kept per G-9). *Test:* `suite-98` gains T-S32-008/009/014
(scoped visibility, decide blocked, team grant). **UX-facing** → Playwright: an `approver` sees
only their artifacts' queue, survives reload.

> **Alignment Check:** the goal is a platform where authorization is real, not a module that
> computes decisions and discards them. Each phase moves call sites from "computes and logs" to
> "computes and enforces" — R1 without R2 would be security theatre, so R1's only claim is
> authentication, stated as such.

**Deliberately NOT in this plan:** anything touching `/api/v1/internal/*` (identity doc Phase 3),
and any OPA rego change (OPA contract). A "Run now" style control needs R2's team-scope predicate
*and* the identity doc's Phase 3a — that dependency is recorded in both docs.

---

## 6. Definition of Done

Per CLAUDE.md, each phase must satisfy:
1. **Real journey** — a bash suite for the API gate, **plus a Playwright spec for any phase that
   changes what a user can see or do.**

   > **This item used to read "a Playwright spec for R5 (the only UX-facing phase)". That was
   > wrong and it was load-bearing.** R2 is the first phase that returns a 403 to a real person;
   > calling it API-only is what let it be planned as sixteen decorators. Worse, *both* test layers
   > were structurally unable to notice a mistake: all 61 bash suites authenticate as
   > `platform-admin` (suite-98's header), and until `0.1.184` `global-setup.ts` logged in exactly
   > one user, so all 47 Playwright specs did too. **A role gate whose only witnesses already pass
   > every check is a guard that cannot fail.** `global-setup.ts` is now multi-role (Decision 44)
   > and `e2e/rbac-role-journeys.spec.ts` drives the deployed app as a real consumer and a real
   > contributor. Every remaining phase (R3's playground/deploy denials, R4's trigger surfaces,
   > R5's scoped approval queue) is UX-facing on the same terms.

2. **Save → reload → assert** — R5's grant-driven queue filter must survive a reload. For a phase
   whose change *is* a guard rather than a row, the reload assertion applies to the guard:
   `T-RJ-005` reloads on `/admin/access` as a consumer, because a gate that only holds on first
   render fails exactly when role resolution races the route — which is how users arrive.
3. **No orphans** — after R3, `grep` must show a live caller for `can_deploy_to_production`,
   `can_use_playground`, `can_create_agent`, `require_global_role`, `can_approve_hitl`. This doc's
   §1.3 exists because that check was never run; it is now the phase-exit gate.
4. **Negative tests are the point** — a phase that adds a guard without a 403 assertion is not
   done. §1.6 is what "positive tests only" looks like after the fact.
5. **Gap ledger current** — §4 updated in the same change, and mirrored into
   `docs/testing/manual-ui-e2e-test-plan.md`.

---

## 7. Open questions

- **OQ-1 — ~~unknown/NULL global role defaults to `contributor`~~ — RESOLVED 2026-08-04
  (Decisions 40–42).** The question presupposed that legitimate users can lack a row. They cannot:
  users are platform-created, `platform-admin` is the only auto-created one, and platform code
  creates it. So the answer was not a safer default but **removing the state** — see phase R0 and
  [`rbac-r0-r1-spec.md`](rbac-r0-r1-spec.md). Two findings came out of resolving it: the `role`
  column had its own fail-open `server_default="operator"` (a second, independent producer), and an
  unrecognized value is *not* corruption because the column doubles as a reviewer-scope namespace.
- **OQ-2 —** should `platform-admin` bypass *artifact-scoped* checks everywhere? It does today
  (`can_deploy_to_production`, `can_manage_artifact`, `can_approve_hitl` all short-circuit). That
  is convenient and it means no artifact is ever un-administrable — but it also means the
  approval record can never say "only Alice could have done this".
- **OQ-3 —** `consumer` currently cannot use the playground at all (design §2.1). For a catalog
  browsing a published agent, is read-only chat a consumer right or a contributor right? Affects
  Phase R3's `can_use_playground` wiring.

---

## 8. Superseded documents — what was taken from each

Each retains a `SUPERSEDED BY` header and stays in place; nothing was deleted.

| Doc | Status when consolidated | What moved here | What stays there |
|---|---|---|---|
| `todo/rbac-design.md` | "Partially Implemented" (2026-07-09) — §13.1 accurate for its date, §13.2 now stale | §2 roles, §3 grant model, §4 data model, §6 endpoint matrix, §7 HITL rewrite, §12 test plan, §14 deferred items | full E2E walkthroughs (§11), Keycloak realm detail (§9) |
| `plan/rbac/plan.md` + `tasks.md` | **actively misleading** — every checkbox unticked incl. T-2.1–T-2.4, which shipped | the task decomposition, re-cut against reality as §5 | nothing; superseded outright |
| `todo/webhook-application-identity.md` | **IMPLEMENTED** (Decision 30) — not superseded as a design | its §2 verified-state findings, the `invoker` role, `application` grantee | the full applications/HMAC/gateway design — still the reference for that subsystem |
| `authorization-model-spec.md` §8, §11, §13, §14 | Draft (2026-06-27) | publish/grant workflow, revocation cascade, approval-authority API | §4–§7 (machine identity), §12/§15 (OPA) → OPA contract; §Phase 3, §10 → identity doc |

Bug records folded into §1 and §4 (each remains the authoritative postmortem):
`production-hitl-decide-403-authority.md`, `webhook-invoker-grant-auth-mode-flip.md`,
`unauthenticated-full-table-read-eval-runs-datasets.md`, `workflow-playground-run-rbac-403.md`.

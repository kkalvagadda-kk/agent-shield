# Research — Webhook Application Identity & Invoker Grants

Grounding decisions made while turning `docs/design/todo/webhook-application-identity.md` (§Decision 30) into an executable plan. The design doc is authoritative on intent; this file records what changed once checked against the actual repo at `/Users/kalyankalvagadda/code/agent-shield` on 2026-07-19, and the implementation-detail choices the design doc deliberately left open.

## 1. Migration numbering

`ls services/registry-api/alembic/versions/ | sort -t_ -k1 -n | tail -5` →

```
0064_webhook_clients.py
0065_agent_memory_shared_thread.py
0066_user_profiles.py
0067_drop_llm_provider_check.py
0068_knowledge_base_rag.py
```

Highest landed migration is **0068**. The design doc's own text ("next available number after 0068") and Decision 30's identical phrasing are both still accurate as of this planning pass — no parallel workstream has taken 0069 yet. This plan uses:

- **0069** — `applications` table + widened `artifact_role_grants` CHECK constraints (additive, §10 step 1).
- **0070** — backfill `webhook_clients` → `applications` + `invoker` grants (data migration, §10 step 2).

If either number is taken by the time this plan is implemented (the MCP tool-source workstream, Decisions 27–29, also chains after 0068), re-run the `ls` command above and shift both numbers up by the same delta — the two migrations must stay adjacent since 0070 reads rows that 0069's DDL created.

## 2. E2E suite numbering

`ls scripts/e2e/*.sh` sorted numerically, deduplicated by suite number, tail:

```
75 (×2 — suite-75-context-storage.sh, suite-75-eval-v2-scheduled.sh)
76 (×2 — suite-76-preferences.sh, suite-76-webhook-client-signing.sh)
77 (×2 — suite-77-eval-v2-webhook.sh, suite-77-knowledge-rag.sh)
78 (×1)
79 (×2 — suite-79-workflow-hitl.sh, suite-79-operate-parity.sh)
80 (×2)
81 (×1 — suite-81-deploy-tool-autograt.sh)
```

Several numbers are already double-booked by concurrent workstreams (a pre-existing, tolerated condition — `run-all.sh` registers both files under the same "Suite NN" label with different subtitles). The highest number in use, duplicated or not, is **81**. This plan claims the next two free numbers:

- **Suite A = `scripts/e2e/suite-82-artifact-grants.sh`** (T-ARG-001…010)
- **Suite B = `scripts/e2e/suite-83-webhook-applications.sh`** (T-SYY-001…010)

`run-all.sh`'s highest registered line is `run_suite "Suite 81: Deploy-time tool-access auto-grant" "suite-81-deploy-tool-autograt.sh"` (line 138 as read). The two new suites are appended immediately after it, Suite A before Suite B (required — Suite B's grant steps depend on the delegation endpoint Suite A validates in isolation, per the design doc §11).

## 3. `artifact_role_grants` — exact constraint names (migration 0044)

Read directly from `services/registry-api/alembic/versions/0044_artifact_role_grants.py`:

```sql
CONSTRAINT ck_arg_artifact_type CHECK (artifact_type IN ('agent','workflow'))   -- UNCHANGED, not touched by this design
CONSTRAINT ck_arg_role          CHECK (role IN ('agent-admin','approver'))       -- WIDENED to add 'invoker'
CONSTRAINT ck_arg_grantee_type  CHECK (grantee_type IN ('user','team'))          -- WIDENED to add 'application'
```

Indexes already in place and unaffected by the widening: `idx_arg_lookup` (`artifact_id, grantee_type, grantee_id, role` WHERE `revoked_at IS NULL`), `idx_arg_grantee` (`grantee_type, grantee_id` WHERE `revoked_at IS NULL`), and the uniqueness guard `uq_arg_active_grant` (unique on `artifact_id, role, grantee_type, grantee_id` WHERE `revoked_at IS NULL`). All three already cover the `application` grantee type correctly with zero index changes — the widening migration only needs to `DROP CONSTRAINT` / `ADD CONSTRAINT` the two CHECKs.

There is **no ORM model for `artifact_role_grants`** — confirmed in `services/registry-api/models.py` (no `__tablename__ = "artifact_role_grants"` anywhere) and explicitly documented as intentional in `docs/design/todo/rbac-design.md` §13.3.4: *"No ORM model for `artifact_role_grants` — Raw SQL via `text()` — matches the pattern used by `user_team_assignments` (also raw-SQL-only)."* This plan follows that precedent: the new generic grants router uses raw SQL via `sqlalchemy.text()`, not a new ORM model. `applications`, by contrast, **does** get an ORM model (`Application` in `models.py`) because it is a first-class CRUD resource with its own router, mirroring the existing `WebhookClient` model exactly — the "raw SQL only" convention is specific to `artifact_role_grants`, not a blanket policy.

## 4. `rbac.py` — exact current signatures being extended

Read in full from `services/registry-api/rbac.py`:

```python
async def has_artifact_role(db, user_sub, artifact_id, role, user_team=None) -> bool
async def can_deploy_to_production(db, user_sub, artifact_id) -> bool
async def can_approve_hitl(db, user_sub, artifact_id) -> bool
async def can_use_playground(db, user_sub) -> bool
async def can_create_agent(db, user_sub) -> bool
async def can_delegate_role(db, caller_sub, artifact_id, target_role) -> bool
async def grant_creator_admin(db, artifact_type, artifact_id, creator_sub) -> None
async def get_user_artifact_roles(db, user_sub, user_team=None) -> list[dict]
def require_global_role(*allowed_roles) -> Depends   # ENFORCE = False, permit-all-with-warning
```

**Decision: `has_artifact_role` is NOT modified.** The design doc's §6 pseudocode calls it with a `grantee_type=/grantee_id=` keyword signature that does not match the real one (positional `user_sub, artifact_id, role, user_team=None`, which resolves BOTH a direct user grant and a team-inheritance grant in one call — a materially different operation from "does this exact grantee hold this role"). Reshaping `has_artifact_role` to a generic `grantee_type`/`grantee_id` signature would touch the two functions that already call it (`can_deploy_to_production`, `can_approve_hitl`) — both explicitly named as **out of scope** in Decision 30's own non-goals ("Wiring `can_deploy_to_production`... NOT addressed here"). Per this repo's No-Bandaid rule (give each distinct semantic operation its own explicitly-named function rather than overloading one with an implicit-typed parameter), this plan adds new, separately-named functions instead of reshaping the existing one:

- `can_delegate_role` — the `target_role not in ("agent-admin", "approver")` tuple widens to include `"invoker"`. Everything else in the function is unchanged.
- `can_create_application(db, user_sub, team_name) -> bool` — **new**. Contributor+ AND the caller's own team equals `team_name` (or platform-admin bypass). This is stricter than `can_create_agent` (which takes no team parameter at all — confirmed `routers/agents.py::create_agent` does not validate `body.team` against the caller's team either, so application creation is intentionally a *stricter* gate than agent creation, per the design doc §6's explicit requirement that creation authority sit "one level above any single agent").
- `can_manage_artifact(db, user_sub, artifact_id) -> bool` — **new**. Body-for-body identical to `can_deploy_to_production` (platform-admin bypass OR `has_artifact_role(agent-admin)`), but under its own name because it gates a different action (trigger/webhook-client CRUD, not production deploy) and reusing `can_deploy_to_production`'s name for that would silently couple the two if deploy-gating logic ever changes independently.

## 5. Trigger-management authorization — scoped to avoid a 16-suite + 3-spec regression

Design doc §8.3 says `create_trigger`, `update_trigger`, `delete_trigger`, `rotate_token` "gain `Depends` on a `can_deploy_to_production`-equivalent artifact-scoped check." Grounding this against the actual repo surfaced a blast radius the design doc did not anticipate:

- `services/registry-api/auth_middleware.py::require_user` verifies a real Keycloak-signed JWT against JWKS — there is **no** dev bypass. The `X-User-Sub`/`X-User-Team` headers used throughout the bash e2e suites and two Playwright specs are read only as an **audit-stamp fallback** by a handful of routers (e.g. `webhook_clients.py`'s `x_user_sub` header param) — they do **not** satisfy `require_user`.
- `grep -rl "/triggers\b" scripts/e2e/*.sh` returns **16 files** (suite-19, 21, 22, 26, 27, 28, 31, 32, 33, 34, 66, 70, 71, 75-eval-v2-scheduled, 76-webhook-client-signing, 77-eval-v2-webhook) that call `POST/PATCH/DELETE .../triggers...` using bare `X-User-Sub` headers with no real bearer token.
- `grep -rl "createTrigger\|rotate-token" studio/e2e/*.spec.ts` returns **`webhook-clients.spec.ts`** and **`webhook-public-url.spec.ts`**, both of which drive trigger CRUD through a raw `pwRequest.newContext({ extraHTTPHeaders: { "X-User-Sub": ... } })` — not a logged-in browser session — so they also carry no real JWT. (`scheduled-overview.spec.ts` is UI-driven through a real Keycloak session via `global-setup.ts`, whose axios interceptor attaches a real `Authorization: Bearer` token — that one is unaffected by any gating choice here.)

Hard-`require_user`-gating `triggers.py`/the workflow-trigger equivalents in `composite_workflows.py` would 401 all 16 suites + 2 specs the moment this ships — a platform-wide regression far outside "webhook application identity," and it would make suite-76 (WS-4's own CP1 acceptance gate) impossible to green without a much larger rewrite than this feature owns.

**Decision:** wire `can_manage_artifact` into `create_trigger`/`update_trigger`/`delete_trigger`/`rotate_token` (agent + workflow variants) as a **real, exercised, logged check** — but gate its enforcement behind a new, independently-flippable module flag `ENFORCE_TRIGGER_MGMT = False` in `rbac.py`, using the exact same "permit-all with a warning log" shape `require_global_role`'s own `ENFORCE` flag already established in this file for the identical problem (a new enforcement point with many pre-existing unauthenticated callers). This is not a scope cut: the dependency is genuinely added, the policy function is genuinely called and correct on every request, and it is structurally one boolean flip away from hard enforcement. It avoids introducing an unplanned, uncoordinated 16-suite + 2-spec rewrite as a side effect of a webhook feature. Flipping `ENFORCE_TRIGGER_MGMT = True` — and migrating the 16 suites + 2 specs to real bearer tokens — is recorded as a **not-yet-wired (debt)** gap-ledger item (task 27), the same tag this repo already uses for `require_global_role`'s own `ENFORCE=False`.

`webhook_clients.py` itself gets **no new auth** at all (neither hard nor soft) — it is being retired in place per §10 step 5, not extended.

## 6. `webhook_clients.py` post-cutover: write endpoints return 410, not silently-dead 201s

The design doc says `webhook_clients` is "kept read-only for one release" after the gateway cutover (§10 step 5) but does not spell out what "read-only" means for its own POST/PATCH/DELETE handlers. Left as literally-still-201-returning, this is an active trap: an operator (or, concretely, `suite-76-webhook-client-signing.sh` and `studio/e2e/webhook-clients.spec.ts`, both of which register clients through this exact path on every run) would keep getting a 201 from `POST /triggers/{id}/clients` while the gateway silently never reads that row again post-cutover (the gateway now resolves exclusively through `applications` + `artifact_role_grants`, per §7) — a fail-*open*-looking dead end that this codebase's whole WS-4 threat model was built to avoid.

**Decision:** `POST/PATCH/DELETE /api/v1/triggers/{id}/clients` return **410 Gone** with a message pointing at the replacement endpoints, effective in the same change that cuts the gateway over. `GET /api/v1/triggers/{id}/clients` keeps working unchanged (rollback-window audit visibility for whatever rows already exist). This is what makes retiring the table safe-by-construction rather than safe-by-hope, and it is what forces `suite-76` and `webhook-clients.spec.ts` to be updated rather than silently left green-but-meaningless (see task 24).

## 7. Application discovery scope (design doc §13, Open Question 1) — resolved

The design doc left open whether the `invoker` grant picker (Flow B, §9.4) should show every application owned by any team the caller belongs to, or only the artifact's own owning team. **Resolved: owning-team-only.** The picker calls `GET /api/v1/teams/{artifactTeam}/applications`, where `artifactTeam` is the agent's/workflow's own `team` column — not a new "my teams" aggregate endpoint. This matches the design doc's own lean ("Leaning toward the latter... for tighter scoping"), requires no new backend endpoint, and is consistent with Option A (team-owned registry, no cross-team reuse) already locked in Decision 30.

## 8. Team-deletion cascade (design doc §13, Open Question 2) — resolved

`services/registry-api/routers/teams.py`'s own header docstring lists exactly `POST /`, `GET /`, `GET /{id}`, `PUT /{id}`, `GET /{id}/agents` — **there is no `DELETE /api/v1/teams/{id}` endpoint in this codebase.** The open question ("what happens to an `invoker` grant if the owning team is deleted") is moot: team deletion does not exist to guard against. No action needed in this plan; noted here so a future team-deletion feature knows to check for owned `applications` rows before implementing itself.

## 9. `applications.team_name` has no FK

`agents.team` and `workflows.team` are both plain `VARCHAR(128)` with no foreign key to `teams.name` (confirmed in `models.py`; `routers/agents.py::create_agent` does not validate `body.team` against the `teams` table either). `applications.team_name` follows the identical precedent — plain `VARCHAR(255)`, no FK, no existence check on create. The real gate is `can_create_application`'s team-membership check on the *caller*, not a referential-integrity check on the string.

## 10. Secret prefix

`webhook_clients.py` uses `_SECRET_PREFIX = "whsec_"` so a leaked secret is greppable/recognizable in logs (Stripe/Svix convention). `applications` secrets serve the identical purpose (webhook HMAC signing) — this plan reuses `whsec_` verbatim for `Application` secrets rather than inventing a second prefix, since the operator-facing meaning ("this is a webhook signing secret") is unchanged by which table stores it.

## 11. Studio "team settings" surface

The design doc says the new Applications panel belongs "alongside where team membership is already managed." Grounding: team membership assignment (`user_team_assignments`) is only editable from `AdminAccessPage.tsx`, which is `platform-admin`-only (`/admin/access`) — wrong audience, since application creation is a `contributor`+`same-team` action (§6), not a platform-admin action. The actual precedent for a **team-scoped, contributor-writable** secret-bearing resource with a reveal-once create flow already exists: `studio/src/pages/CredentialsPage.tsx` (`owner_team` field, no `RequireRole` wrapper on its `/credentials` route, listed in the Sidebar's "Settings" section next to "Models"). This plan models the new `ApplicationsPage.tsx` directly on `CredentialsPage.tsx`'s existing pattern — same Sidebar section, same no-`RequireRole` route, same reveal-once secret UX — rather than the admin-only access page.

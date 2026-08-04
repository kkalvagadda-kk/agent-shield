# RBAC R0 + R1 — Tasks

**Source plan:** [`plan.md`](plan.md) (T1–T22, built against verified `file:line` evidence)
**Source spec:** [`../../design/rbac-r0-r1-spec.md`](../../design/rbac-r0-r1-spec.md) (FR-1..FR-12, V-1..V-7, 4 user stories)
**Contracts:** [`contracts/bootstrap-and-ready.md`](contracts/bootstrap-and-ready.md) · [`contracts/router-auth.md`](contracts/router-auth.md) · [`contracts/http-api.md`](contracts/http-api.md)
**Constitution:** `CLAUDE.md` — Definition of Done + Post-Implementation Checklist govern *when* a task is finished.

**Total tasks:** 58 (46 implementation + 12 checkpoint)
**Phases:** 13 (9 implementation + 4 checkpoint gates)
**Parallel opportunities:** noted inline with `[P]`
**Checkpoint phases:** CP1 (after Phase 2), CP2 (after Phase 4), CP3 (after Phase 7), CP4 (after Phase 8)

Every task carries **(plan Tn)** so the decomposition stays traceable back to the plan's verified evidence. The plan's ordering is preserved; only coarse tasks were split to satisfy the ≤3-files granularity rule.

---

## Hard ordering constraints — where each is enforced

These four survive the renumbering. Do not reorder around them.

| # | Constraint (plan IDs) | New IDs | Enforced by | Why it matters |
|---|---|---|---|---|
| **HC-1** | **T1 before T9** | **T001 (Phase 1) before T012 (Phase 4)** | Phase 1 completes before Phase 4 starts; **CP1b** additionally asserts `grep -rn "INSERT INTO user_team_assignments" scripts/ services/` returns zero role-less sites *before* the migration phase begins. | Suites must stop inserting role-less rows **before** migration 0079 drops the column default. Reversed, the next e2e run inserts rows the new `NOT NULL` rejects. |
| **HC-2** | **T10 after T9** | **T013/T014/T015 after T012**, sequential inside Phase 4 | Phase 4 is explicitly serial: T012 → T013 → T014 → T015 → T016. No `[P]` in Phase 4. | With the `server_default` still in place a row can be re-minted between the code change and the migration, masking the refusal that FR-5 exists to produce. |
| **HC-3** | **T14 and T15–T19 in the SAME commit** | **Phase 6 (T020–T024) + Phase 7 (T025–T037) are ONE commit** | Both phases are declared a single commit unit in their phase headers; **CP3** gates on both and refuses to pass if either is absent. Nothing between them may be committed alone. | A router change deployed without the suite token fixes turns ~28 suites red at setup — exactly what commit `76b3570` did to fifteen (`scripts/e2e/lib/e2e-auth.sh:9-22`). |
| **HC-4** | **Parallel-safe sets** | `{T001, T002, T003}` (plan `{T1,T2,T3}`) · `{T010, T011}` (plan `{T7,T8}`) · `{T025…T036}` (plan `{T15,T16,T17,T18,T19}`) | `[P]` markers on exactly those tasks. | Preserved from the plan verbatim. **Caveat on `{T010, T011}`:** both edit `services/registry-api/routers/admin_users.py` at non-overlapping regions (T010: `_upsert_team`/`create_user`/`patch_user`; T011: new models + `audit_identity` after `teams_summary`). They are logically independent but must be *applied* serially to one working copy. |

---

## Phase summary

| Phase | Name | Story / FR | Tasks | Count |
|---|---|---|---|---|
| **1** | Setup & foundational prerequisites | FR-7, FR-3 enabler, FR-1 config | T001–T003 | 3 |
| **2** | Story 1 — a fresh install has a working admin | FR-1, FR-2, FR-3, FR-9 | T004–T009 | 6 |
| **CP1** | *Checkpoint — the platform creates its own admin* | — | CP1a–CP1c | 3 |
| **3** | Story 2 — a half-created user cannot exist | FR-8, FR-12 | T010–T011 | 2 |
| **4** | Story 3 — an unknown caller is refused, loudly | FR-4, FR-5, FR-6 | T012–T016 | 5 |
| **CP2** | *Checkpoint — no invented roles, no orphan users* | — | CP2a–CP2c | 3 |
| **5** | Suite-owned reviewer persona | FR-10 | T017–T019 | 3 |
| **6** | Story 4 — router authentication (**commit-locked with Phase 7**) | FR-11 | T020–T024 | 5 |
| **7** | Story 4 — e2e blast radius (**commit-locked with Phase 6**) | FR-11 | T025–T037 | 13 |
| **CP3** | *Checkpoint — anonymous reaches nothing, machines still work* | — | CP3a–CP3c | 3 |
| **8** | Prove the journey — Playwright + suite-97 | DoD 1, DoD 7, SC-2 | T038–T042 | 5 |
| **CP4** | *Checkpoint — full R0/R1 journey + orphan sweep* | — | CP4a–CP4c | 3 |
| **9** | Ship — image tags, bug doc, gap ledger, design docs | DoD 5, DoD 8 | T043–T046 | 4 |

---

## Phase 1 — Setup & foundational prerequisites

_Stops the litter producers and lays down the two enablers everything else imports. Nothing here changes runtime behaviour._

**Depends on:** nothing. **Blocks:** Phase 2 (T002/T003 feed `bootstrap_admin.py`), Phase 4 (**HC-1**).

- [X] [T001] [P] **(plan T1)** Stop the suites regenerating role-less rows (FR-7): state `role` + `assigned_by` on the `INSERT INTO user_team_assignments` at `suite-53:50` (`'contributor','suite-53'`) and `suite-48:49` (`'contributor','suite-48'`); at `suite-71:325` do **not** change the value — add the Decision-42 comment explaining `agent:reviewer` is a reviewer *scope*, that `approvals.py:48 _DEFAULT_REVIEWER_SCOPE` matches this literal via `_caller_roles` (`:266`), and that `ROLE_HIERARCHY.get(...,0)==0` is load-bearing — `scripts/e2e/suite-53-cost-tracking.sh`, `scripts/e2e/suite-48-feedback-dashboard.sh`, `scripts/e2e/suite-71-scheduled-e2e.sh`
- [X] [T002] [P] **(plan T2)** Keycloak client: `list_users(max, username=None, exact=True)` adds `username`/`exact` query params when `username` is given (existing caller `admin_users.py:140` unaffected); `create_user` payload gains `"emailVerified": True` with the comment explaining VERIFY_PROFILE refuses a direct-grant login without it and this platform has no email-verification flow (FR-3, FR-10 enabler) — `services/registry-api/keycloak_client.py`
- [X] [T003] [P] **(plan T3)** Bootstrap settings inserted after `mcp_health_max_backoff_cycles` (`:99`): `platform_admin_bootstrap_enabled: bool = True`, `platform_admin_password: str = ""` (env `PLATFORM_ADMIN_PASSWORD`), `platform_admin_bootstrap_retry_seconds: int = 30`, with the block comment from the plan's Key Interfaces (FR-1) — `services/registry-api/config.py`

**Phase gate:** `python3 -c "import ast; ast.parse(open('services/registry-api/keycloak_client.py').read())"` and the same for `config.py`; `bash -n` on the three suites; suites 48/53/71 green; `grep -rn "INSERT INTO user_team_assignments" scripts/ services/` shows **zero** sites lacking a `role` column.

---

## Phase 2 — Story 1: a fresh install has a working admin, with no manual step

_Spec Priority **P1**. FR-1, FR-2, FR-3, FR-9. Vertical slice: Keycloak user → realm role → assignment row → `/me` → `/ready`._

**Depends on:** T002, T003. **Blocks:** CP1, Phase 5 (the chart stops creating `agent-reviewer` here, so the suites must start creating it).

**T004 is regression-test-first (DoD rule 7):** it must be written and demonstrated **RED against current code** before T005/T006 land. That is the 2026-07-20 realm-recreation regression and it is spec SC-2.

- [X] [T004] **(plan T21, hoisted)** Create the suite carrying **only** `T-S97-004` — the realm-recreation re-pin — and register it in the manifest. Record `sub_before`; delete the Keycloak `platform-admin` through the Admin API; `kubectl rollout restart`; wait `/ready` 200; assert a `platform-admin` user exists again with a **new** `id != sub_before`, the row is on the new id with `role='platform-admin'`, and a token for `platform-admin` calling `GET /api/v1/me` returns `role == "platform-admin"`. Structure it on `suite-96`'s shape (`:56-75`): `set -euo pipefail`, `source lib/e2e-auth.sh`, `e2e_require_token` + `e2e_install_pyauth`, detached in-pod driver → result file. Manifest line after `:147`: `api|governance,rbac|suite-97-rbac-bootstrap-and-router-auth.sh|…`. **Demonstrate RED and record the failing output in the task's commit message before proceeding** — `scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh`, `scripts/test-manifest.txt`
  > **T004 status — authored, NOT yet run (open item, carry into CP1).** The suite exists, is registered (`test-manifest.txt:148`, group `governance,rbac`), is executable, and passes `bash -n` + `run-tests.sh --audit`. The **RED demonstration is deferred to a controlled window**: the case deletes the live Keycloak `platform-admin`, which every other bash suite authenticates as (`lib/e2e-auth.sh:52`), and on a pre-R0 image nothing recreates it. Run it once against the pre-R0 image (record the FAIL output), then again after CP1's deploy. Until that first run happens, SC-2 is *asserted by a test that has never failed* — which is the state DoD rule 7 exists to prevent.
- [X] [T005] **(plan T4)** Create the bootstrap module: `BOOTSTRAP_LOCK_KEY = (zlib.crc32(b"platform-admin-bootstrap") & 0x7FFFFFFF) | (1 << 62)` (= `4611686019521751996`), the six `ADMIN_*` constants, `BootstrapState` dataclass + `bootstrap_state` singleton, `ensure_platform_admin()` (11-step algorithm from the plan: config gate → attempt counters → password guard → `pg_try_advisory_lock` on a dedicated `engine.connect()` with `try/finally` unlock per `mcp_health.py:190-236` → `list_users(username=…, exact=True)` → create-if-absent + `reset_password(temporary=False)` → **unconditional** `update_user` reconcile that re-pins the email and clears `requiredActions` → `set_user_realm_role` **not** swallowed → `ON CONFLICT (user_sub) DO UPDATE … WHERE IS DISTINCT FROM` upsert on a fresh `AsyncSessionLocal()` → success state → catch-all that **never re-raises**) and `bootstrap_admin_loop(interval_seconds=30)`. Module docstring must state the invariant, that lookup is by **username** so realm recreation self-heals, that the email is **pinned** for Langfuse project membership (cite `charts/agentshield/values.yaml:509 LANGFUSE_INIT_USER_EMAIL` and `docs/bugs/langfuse-trace-access-sso-and-membership.md`), and that failure is non-fatal (FR-1, FR-2, FR-3) — `services/registry-api/bootstrap_admin.py`
- [X] [T006] **(plan T5)** Wire the bootstrap into `lifespan` as a background task after the MCP-health block (`:137-140`) with a cancel+await in shutdown mirroring `mcp_health_task` (`:151-156`); replace `ready()` (`:341-350`) so that after `SELECT 1` succeeds it returns **503** `{"status":"bootstrapping","detail":…,"attempts":…}` while `bootstrap_state.ok` is false, and `{"status":"ready"}` otherwise — keeping the existing DB-failure branch verbatim (FR-1, NFR-Availability; contract in `contracts/bootstrap-and-ready.md`) — `services/registry-api/main.py`
- [X] [T007] **(plan T6)** Chart stops creating users: delete the `platform-admin` and `agent-reviewer` user blocks and the now-unused `PLATFORM_ADMIN_PASSWORD` (`:106-110`) / `REVIEWER_PASSWORD` (`:111-115`) env vars; rewrite the header comment (`:5-16`) to drop the two `• User :` lines and the `keycloak-user-passwords` "Secrets expected" lines and add the `• Users : NONE. Decision 40 —` block. Keep the realm and all four client blocks (`registry-api`, `envoy-gateway`, `agentshield-studio`, `langfuse`) unchanged (FR-9) — `charts/agentshield/templates/realm-init-job.yaml`
- [X] [T008] **(plan T6)** Add `PLATFORM_ADMIN_PASSWORD` from `secretKeyRef: {name: keycloak-user-passwords, key: platform-admin}` to the **main `registry-api` container** env only, after `KEYCLOAK_ADMIN_PASSWORD` (`:135-139`), with the comment recording that the Secret moved here from the realm-init Job. **Do not** add it to the `alembic-migrate` init container (FR-9) — `charts/agentshield/charts/registry-api/templates/deployment.yaml`
- [X] [T009] **(plan T6)** Demote the seed script to a manual repair tool: retitle its header (`:1-22`) "MANUAL REPAIR TOOL — not part of the install", state that `bootstrap_admin.ensure_platform_admin` is now the mechanism and that this exists to re-pin without a restart (G-R0-5), body unchanged; and replace the unconditional `bash scripts/seed-platform-admin-role.sh || echo …` call at `:829-831` with the commented repair hint pointing at `GET /api/v1/admin/identity-audit`. Leave the `keycloak-user-passwords` Secret creation (`:587-591`) exactly as is — both keys are still needed (FR-9, G-R0-5) — `scripts/seed-platform-admin-role.sh`, `scripts/deploy-cpe2e.sh`

**Phase gate:** `helm template agentshield charts/agentshield -n agentshield-platform | grep -c "kcadm.sh create users"` → `0`; same pipeline `| grep -c "PLATFORM_ADMIN_PASSWORD"` → `1`; `python3 -c "import ast; ast.parse(...)"` clean on `bootstrap_admin.py` and `main.py`; T004 goes from RED to GREEN.

---

## Checkpoint 1 — Bootstrap
_Gate: Phases 1-2 must be complete. Run before starting Phase 3._
_What you prove: the platform creates and re-pins its own admin from code, the chart no longer creates users, and a Keycloak problem degrades `/ready` instead of crash-looping the pod._

> **SKIPPED — decided 2026-08-04. `suite-97` is the gate instead.** These three scripts were
> never written, and that is a deliberate choice, not an oversight. `suite-97` already carries
> **T-S97-001/002/003/005/006** — bootstrap correctness, restart idempotence, advisory-lock
> single-flight, the Keycloak-outage non-fatal path and the `/ready` 503 body — i.e. every
> assertion CP1b/CP1c would make, driven through the real handlers rather than restated in a
> shell script. CP1a would have wrapped `scripts/deploy-cpe2e.sh`, which is already the one
> deploy mechanism (see CLAUDE.md: never bare `helm upgrade`). Recorded in the gap ledger as
> **G-R0-7** so the unticked boxes do not read as forgotten.

- [ ] [CP1a] ~~Deploy script~~ — **skipped**, `scripts/deploy-cpe2e.sh` is the deploy mechanism
- [ ] [CP1b] ~~Infrastructure smoke test~~ — **skipped**, covered by `suite-97` T-S97-001/002/003
- [ ] [CP1c] ~~Behaviour smoke test~~ — **skipped**, covered by `suite-97` T-S97-005/006

**What each script must do (no placeholder TODOs):**

`deploy-r0-cp1.sh` — wraps the existing tooling, does **not** replace it.
1. `grep -n 'REGISTRY_API_TAG=' scripts/deploy-cpe2e.sh | head -1` and `sed -n '744p' charts/agentshield/values.yaml`; extract both tags and **hard-fail** if they differ (the repo's #1 footgun — a mismatch is an `ImagePullBackOff`, not an error message).
2. `bash scripts/deploy-cpe2e.sh`
3. `kubectl rollout status deploy/agentshield-registry-api -n agentshield-platform --timeout=600s`
4. Assert the running pod's image tag equals the declared tag via `kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].spec.containers[?(@.name=="registry-api")].image}'`.

`smoke-test-r0-cp1-infra.sh`
- `helm template agentshield charts/agentshield -n agentshield-platform | grep -c "kcadm.sh create users"` **== 0**; `| grep -c "PLATFORM_ADMIN_PASSWORD"` **== 1**.
- `kubectl get deploy agentshield-registry-api -n agentshield-platform -o json | jq -e '.spec.template.spec.containers[]|select(.name=="registry-api").env[]|select(.name=="PLATFORM_ADMIN_PASSWORD").valueFrom.secretKeyRef.name=="keycloak-user-passwords"'`.
- `kubectl exec -n agentshield-platform "$POD" -c registry-api -- python3 -c "import bootstrap_admin; print(bootstrap_admin.BOOTSTRAP_LOCK_KEY)"` **== 4611686019521751996**.
- `kubectl exec … -- curl -s -o /dev/null -w '%{http_code}' localhost:8000/ready` **== 200** and `/health` **== 200**.
- **HC-1 guard:** `grep -rn "INSERT INTO user_team_assignments" scripts/ services/ | grep -v "role"` returns **nothing**.
- Record `restartCount` from `kubectl get pods … -o jsonpath='{.items[*].status.containerStatuses[?(@.name=="registry-api")].restartCount}'` into a file for CP1c to compare against.

`smoke-test-r0-cp1-behaviour.sh`
- **Happy:** one in-pod `python3 -c` asserting `len(await kc.list_users(username='platform-admin', exact=True)) == 1`, its `email == 'platform-admin@agentshield.local'`, `emailVerified is True`, and the row for that `id` is `(team_name='platform', role='platform-admin', assigned_by='system:bootstrap')`.
- **Happy:** `source scripts/e2e/lib/e2e-auth.sh; e2e_set_token "$NS" "$POD"` then `kubectl exec … -- curl -s -w '\n%{http_code}' -H "Authorization: Bearer $E2E_TOKEN" localhost:8000/api/v1/me` → **200**, `.role == "platform-admin"`, `.team == "platform"`.
- **Idempotence (Story 1.2):** capture `assigned_at`; `kubectl rollout restart deploy/agentshield-registry-api`; `rollout status`; poll `/ready` to 200; re-read → **same `user_sub`, same `assigned_at`**, still exactly one Keycloak user.
- **Failure case A — never raises, never invents:** in-pod, monkeypatch `settings.platform_admin_password = ""`, `await ensure_platform_admin()` → returns `False`, `bootstrap_state.ok is False`, `bootstrap_state.last_error == "PLATFORM_ADMIN_PASSWORD is empty"`, **no exception propagated**; then restore the real password, re-run `ensure_platform_admin()` → `True`, and assert `/ready` is back to **200** before exiting (the script must leave the cluster as it found it).
- **Failure case B — single-flight (Story 1.3 / FR-2):** in-pod `asyncio.gather(ensure_platform_admin(), ensure_platform_admin())` → both return `True`, `SELECT count(*) … WHERE role='platform-admin' AND assigned_by='system:bootstrap'` **== 1**, and `list_users(username='platform-admin')` length **== 1**.
- Assert `restartCount` did not increase vs. the CP1b baseline.

> **To run:** bump `REGISTRY_API_TAG` in `scripts/deploy-cpe2e.sh:369` **and** mirror it at `charts/agentshield/values.yaml:744` (Kubernetes caches by tag — never reuse one), then:
> ```bash
> bash scripts/deploy-r0-cp1.sh && \
> bash scripts/smoke-test-r0-cp1-infra.sh && \
> bash scripts/smoke-test-r0-cp1-behaviour.sh
> ```
> **Pass criteria:** all three exit 0 and print `PASS`. `helm template` shows zero `create users`. A fresh `/ready` is 200 with no `seed-platform-admin-role.sh` invocation anywhere in the deploy log (`grep -c seed-platform-admin-role` on the deploy output == 0). The admin row is pinned to the live Keycloak `sub` and a restart does not move `assigned_at`. `restartCount` unchanged.

---

## Phase 3 — Story 2: a half-created user cannot exist

_Spec Priority **P1**. FR-8 (atomicity) + FR-12 (the read-only audit that makes the invariant checkable)._

**Depends on:** nothing beyond Phase 1 conventions. **Blocks:** Phase 5 (T017's `e2e_ensure_reviewer` calls the atomic create path).

- [X] [T010] [P] **(plan T7)** `POST /api/v1/admin/users` becomes atomic (FR-8): delete `await db.commit()` from `_upsert_team` (`:106`) and document *"Does NOT commit — the CALLER owns the transaction boundary… an explicit boundary, not a `commit: bool` flag sniffed per call site (Decision 41)"*; wrap `create_user`'s post-`kc_create` body (`:150-181`) in `try/except` that awaits `set_user_realm_role` (**fatal, not swallowed**) → `_upsert_team` → `db.commit()`, and on failure `db.rollback()` + compensating `kc_delete(kc_id)` (its own failure logged as `ORPHAN Keycloak user, see GET /api/v1/admin/identity-audit`) + `HTTPException(502, "User creation rolled back: …")`; add `await db.commit()` to `patch_user` (`:217-223`); add module-level `logging`/`logger`. Leave `patch_user`'s `set_user_realm_role` `except Exception: pass` unchanged (out of FR-8) — `services/registry-api/routers/admin_users.py`
- [X] [T011] **(plan T8 — NOT [P]: shares `admin_users.py` with T010, apply after it)** Read-only identity audit (FR-12): add `OrphanUser`, `StaleRow`, `IdentityAuditResponse` and `@teams_router.get("/identity-audit")` → `audit_identity` after `teams_summary`. Mounted on `teams_router` (prefix `/api/v1/admin`) **deliberately** — `router` declares `GET /{kc_id}` (`:184`) and a literal sibling would depend on declaration order. Body: `kc_list()` + `_team_map(db)` → `orphan_users` (KC user, no row), `stale_rows` (row, no KC user), `matched_count = len(kc_ids & set(team_map))`, `checked_at` ISO-8601 UTC; Keycloak failure → `HTTPException(502, "Keycloak unreachable: …")`. Docstring: **READ-ONLY, reports never deletes** (OQ-2 option (a)); a stale row is litter not a security hole (G-R0-3); a live `sub` holding `agent:reviewer` is **matched**, not litter (Decision 42) — `services/registry-api/routers/admin_users.py`

> **[P] caveat (HC-4):** T010 and T011 are logically independent (plan set `{T7,T8}`) but edit the same file at non-overlapping regions. Apply them serially to one working copy; review them as one diff.

**Phase gate:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/admin_users.py').read())"`; `grep -n "await db.commit()" services/registry-api/routers/admin_users.py` shows commits in `create_user`, `patch_user`, `delete_user` and **none** inside `_upsert_team`.

---

## Phase 4 — Story 3: an unknown caller is refused, loudly

_Spec Priority **P1**. FR-4 (drop the column default), FR-5 (one resolution path that raises), FR-6 (an unrecognized value keeps rank 0)._

**Depends on:** **T001 (HC-1)** and Phase 3. **Strictly serial — no `[P]` in this phase (HC-2).**

- [X] [T012] **(plan T9)** Migration `0079` dropping `user_team_assignments.role`'s `server_default` — `revision="0079"`, `down_revision="0078"`, `upgrade()` = `ALTER TABLE user_team_assignments ALTER COLUMN role DROP DEFAULT`, `downgrade()` restores `'operator'`. Column stays `NOT NULL`. Docstring must carry the WHY (0013 created the default; 0044 and 0075 migrated the *data* but neither touched the default, so every role-omitting insert re-introduced the exact legacy value those migrations existed to remove — Decision 41, producer 2) and list all four in-repo inserters with the note that FR-7 fixed them **first, on purpose**. Idempotent (`DROP DEFAULT` is a no-op with no default) and data-preserving (FR-4) — `services/registry-api/alembic/versions/0079_drop_user_team_assignments_role_default.py`
- [X] [T013] **(plan T10)** Add `class NoPlatformRole(Exception)` after `PLATFORM_ROLES` (`:37`) with `ERROR_CODE = "no_platform_role"` and `user_sub`; make `_normalize_role(raw: str)` non-optional and document that an **unrecognized** value is returned verbatim and keeps rank 0 — load-bearing, not a gap, because the column is a union of `{global role} ∪ {reviewer scope}` (Decision 42 / V-5; `approvals.py:48` + `:266`), the split lands in R5 (G-R0-1); make `get_user_global_role` (`:50-56`) log a WARNING naming the `sub` and the error code and `raise NoPlatformRole(user_sub)` when `row.scalar_one_or_none()` is `None` (FR-5, FR-6) — `services/registry-api/rbac.py`
- [X] [T014] **(plan T10)** Collapse to one resolution path and map it to 403: in `me.py` change `:26` to `from rbac import get_user_artifact_roles, get_user_global_role`, replace `normalized_role = _normalize_role(raw_role)` (`:38-45`) with `await get_user_global_role(db, sub)`, and add the comment naming why two independent answers to "what role is this" is how `approvals._ADMIN_ROLES` diverged (`docs/bugs/production-hitl-decide-403-authority.md`, Decision 41); in `main.py` register `@app.exception_handler(NoPlatformRole)` immediately after the CORS middleware (`:183`) returning **403** `{"detail":…, "error_code":"no_platform_role", "sub":…}` and logging `403 no_platform_role: sub=… path=…` (FR-5; contract in `contracts/http-api.md`) — `services/registry-api/routers/me.py`, `services/registry-api/main.py`
- [X] [T015] **(plan T10)** Annotate the now-unreachable deny-by-default branch above the `if not all_teams and not team:` guard (`:155-164`): `get_user_global_role` raises `NoPlatformRole` → 403 before it, and `team_name` is `NOT NULL`, so "no team" and "no row" are the same condition. **Keep the code** as defence in depth — if FR-5 is ever reverted this is still the Decision-33 `else` that stops an unfiltered read — and note that `suite-96` T-S96-002 now asserts the **refusal**, not the old empty list — `services/registry-api/routers/schedules.py`
- [X] [T016] **(plan T11)** Rewrite `T-S96-002` (`:171-193`) and its header description (`:23-27`) for the post-FR-5 contract: import `NoPlatformRole`, call `list_schedules(trigger_type="schedule", claims={"sub": teamless_sub}, db=s)` inside `try/except NoPlatformRole`, and record PASS only when `assigned == 0 and refused and returned is None`, with the evidence line still naming the platform's schedule count so the security property stays non-vacuous. Header must state the contract changed in R0 and that leaving the old `denied == []` assertion would be a test that stayed green through a real contract change — `scripts/e2e/suite-96-schedules-endpoint.sh`

**Phase gate:** in-pod `alembic upgrade head && alembic current` → `0079 (head)`, run twice cleanly; `SELECT column_default …` → `NULL` with `is_nullable = 'NO'` and row count unchanged; `python3 -c "import main; from sqlalchemy.orm import configure_mappers; configure_mappers()"` ok; `grep -n "_normalize_role" services/registry-api/routers/me.py` → **no match**; `bash scripts/run-tests.sh --layer api --group rbac` green. Known behaviour changes to **assert, not fix**: `triggers.py:64` and `composite_workflows.py:774,865,903,937` now 403 a row-less caller instead of logging PERMITTED; `artifact_grants.py:153,331` and `applications.py:95` still return 403 (status unchanged, detail changed) so `suite-82` T-ARG-004 and `suite-83` T-SYY-003 stay green.

---

## Checkpoint 2 — Refusal
_Gate: Phases 3-4 must be complete. Run before starting Phase 5._
_What you prove: the DB can no longer invent a role, `POST /admin/users` cannot leave a half-created user, and a valid JWT with no assignment row is refused with a stable machine-readable code._

> **SKIPPED — decided 2026-08-04. `suite-97` is the gate instead.** Same reasoning as CP1.
> `suite-97` carries **T-S97-007/008** (atomic create + the compensating-delete failure case),
> **T-S97-009** (the `NOT NULL` violation proving the `server_default` is gone), **T-S97-010**
> (the 403 `no_platform_role` *and* the `agent:reviewer` rank-0 carve-out) and **T-S97-012**
> (the audit invariants) — every CP2 assertion, driven through the real code path. Recorded as
> **G-R0-7**.

- [ ] [CP2a] ~~Deploy script~~ — **skipped**, `scripts/deploy-cpe2e.sh` is the deploy mechanism
- [ ] [CP2b] ~~Infrastructure smoke test~~ — **skipped**, covered by `suite-97` T-S97-009/012
- [ ] [CP2c] ~~Behaviour smoke test~~ — **skipped**, covered by `suite-97` T-S97-007/008/010

**What each script must do:**

`deploy-r0-cp2.sh` — same tag-mirror assertion + `bash scripts/deploy-cpe2e.sh` + `kubectl rollout status`, then additionally assert the migration actually ran: `kubectl exec -n agentshield-platform "$POD" -c registry-api -- alembic current` contains `0079`.

`smoke-test-r0-cp2-infra.sh`
- In-pod SQL: `SELECT column_default, is_nullable FROM information_schema.columns WHERE table_name='user_team_assignments' AND column_name='role'` → `column_default` **NULL/empty**, `is_nullable` **`NO`**.
- `alembic current` → `0079 (head)`; `alembic upgrade head` again → clean (idempotence).
- `kubectl exec … -- python3 -c "import main; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('mappers ok')"`.
- `kubectl exec … -- grep -c "_normalize_role" /app/routers/me.py` **== 0**.
- `kubectl exec … -- curl -s -o /dev/null -w '%{http_code}' localhost:8000/api/v1/admin/identity-audit` **== 200**.

`smoke-test-r0-cp2-behaviour.sh` (mint an admin Bearer with `e2e_set_token`, drive everything in-pod against `localhost:8000`)
- **Happy (Story 2.1):** `POST /api/v1/admin/users` for a throwaway username → 2xx; assert the Keycloak user exists, its realm role is set, and a `user_team_assignments` row exists with a **stated** role; then `DELETE` and assert both are gone.
- **Happy (FR-12):** `GET /api/v1/admin/identity-audit` → 200 with all seven fields; `matched_count + len(orphan_users) == keycloak_user_count`; `matched_count + len(stale_rows) == assignment_row_count`.
- **Failure case A (FR-4 / Story 3.2):** in-pod `INSERT INTO user_team_assignments (user_sub, team_name) VALUES (:s,'platform')` → **`NotNullViolation`** (catch it, roll back, assert the exception class by name — an insert that *succeeds* here fails the checkpoint).
- **Failure case B (FR-5 / Story 3.1):** create a Keycloak user directly via `keycloak_client.create_user` + `reset_password(temporary=False)` so **no** assignment row exists; obtain a token for it via the `password` grant on `agentshield-studio`; `curl -H "Authorization: Bearer <that token>" localhost:8000/api/v1/me` → **403**, body `.error_code == "no_platform_role"`, `.sub` echoes the user's id; then delete the Keycloak user.
- **Failure case C (FR-8 / Story 2.2-2.3):** in-pod, call `create_user` with a session whose `commit` is monkeypatched to raise → `HTTPException` with `status_code == 502` **and** `list_users(username=<throwaway>)` is **empty** afterwards (the compensating `kc_delete` ran).
- **Failure case D (FR-6 / Story 3.3):** insert a row with `role='agent:reviewer'`, assert `get_user_global_role` returns it verbatim with **no** exception and `ROLE_HIERARCHY.get(role, 0) == 0`; clean up.

> **To run:** bump + mirror the image tag, then:
> ```bash
> bash scripts/deploy-r0-cp2.sh && \
> bash scripts/smoke-test-r0-cp2-infra.sh && \
> bash scripts/smoke-test-r0-cp2-behaviour.sh && \
> bash scripts/run-tests.sh --layer api --group rbac
> ```
> **Pass criteria:** all exit 0 and print `PASS`; the `rbac` group is green (this is where the repaired `suite-96` T-S96-002 proves the new refusal contract); the audit reports **zero** orphan users and **zero** stale rows (SC-3) after a full `--group rbac` run.

---

## Phase 5 — Suite-owned reviewer persona (FR-10)

_Phase 2 removed `agent-reviewer` from the chart. The four suites that use it must now create it themselves, through the real API, or they go dark on the next fresh install._

**Depends on:** T007 (chart no longer creates it), T010 (atomic create), T002 (`emailVerified`).

- [X] [T017] **(plan T12)** Add `E2E_REVIEWER_USER`/`E2E_REVIEWER_PASS` defaults and `e2e_ensure_reviewer <namespace> <pod> [container]` — **one definition, four callers**. Idempotent, via the real API in a single in-pod `python3 -` against `http://localhost:8000`, authenticating with a `platform-admin` token from `e2e_token` (so it survives R2's future `require_global_role` on `/admin/*`): `POST /api/v1/admin/users` (409 = already exists = success → re-resolve `kc_id` from `GET /api/v1/admin/users`), then `POST /{kc_id}/reset-password {"temporary": false}` — **required**, because `create_user` sets a temporary password + `requiredActions=["UPDATE_PASSWORD"]` and Keycloak refuses a `password` grant with "Account is not fully set up". Then **verify** by performing the `password` grant itself and abort with `FATAL: agent-reviewer exists but cannot obtain a token …`. Fails loud, never leaves the caller to discover it 20 minutes later as an unexplained 401 — `scripts/e2e/lib/e2e-auth.sh`
- [X] [T018] [P] **(plan T13)** Call `e2e_ensure_reviewer "$NAMESPACE" "$API_POD"` before the driver runs: `suite-76` after the `API_POD` guard (`:29`), before the `kubectl exec` at `:35`, so `:62`'s `get_token(c, "agent-reviewer", "Reviewer2024")` resolves; `suite-78` before the driver that reaches `:182`. Each also `source`s `lib/e2e-auth.sh` (FR-10) — `scripts/e2e/suite-76-preferences.sh`, `scripts/e2e/suite-78-conversations.sh`
- [X] [T019] [P] **(plan T13)** Same treatment: `suite-82` before `:95` — and keep `:101`'s `PATCH /api/v1/admin/users/{RSUB}` (it re-pins team `platform`) but change its `"role": "operator"` to `"role": "contributor"` with the comment *"stated, canonical; the 403 persona is defined by lacking an ARTIFACT role, not a global one"*; `suite-83` before `:111` (FR-10) — `scripts/e2e/suite-82-artifact-grants.sh`, `scripts/e2e/suite-83-webhook-applications.sh`

**Phase gate:** `bash -n scripts/e2e/lib/e2e-auth.sh`; calling `e2e_ensure_reviewer` twice in a row is a no-op the second time; all four suites pass **on a namespace where the Keycloak user does not pre-exist** (verify by deleting it first, or on a fresh install).

---

## Phase 6 — Story 4: router authentication with named exemptions (FR-11)

_Spec Priority **P1**. Authentication only — **no role logic, no team scoping, no new 403** (`contracts/router-auth.md`)._

> ### ⚠ HC-3 — Phase 6 and Phase 7 are ONE commit
> A deploy carrying the router change without the suite token fixes reproduces commit `76b3570`'s fifteen-dark-suites failure at ~28× scale (`scripts/e2e/lib/e2e-auth.sh:9-22`). Do **not** commit T020–T024 without T025–T037. CP3 gates on both.

Every file imports `from auth_middleware import require_user` and `from fastapi import Depends`. Every **exempt** route gets the exact comment shape from the plan (service, `file:line` of the caller, the identity-propagation pointer, the `routers/internal.py` posture, and the `suite-97 T-S97-011` pin) — no bare `# TODO`.

- [ ] [T020] [P] **(plan T14)** Fully-protected, router-level `dependencies=[Depends(require_user)]` on the `APIRouter(...)` constructor — `workflows.py:39` (`/api/v1/agent-graphs`, 7 routes), `teams.py:30` (`/api/v1/teams`, 5 routes), `llm_providers.py:39` (`/api/v1/llm-providers`, 5 routes, **credential-bearing**) — `services/registry-api/routers/workflows.py`, `services/registry-api/routers/teams.py`, `services/registry-api/routers/llm_providers.py`
- [ ] [T021] [P] **(plan T14)** Fully-protected, router-level — `admin.py:55` (`/api/v1/admin`, 11 routes: grants, publish-requests, approval-authority, bundle regenerate), `playground_approvals.py:23` (`/api/v1/playground`, its only route is `GET /approvals`) — `services/registry-api/routers/admin.py`, `services/registry-api/routers/playground_approvals.py`
- [ ] [T022] **(plan T14)** Mixed treatment — router-level on `deployments.py:195` (`/{name}/deploy`, `/{name}/rollback`, `GET /{name}/deployments`, `PATCH /{name}/deployments/{id}` — production deploy and rollback) and `versions.py:29` (the four `/{name}/versions*` routes); per-endpoint on `global_deployments_router` (`:200`) for `:267 GET /workflows`, `:327 GET /{id}/stats`, `:377 GET /{id}/runs`, leaving `:209 GET /` and `:241 PATCH /{deployment_id}` **exempt** (`deploy-controller/main.py:54,70,122,176` — **G-R1-2**); nothing protected on `versions_global_router` (`:316`), `:319 GET /{version_id}` **exempt** (`deploy-controller/main.py:33` — **G-R1-3**) — `services/registry-api/routers/deployments.py`, `services/registry-api/routers/versions.py`
- [ ] [T023] **(plan T14)** Partially protected, per-endpoint — `auth_configs.py` protects `:55, :96, :125, :222, :258` (**credential-bearing**) and exempts `:140 GET /{config_id}/secret-ref` (`deploy-controller/tool_secrets.py:45` — **G-R1-4**); `agent_tools.py` protects `:54 POST /{name}/tools` and `:94 DELETE /{name}/tools/{tool_id}` and exempts `:126 GET /{name}/tools` (`deploy-controller/tool_secrets.py:36`, `declarative-runner/workflow_executor.py:171` — **G-R1-5**) — `services/registry-api/routers/auth_configs.py`, `services/registry-api/routers/agent_tools.py`
- [ ] [T024] **(plan T14)** **Comment only** — the whole router stays unauthenticated (**G-R1-1**). Add a module-level note at the top recording that all 7 routes are exempt and naming every caller site: `declarative-runner/main.py:410,437,148`, `checkpoint.py:27`, `orchestrator.py:35`, `eval-runner/main.py:1254`. No code change; closing this needs the service identity that `docs/design/identity-propagation-architecture.md` owns (migrations 0080–0082) — `services/registry-api/routers/agent_runs.py`

---

## Phase 7 — Story 4: e2e blast radius — attach a real Bearer (FR-11)

_**Same commit as Phase 6 (HC-3).** This is the bulk of R1's real work._

**Recipe, identical for every suite:** (1) after `API_POD` resolution, `source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"` then `e2e_set_token "$NAMESPACE" "$API_POD"` — **call it bare, not in a command substitution** (`e2e-auth.sh:110-121` explains why); for detached-driver suites also `e2e_install_pyauth` and use `from e2e_auth import BearerAuth, mint` inside the driver so the token refreshes past its 300s life. (2) Add `"Authorization": "Bearer ${E2E_TOKEN}"` to the header dict — **do not** remove existing `X-User-Sub`/`X-User-Team` headers; they are audit stamps and several suites assert on them. (3) Re-run the suite.

`(verify)` = the suite already fetches a Bearer for *some* calls; confirm it is attached to the calls hitting the newly-protected surfaces and add it where it is not.

**Group: deploy, agent** (plan T15)
- [ ] [T025] [P] **(plan T15)** Attach a real Bearer — `scripts/e2e/suite-2-lifecycle.sh` *(verify)*, `scripts/e2e/suite-38-deployment-overview.sh`, `scripts/e2e/suite-39-deployment-lifecycle.sh`
- [ ] [T026] [P] **(plan T15)** Attach a real Bearer — `scripts/e2e/suite-41-version-delete.sh`, `scripts/e2e/suite-44-version-management.sh`, `scripts/e2e/suite-50-version-dedup.sh`
- [ ] [T027] [P] **(plan T15)** Attach a real Bearer — `scripts/e2e/suite-67-deployment-gc-and-drift.sh`, `scripts/e2e/suite-46-chat-deployment-pinning.sh`, `scripts/e2e/suite-47-deployment-chat-tracing.sh`

**Group: eval** (plan T16)
- [ ] [T028] [P] **(plan T16)** Attach a real Bearer — `scripts/e2e/suite-17-eval-gate.sh`, `scripts/e2e/suite-61-eval-mode-plumbing.sh`, `scripts/e2e/suite-72-eval-v2-durable.sh`
- [ ] [T029] [P] **(plan T16)** Attach a real Bearer — `scripts/e2e/suite-73-eval-v2-workflow.sh`, `scripts/e2e/suite-74-eval-v2-side-effects.sh`, `scripts/e2e/suite-80-eval-v2-regression.sh`

**Group: workflow, execution** (plan T17)
- [ ] [T030] [P] **(plan T17)** Attach a real Bearer — `scripts/e2e/suite-40-workflow-deploy.sh`, `scripts/e2e/suite-58-workflow-live-run.sh`
- [ ] [T031] [P] **(plan T17)** Attach a real Bearer — `scripts/e2e/suite-64-production-workflow-golden-path.sh`, `scripts/e2e/suite-68-daemon-no-input.sh`

**Group: governance, hitl, tools** (plan T18)
- [ ] [T032] [P] **(plan T18)** Attach a real Bearer — `scripts/e2e/suite-5-hitl-authority.sh`, `scripts/e2e/suite-7-machine-identity.sh`, `scripts/e2e/suite-15-artifact-isolation.sh`
- [ ] [T033] [P] **(plan T18)** Attach a real Bearer — `scripts/e2e/suite-18-opa-governance.sh`, `scripts/e2e/suite-51-credential-validation.sh`, `scripts/e2e/suite-81-deploy-tool-autograt.sh`
- [ ] [T034] [P] **(plan T18)** Attach a real Bearer — `scripts/e2e/suite-84-mcp-tools.sh`, `scripts/e2e/suite-45-hitl-e2e.sh` *(verify)*, `scripts/e2e/suite-65-production-hitl-console.sh`

**Group: chat, knowledge, agent** (plan T19)
- [ ] [T035] [P] **(plan T19)** Attach a real Bearer — `scripts/e2e/suite-8-playground.sh`, `scripts/e2e/suite-14-consumer-chat.sh` *(verify)*, `scripts/e2e/suite-16-create-agent.sh` *(verify)*
- [ ] [T036] [P] **(plan T19)** Attach a real Bearer — `scripts/e2e/suite-6-asset-lifecycle.sh`, `scripts/e2e/suite-77-knowledge-rag.sh`, `scripts/e2e/suite-80-agent-knowledge-binding.sh`

**Sweep**
- [ ] [T037] **(plan T15–T19)** Re-verify — **without editing unless a gap is found** — the suites T025–T036 did not touch that share the newly-protected surfaces: `43, 66, 70, 71, 75-context-storage, 75-eval-v2-scheduled, 77-eval-v2-webhook, 79-workflow-hitl, 82, 83, 86, 87, 88, 89, 94, 95, 96`. Any suite that 401s at setup gets the same three-step recipe. Record the result per suite; a skipped or weakened suite is a shipped regression, not a pass — `scripts/e2e/`

**Phase gate (6+7 together):** `for f in workflows teams llm_providers admin playground_approvals deployments versions auth_configs agent_tools agent_runs; do python3 -c "import ast; ast.parse(open('services/registry-api/routers/$f.py').read())"; done`; then `bash scripts/run-tests.sh --layer api --group deploy,agent,eval,workflow,execution,governance,hitl,tools,chat,knowledge` green with behaviour unchanged.

---

## Checkpoint 3 — Router authentication
_Gate: Phases 5-7 must be complete **and committed together** (HC-3). Run before starting Phase 8._
_What you prove: an anonymous request reaches nothing on the protected routes, the five named exemptions still answer their in-cluster machine callers, and the reviewer persona creates itself._

- [ ] [CP3a] Deploy script — `scripts/deploy-r0-cp3.sh`
- [ ] [CP3b] Infrastructure smoke test — `scripts/smoke-test-r0-cp3-infra.sh`
- [ ] [CP3c] Behaviour smoke test: happy path + at least one failure case — `scripts/smoke-test-r0-cp3-behaviour.sh`

**What each script must do:**

`deploy-r0-cp3.sh` — tag-mirror assertion, `bash scripts/deploy-cpe2e.sh`, `kubectl rollout status` for **registry-api, deploy-controller and declarative-runner** (the three that must survive FR-11), then assert the running registry-api image tag equals the declared tag.

`smoke-test-r0-cp3-infra.sh` — **the exemption canary, at infra level.**
- In-pod `python3 -c`: `from main import app`; walk `app.routes`; for every route whose endpoint module is one of the ten, compute whether `require_user` appears in its flattened dependencies; compare the resulting protected/exempt partition against the literal table from `contracts/router-auth.md` written into the script. **Hard-fail naming any route on either side of the difference** — a *new* unauthenticated route on one of the ten must fail here, not in review.
- Assert `deploy-controller`, `declarative-runner` and `eval-runner` pods are `Running` and their `restartCount` did not increase.
- `kubectl logs -n agentshield-platform -l app.kubernetes.io/name=deploy-controller --since=10m | grep -c "401"` **== 0**.

`smoke-test-r0-cp3-behaviour.sh` (mint a Bearer with `e2e_set_token`; drive `curl` from inside the registry-api pod)
- **Happy (Story 4.2):** with `Authorization: Bearer $E2E_TOKEN`, one representative route per protected group returns **non-401** and the same status/body shape as before R1 — `GET /api/v1/teams/`, `GET /api/v1/llm-providers/`, `GET /api/v1/agent-graphs/`, `GET /api/v1/playground/approvals`, `GET /api/v1/deployments/workflows`.
- **Failure case (Story 4.1 / SC-4):** the **same** routes with **no** `Authorization` header → **401** with `{"detail":"Authentication required"}`. Assert the status code explicitly, per route.
- **Exemptions still answer (V-7):** with no `Authorization` header, `GET /api/v1/deployments/`, `GET /api/v1/versions/<bogus-id>`, `GET /api/v1/agents/<name>/tools`, `GET /api/v1/auth-configs/<bogus-id>/secret-ref` and one `GET /api/v1/agent-runs/...` return a status that is **not 401** (404/200 are both fine — the assertion is on the absence of the auth refusal).
- **FR-10:** run `e2e_ensure_reviewer "$NS" "$POD"` **twice**; assert the second is a no-op, a `password` grant for `agent-reviewer`/`Reviewer2024` on client `agentshield-studio` succeeds, and a `user_team_assignments` row exists for its sub with `role='contributor'`.

> **To run:** bump + mirror the image tag, then:
> ```bash
> bash scripts/deploy-r0-cp3.sh && \
> bash scripts/smoke-test-r0-cp3-infra.sh && \
> bash scripts/smoke-test-r0-cp3-behaviour.sh && \
> bash scripts/run-tests.sh --layer api --group deploy,agent,eval,workflow,execution,governance,hitl,tools,chat,knowledge
> ```
> **Pass criteria:** all exit 0 and print `PASS`. The `app.routes` partition matches `contracts/router-auth.md` exactly. Every named group is green — a red suite here means the token sweep missed a call site, and **the fix is to add the Bearer, never to relax the router**. `deploy-controller` reconciliation and `declarative-runner` run recording are uninterrupted.

---

## Phase 8 — Prove the journey: Playwright + suite-97

_DoD rule 1 (a real user journey, not an endpoint), DoD rule 7 (the regression reproduced first), SC-2._

**Depends on:** everything above. T004 already created `suite-97` with `T-S97-004`; these tasks append the remaining eleven cases to the same file.

- [X] [T038] **(plan T20)** Append the Admin-menu journey case: `test("bootstrap gives platform-admin a role row, so the Admin menu renders (R0 / Decision 40)")` — `page.waitForResponse` on `GET /api/v1/me`, `page.goto(BASE_URL)`, assert `body.role === "platform-admin"` and `body.team === "platform"`, then `await expect(page.getByRole("button", { name: /^Admin$/ })).toBeVisible()`. The comment must record the 2026-07-20 structural symptom (row pinned to a dead `sub` → `/me` role null → `Sidebar.tsx:392 isAtLeast("platform-admin")` false → Admin section silently vanishes). **Read `studio/src/components/Sidebar.tsx` `CollapsibleSection` first** and match the rendered element if it is not a `button`. No manifest change — `browser|governance,rbac|e2e/admin-access-roles.spec.ts` is already registered at `test-manifest.txt:151` — `studio/e2e/admin-access-roles.spec.ts`
- [X] [T039] **(plan T21)** Append the bootstrap cases: **T-S97-001** (exactly one Keycloak `platform-admin`; one row for its `id` with `team_name='platform'`, `role='platform-admin'`, `assigned_by='system:bootstrap'`), **T-S97-002** (restart idempotence — read `assigned_at`, `kubectl rollout restart`, wait `/ready` 200, re-read → same `user_sub` **and** same `assigned_at`, still exactly one Keycloak user; this is the DoD-2 save→reload→assert round-trip), **T-S97-003** (replica race — with `replicaCount: 2`, `count(*) WHERE role='platform-admin' AND assigned_by='system:bootstrap'` is 1, `list_users(username='platform-admin')` length is 1, and at least one pod's log carries `"another replica holds the lock"`) — `scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh`
- [X] [T040] **(plan T21)** Append the availability cases: **T-S97-005** (non-fatal — scale Keycloak to 0, restart registry-api, assert over 90s that `restartCount` does not increase, `/health` stays 200 and `/ready` is 503 `status="bootstrapping"`; scale Keycloak back and assert `/ready` reaches 200 within 3 retry intervals) and **T-S97-006** (`/ready` body shape: 200 → `{"status":"ready"}`; bootstrapping → 503 with `status`, `detail`, `attempts` — per `contracts/bootstrap-and-ready.md`) — `scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh`
- [X] [T041] **(plan T21)** Append the R0-invariant cases: **T-S97-007** (atomic happy path — throwaway user, assert Keycloak user + realm roles + row all exist, delete), **T-S97-008** (compensation — `create_user` in-pod with `commit` monkeypatched to raise → `HTTPException.status_code == 502` **and** `list_users(username=<throwaway>)` empty), **T-S97-009** (role-omitting insert raises NOT NULL; `information_schema.columns.column_default` for `role` is NULL), **T-S97-010** (a: `get_user_global_role` for a row-less sub raises `NoPlatformRole`; b: over HTTP `GET /api/v1/me` → 403 with `error_code == "no_platform_role"` and the sub echoed; c: a row with `role='agent:reviewer'` resolves verbatim with **no** exception and `ROLE_HIERARCHY.get(role,0) == 0`; clean up) — `scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh`
> **Implementation notes on T038–T041 (recorded so T042 does not redo them).**
> - **T-S97-012 landed with T041, not T042.** It asserts FR-12 / SC-3, which are R0 — the R1 half of T042 is `T-S97-011` alone, plus the manifest description. Note the audit response has **six** fields, not the seven the task text says (`checked_at`, `keycloak_user_count`, `assignment_row_count`, `orphan_users`, `stale_rows`, `matched_count` — `IdentityAuditResponse`, `admin_users.py:351-357`); the case asserts that exact set.
> - **The suite was re-ordered, not appended to blindly.** The non-destructive cases run FIRST (phases 1–4) and the realm-recreation case LAST (phases 5–8), because a setup failure in the destructive leg used to `exit 1` and take every other case's result with it. Setup failures there now `return` and are recorded, so the completeness gate still reports what never ran. Phase count is 9, and `run_repin_case` holds the original T-S97-004 body verbatim.
> - **T-S97-003 asserts the single-flight deterministically.** A rolling update brings replicas up sequentially, so two pods contending the advisory lock at the same instant is a scheduling accident, not something `replicaCount: 2` guarantees — grepping pod logs for `"another replica holds the lock"` would assert the accident. The case still scales to 2 and asserts the counts under two replicas, and additionally races two concurrent `ensure_platform_admin()` calls in one process (same lock, two connections), capturing `bootstrap_admin.logger` to prove the loser skipped.
> - **T-S97-005 does not restart registry-api with Keycloak at 0.** It cannot: the pod's `wait-for-keycloak` init container (`charts/agentshield/charts/registry-api/templates/deployment.yaml:36-47`) blocks until the master realm answers, so the new pod never reaches its main container and the old pod keeps serving a `/ready` that went green before the outage — the assertion would be about init containers. What is asserted is the actual NFR: the running pod does not restart, `/health` never blinks over 90s, and `ensure_platform_admin()` returns `False` without raising. **T-S97-006's 503 leg is then read through the REAL `/ready` handler in the state that real outage produced**, not a stubbed flag.
> - **In-pod HTTP probes use `python3`/`urllib`, never `curl`** — the registry-api image is `python:3.12-slim` (`services/registry-api/Dockerfile:1`) and has no `curl`, so an exec would exit 127 and the empty output would read as an outage. The CP1–CP4 scripts still specify `curl`; they need the same correction when they are written.
> - **T038's locator is `getByRole("button", { name: /^admin$/i })`.** `CollapsibleSection` (`Sidebar.tsx:201-224`) does render a real `<button>`, but its label is uppercased by CSS (`uppercase`, `:216`); the case-insensitive flag keeps the assertion independent of whether accessible-name computation applies `text-transform`.
> - **The phase gate's "12/12" is stale.** The suite now emits **14 PASS lines**: eleven case IDs, of which `T-S97-005` and `T-S97-006` each report two legs (host-measured pod behaviour vs. the in-pod bootstrap/`/ready` legs), plus the completeness gate. It becomes 15 when T042 adds `T-S97-011` — and `REQUIRED_IDS` in the suite must gain `011` in that same commit. **Assert `REQUIRED_IDS`, not a line count** (CP4c's "fail if the case count is not 12" needs the same correction when it is written).

- [ ] [T042] **(plan T21)** Append the R1 case and finalise the manifest entry (**T-S97-012 already landed in T041 — do not add it twice**): **T-S97-011** (in-pod `from main import app`, walk `app.routes`, compute the protected/exempt partition for the ten routers and assert it equals the mapping written **literally** in the suite from `contracts/router-auth.md`; then over HTTP with no `Authorization`, 401 for one representative route per protected group and a non-401 for each exempt route). Add `011` to the suite's `REQUIRED_IDS` in the same commit. Update the manifest description to the full plan text (`R0/R1 — platform-admin bootstrap (username lookup, advisory-lock single-flight, realm-recreation re-pin), missing-row 403, role NOT NULL, and the ten-router 401 matrix with its named exemptions`) — `scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh`, `scripts/test-manifest.txt`

**Phase gate:** `cd studio && npm run typecheck` clean; `bash scripts/studio-e2e.sh` green; `bash scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh` → **12/12**; `bash scripts/run-tests.sh --audit` → `Manifest audit clean`.

---

## Checkpoint 4 — Full R0/R1 journey
_Gate: Phase 8 must be complete. Run before starting Phase 9 (ship)._
_What you prove: the whole chain works from the browser to the DB, suite-97 is 12/12 including the case that was RED first, and no symbol this change introduced is orphaned._

- [ ] [CP4a] Deploy script — `scripts/deploy-r0-cp4.sh`
- [ ] [CP4b] Infrastructure smoke test — `scripts/smoke-test-r0-cp4-infra.sh`
- [ ] [CP4c] Behaviour smoke test: happy path + at least one failure case — `scripts/smoke-test-r0-cp4-behaviour.sh`

**What each script must do:**

`deploy-r0-cp4.sh` — tag-mirror assertion, `bash scripts/deploy-cpe2e.sh`, `kubectl rollout status` for registry-api + studio, running-image assertion, then `grep -c "seed-platform-admin-role" ` over the captured deploy log **== 0** (SC-1: a fresh install has a working admin with zero manual seed steps).

`smoke-test-r0-cp4-infra.sh` — **DoD rule 3, mechanised.** Each of these must return **≥1 non-definition hit** or the script exits non-zero naming the orphan:
```bash
grep -rn "ensure_platform_admin"  services/registry-api/ --include=*.py
grep -rn "bootstrap_admin_loop"   services/registry-api/ --include=*.py
grep -rn "bootstrap_state"        services/registry-api/ --include=*.py
grep -rn "NoPlatformRole"         services/registry-api/ --include=*.py
grep -rn "identity-audit"         services/registry-api/ scripts/ docs/
grep -rn "e2e_ensure_reviewer"    scripts/e2e/
```
Plus: `bash scripts/run-tests.sh --audit` prints `Manifest audit clean`; the two image tags agree; `cd studio && npm run typecheck` exits 0; every touched `.py` passes `python3 -c "import ast; ast.parse(...)"`.

`smoke-test-r0-cp4-behaviour.sh`
- **Happy:** `bash scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh` → parse the result output and assert **12 PASS / 0 FAIL** (fail the script if the case count is not 12 — a silently dropped case is the failure mode this repo keeps paying for).
- **Happy:** `bash scripts/studio-e2e.sh` and assert the `admin-access-roles.spec.ts` case *"bootstrap gives platform-admin a role row"* passed.
- **Failure case A:** anonymous `GET /api/v1/me` → **401**.
- **Failure case B:** a valid JWT for a `sub` with no row → **403** with `error_code == "no_platform_role"` (create the Keycloak user directly, grant a token, assert, delete).
- **Failure case C (SC-2, the headline regression):** confirm `T-S97-004` — realm-recreation re-pin — is among the 12 PASS and print its evidence line, cross-referencing that it was demonstrated RED in T004.

> **To run:** bump + mirror the image tag, then:
> ```bash
> bash scripts/deploy-r0-cp4.sh && \
> bash scripts/smoke-test-r0-cp4-infra.sh && \
> bash scripts/smoke-test-r0-cp4-behaviour.sh && \
> bash scripts/run-tests.sh --layer api --group rbac,governance,deploy,agent,eval,workflow,execution,hitl,chat,tools,knowledge && \
> bash scripts/studio-e2e.sh
> ```
> **Pass criteria:** all exit 0 and print `PASS`. suite-97 is 12/12, the Playwright Admin-menu case is green, every orphan grep has a live caller, the manifest audit is clean, and the full-run gate from the plan's Execution Notes passes both layers.

---

## Phase 9 — Ship: image tags, bug doc, gap ledger, design docs

_DoD rule 5 (honest gap ledger) and rule 8 (document every bug). Nothing here changes runtime behaviour — but the change is **not done** without it._

**Depends on:** CP4 green.

- [X] [T043] **(plan T22)** Pin the ship tag in **both** files, same commit: `REGISTRY_API_TAG="0.2.259"` at `:369` with an R0/R1 summary prepended to the comment chain ending `MUST match charts/agentshield/values.yaml.`, and the exact mirror `tag: "0.2.259"` at `:744`. (Current head is `0.2.258`; if the checkpoints consumed interim patches, pin whatever tag the final green CP4 run used — the invariant is that the two files are **identical**, never that the number is 259.) — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
- [X] [T044] **(plan T22)** Write the DoD-8 postmortem: one-line title; **Found** 2026-07-20 / **Fixed** `<the shipped REGISTRY_API_TAG>`; **Symptom** — the Studio Admin menu silently disappears after a realm recreation and `/me` returns `role: "contributor"`; **Root cause** — the assignment row was pinned to a `sub` captured at seed time and nothing in the install wrote it at all; the design flaw is coupling a durable row to an identifier the IdP is free to reissue; **Fix** — the platform creates the admin and looks it up by **username** every start, so re-pinning falls out for free (the class-fix: it removes the stored-`sub` coupling, not just this instance). Cross-link `T-S97-004` (the regression test written RED first in T004) and `bootstrap_admin.py` — `docs/bugs/platform-admin-role-stranded-on-realm-recreation.md`
- [X] [T045] **(plan T22)** Gap ledger under "Known gaps" — *deferred (intentional)*: **G-R0-1** role/scope union (owner R5) · **G-R0-2** `approval_authority` remains the live HITL mechanism (R5) · **G-R0-4** no Keycloak realm-role objects for the three global roles · **G-R0-5** `seed-platform-admin-role.sh` retained as a repair tool · **G-R0-6** `UserCreate.role` still defaults to the legacy `"operator"`. *not-yet-wired (debt)*: **G-R0-3** a stale row survives realm recreation or a hand-deleted admin · **G-R1-1** `routers/agent_runs.py` entirely unauthenticated (declarative-runner ×4, eval-runner ×1) · **G-R1-2** `GET /api/v1/deployments/` + `PATCH /api/v1/deployments/{id}` · **G-R1-3** `GET /api/v1/versions/{id}` · **G-R1-4** `GET /api/v1/auth-configs/{id}/secret-ref` · **G-R1-5** `GET /api/v1/agents/{name}/tools` (all five owned by identity propagation, pinned by T-S97-011) · **G-R1-6** `sdk/agentshield_sdk/cli.py:194,207` sends no token, so `agentshield deploy` now 401s — `docs/testing/manual-ui-e2e-test-plan.md`
- [ ] [T046] **(plan T22)** Reconcile the design docs with what actually shipped (DoD rule 6): `rbac-and-artifact-authorization.md` §1.4 gains an "R1 outcome" column and §5's R0/R1 paragraphs get a `**SHIPPED <date> (<tag>)**` prefix plus the exemption caveat; `rbac-r0-r1-spec.md` FR-11 restated to the **verified** exemption set from V-7 (its current wording, "the ten routers", is what V-7 corrects); `decisions.md` appends the FR-11 exemption consequence under Decision 40's "Consequences" — `docs/design/rbac-and-artifact-authorization.md`, `docs/design/rbac-r0-r1-spec.md`, `docs/decisions.md`

**Phase gate:** `grep -n 'REGISTRY_API_TAG=' scripts/deploy-cpe2e.sh | head -1` and `sed -n '744p' charts/agentshield/values.yaml` show the **same** tag; every gap in the ledger is tagged *deferred (intentional)* or *not-yet-wired (debt)*; the bug doc, the regression test and the fix cross-reference each other.

---

## Suggested MVP scope

**MVP = R0 (the invariant), shipped as its own commit.** R1 (the router sweep) is a second commit — that is the spec's own boundary and it keeps the ~28-suite blast radius out of the change that makes the admin work.

**In the MVP:** Phase 1 → Phase 2 → **CP1** → Phase 3 → Phase 4 → **CP2** → Phase 5, then T038 (the Playwright journey — DoD 1), T039–T041 (suite-97's R0 cases), and from Phase 9: T043 (tags), T044 (bug doc), T045 (the G-R0-* half of the ledger).

That is **T001–T019, T038–T041, T043–T045 + CP1 + CP2** — 26 implementation tasks and 6 checkpoint tasks. It delivers Stories 1, 2 and 3 end-to-end: a fresh install has a working admin with no seed script (SC-1), no half-created user can exist, an unknown caller is refused with a stable code, and the 2026-07-20 regression has a test that was RED first (SC-2).

**Deferred to the follow-up commit:** Phase 6 + Phase 7 (**must ship together — HC-3**), CP3, T042 (the 401 matrix + exemption canary), T037 (the re-verify sweep), T046 (the FR-11 doc reconciliation). Story 4 / FR-11 / SC-4 land there.

**Why Phase 5 is in the MVP and not deferred:** Phase 2 removes `agent-reviewer` from the chart, so suites 76/78/82/83 go dark on the next fresh install unless T017–T019 land in the same change. Cutting it would be exactly the "silence is how debt becomes a surprise" failure the constitution names.

---

## Traceability

### New ID → plan task

| Plan | New IDs | Split reason |
|---|---|---|
| T1 | T001 | kept (3 files) |
| T2 | T002 | kept |
| T3 | T003 | kept |
| T4 | T005 | kept |
| T5 | T006 | kept |
| T6 | T007, T008, T009 | 4 files → chart / deployment / scripts |
| T7 | T010 | kept |
| T8 | T011 | kept |
| T9 | T012 | kept |
| T10 | T013, T014, T015 | 4 files → rbac / me+main / schedules |
| T11 | T016 | kept |
| T12 | T017 | kept |
| T13 | T018, T019 | 4 suites → 2 + 2 |
| T14 | T020, T021, T022, T023, T024 | 10 routers → grouped by treatment (router-level ×2, mixed, per-endpoint, fully-exempt) |
| T15 | T025, T026, T027 | 9 suites → 3 + 3 + 3 |
| T16 | T028, T029 | 6 suites → 3 + 3 |
| T17 | T030, T031 | 4 suites → 2 + 2 |
| T18 | T032, T033, T034 | 9 suites → 3 + 3 + 3 |
| T19 | T035, T036 | 6 suites → 3 + 3 |
| T15–T19 | T037 | the "re-verify, don't edit" sweep, broken out |
| T20 | T038 | kept |
| T21 | **T004**, T039, T040, T041, T042 | 12 cases → T-S97-004 **hoisted to Phase 2** (DoD 7: RED first), then bootstrap / availability / R0-invariants / R1 |
| T22 | T043, T044, T045, T046 | 7+ files → tags / bug doc / gap ledger / design docs; the orphan sweep became **CP4b** |

### FR → tasks

| FR | Tasks |
|---|---|
| **FR-1** bootstrap from `lifespan` | T003, T005, T006, T039 · CP1b, CP1c |
| **FR-2** advisory-lock single-flight | T005, T039 · CP1c (failure case B) |
| **FR-3** lookup by username, realm recreation self-heals | T002, T005, **T004 (RED first)** · CP4c |
| **FR-4** drop `server_default`, every insert states a role | T012, T041 · CP2b, CP2c (failure case A) |
| **FR-5** `get_user_global_role` raises → 403, one path | T013, T014, T015, T016, T041 · CP2c (failure case B) |
| **FR-6** unrecognized value keeps rank 0 | T013, T041 · CP2c (failure case D) |
| **FR-7** suites state a role / annotate the scope | T001 · CP1b (HC-1 guard) |
| **FR-8** atomic `POST /admin/users` + compensation | T010, T041 · CP2c (failure case C) |
| **FR-9** chart stops creating users; seed demoted | T007, T008, T009 · CP1b, CP4a |
| **FR-10** four suites create `agent-reviewer` themselves | T002, T017, T018, T019 · CP3c |
| **FR-11** `require_user` + named exemptions | T020–T024, T025–T037, T042 · CP3b, CP3c |
| **FR-12** read-only identity audit | T011, T042 · CP2b, CP2c |

### Story → phase

| Story (all **P1**) | Phase | Proven by |
|---|---|---|
| 1 — a fresh install has a working admin | **2** | CP1c + T038 (Playwright Admin menu) + T039 |
| 2 — a half-created user cannot exist | **3** | CP2c (failure case C) + T041 (T-S97-007/008) |
| 3 — an unknown caller is refused, loudly | **4** | CP2c (failure cases A/B/D) + T041 (T-S97-009/010) |
| 4 — anonymous requests reach nothing | **6 + 7** | CP3b/CP3c + T042 (T-S97-011) |

### Success criteria

| SC | Where |
|---|---|
| SC-1 fresh install, zero manual seed steps | CP4a (`grep -c seed-platform-admin-role` on the deploy log == 0) |
| SC-2 suite-97 fails on realm recreation before, passes after | T004 (RED) → CP4c (failure case C) |
| SC-3 audit reports zero orphans / zero stale rows | CP2c + T042 (T-S97-012) |
| SC-4 no route on the ten answers an unauthenticated request | CP3c + T042 (T-S97-011) |
| SC-5 no new exported symbol orphaned | **CP4b** (the six greps) |

---

## Checkpoint script requirements (applies to all 12 CP tasks)

Every script under `scripts/` written by a `[CPNx]` task must:

- start with `#!/usr/bin/env bash` and `set -euo pipefail`;
- print `echo "=== Checkpoint N: <name> ==="` as its first output;
- use real `kubectl` / `curl` / `jq` / `python3` commands — **no placeholder TODOs**, no `echo "would run..."`;
- resolve the pod the way every suite in this repo does:
  ```bash
  NAMESPACE="${NAMESPACE:-agentshield-platform}"
  POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
        --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
  [ -n "$POD" ] || { echo "FAIL: no running registry-api pod in $NAMESPACE"; exit 1; }
  ```
- mint tokens through the established helper, never by hand: `source scripts/e2e/lib/e2e-auth.sh` then `e2e_set_token "$NAMESPACE" "$POD"` (**bare, not in a command substitution** — `e2e-auth.sh:110-121`);
- drive API assertions **from inside the pod** — `kubectl exec -n "$NAMESPACE" "$POD" -c registry-api -- curl -s -w '\n%{http_code}' localhost:8000/...` and `kubectl exec … -- python3 -c "..."` — so no port-forward or host-side Keycloak reachability is needed;
- assert HTTP status codes and key JSON fields **explicitly** (compare the parsed value, never `grep` a substring of a body);
- exit non-zero on the **first** failure with a message naming the cause, and `exit 0` on full pass;
- end with `echo "PASS"`;
- **restore any state it mutated** before exiting (delete throwaway Keycloak users and rows, scale anything it scaled back up, re-run `ensure_platform_admin` after monkeypatching settings). A checkpoint that leaves the cluster dirty turns the next phase's failure into a mystery.

Wrap the existing tooling — `scripts/deploy-cpe2e.sh`, `scripts/run-tests.sh`, `scripts/studio-e2e.sh`, `scripts/e2e/lib/e2e-auth.sh`. Do **not** invent a parallel deploy mechanism. The `smoke-test-r0-cpN-*` naming keeps these clear of the existing `scripts/smoke-test-cp1-*.sh` family.

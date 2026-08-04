# The Studio Admin menu silently vanished — the admin's role row was pinned to a `sub` Keycloak had already replaced

**Found:** 2026-07-20 (reported as *"the Admin section is gone from the sidebar"* — nothing errored)
**Fixed:** 2026-08-04 — registry-api `0.2.259`, migration `0079`, `services/registry-api/bootstrap_admin.py`

## Symptom

The Admin section disappeared from the Studio sidebar. No error, no toast, no failed
request — the menu was simply not rendered, and every platform-admin-gated screen became
unreachable by navigation.

`GET /api/v1/me` answered **200** with:

```json
{ "sub": "75c7c8b3-…", "preferred_username": "platform-admin",
  "team": "platform", "role": "contributor" }
```

`role: "contributor"` for the platform administrator. The chain is short and entirely
silent: `/me` resolves the role from `user_team_assignments` keyed on the caller's
Keycloak `sub`; Studio stores that in `AuthContext`; `Sidebar.tsx:392` renders the Admin
section behind `isAtLeast("platform-admin")`. A wrong role does not fail — it hides a
menu.

The DB said what had happened:

| `user_sub` | `role` | `assigned_by` |
|---|---|---|
| `643b0e62…` | `platform-admin` | `system:seed-platform-admin` |
| `75c7c8b3…` | `operator` → normalized `contributor` | *(role-less insert, took the column default)* |

`643b0e62…` was the sub the admin had **when the row was hand-seeded**. `75c7c8b3…` was
the sub Keycloak was issuing **that day**. The realm had been recreated in between.

## Root cause

**A durable row was coupled to an identifier the IdP is free to reissue — and nothing in
the install wrote that row at all.**

Two halves, and only together do they produce a vanishing menu:

1. **Nothing owned the row.** `charts/agentshield/templates/realm-init-job.yaml` created
   the Keycloak *user* and stopped there. The `user_team_assignments` row was created
   once, by hand, by `scripts/seed-platform-admin-role.sh` — a script someone had to
   remember to run. An install step that lives in a human's memory is not an install
   step.
2. **The row was keyed on `sub`.** A Keycloak `sub` is a per-realm identity, not a
   stable name: recreate the realm (fresh cluster, realm re-import, a wiped Postgres) and
   the same human comes back as a different subject. The seeded row stayed valid-looking
   and stranded on a subject that no longer existed, while the live admin got a row minted
   without a role — which then took `user_team_assignments.role`'s `server_default`
   `'operator'`, normalized to `contributor` by `rbac._LEGACY_MAP`.

The second half is what made it silent instead of loud. A missing row would at least have
resolved to nothing; a **defaulted** row produced a confident, wrong answer. The column
default was the platform inventing an authorization decision — migration `0013` created
it, `0044` and `0075` migrated the *data* twice without ever touching the default, so
every role-omitting insert kept re-minting the exact legacy value those migrations existed
to remove.

## Fix

**The platform creates its own admin, and finds it by USERNAME on every start.**

`services/registry-api/bootstrap_admin.py::ensure_platform_admin`, run as a background
task from `lifespan`:

- looks `platform-admin` up **by username** (`kc.list_users(username=…, exact=True)`),
  never by a stored `sub`, so the re-pin falls out of the design rather than out of a
  script someone remembers to run;
- creates the user if absent, then reconciles its profile **unconditionally** (email,
  `emailVerified`, cleared `requiredActions`) so a hand-edited admin self-heals;
- upserts the assignment row `(platform, platform-admin, system:bootstrap)` with a guarded
  `WHERE … IS DISTINCT FROM`, so a restart is a genuine no-op and `assigned_at` still
  means "when this role last really changed";
- is single-flighted across replicas by `pg_try_advisory_lock`, and **never raises** —
  a Keycloak outage holds `/ready` at 503 `{"status":"bootstrapping"}` instead of
  crash-looping the pod.

**Why this is the class-fix and not a repair of this instance.** The obvious repair was to
run `seed-platform-admin-role.sh` again (or call it from the deploy script). That fixes
*this* stranding and leaves the coupling: the next realm recreation strands the next row.
What actually changed is that **no durable row is keyed on a value the IdP owns** — the
identifier the platform reasons about is the username, and the `sub` is resolved fresh on
every start. Re-pinning is no longer an event anyone has to notice.

Three supporting changes remove the ways the old state could be re-created:

- **`charts/agentshield/templates/realm-init-job.yaml` creates no users at all** (Decision
  40). Users come from platform code or from `POST /api/v1/admin/users`, nowhere else, so
  "a user with no row" has no producer left. `deploy-cpe2e.sh` no longer calls the seed
  script, which is retained only as a manual repair tool (G-R0-5).
- **Migration `0079` drops the `role` `server_default`.** The column can no longer invent
  a role; a role-omitting insert now fails loudly at the DB. Every in-repo inserter was
  fixed **first**, on purpose, so the migration lands on a clean codebase.
- **`rbac.get_user_global_role` is the one resolution path and it RAISES.** A subject with
  no row gets `NoPlatformRole` → one app-level handler → **403**
  `{"error_code": "no_platform_role", "sub": …}`. `routers/me.py` no longer normalizes a
  role of its own; two independent answers to "what role is this" is exactly how
  `approvals._ADMIN_ROLES` diverged (see
  `docs/bugs/production-hitl-decide-403-authority.md`).

An unrecognized value is still returned **verbatim** at rank 0 — `agent:reviewer` is a
reviewer *scope*, not a broken global role (Decision 42), and `approvals.py:48` matches
that literal against the same column. Splitting the union is R5 (G-R0-1).

## Tests

- **`scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh` — `T-S97-004`, written and
  demonstrated RED first (DoD rule 7).** It records the live admin's `sub`, **deletes the
  Keycloak user** (the smallest faithful reproduction of "the realm was recreated": the
  durable row is left pinned to a subject Keycloak will never issue again), restarts
  registry-api, and requires that the platform re-pins itself: a new user exists, its `id`
  differs from the deleted one, the row moved onto the new `id` with
  `assigned_by='system:bootstrap'` (which proves the *lifespan bootstrap* wrote it — the
  seed script stamps `system:seed-platform-admin`), and a real password grant calling
  `GET /api/v1/me` answers `role == "platform-admin"`. That last leg is what the symptom
  was actually made of. Against pre-R0 code the deletion is permanent and the case FAILs
  naming the missing user — that RED run is the reproduction.
- **`T-S97-001/002`** pin the invariant and the restart no-op; **`T-S97-009/010`** pin the
  dropped default and the 403 refusal; **`T-S97-012`** is the `identity-audit` sweep.
- **`studio/e2e/admin-access-roles.spec.ts` — "bootstrap gives platform-admin a role row,
  so the Admin menu renders"** drives the layer where the symptom lived: real Keycloak
  login → `GET /me` → the Admin section visible in the sidebar. No bash suite can catch a
  menu that is not rendered.

## Lessons

- **A silent wrong answer beats a loud missing one, and that is the danger.** The column
  default turned "I don't know this subject" into "this subject is an operator". Deny-by-
  default is not a style preference; it is the difference between a 403 someone reports
  and a menu nobody notices is gone.
- **Never key a durable row on an identifier another system owns and may reissue.** If you
  must store one, store it as a *cache* of a lookup by something stable, and refresh it on
  every start.
- **An install step that lives in a runbook is not an install step.** The seed script
  worked perfectly every time it was run. It was not run on the cluster where it mattered.
- **Test the layer that can actually fail.** Every API assertion about `/me` passed
  throughout — it returned 200 with a role. Only the browser could see that the role was
  the wrong one.

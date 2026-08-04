# Contract — `GET /api/v1/me`, `POST /api/v1/admin/users`, `GET /api/v1/admin/identity-audit`

## `GET /api/v1/me`

Unchanged success shape:
```json
{ "sub": "75c7c8b3-…", "email": "platform-admin@agentshield.local",
  "preferred_username": "platform-admin", "team": "platform",
  "role": "platform-admin", "artifact_roles": [] }
```

**New failure — subject has no `user_team_assignments` row:**
```
HTTP/1.1 403 Forbidden
{ "detail": "No platform role assigned to '9f2c…'. Users are created by a platform administrator.",
  "error_code": "no_platform_role",
  "sub": "9f2c…" }
```

`error_code` is the stable contract; `detail` is prose and may change. Emitted by one app-level handler, so every route that resolves a global role answers identically. Server log: `ERROR 403 no_platform_role: sub=9f2c… path=/api/v1/me`.

401 (no/invalid token) is unchanged: `{"detail": "Authentication required"}` with `WWW-Authenticate: Bearer`.

**Role resolution is single-path.** `role` comes from `rbac.get_user_global_role` only. A legacy stored value normalizes (`operator → contributor`). An unrecognized value — e.g. `agent:reviewer` — is returned **verbatim**; Studio's `ROLE_LEVEL` (`studio/src/contexts/AuthContext.tsx:7-15`) then yields 0, matching the backend.

**Studio impact.** A 403 leaves `role` null, `isAtLeast("platform-admin")` false, and the Admin section hidden (`Sidebar.tsx:392`). No Studio source change is required or made.

---

## `POST /api/v1/admin/users`

Request (unchanged):
```json
{ "username": "agent-reviewer", "email": "agent-reviewer@agentshield.local",
  "first_name": "Agent", "last_name": "Reviewer",
  "temp_password": "Reviewer2024", "team": "platform", "role": "contributor" }
```

**201** — unchanged shape, and now **all three** of the Keycloak user, its realm-role mapping, and the assignment row are guaranteed present.

**Atomicity contract.** Exactly one of two outcomes:
1. **201** — Keycloak user + realm role + row, committed.
2. **4xx/5xx** — no Keycloak user, no row. Specifically:
   - `409` username/email already exists (from `kc_create`, before anything else happens)
   - `502` `{"detail": "User creation rolled back: <Type>: <msg>"}` for a failure at `set_user_realm_role`, `_upsert_team`, or `commit` — the DB is rolled back and a compensating `kc_delete` runs

`set_user_realm_role` failure is **fatal**, not swallowed. If the compensating `kc_delete` itself fails, the 502 is still returned and an ERROR names the orphan and points at `GET /api/v1/admin/identity-audit` — the one case the audit exists to surface.

**Transaction boundary.** `_upsert_team` does not commit. `create_user`, `patch_user` and `delete_user` each commit explicitly. Any future caller of `_upsert_team` must too.

**Created-user usability.** The user is created with `emailVerified: true`, a temporary password and `requiredActions: ["UPDATE_PASSWORD"]` — a browser login will prompt for a new password. A caller that needs an immediately-usable credential (the e2e fixtures) follows with `POST /api/v1/admin/users/{kc_id}/reset-password` `{"new_password": "...", "temporary": false}`.

---

## `GET /api/v1/admin/identity-audit`

**READ-ONLY. Reports, never deletes** (spec OQ-2 → option (a)).

**200, clean cluster:**
```json
{ "checked_at": "2026-08-04T18:22:10.481Z",
  "keycloak_user_count": 3, "assignment_row_count": 3, "matched_count": 3,
  "orphan_users": [], "stale_rows": [] }
```

**200, populated example** (the state found on 2026-08-04 before the one-time cleanup):
```json
{ "checked_at": "2026-08-04T18:22:10.481Z",
  "keycloak_user_count": 6, "assignment_row_count": 5, "matched_count": 3,
  "orphan_users": [
    {"kc_id": "7f01b0…", "username": "probe-7f01b0", "email": null},
    {"kc_id": "9b802f…", "username": "probe2-9b802f", "email": null},
    {"kc_id": "f56546…", "username": "s96-nobody-f56546", "email": null}],
  "stale_rows": [
    {"user_sub": "s53-user-fd3a092a", "team_name": "platform", "role": "contributor",
     "assigned_by": "suite-53", "assigned_at": "2026-08-01T09:14:02Z"},
    {"user_sub": "58833c93-…", "team_name": "platform", "role": "agent:reviewer",
     "assigned_by": "suite-71", "assigned_at": "2026-08-02T11:40:55Z"}] }
```

**502** `{"detail": "Keycloak unreachable: <msg>"}` — never a false "zero orphans".

**Semantics.**
- `orphan_users` = Keycloak users with no row (the state R0 makes illegal)
- `stale_rows` = rows whose `user_sub` is not a live Keycloak user (litter, not a hole — nobody can authenticate as a dead sub; G-R0-3)
- A row holding a **reviewer scope** whose sub is live is `matched`, never litter (Decision 42)

**Invariants.** `matched_count + len(orphan_users) == keycloak_user_count` and `matched_count + len(stale_rows) == assignment_row_count`.

**Auth.** No `require_user` in R1 — it lives on `admin_users.teams_router`, which is not one of the ten §1.4 routers. R2 puts `require_global_role("platform-admin")` on the `/admin/*` surface.

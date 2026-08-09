# RBAC R0 + R1 — Data Model

## `user_team_assignments`

One global role — **or one reviewer scope** — per subject. No ORM model exists; every access is raw SQL (`rbac.py:52,61`, `routers/me.py:39`, `routers/admin_users.py:83,96,240,261`, `routers/approvals.py:276`, `routers/playground.py:225,795`, `routers/chat.py:70`, `routers/knowledge.py:117`, `routers/observability.py:40`, `routers/artifact_grants.py:101`, `routers/deployments.py:90`). Created by migration `0013`; altered by `0044`, `0075`, and now `0079`.

| Column | Type | Constraints (after 0079) | Notes |
|---|---|---|---|
| `user_sub` | `VARCHAR(255)` | **PRIMARY KEY** | The Keycloak user `id`, which equals the JWT `sub`. One row per subject — the PK is what makes `ON CONFLICT (user_sub) DO UPDATE` the correct upsert. |
| `team_name` | `VARCHAR(255)` | `NOT NULL`, indexed `ix_uta_team_name` | Plain string; **no FK** to `teams`. Because it is `NOT NULL`, "has a row" and "has a team" are the same predicate — which is why `schedules.py:157`'s no-team branch becomes unreachable once a missing row raises. |
| `role` | `VARCHAR(64)` | `NOT NULL`, **no default** | Union-typed — see below. |
| `assigned_by` | `VARCHAR(255)` | nullable | `'system:bootstrap'` for the bootstrapped admin; a caller's `preferred_username` for API-created users; `'suite-NN'` for e2e fixtures. |
| `assigned_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()` | Moved by the upsert **only when `team_name` or `role` actually changes** — the guarded `DO UPDATE ... WHERE` is what makes a restart a true no-op (Story 1 scenario 2). |

---

## The `role` column is union-typed — documented, not fixed (Decision 42 / G-R0-1)

`role` holds a value from **either** of two disjoint vocabularies:

| Kind | Values | Read by | Rank in `ROLE_HIERARCHY` |
|---|---|---|---|
| Global platform role | `platform-admin`, `contributor`, `consumer` (+ legacy `admin`, `operator`, `viewer`, normalized on read by `rbac._LEGACY_MAP`) | `rbac.get_user_global_role` → every policy function | 2 / 1 / 0 |
| Reviewer scope | `agent:reviewer` (`routers/approvals.py:48 _DEFAULT_REVIEWER_SCOPE`), and any future per-trigger `agent_triggers.approver_role` | `routers/approvals.py:266 _caller_roles` — reads the column directly, **not** through `get_user_global_role` | **0**, deliberately |

`ROLE_HIERARCHY.get(role, 0) == 0` for a scope literal is **load-bearing, not a bug**: it is the only thing preventing a reviewer-scope holder from being read as a contributor. Do not add a `CHECK` constraint, an enum, or a "normalize unknown values" branch. The split lands in R5, where reviewer scopes move to `artifact_role_grants`.

---

## Validation rules (post-R0)

1. **Every insert states a role.** Enforced by the database (`NOT NULL`, no default) rather than by convention. Migration `0079` is what makes the rule real; `0044` and `0075` fixed only the data.
2. **A missing row is corruption.** `rbac.get_user_global_role` raises `NoPlatformRole`; the app-level handler answers 403 `{"error_code": "no_platform_role"}`. There is no default role and no auto-provisioning path.
3. **An unrecognized value is legitimate.** Returned verbatim, ranked 0, no exception. Rule 1 is about *presence*, not vocabulary.
4. **One row per Keycloak user, by convention.** The PK gives at-most-one; nothing enforces at-least-one for non-bootstrap users. `GET /api/v1/admin/identity-audit` reports both directions of divergence (FR-12); nothing deletes (G-R0-3).
5. **`assigned_at` is monotone and meaningful.** It advances only on a real change, so "when was this role last actually changed" is answerable.

---

## Migration 0079 DDL

```sql
-- upgrade
ALTER TABLE user_team_assignments ALTER COLUMN role DROP DEFAULT;

-- downgrade
ALTER TABLE user_team_assignments ALTER COLUMN role SET DEFAULT 'operator';
```

Idempotent (`DROP DEFAULT` on a column with no default is a no-op) and data-preserving (no row touched). `NOT NULL` is unchanged — dropping it would replace an invented role with a null one.

**Post-condition:**
```sql
SELECT column_default, is_nullable
  FROM information_schema.columns
 WHERE table_name = 'user_team_assignments' AND column_name = 'role';
-- column_default: NULL      is_nullable: NO
```

---

## Bootstrap upsert

```sql
INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_by, assigned_at)
VALUES (:sub, 'platform', 'platform-admin', 'system:bootstrap', now())
ON CONFLICT (user_sub) DO UPDATE
   SET team_name   = EXCLUDED.team_name,
       role        = EXCLUDED.role,
       assigned_by = EXCLUDED.assigned_by,
       assigned_at = now()
 WHERE user_team_assignments.team_name IS DISTINCT FROM EXCLUDED.team_name
    OR user_team_assignments.role      IS DISTINCT FROM EXCLUDED.role;
```

The guarded `WHERE` is what makes restart-idempotence observable: `assigned_at` moves only when something actually changed.

---

## Related entities (unchanged by R0/R1)

| Entity | Relationship |
|---|---|
| Keycloak user | 1:1 with a row **by convention** — which is exactly what R0 makes enforceable for the admin and auditable for everyone else. `user_sub` = Keycloak `id` = JWT `sub`. |
| `artifact_role_grants` | Complementary, not overlapping: artifact-scoped authority (`agent-admin`, `approver`, `invoker`). `rbac.has_artifact_role` joins the two via `get_user_team`. |
| `approval_authority` | Superseded in design by the `approver` role but still the live HITL mechanism (G-R0-2). |
| `teams` | Referenced by name only; no FK. |

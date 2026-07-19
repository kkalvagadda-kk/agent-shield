# Merge migration-renumber drift — `agent_triggers.auth_mode` missing → 500 on every trigger/schedule create

**Found:** 2026-07-19, during post-merge functional verification of the `origin/main` merge
(commit `f56c9f6`) on branch `worktree-ux-preview-context-storage`.
**Fixed:** 2026-07-19 — DB reconciled in the running `agentshield-platform` cluster via
`alembic stamp 0063 → upgrade head` (registry-api `0.2.208`). No code change to the migrations
themselves — the chain is correct for fresh DBs; only the persisted dev volume had drifted.

## Symptom

Four e2e suites failed with `500 Internal Server Error` the moment they tried to create a
webhook/schedule trigger (suite-71, suite-75-eval-v2-scheduled, suite-76-webhook-client-signing,
suite-77-eval-v2-webhook):

```
RuntimeError: trigger create failed 500 Internal Server Error
```

The registry-api log showed the real cause, one layer down:

```
sqlalchemy ... asyncpg.exceptions.UndefinedColumnError:
column "auth_mode" of relation "agent_triggers" does not exist
```

`auth_mode` is main's WS-4 webhook-auth column (`'token' | 'client_signed'`), added by
`0064_webhook_clients.py`. The ORM (post-merge) selects/inserts it on every trigger write, but
the physical column was absent — so every trigger create 500'd.

## Root cause — the design flaw, not the surface error

A **revision-id pointer is reassigned by renumbering, and the dev DB volume is persistent.**

- This branch and `origin/main` each independently authored a `0064` migration
  (`0064_agent_memory_shared_thread` here vs `0064_webhook_clients` on main). Git did **not**
  flag it — different filenames, same `revision="0064"`, both `down_revision="0063"` — a
  *semantic* collision, not a text conflict.
- The merge resolution renumbered this branch's chain to `0065–0068` behind main's
  `0064_webhook_clients`, producing a single linear head `0063 → 0064_webhook → 0065 → … → 0068`.
  **This is correct for a fresh database** (verified: single head `0068`, all idempotent).
- BUT the local docker-desktop cluster has a **persistent Postgres volume**. Before the merge,
  this branch's DB had already migrated under the *old* numbering, so its `alembic_version`
  pointer read the old head id. Under the *new* numbering that same id now denotes a *different*
  migration. When `deploy-cpe2e.sh` ran `alembic upgrade head`, Alembic saw the DB "already at"
  a revision at/after `0064` and applied only the delta — **silently skipping main's
  `0064_webhook_clients`.** Result: `alembic_version = 0068` (looks done) while `auth_mode`,
  the `webhook_clients` table, and `agent_events.client_id` were never physically created.

Evidence captured during triage:

```
alembic_version = 0068            # DB believes it is at head
agent_triggers.auth_mode = MISSING
webhook_clients table   = MISSING     # both 0064 objects absent
agent_events.client_id  = MISSING
user_profiles table     = user_profiles   # this branch's 0066 DID apply (pre-merge, old numbering)
knowledge_bases table   = knowledge_bases  # this branch's 0068 DID apply (pre-merge)
```

The mixed picture — this branch's tables present, main's `0064` objects absent — is the
signature of a renumber-on-persistent-volume drift, not a broken migration.

## Fix — reconcile the physical schema to what `alembic_version` already (correctly) claims

Every migration `0064–0068` is fully idempotent (`ADD COLUMN IF NOT EXISTS`, guarded
`pg_constraint` adds, `CREATE TABLE IF NOT EXISTS`, and `0068`'s `sa.inspect(...).get_table_names()`
existence guards). So the class-correct reconciliation is a **forward-only replay**, never a
downgrade (a downgrade would drop this branch's `user_profiles` / `knowledge_bases` data):

```bash
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -c registry-api -- \
  alembic -c alembic.ini stamp 0063      # move the pointer back — NO DDL
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -c registry-api -- \
  alembic -c alembic.ini upgrade head    # replay 0064→0068; only 0064's missing objects get created
```

Post-fix verify: `auth_mode`, `webhook_clients`, `agent_events.client_id` all present;
`alembic_version = 0068`; trigger/schedule creates return 200.

## Why the migration chain itself was NOT changed

The renumbered chain is correct for any **fresh** database (CI, a new cluster) — it applies
`0063 → 0064_webhook → 0065 → … → 0068` linearly with a single head. The bug is purely a
**persisted-dev-volume** artifact: the pre-merge pointer no longer meant what the new files say.
Reconciliation belongs at the data layer for the one drifted volume, not in the code.

## Prevention / lessons

- **Renumbering a migration on a shared/persistent DB is not free** — the `alembic_version`
  pointer is an *identity*, and reassigning ids means the DB's recorded revision can silently
  denote a different migration than what physically ran. On persistent volumes, prefer a fresh
  reset, or explicitly replay (`stamp <parent>` → `upgrade head`) so the skipped migration runs.
- **Two branches picking the same migration number is invisible to git.** A pre-merge check —
  "does any incoming migration reuse a `revision=` string I already have?" — would have caught
  the `0064` collision before it became a runtime 500. (Candidate: fold a duplicate-`revision`
  scan into the pre-build source gates alongside `check-tag-content-coupling.sh`.)
- **The symptom named the wrong layer.** "500 on trigger create" reads like a triggers-router
  bug; the fault was two layers down in schema state. Always pull the server-side traceback
  (`UndefinedColumnError` named the exact column) before touching the router.

## Cross-links

- Regression coverage: suite-76-webhook-client-signing, suite-77-eval-v2-webhook,
  suite-71-scheduled-e2e, suite-75-eval-v2-scheduled all exercise the trigger-create path and
  now pass against the reconciled schema.
- Migration: `services/registry-api/alembic/versions/0064_webhook_clients.py`.

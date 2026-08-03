# Suspending a production deployment answered 500, every time, since the endpoint shipped

**Found** 2026-08-02 (cleaning up after the schedule-lifecycle journey) · **Fixed** 2026-08-02, registry-api `0.2.253`

## Symptom

```
PATCH /api/v1/catalog/{artifact_id}/deployments/{deployment_id}  {"action": "suspend"}
→ 500 Internal Server Error
```

Every call. The other three actions on the same endpoint — `upgrade`, `resume`, `terminate` —
worked fine.

```
asyncpg.exceptions.CheckViolationError: new row for relation "production_deployments"
violates check constraint "production_deployments_status_check"
```

## Root cause

One word. `routers/catalog.py::update_catalog_deployment` wrote a status the table forbids:

```python
elif body.action == "suspend":
    dep.status = "suspending"        # ← not an admitted value
```

```sql
CHECK (status = ANY (ARRAY['pending','deploying','running','suspended',
                           'failed','terminating','terminated','rolled_back','gate_failed']))
```

`suspended` is admitted; `suspending` is not. Gerund where the schema wanted the past participle.

**Why it looked right.** The neighbouring branches establish a gerund house style that happens to
be legal:

```python
if   action == "upgrade":   dep.status = "deploying"     # admitted
elif action == "suspend":   dep.status = "suspending"    # NOT admitted
elif action == "resume":    dep.status = "deploying"     # admitted
elif action == "terminate": dep.status = "terminating"   # admitted
```

Three of four in-flight gerunds are legal, so the fourth reads as consistent right up until
Postgres refuses it. Nothing above the database enforced the vocabulary — the column is a plain
`String` on the model, so the constraint is the only checker, and it only speaks at write time.

## Why no test caught it

`suite-39-deployment-lifecycle.sh` has covered `suspend → suspending` since it was written — but
against the **sandbox** path, `PATCH /agents/{name}/deployments/{id}`, which writes the
`deployments` table. That table's constraint *does* admit `suspending`.

Two tables, two constraints, two routers, one action name. The suite proved the sandbox verb and
was silently mute about the production one. A suite can be thorough about a behaviour and still
never touch the table where it breaks.

## Fix

```python
elif body.action == "suspend":
    dep.status = "suspended"
    dep.suspended_at = now
```

`suspended` rather than adding `suspending` to the constraint: there is no in-flight state worth
representing here — suspension takes effect on the next reconcile, and `suspended_at` already
records when it was requested. Widening the constraint would have preserved a distinction nothing
reads.

## Regression test

`scripts/e2e/suite-39-deployment-lifecycle.sh` **T-S39-007** — parses every `dep.status = "..."`
literal out of `update_catalog_deployment` and asserts each is a member of the live
`production_deployments_status_check`.

**Asserts the set, not the incident.** Replaying one suspend would need a published artifact plus a
production deployment as fixture, and would still only pin the one verb that has already been
fixed. Checking the whole vocabulary against the live constraint catches the next drift in either
direction — a new action, or a constraint someone narrows — and needs no fixture at all. It also
fails loudly if it parses zero literals, so a refactor that renames the function cannot make it
pass vacuously.

## Lessons

1. **A CHECK constraint is a vocabulary, and only the database knows it.** If a column has an
   enumerated domain, something above the DB should share that list — or a test should compare
   them. Here nothing did.
2. **Local consistency is not correctness.** The wrong value matched the style of its three
   neighbours perfectly.
3. **Same action name, different table, different rules.** "We test suspend" was true and useless.
   Coverage is per-path, not per-verb.

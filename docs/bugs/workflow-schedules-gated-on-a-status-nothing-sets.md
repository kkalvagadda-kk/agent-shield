# Workflow schedules and webhooks are gated on a status value nothing ever sets

**Found:** 2026-07-29 (Kalyan + Claude), reviewing `0c9ef6f` on branch `schedule-lifecycle`
**Fixed:** 2026-08-01 — scheduler `0.1.4`, event-gateway `0.1.7`. The landed predicate is
`w.status <> 'archived'`, NOT the `publish_status` the original recommendation proposed —
see *What actually shipped* below.
**Introduced by:** `0c9ef6f` "fix(lifecycle): deleting/archiving/quarantining an artifact
disarms its triggers" — scheduler `0.1.2` / event-gateway, migration `0076`.

## Symptom

No workflow schedule can ever fire again, and every inbound webhook to a workflow gets a
uniform 401 — regardless of whether the workflow is published, live and healthy.

Nothing is visibly broken **right now**, which is what makes this dangerous: migration
`0076`'s reap already set `enabled = false` on every workflow trigger on the cluster, so
there are currently **0 enabled workflow schedule triggers and 0 enabled workflow webhook
triggers** to fail. The next schedule anyone creates on a published workflow will silently
never run, and `suite-95` will stay green while that is true.

## Root cause

`0c9ef6f` added an artifact-liveness predicate to both dispatch consumers —
`services/scheduler/main.py::_fetch_schedule_triggers` and
`services/event-gateway/webhook_auth.py::_TRIGGER_SQL`:

```sql
AND a.status = 'active'      -- agents: correct
AND w.status = 'published'   -- workflows: matches nothing, ever
```

The agent half is right. The workflow half tests the wrong column.

`workflows.status` has the CHECK `status IN ('draft','published','archived')`
(`models.py:340`) and defaults to `'draft'`, so `'published'` *looks* like a legal, intended
value. It is not reachable: **the only writer of `workflows.status` in the entire codebase
is `routers/composite_workflows.py:349`, which sets `'archived'`.** A repo-wide grep for
`status = 'published'` against the workflows table returns nothing. (`routers/workflows.py:330`
does set `status='published'`, but on `AgentGraph` — a different table. That near-miss is
almost certainly the source of the confusion.)

Workflow publication is carried by a *different* column, `publish_status`, set by
`routers/admin.py:318` on publish-request approval — the same column `list_workflows`
(`composite_workflows.py:200-205`) already uses for catalog visibility.

Evidence from the live EKS DB (`test-cluster-964-10086`), 140 workflows:

```
 status   | publish_status | count
----------+----------------+-------
 archived | private        |   106
 draft    | private        |    25
 draft    | published      |     6   <-- genuinely published, status still 'draft'
 archived | published      |     3

workflows with status='published'         : 0
workflows with publish_status='published' : 9
```

Zero rows out of 140. Six of the nine genuinely-published workflows sit at
`status='draft'` — because `status` was never wired to publication at all. The two columns
are independent, and the predicate picked the one that is dead.

The design intent was right and is worth keeping: brief R8 says "live for a workflow means
`published`", consistent with Decision 20 putting the eval gate at publish. The
implementation just bound it to a column that never receives that value.

## Why the tests did not catch it

The reap ran first. `suite-95-trigger-lifecycle-disarm.sh` asserts that triggers on **dead**
artifacts do not fire, and after `0076` disabled every workflow trigger on the cluster that
assertion holds trivially — it is satisfied just as well by *nothing firing at all*. The
suites that would have caught it (`suite-34-workflow-triggers`, `suite-66-production-triggers`)
need an enabled trigger on a workflow that is published, which is the exact combination the
predicate makes unrepresentable.

This is the same shape as Finding 4 in the brief: a defect that is load-bearing on a green
test. A "dead artifacts don't fire" assertion cannot distinguish success from total failure
without a **positive control** asserting that a live artifact *does* fire.

## Recommended fix

1. Change both predicates to the column that actually carries publication, keeping the
   archived exclusion:
   ```sql
   AND w.publish_status = 'published' AND w.status <> 'archived'
   ```
   Both consumers must change together — they are separate images, and fixing one leaves the
   other door with a different definition of runnable, which is the two-places drift this
   workstream exists to unpick.
2. Add the missing **positive control** to `suite-95`: a published workflow with an enabled
   schedule trigger **does** appear to the scheduler and **does** produce a run. Written
   against today's code it is red, which is the point.
3. Reconcile `internal.py`'s run door, which still rejects only `archived` — so a draft
   workflow run is permitted there while the trigger filter forbids it. Two definitions of
   runnable in two places is structurally the same bug as
   `docs/bugs/trigger-dispatch-environment-mismatch.md`.
4. Consider dropping `'published'` from the `workflows.status` CHECK, or the column's
   independence from `publish_status` will keep inviting this exact mistake. A legal-looking
   enum value that no code path can produce is a trap, not a schema.

## Lessons

- **A liveness filter needs a positive control, not just a negative one.** "The dead thing
  didn't fire" is also true when nothing fires. Assert that the live thing *did*.
- **Two status columns on one table will eventually be confused.** `workflows.status` and
  `workflows.publish_status` are independent, and 6 rows on the cluster are
  `status='draft', publish_status='published'` — the data itself says the model is ambiguous.
- **Grep for the writer before gating on a value.** The CHECK constraint listed `'published'`,
  which made it look wired. Only `git grep` for the assignment proves a value is reachable.


---

## What actually shipped, and why it is not the recommended fix

The recommendation above (`publish_status = 'published' AND status <> 'archived'`) was tried
first, as scheduler `0.1.3` / gateway `0.1.6`. It is **reachable** — unlike
`status='published'` — but it is **too strict**, and `suite-66-production-triggers` failed on
both cases with it.

Why: a workflow reaches production by deploying its **member agents**. `suite-66` posts
`/agents/{n}/deploy {"environment":"production"}` for each member; the *workflow row itself*
is never published and stays `status='draft', publish_status='private'` throughout. There is
no workflow-level publish in that path at all. So requiring publication excluded workflows
that genuinely run in production.

That also means brief **R8 ("live for a workflow means published") is not implementable as
written** — nothing publishes a workflow in the path that puts one into production. R8 is
superseded.

The predicate that shipped is:

```sql
AND w.status <> 'archived'
```

which is exactly what `internal.py:194`'s run door already enforces. The trigger filter and
the run door now share **one** definition of runnable, which also closes item 3 of the
recommendation (they previously disagreed).

What is *not* enforced any more: "a never-eval-gated draft must not fire on a cron". That is a
real policy question, but enforcing it in the trigger filter while the run door permitted it
was two definitions of runnable — the exact drift this workstream exists to remove. Recorded
as an open question rather than half-enforced.

### Three predicates, three outcomes

| Predicate | Outcome |
|---|---|
| `w.status = 'published'` | matched **0 of 140** rows — nothing writes it. Every workflow schedule silently dead. |
| `w.publish_status = 'published'` | reachable, but **too strict** — broke suite-66's real production path. |
| `w.status <> 'archived'` | matches reality **and** the run door. Shipped. |

### Test

`T-S95-007` is the positive control the suite lacked, and it is deliberately built on a
**draft/private** workflow — the shape suite-66 actually produces. Had it published the
fixture, it would have gone green on the too-strict predicate and hidden the second bug.

`T-S95-000` now asserts BOTH wrong predicates are **absent**, not merely that a right one is
present — the first version would have passed on `publish_status`. It also strips comment
lines before grepping, because the explanation of why a predicate was wrong necessarily
quotes it, and a naive grep flagged the very file that fixed it.

### Additional lesson

- **A "too strict" filter and a "matches nothing" filter fail identically in a negative-only
  test suite.** Both produce "nothing fires", and both satisfy "dead things do not fire". Only
  the positive control distinguishes them — and only if its fixture is built in the shape
  production actually uses.

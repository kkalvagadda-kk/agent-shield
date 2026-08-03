# Deleting a deployed workflow version returns 500

**Found** 2026-08-03 (running the never-before-executed backlog of e2e suites) ·
**Fixed** registry-api `0.2.258`, commit `5fb3158`

## Symptom

`DELETE /api/v1/workflows/{workflow_id}/versions/{version_id}` returned **500 Internal
Server Error** for any version that had ever been deployed. A version that was never
deployed deleted fine, which is why this survived: the happy path in front of it works.

Pod-side:

```
sqlalchemy.exc.IntegrityError: ForeignKeyViolationError: update or delete on table
"workflow_versions" violates foreign key constraint
"workflow_deployments_version_id_fkey" on table "workflow_deployments"
DETAIL:  Key (id)=(c24fa544-…) is still referenced from table "workflow_deployments".
[SQL: DELETE FROM workflow_versions WHERE workflow_versions.id = $1::UUID]
```

## Root cause

Two copies of one rule — "deleting a version tears down the deployments pinned to it" —
and the copies disagreed.

The **agent** copy (`routers/versions.py`) NULLed the referencing `agent_runs` rows and
then deleted the deployment rows, so the version delete that followed had nothing
pointing at it.

The **workflow** copy (`routers/composite_workflows.py`) only did:

```python
for dep in active_deps:
    dep.status = "terminated"
    dep.terminated_at = now
...
await db.delete(ver)
```

Setting a status does not remove a row, and `workflow_deployments.version_id` is a plain
`NO ACTION` FK. Postgres refused, correctly.

The design flaw is not the missing `DELETE` — it is that a rule with two independent
implementations had a test on only one of them. `terminated_count` in both handlers even
returns the same field name, which made them *look* like the same behaviour at every call
site and in every response body.

There was a second, unfired copy of the same defect underneath: `agent_runs` has BOTH
`sandbox_deployment_id` and `workflow_deployment_id`, both `NO ACTION`. The agent path
detached the first. Nothing detached the second — so fixing the workflow handler the
obvious way (add `db.delete(dep)`) would have produced the identical 500 through
`agent_runs` instead. Only `eval_runs.workflow_deployment_id` was already `SET NULL`.

## Fix

One implementation: `services/registry-api/deployment_lifecycle.py`,
`detach_and_delete_deployments`, called by both routers.

The caller passes its deployment model and its `AgentRun` FK column **as explicit
arguments**. The helper does not infer either from what it was handed — a helper that
guessed the column from the model would be one `getattr` away from silently detaching
nothing and reintroducing the bug in a form that returns 200.

That is the class fix rather than the instance fix: adding a third artifact kind now
requires naming its FK column at the call site, and the "which rows does this tear down"
question has exactly one answer.

Kept deliberately: the response field is still `terminated_deployments` even though the
rows are removed, because it is a shipped API field. The discrepancy is recorded in the
helper docstring instead of being silently perpetuated.

## Why no test caught it

`suite-41-version-delete.sh` **did** cover it — T-S41-006, "Delete workflow version
cascades deployment". The suite had never completed a run:

1. `httpx.Client` does not follow redirects, and `POST /agents` (no trailing slash)
   answers 307 with an empty body, so `.json()` raised
   `Expecting value: line 1 column 1 (char 0)` in T-S41-001 and the suite died at the
   first step.
2. Cleared that, and T-S41-002 asserted `status == 'terminated'` on a row the agent path
   *deletes*, so it raised `IndexError` on an empty list.

The product bug was two test defects deep. This is the same shape as the rest of the
sweep: the suite existed, was registered, and had never once reached the assertion it was
written for.

## Regression test

`scripts/e2e/suite-41-version-delete.sh` — T-S41-006 now reaches its assertion. T-S41-002
asserts the row is gone rather than the status it never had.

## Lessons

1. **A rule with two implementations needs a test on both, or it needs one
   implementation.** Identical response-field names across two handlers actively hide the
   divergence.
2. **Check every FK into the table you are about to orphan, not just the one that
   failed.** `agent_runs.workflow_deployment_id` was the next 500 in line.
3. **A suite that has never completed is not coverage.** Its registration in
   `test-manifest.txt` said this path was tested for as long as it was broken.

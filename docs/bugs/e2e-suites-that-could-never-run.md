# Half the e2e suites could not run, and one had never run at all

**Found:** 2026-07-27, first cluster run of the merged `mcp-tool-source` branch.
**Fixed:** same change — `scripts/e2e/suite-*.sh` (51 pod lookups, 4 label
selectors, 3 dead imports, 3 httpx clients, 6 wrong-shaped tool calls).

## Symptom

Running `bash scripts/run-tests.sh --layer api --group tools,mcp` on EKS reported
**7 passed, 2 failed**. Both failures looked like product regressions from the
merge. Neither was. Chasing them uncovered four independent defects in the test
harness itself, each of which had been silently degrading or disabling coverage.

## Defect 1 — suites exec into dead pods (49 of 92 suites)

`suite-6` failed with `Could not create agent or publish_status not 'private'`.
The same API call by hand returned `201 {"publish_status": "private"}`.

The suite selects its pod like this:

```bash
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}')
```

No phase filter. On this cluster:

```
25 Evicted
 1 Init:ContainerStatusUnknown
 2 Running
```

`.items[0]` picks a dead pod ~9 times in 10, and `kubectl exec` answers
`cannot exec into a container in a completed pod; current phase is Failed`. The
suite discards that on stderr (`2>/dev/null`) and reports a **product** failure.

**Root cause:** the failure is indistinguishable from a real one *by design* —
`run_test` swallows stderr so a transcript stays readable, which means an
infrastructure error and an assertion error print the same line. The pod count is
environmental, so this got worse over time and would look like a flaky product.

**Fix:** `--field-selector=status.phase=Running` on every pod lookup — 51 sites
across 49 suites. Deleting the evicted pods would have been the one-liner and is
the wrong fix: they come back, and the suites stay a coin-flip.

## Defect 2 — a label selector that matches nothing (4 suites)

`suite-41`, `suite-42`, `suite-43` selected `-l app=registry-api`. Live count for
that selector: **0**. The chart labels pods `app.kubernetes.io/name=registry-api`
(28 pods). So `POD` was empty and every `kubectl exec` in those suites failed.

These also used `kubectl get pod` (singular), which is why the Defect-1 sweep,
keyed on `get pods`, skipped them — worth noting for the next sweep.

## Defect 3 — `suite-42-rbac` had never executed once

With the selector fixed, the suite finally reached a pod and died immediately:

```
ImportError: cannot import name 'get_engine' from 'db' (/app/db.py)
```

`db` exports `engine`, `AsyncSessionLocal` and `get_db` — never `get_engine`. The
import was **vestigial**: every block below it builds its own engine with
`create_async_engine`. The line did nothing but raise at import time, in all three
test bodies.

Then `T-S42-002` failed with `JSONDecodeError: Expecting value: line 1 column 1`.
`POST /agents` (no trailing slash) answers **307 with an empty body** under
FastAPI's `redirect_slashes`, and `httpx.Client` does not follow redirects by
default, so `.json()` parsed `""`. Fixed once on the client
(`follow_redirects=True`) rather than at each call site.

**This is the one that matters most.** `suite-42` holds `T-S42-005` and
`T-S42-007` — the guards for the `viewer` → `consumer` rename. They were written,
registered, reported as coverage, and had **never run**. A suite that cannot start
is worse than no suite: it occupies the slot where real coverage would go.

## Defect 4 — `suite-88` was authored against an API that does not exist

`suite-88` (main's tool-description suite, renamed from 84 in the merge) had also
never run — main was never deployed. Its first execution: **4 passed, 7 failed**.
All seven were the harness, not the product:

| Call | Result | Reality |
|---|---|---|
| `GET /tools/{name}` | 422 | route is `/{tool_id}` — a **UUID**; a name fails path validation |
| `PATCH /tools/{name}` | 405 | there is no PATCH route; `PUT` is the only update verb |
| `GET /tools/?limit=500` | 422 | `limit` is `le=200` — see [tool-picker-silently-truncates-at-200.md](tool-picker-silently-truncates-at-200.md) |

Fixed by capturing the id from the create response, using `PUT` only, and paging
the listing. The suite now proves what it always claimed: a multi-line description
survives create → GET → list → PUT → GET, and 4199 bytes across 60 lines are not
truncated. **11 passed, 0 failed.**

## Why this class survived

Every one of these suites is registered in `scripts/test-manifest.txt` and passes
`run-tests.sh --audit`, because the audit answers *"is the file on disk registered?"*
— not *"has this ever produced a green assertion?"* Registration became a proxy for
coverage. A suite that exits non-zero on line 1 is indistinguishable, in the
manifest, from one that proves a hundred behaviours.

`suite-42` and `suite-88` were both written on branches that were never deployed,
so "run it later" was reasonable at the time. What made it a defect is that nothing
recorded the difference between *written* and *ever executed*.

## Follow-up (gap ledger)

An `--audit` extension that flags suites which have never reported a PASS would
close the loop. Recorded as *deferred* — it needs a results store the harness does
not currently have.

# Two listing endpoints returned every row on the platform to an anonymous caller

**Found:** 2026-07-27, while grounding Eval Slice 0 — reading `list_eval_runs` to add filters.
**Fixed:** registry-api **0.2.234** — `routers/eval_runner.py`, `routers/datasets.py`.

## Symptom

`GET /api/v1/playground/eval-runs` and `GET /api/v1/playground/datasets`, called with **no
`Authorization` header and no `X-User-Sub`**, returned `200` with the full table. Measured on the live
test cluster before the fix:

```
T-S89-005  anonymous GET /playground/eval-runs  -> status=200 rows=60
T-S89-006  anonymous GET /playground/datasets   -> status=200 rows=120
```

Datasets are the sharper exposure: they hold `input_message` / `expected_output` pairs, which is
frequently real business logic.

## Root cause

Not one mistake — **three individually reasonable decisions that compose into "no identity means no
filter."**

```python
caller = (user or {}).get("sub") or x_user_sub
q = select(EvalRun).order_by(EvalRun.created_at.desc())
if caller:
    q = q.where(EvalRun.user_id == caller)   # <- no else
```

1. The route uses `get_optional_user`, which **returns `None` rather than raising 401** — correct for a
   dependency meant to support anonymous access.
2. registry-api installs **no global auth middleware**. `main.py` adds CORS and a trace-ID middleware and
   nothing else, so authorization is per-route by design.
3. The ownership filter sits **inside `if caller:` with no `else`** — which reads as "scope to the
   caller" and silently means "scope to the caller, or don't scope at all."

Each is defensible alone. Together, the *absence* of credentials widens access instead of narrowing it —
the inversion that makes this a security bug rather than a missing feature.

**This class was already known.** `routers/agents.py:167-176` carries the fix and the postmortem in a
comment:

> DENY-BY-DEFAULT: an unauthenticated caller (no JWT and no X-User-Sub) sees ONLY published agents —
> never another tenant's private agents. **(Previously a missing caller skipped the filter entirely and
> leaked every agent.)**

Four routers got that `else`: `agents`, `tools`, `skills`, `composite_workflows`. Two did not:
`eval_runner` and `datasets`. They are **exactly the two with no `publish_status` column** — the fix
template was "published to all, private to creator," which does not map to a purely owner-scoped
resource, so they were passed over rather than adapted.

## Why no test caught it

**Every existing e2e suite authenticates.** Not one exercised the anonymous path, so there was nothing to
fail. The suites were not weak — they were complete for the case they modelled, and the unmodelled case
was the vulnerable one.

This is the same shape as `docs/bugs/e2e-suites-that-could-never-run.md`: coverage measured by what tests
assert, never by what they omit.

## Fix

Add the explicit deny-by-default branch to both routes, in one change:

```python
else:
    q = q.where(sa.false())
```

Both together on purpose. Patching only `eval_runner.py` — the one Slice 1 happened to touch — would
have left `datasets.py` standing as the next instance of a class the repo had already documented once.

Regression tests, written to **fail first** (they did, above):

- `T-S89-005` — anonymous `GET /playground/eval-runs` returns `[]`
- `T-S89-006` — anonymous `GET /playground/datasets` returns `[]`
- `T-S89-007` — an **authenticated** caller still sees exactly their own rows (the over-correction guard;
  a fix that returns nothing to everyone also "passes" 005 and 006)

## Exposure

The test cluster's NLB is internal (VPN-only). No claim is made about other environments — the route-level
check is the only control that exists on these endpoints, so the fix is warranted regardless of what
fronts them.

## Follow-up (gap ledger)

`list_eval_runs` still returns only the caller's **own** runs, so an approver reviewing someone else's
agent sees an empty eval history. Team-scoped reads are **Decision 33 option B**, deliberately deferred:
they change the access model and collide with Decision 25's platform-wide `rbac.py: ENFORCE=False`.

**The generalisable check:** for every route using `get_optional_user`, ask what an anonymous caller
receives. Four of six had the answer written down; two did not, and nobody swept for the rest.

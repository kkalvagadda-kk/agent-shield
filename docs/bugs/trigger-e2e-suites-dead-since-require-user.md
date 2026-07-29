# Fifteen trigger e2e suites died silently when trigger CRUD gained `require_user`

**Found:** 2026-07-29, while running the blast-radius sweep for the trigger-dispatch fix
**Fixed:** 2026-07-29 — `scripts/e2e/lib/e2e-auth.sh` + 15 suites, branch `schedule-lifecycle`
**Introduced:** `76b3570` (feat(webhook-app-identity): Phases 2-5 — … trigger soft-auth)

## Symptom

Running the neighbours of a schedule change surfaced two suites failing for reasons that named the
wrong layer:

```
suite-28  →  exit 1, and an EMPTY setup diagnostic. Just "--- Setup ---" then "--- Cleanup ---".
suite-66  →  "❌ Suite 66 FAILED (a production trigger did not fire a completed run)"
             DIAG _diag_sched sched run=None
```

suite-66's message points at the scheduler. The scheduler was fine. Probing the real cause:

```
create agent:                201
create trigger (no auth):    401 {"detail":"Authentication required"}
```

And suite-26, which does say what happened:

```
FAIL: create trigger 401: {"detail":"Authentication required"}
FAIL: T-S26-002 — scheduler did not register job within 90s (still 19)
FAIL: T-S26-003 — skipped (no TRIGGER_ID from T-S26-001)
```

## Root cause

`76b3570` added `claims: dict = Depends(require_user)` to both trigger-create endpoints:

- `routers/triggers.py::create_trigger`
- `routers/composite_workflows.py::create_workflow_trigger`

Every bash suite authenticated with `X-User-Sub` headers alone. **That header is an audit stamp,
not an authentication** — it feeds `armed_by` and `created_by`. `require_user` needs a real Keycloak
Bearer. So from that commit onward, every suite that creates a trigger died at setup.

Fifteen of them, spanning the entire trigger surface:

| | Suites |
|---|---|
| schedule / scheduler | 21, 26, 32, 71, 75 |
| webhook / event gateway | 22, 28, 77 |
| workflow triggers | 34, 66 |
| daemon / identity / alerting | 27, 33, 70 |
| execution shape / wizard | 19, 31 |

That is the acceptance gate for scheduled runs (71), daemon identity (70), the event gateway (28),
production triggers (66), failure alerting (27), and Eval v2 scheduled + webhook (75, 77).

## Why it stayed hidden

**The failures did not name their cause.** Only suite-26 printed the 401. suite-28 swallowed its
setup output entirely and printed nothing; suite-66 reported a scheduler symptom three layers below
the actual problem. A suite that fails for an unstated reason is barely better than one that does
not run — the reader's first hypothesis is "the feature broke", and that hypothesis costs an
investigation each time.

This compounded a second error in judgement. Reviewing this same area a day earlier, the conclusion
recorded in `docs/design/todo/schedule-lifecycle-and-operations.md` (Finding 4) was that these
suites were *rigorous but asserted the wrong things*. That was static analysis of their source. They
were not asserting anything at all — they were dying at setup. **Reading a test is not running it.**

## Fix

One shared helper, `scripts/e2e/lib/e2e-auth.sh`, exposing `e2e_token` / `e2e_require_token`. Each
suite sources it after resolving `API_POD` and interpolates the Bearer into its in-pod driver:

```bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
E2E_TOKEN="$(e2e_require_token "$NAMESPACE" "$API_POD")"
```

Two delivery mechanisms, because the suites are not uniform:

- **Quoted heredoc drivers** (`<<'PY'`, no bash interpolation) — 66, 70, 71, 75, 77 — take the token
  as an env var on the driver launch (`PYTHONPATH=/app E2E_TOKEN=$E2E_TOKEN …`) and read
  `os.environ["E2E_TOKEN"]`.
- **Inline `python3 -c "…"`** — the other ten — interpolate `${E2E_TOKEN}` directly, the same way
  they already interpolate agent names.

**Why a shared file rather than fifteen pasted snippets.** Fifteen copies of one decision is exactly
the pattern this repo already carries postmortems about — `agent_endpoints.py` exists because a URL
was built in eight places and drifted, and `routers/webhook_clients.py`'s header documents the same
class. One definition, fifteen callers.

`e2e_require_token` aborts with a message naming the cause, so the *next* auth change produces
"could not authenticate" rather than another round of "the scheduler is broken".

## Verification

| Suite | Before | After |
|---|---|---|
| suite-26 (scheduler) | 1 pass / 3 fail — `create trigger 401` | **4/4 PASS** |
| suite-28 (event gateway) | exit 1, empty diagnostic, 0 assertions | **7/7 PASS** |
| suite-66 (production triggers) | 1 pass / 2 fail — `sched run=None` | see run log |

## The second bug, made while fixing the first

The initial fix minted **one** token in bash at suite start. Keycloak issues
`expires_in = 300`. suite-71 runs ~25 minutes. So the token died mid-suite and the last
cases 401'd, twenty minutes after the same client had been working:

```
POST /api/v1/agents/s71-fail-da2215/triggers  401 Unauthorized
→ suite-71 reported: T-S71-005 alerting … pos_run=None
```

An alerting failure, three layers from an expired token — **the same misdirection this helper
exists to eliminate, reproduced one layer down.** "Suites don't authenticate" was replaced by
"suites authenticate once."

The real fix is that authentication is a **per-request** concern, not a per-run one.
`lib/e2e_auth.py::BearerAuth` is an `httpx.Auth`, which httpx re-evaluates on every request for
both sync and async clients, re-minting ~60s before expiry. A static
`headers={"Authorization": …}` is evaluated once at client construction and structurally cannot
outlive the token — no amount of tuning fixes that shape.

Applied to the five detached-driver suites (66, 70, 71, 75, 77). The ten short inline suites keep
the interpolated token: they finish well inside 300s, proven by 26 (4/4) and 28 (7/7).

### Operational footnote

Editing a suite **while it is running** corrupts it. Bash reads a script incrementally by byte
offset, so a mid-run edit lands it mid-line — suite-66 died with `line 156: c: command not found`,
which looks like a syntax defect and is not one. Let a suite finish, or copy it first.

## Lessons

- **An audit header is not a credential.** `X-User-Sub` says *who is acting*; it cannot say *whether
  they may*. Any endpoint that gains real authentication invalidates every caller that only ever
  sent the audit stamp — including tests.
- **A test that fails without naming its cause is a test that will be ignored.** Both silent suites
  had their real error available and discarded it. Setup failures must print the response body.
- **Adding auth to an endpoint is a blast-radius change.** `grep -l '/triggers' scripts/e2e/` would
  have listed all fifteen at the time of `76b3570`.
- **Reading a suite is not running it.** The earlier finding that these suites "asserted the wrong
  things" was drawn from their source while they were failing at setup. Run the neighbours before
  characterising them.
- **A credential's lifetime is part of its contract.** "Does it authenticate?" and "does it still
  authenticate at minute 25?" are different questions, and only the second one is answered by
  actually running the long suite. The first fix passed a 4-minute suite and failed a 25-minute one.
- **Fixing a class of bug is where you are most likely to re-commit it.** Both the original defect
  and my own repair failed the same way: the error surfaced far from its cause, and the surface
  reading ("the scheduler is broken", "alerting is broken") was wrong both times.

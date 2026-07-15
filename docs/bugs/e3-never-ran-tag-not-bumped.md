# E-3's code never ran: the source changed but the tag never moved (eval-runner + studio)

**Found:** 2026-07-15 by the Eval-v2 **E-3** no-fakes gate (suite-75, 6 of 12 assertions FAILED).
**Fixed:** eval-runner `0.1.10 → 0.1.11`, studio `0.1.140 → 0.1.141`.

## Symptom

suite-75 failed six assertions at once, all pointing the same direction: every scheduled eval
item scored on a **single dimension**.

```
FAIL T-S75-003 record ⇒ NOT delivered   |  item 0 has no run_id
FAIL T-S75-004 the job spec IS the input|  item 0 has no run_id
FAIL T-S75-005 satisfied side-effect    |  side_effect=None dims={'response': 1.0} trigger_payload==job_spec=False
FAIL T-S75-006 violated occurs:'never'  |  side_effect=None dims={'response': 0.0} diff0={}
FAIL T-S75-007 durable-inner trajectory |  dims={'response': 0.0}
FAIL T-S75-008 fail-closed              |  playground_runs_for_agent=1 (want 0)
```

Everything E-3 added — `side_effect`, `trajectory`, `tool_call`, `trigger_payload`, the
fail-closed refusal — was simply **absent** at runtime.

## Root cause — a code change that never reached the cluster

`MODE=scheduled` selects the branch:

```python
MODE = os.environ.get("MODE", "reactive")          # main.py:38
...
if MODE == "scheduled" and not WORKFLOW_ID:        # main.py:1117
    outcome = await _run_scheduled_item(client, item, idx, inner_shape)
    continue
```

Commit `6d93401` (**E-2**) set `EVAL_RUNNER_TAG="0.1.10"`; that image was built and cached on
the node. Commit `9f6603a` (**E-3 P1–P4**) then added `_run_scheduled_item` to
`services/eval-runner/main.py` **and left the tag at `0.1.10`**. With
`imagePullPolicy: IfNotPresent`, the node never re-pulled: it kept serving the **E-2 image**,
which has no scheduled branch at all. So `MODE=scheduled` fell straight through to the generic
reactive path (`main.py:1191`, `POST /playground/runs` + `/stream`) — which scores `response`
only and writes no `trigger_payload`.

**E-3's code had never executed a single time.** The same commit changed `studio/src/` (5 files)
without bumping `STUDIO_TAG`, so the served bundle was pre-E-3 too (`scheduled-job-spec` and
`job-spec-evidence`: **0 occurrences** in the shipped assets).

| Dir changed by `9f6603a` | Tag bumped? | Cluster had E-3? |
|---|---|---|
| `services/registry-api/` | ✅ 0.2.184→0.2.185 | yes |
| `sdk/agentshield_sdk/` | ✅ 0.1.47→0.1.48 | yes |
| `services/eval-runner/` (2 files) | ❌ **no** — stayed `0.1.10` | **no** |
| `studio/src/` (5 files) | ❌ **no** — stayed `0.1.140` | **no** |

### Why every existing check stayed green

This is the part worth internalising. The two habits that normally catch deploy drift both
**passed**, because neither was looking at this:

- *"bump the tag in BOTH `deploy-cpe2e.sh` and `values.yaml`"* — both files **agreed**… on the
  stale tag.
- *"verify the cluster matches the tag files"* — the cluster **faithfully matched** `0.1.10`.

The failure isn't disagreement between two sources; it's that **all three sources agree on a
number that no longer corresponds to the source code**. A tag is a claim about content, and
nothing was checking that claim.

### Safety consequence (not just a false red)

On the deployed image, the reactive-inner item that E-3 must **refuse** instead **fired a real
run** (`playground_runs=1`, gate asserts `0`). E-2's record seam does not ride the reactive
`/chat` path, so a scheduled eval on that build would have **delivered the real side effect** —
precisely the hazard E-3 exists to remove. A stale image is not a cosmetic problem.

## Fix

1. `EVAL_RUNNER_TAG` `0.1.10 → 0.1.11`; `STUDIO_TAG` `0.1.140 → 0.1.141`, mirrored in
   `charts/agentshield/values.yaml` (eval-runner appears **twice**: `evalRunnerImage` and
   `registry-api.env.EVAL_RUNNER_IMAGE`; studio's effective pin is the **top-level** values.yaml,
   which overrides the sub-chart default).
2. Rebuilt + deployed via `scripts/deploy-cpe2e.sh`, then **verified content, not the tag**:
   `_run_scheduled_item` present in `:0.1.11`, and all four E-3 markers present in the served
   studio bundle.

## Class fix — service-dir ⇄ tag coupling

`scripts/smoke-test-cp1-e3-constitution.sh` now asserts, per commit: *if any file under a service
dir changed, that service's `*_TAG` must be bumped in the same commit.* Pointed at the real
offender it reproduces the defect in seconds:

```
$ AUDIT_REF=9f6603a bash scripts/smoke-test-cp1-e3-constitution.sh
PASS  T024 registry-api: code changed AND REGISTRY_API_TAG bumped
FAIL  T024 eval-runner: 2 file(s) changed under services/eval-runner/ but EVAL_RUNNER_TAG was NOT bumped
FAIL  T024 studio:      5 file(s) changed under studio/src/ but STUDIO_TAG was NOT bumped
PASS  T024 declarative-runner (SDK): code changed AND DECLARATIVE_RUNNER_TAG bumped
```

It also NOTEs uncommitted service code, which needs a bump before it ships.

## Lessons

1. **A tag is a claim about content — verify the content.** "Deploy succeeded" and "the tag
   matches" are both compatible with the cluster running last week's code. After deploying, grep
   the **image/bundle** for a symbol the change introduced. Every check here was green while the
   feature was absent.
2. **Agreement is not correctness.** Three sources agreeing means nothing if they agree on a
   stale value. Checks that compare A against B miss the case where A and B are both wrong.
3. **Fail-safe defaults hide missing code.** `os.environ.get("MODE", "reactive")` degrades an
   *absent branch* into a *plausible wrong answer* (`dims={'response': 1.0}`, `passed=True`) rather
   than an error. Same shape as the `side_effecting` bug: safe, wrong, invisible.
4. **The gate paid for itself again.** Six failures, one cause, found only because suite-75 drove
   real Jobs and asserted real persisted dimensions. An API-level or mocked test would have proven
   the *scorer* works — while the branch that calls it never ran.
5. **Reproduce before concluding.** Three plausible hypotheses (stale image, missing `MODE`, bad
   dispatch condition) were each disproven by direct inspection before the real cause surfaced.
   The local image *did* contain the code — only the **node's cached** image didn't.

## Files
- `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml` (the fix)
- `services/eval-runner/main.py` (the code that was never deployed — unchanged; it was correct)
- `scripts/smoke-test-cp1-e3-constitution.sh` (the class fix)
- `scripts/e2e/suite-75-eval-v2-scheduled.sh` (the gate that found it)

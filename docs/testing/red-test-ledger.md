# Red-test ledger

**Purpose:** one place that says which tests are failing, why, whose fault they are, and what
closes them. Kept current — a stale entry here is worse than no entry, because it makes a
real regression look like a known one.

**Why this exists.** Failures were being triaged conversationally and fixed as they were hit,
which works until two of them look alike. This session alone produced three cases where a
red test was attributed to the wrong cause: `G-R1-7` (blamed on scavenged fixtures, actually
the OPA identity floor), the first file-level hygiene rule (flagged two suites whose routes
are not gated), and `suite-45` (looks like an auth regression, is a missing fixture). Every
one of those cost time that a written attribution would have saved.

## Running the suites — use the shared runner, and know its two failure modes

```bash
bash scripts/run-tests.sh --groups                 # what groups exist
bash scripts/run-tests.sh --group tools,rbac       # the minimum that discriminates
bash scripts/run-tests.sh --audit                  # registration hygiene, cluster-free
```

**Never hand-roll a `while read ... < manifest` loop.** Without `</dev/null` on the suite
invocation a suite consumes the rest of the manifest from stdin and the loop ends *silently,
reporting success*. That guard is at `run-tests.sh:190`. A hand-rolled loop stopped at 68/105
and then 69/105 on 2026-08-08 and both times the early stop was misread as cluster contention.

**Two things distort a parallel run, and both look like regressions:**

| Symptom | Real cause |
|---|---|
| `rc=124`, no result line | the per-suite timeout. 300s is too short for suites that poll for cluster state (schedulers, triggers, deployment GC). Use 300s for a scoped run, 700s+ for the full manifest. |
| a suite red in parallel, green alone | contention — suites share personas, fixture names and the token endpoint. `suite-94`, `suite-96`, `suite-97`, `suite-98` all did this on 2026-08-08. |

So: **a red from a parallel or short-timeout run is a hypothesis, not a result.** Re-run the
suite alone before believing it. 4-way parallel did 37 suites in ~10 min vs ~2 h serially, so
the speed is worth the re-check — but only if the re-check actually happens.

## How to use it

- **Before fixing a red test**, check here. If it is listed, the cause is already known.
- **Before shipping**, anything not listed here must be green. A new red that is not in this
  table is a regression from the change in flight, full stop.
- **When a test goes green**, delete its row. Do not leave "fixed" rows — the table's value is
  that its length is the size of the problem.

## Status columns

| | |
|---|---|
| **Mine** | caused by work in this session's branch — must be fixed before the branch merges |
| **Pre-existing** | red before this branch; not this branch's job, but tracked so it cannot be mistaken for a regression |
| **Environment** | infra/fixture/capacity, not a product defect |

---

## API layer (bash, `scripts/e2e/`)

Ground truth: full-manifest runs on 2026-08-08.

**CORRECTION.** This section first said the run "contended with concurrent repair runs" and
stopped at 68 of 105. That attribution was wrong. The runner was a hand-rolled
`while IFS='|' read ... done < manifest` loop with no `</dev/null` on the suite invocation —
so a suite that reads stdin consumed the rest of the manifest and the loop ended silently,
reporting success. It stopped at 68 and then at 69 for the same reason, twice, and I blamed
the cluster both times.

`scripts/run-tests.sh:190` has carried the `</dev/null` guard all along. Copying the loop
without the guard is the same shape as every other defect in this file: the mechanism was
already solved somewhere in the repo and I reimplemented the broken half.

The replacement runner also drops the per-suite timeout from 900s to 300s (almost every
suite finishes under 60s; the few that poll for cluster state were burning 15 minutes each)
and runs 4-way parallel.

### FIXED AND RE-VERIFIED GREEN (2026-08-08)

All had the same root cause: the R3 scripted Bearer pass. `bash -n` accepts every one of
these, which is why they sat red.

| Suite | Result now | What was wrong |
|---|---|---|
| `suite-20-durable-playground` | 4/0 | auth block injected **inside a line continuation** — `API_POD=$(kubectl ... \` split from its second half |
| `suite-23-production-runs` | 4/0 | same |
| `suite-24-durable-production` | 5/0 | same |
| `suite-25-memory` | 6/0 | same |
| `suite-29-workflow-composite` | 11/0 | same continuation split, **plus** the Bearer went only to the cleanup DELETE while `H={'X-User-Sub':'system'}` fed every setup call |
| `suite-40-workflow-deploy` | 8/0 | same as suite-29 |
| `suite-30-orchestration-modes` | 12/0 | zero `Authorization` anywhere; `c.post('/agents/', ...)` relative form hid it from every grep |
| `suite-35-approval-resume` | 3/0 | same |
| `suite-95-trigger-lifecycle-disarm` | PASSED | same |
| `suite-96-schedules-endpoint` | PASSED | same — and it had been passing *while* its agent create 401'd, running later cases against rows left by earlier runs |
| `suite-78-conversations` | 6/0 | sourced `lib/e2e-auth.sh` but never called `e2e_set_token` |
| `suite-75-context-storage` | 0/0/2 skipped | same; no longer errors, now skips on a missing fixture |
| `suite-77-knowledge-rag` | 4/0/1 | header dict with no Bearer |
| `suite-80-agent-knowledge-binding` | 4/0 | header dict with no Bearer (was 0 passed / 1 failed) |
| `suite-16`, `50`, `84`, `87` | green | see the previous commit |

### FIXED, NOT YET RE-RUN — must be verified before merge

Patched by the same mechanical pass and syntax-checked, but not executed. **Treat as unknown,
not green.**

`suite-58-workflow-live-run`, `suite-61-eval-mode-plumbing`,
`suite-64-production-workflow-golden-path`, `suite-65-production-hitl-console`,
`suite-68-daemon-no-input`, `suite-72-eval-v2-durable`, `suite-73-eval-v2-workflow`,
`suite-74-eval-v2-side-effects`, `suite-80-eval-v2-regression`,
`suite-94-trigger-dispatch-environment`

### STILL RED — all triaged 2026-08-08

| Suite | Result | Cause | Owner |
|---|---|---|---|
| `suite-45-hitl-e2e` | 3/5/5 | **G-100** — the `hitl-agent` fixture does not exist. 404s, not auth. | fixture |
| `suite-37-workflow-hitl-opa` | 1/2 | **ROOT CAUSE CORRECTED 2026-08-09.** This said "`internal.py` does not mint a RunContext, so a `user_delegated` tool call is denied — the P1 remainder". `internal.py` now mints (`0.2.277`) and the suite is **unchanged at 1/2**, which is how the misdiagnosis surfaced. The run never reaches a pod at all: `agent_runs.error_message` reads *"agent 'serper-agent-4' has no running production deployment — it is deployed to sandbox"*. It fails the ADMISSION check, so identity is never consulted. Same family as 45/59/60 — a fixture gap, not an identity gap. | fixture |
| `suite-59-workflow-orchestrations-live` | FAILED | `001_agents_running` — its fixture agents are not deployed | fixture |
| `suite-60-single-agent-durable-hitl` | FAILED | `001_wf_payout_running` — same | fixture |
| `suite-71-scheduled-e2e` | timeout | genuinely slow; the suite says "can take many min" itself. Needs 900s+, not 300s. **ALSO had a broken assertion (fixed 2026-08-09):** its quoted heredoc meant `ADMIN = "${E2E_SUB}"` was the literal string, so `trig.armed_by == ADMIN` could never be true and `rb != ADMIN` passed for the wrong reason. Now threaded via `S71_ADMIN_SUB`. Its assertions have likely never actually run. | timeout |
| `suite-64`, `suite-65`, `suite-66`, `suite-68`, `suite-94`, `suite-96` | ✅ **REPAIRED 2026-08-09** | were producing NO result at all — the R3/E2E_SUB scripted passes spliced bash (`source`, `e2e_set_token`) INSIDE their Python heredocs, so the driver died with SyntaxError and the suite reported "no result file" rather than a failure. Postmortem: `docs/bugs/six-suites-dead-from-bash-spliced-into-python-heredocs.md`. Class fix: hygiene rules 8b + 8c. Verified after: 96 **10/0**, 94 **9/0**, 68 **3/0**. | fixed |

**37, 45, 59 and 60 are one problem, not four** (37 joined the list 2026-08-09 when its
recorded identity root cause turned out to be wrong — see its row): they need pre-seeded *running* agents, exactly
like the nine red Playwright specs. Seeding a known-good always-running fixture agent would
move ~12 tests from "known red" to actually asserting — the highest-leverage test fix left.

### Fixture seeding — `scripts/seed-e2e-fixtures.sh` (2026-08-08)

New script. Creates and deploys the five always-running agents the suites assume exist —
`hitl-agent` (reactive, bound to `web_search` at risk=high), `wf-router`, `wf-payout`,
`wf-confirm`, `wf-supervisor`. Idempotent; leaves an already-running deployment alone.

**Result: 5/5 fixture pods Running.** What that did and did not fix:

| | Before | After | Reading |
|---|---|---|---|
| `suite-45` | 3 passed / 5 failed / **5 skipped** | 4 passed / 7 failed / **2 skipped** | the fixture gate now passes, so cases that previously SKIPPED now execute and fail on real assertions. More red, more information — the suite went from proving almost nothing to proving something. |
| `suite-59` | `001_agents_running` FAIL | **001 passes**, 002–005 fail | the agents are up; the orchestration RUNS do not complete |
| `suite-60` | `001_wf_payout_running` FAIL | still FAILED | same |

**What is still blocking, precisely:**

1. **Stale hardcoded subs — the same class fixed three times already.** `suite-45` runs the
   playground as `ADMIN="75c7c8b3-…"`, a sub with NO `user_team_assignments` row. R2 derives
   `created_by` from the verified token, so seeded agents are owned by the REAL platform-admin
   (`5cf374d6-…`) and the playground refuses: *"Only the agent owner can run it in the
   playground."* The pre-existing `wf-payout` is owned by that same stale literal, which is
   why the mismatch never surfaced before something new was created properly.
   **Fix:** resolve the sub live, exactly as `suite-70` and `studio/e2e/lib/api.ts` now do.
   Not done — it touches several suites.

2. **Runs do not complete.** `suite-59` 002–005 and `suite-60` need an agent that actually
   executes a turn (LLM provider reachable, tools resolvable). Seeding the deployment is
   necessary but not sufficient. This is the same boundary the bash layer states explicitly
   ("few agent pods are deployed, so runs may not complete") and the browser layer does not.

So the twelve-tests-in-one-fix estimate was **wrong**. The fixture was one of at least three
prerequisites, and finding that out cost less than assuming it.

### Stale subs — fixed across 21 suites (2026-08-08)

`lib/e2e-auth.sh` now exports **`E2E_SUB`, decoded from the token it just minted**. 28 hardcoded
occurrences of two stale subs (`75c7c8b3-…` ×25, `047fad5f-…` ×3) were replaced with it. Neither
literal has a `user_team_assignments` row — a realm recreation mints new subs and nothing updated
them. Same rule as `studio/e2e/lib/api.ts` and `suite-70`: derive the sub FROM the credential, so
the two cannot disagree.

**It did not fix the six reds, exactly as predicted before starting.** What it changed:
`suite-45` T-S45-003/004 stopped returning 403. `suite-45` is still 4 passed / 7 failed — the
remaining failures need a run that actually parks an approval, which needs agent execution.

**Ordering was the hard part, and it bit three times.** A mechanical pass that inserts a
definition without checking where the variable is first *read* produces:

- `e2e_set_token` before `API_POD` is assigned (the mint needs the pod)
- `${E2E_SUB}` read before `e2e_set_token` (expands to empty under `set -u`, or aborts)
- both at once, needing the pod resolution itself hoisted

Now **gate rule 9** — `${E2E_TOKEN}`/`${E2E_SUB}` used above the `e2e_set_token` call. Remembering
had not worked across three passes; the rule is the substitute. Also: rule 5 now recognises
`BearerAuth` (httpx's credential hook never writes a literal `Authorization`, so `suite-95` was a
false positive).

**Two platform-admin subs exist on this cluster** (`643b0e62-…`, `5cf374d6-…`). `suite-45`
authenticated as one and called the playground as the other, so the owner check refused it.
Hardcoding either is a coin flip; the caller now comes from the token.

### FIXED after triage (2026-08-08)

| Suite | Now | Was |
|---|---|---|
| `suite-54-agent-class-shape-dispatch` | **14/0** | called `create_agent`/`update_agent` DIRECTLY in-pod; R2/R3 replaced their `x_user_sub`/`user` params with `claims` and added gates. Now inserts a real role row and passes `claims`, so it exercises the authorization path rather than bypassing it. |
| `suite-70-daemon-identity` | **9/0** | three separate causes: (a) `deny_reason` precedence — a real defect, fixed in 0.2.273; (b) a bundle saying `user_delegated` while the input claimed `daemon` — asserting the behaviour D-1 removes; (c) `ADMIN_SUB` hardcoded to a sub with no `user_team_assignments` row, the same staleness that was in `studio/e2e/lib/api.ts`. |
| ~~`suite-26-scheduler`~~, ~~`suite-67`~~ | GREEN | timing flakes |
| `suite-94`, `96`, `97`, `98` | GREEN alone | false reds — see the parallel-run distortions above |

### NEVER RUN in this pass

Suites 68–105 in manifest order — the run stopped at 68. **Unknown, not green.**

## Browser layer (Playwright, `studio/e2e/`)

Baseline for attribution: the **2026-08-05 full run — 92 passed / 18 failed**. Latest full run
(2026-08-08, before the Decision 45 work) — **117 passed / 10 failed**.

| Spec | Case | Status | Cause | Closes when |
|---|---|---|---|---|
| `context-attribution` | catalog workflow chat renders ≥2 attributed member bubbles | Pre-existing | in the Aug-5 baseline | needs a running multi-member workflow deployment |
| `context-attribution` | share-context toggle persistence | Pre-existing | in the Aug-5 baseline | cascades from the case above (serial mode) |
| `deployment-overview` | deploy → overview → reload survives | Pre-existing | in the Aug-5 baseline | needs a running deployment |
| `deployment-overview` | runs + memory tabs render deployment-scoped | Pre-existing | in the Aug-5 baseline | as above |
| `hitl-deployment-chat` | high-risk tool → self-approve → resume COMPLETES | Pre-existing | in the Aug-5 baseline | needs a live agent pod that can complete a run |
| `playground` | History dock opens for a reactive deployment | Pre-existing | in the Aug-5 baseline | needs a reactive deployment with conversations |
| `workflow-builder` | sandbox workflow HITL approved inline | Pre-existing | in the Aug-5 baseline | needs a live workflow run |
| `workflow-cost` | trigger-demo-flow row renders a $ cost | Pre-existing | in the Aug-5 baseline | cost tracking is unwired (see the cost-tracking memory) |
| `workflow-inline-approval-live` | sandbox workflow parks and is approved INLINE | Pre-existing | in the Aug-5 baseline | needs a live workflow run |

**Common shape:** every one of these needs a *running agent or workflow that completes a
turn*. The bash layer accepts that boundary explicitly ("few agent pods are deployed, so runs
may not complete"); the browser layer does not, and these nine are where that shows. Whether
to seed a known-good always-running fixture agent for them is an open call — it would move
nine tests from "known red" to "actually asserting", which is the only thing that makes them
worth having.

---

## Fixed this session — kept only as attribution examples, delete when this file next grows

| Was red | Real cause | Wrong first diagnosis |
|---|---|---|
| `suite-18` T-S18-005/006/011 | OPA Gate 6 identity floor: every case sent `user_id: ""` | "scavenged tool fixtures / zero of 47 agents grant them" — recorded in the ledger for a day |
| `suite-80` 0 passed / 1 failed | `HDR = {"X-User-Sub": USER}` — no bearer, red since `0.2.267` | invisible to three sweeps because every grep used the literal path, not `f"{BASE}/tools/"` |
| `mcp-servers.spec.ts` bind case | discovered tools were `private` with `owner_team = NULL` → invisible to everyone | assumed a Playwright flake |
| `suite-98` T-S98-023/024/026 "ERR" | `status_body` truncates at `r.read(400)` | read as a product failure |

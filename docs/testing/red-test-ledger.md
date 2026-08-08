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

Ground truth: a full-manifest run on 2026-08-08 reached **68 of 105** suites before it
contended with concurrent repair runs — **53 green, 15 red**. That run is what produced this
table; nothing here is from memory.

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

### STILL RED — not triaged

| Suite | Result | Notes |
|---|---|---|
| `suite-45-hitl-e2e` | 3/5/5 | **G-100** — the `hitl-agent` fixture does not exist. 404s, not auth. |
| `suite-26-scheduler` | 3 passed / 1 failed | untriaged |
| `suite-37-workflow-hitl-opa` | 1 passed / 2 failed | untriaged; touches OPA, so re-check against the Decision 45 rego |
| `suite-54-agent-class-shape-dispatch` | traceback | untriaged |
| `suite-59-workflow-orchestrations-live` | FAILED | untriaged |
| `suite-60-single-agent-durable-hitl` | FAILED | untriaged |
| `suite-67-deployment-gc-and-drift` | no result line | untriaged |

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

# Evaluation — State of Play

**Status:** LEDGER — the status of record for evaluation. **Verified against code 2026-07-27.**
**Author:** Kalyan + Claude

Three eval docs disagreed with the product and with each other. This file is the single answer to
*"what actually evaluates today, what is deferred, and what should we build next."* Every claim below
was read out of code, not out of a plan. Where a design doc contradicts this file, **check the code and
fix the doc** — that is how the drift started.

| Doc | What it is now |
|---|---|
| **this file** | Status of record. Start here. |
| `eval-ux-enrichment.md` | The **approved forward plan** (Slice 0 → Wave 1 → Wave 2). Still accurate. |
| `todo/agent-evaluation-capability.md` | Market research + prioritization frame from 2026-07-07. Its "current state" section is **history**; re-scored in place. |
| `plan/execution-models-v2/eval-v2/` | How the engine got built. E-0…E-5 are done; for those the **code is the spec**. |

---

## 1. What evaluates today

Eval is **mode-aware**, not response-only. One door — `POST /playground/eval/score` — dispatches on an
**explicit `mode` discriminator**, never by sniffing which keys an item happens to carry. Seven scorers
live in `services/registry-api/judge.py`; **six of the seven are pure code, no LLM.**

| Scorer | Kind | What it asserts |
|---|---|---|
| `score_response` | LLM judge | Output vs `expected_output`, or reference-free against a `rubric` |
| `score_trajectory` | deterministic | Ordered tool sequence vs a golden one, under 4 match modes |
| `score_tool_calls` | deterministic | Tool name + `args_match` dict-subset + HITL parking |
| `score_side_effects` | deterministic | Recorded writes vs assertions (`exactly` / `at_least` / `never`) |
| `score_filter` | deterministic | Webhook routing decision (match / miss) vs `AgentEvent.status` |
| `score_injection` | deterministic | `must_not_call` + `must_refuse` under adversarial input |
| `score_member_path` | deterministic | Which workflow members ran, in which order |

**Trajectory match modes** — `exact` (same calls, same order, no extras), `ordered` (expected appear
in-order, extras allowed between), `superset` (every expected call happened; order and extras free),
`unordered` (same set, any order; missing *and* extras both penalize). Default is `superset`, the most
forgiving; workflow member-path defaults to `ordered` on purpose, so a right answer reached by the
**wrong route** scores below 1.0.

### Which dimensions each mode produces

Read off `routers/playground.py`. The mode is the dataset's mode; you cannot mix.

| Mode | Dimensions | Default weights |
|---|---|---|
| `reactive` | `response` | composite **==** response (byte-identical to pre-Eval-v2 behaviour) |
| `workflow` | `member_path`, `response`, `per_member` | 0.4 / 0.4 / 0.2 |
| `durable` | `response`, `trajectory`, `tool_call`, `side_effect` | side-effect-skewed |
| `scheduled` | `response`, `side_effect` (+ `trajectory`/`tool_call` when durable-inner) | 0.4 / 0.6 |
| `webhook` | `filter`, `response`, `trajectory`, `tool_call`, `side_effect`, `injection` | per e4 |

### Three properties worth knowing before you extend it

**Present-dims-only reduction.** The reducer sums the weights of the dimensions that are actually
present. A weight naming an absent dimension cannot silently drag the composite down.

**Vetoes fire on exact facts, and weights cannot override them.** `filter_error` and
`injection_succeeded` force the composite to 0.0 (`playground.py:1610-1617`). They are facts, not
heuristics — which is also why a what-if re-score can read a stored veto as truth without re-running
policy.

**A correctly-rejected webhook event is not judged.** If the filter rightly dropped an event, nothing
ran, so there is no response to score — calling the judge there would burn a model call on empty text
and turn a judge outage into a *failed filter* item. Keyed off the mode discriminator plus the door's
explicit `matched` decision.

### The gate

`eval_passed` **is an evaluated result**, set automatically at `routers/eval_runner.py:594` when a
completed run's composite clears that run's **own** `pass_threshold` — per-run, not a platform-wide 0.7.

---

## 2. Deferred — 13 of the capability doc's 17 priorities

Nothing below exists in code. Verified by absence of any model, table, endpoint, or page.

| Priority | Capability | Note |
|---|---|---|
| **P0** | Custom evaluator definitions (1.2) | No evaluator model or table at all |
| **P1** | Evaluator library (1.3) | Follows 1.2 |
| **P1** | Version comparison (2.1) | Designed — `eval-ux-enrichment.md` Wave 1 Slice 2 |
| **P2** | Regression detection (2.2) | No score-drop alert anywhere |
| **P2** | Online / production eval (3.1) | No sampling of live traffic |
| **P2** | Human annotation queues (3.2) | Playground thumbs exist; no review queue |
| **P2** | Experiment history (2.3) | No eval-runs list page exists |
| **P3** | Production → dataset (3.3) | Endpoints exist but are **dark** (no caller) |
| **P3** | Red team (4.3) | — |
| **P3** | Dataset versioning (5.1) | Datasets are typed per mode, but not versioned |
| **P3** | CI/CD integration (5.2) | — |
| **P3** | Analytics dashboard (5.3) | — |
| **P3** | Multi-turn / conversation eval (5.4) | See §4 |

Partially shipped: **safety suite (4.1)** — `score_injection` and the veto are in, but there is no
pre-built adversarial dataset. **Governance regression (4.2)** — `expect_approval` is the assertion
primitive (fail-closed: a gate expected to park that did **not** park scores 0), but no suite wraps it.

---

## 3. Declared but inert — the dangerous middle

These read as shipped and are not. Each is worse than a missing feature, because a reader assumes
coverage that does not exist.

**`tool_mocks` — accepted, persisted, ignored.** Declared on the durable/scheduled/webhook item
schemas. The record seam never reads it; every intercepted call gets a type-default success sentinel.
The reason is architectural, not neglect: the seam runs **in the agent pod**, and the item never travels
there — only `eval_mode` rides the dispatch body. Wiring it means threading a per-tool mock map through
`dispatch_durable_run` → `DurableRunRequest` → `begin_eval_context`. Its only readers today are two unit
tests. *Consequence:* you can assert what the agent **called**, never how it behaves when a tool
**returns** a particular value.

**`adversarial_eval_passed` — an attestation the product presents as an evaluation.** It gates production
deploy (`deployments.py:641`) and publish for risky tools (`agents.py:551`), and **no eval run can set
it** — the only writers are `versions.py:120/205`, reached by a manual `PATCH`.

*Corrected 2026-07-28:* an earlier revision of this file called it "a hard gate with no producer", which
overstated the impact. `PlaygroundPage.tsx:199` ships a working "mark adversarial-eval passed" button with
clear 422 copy on the blocked path, so **nobody is blocked** — it is a one-click attestation, not an
outage. The real gap is that a human clicking "yes, this is safe" is the same shape as the `eval_passed`
rubber stamp Decision 20 existed to remove. Priority set accordingly in §6 (slot 5, not slot 1).

Do not "fix" this by auto-flipping it from injection scores: it is monotonic, it blocks production, and
"ASR = 0 on the 2 probes this dataset happened to include" is not "adversarially safe". If built, it needs
an explicit `adversarial=true` run, ≥N probed items, ASR = 0 on all, and zero vetoes — its own slice with
its own review, never a side effect of another change.

**`_DatasetItemBase.weight`** — a declared field no scorer reads.

---

## 4. Multi-turn: the substrate exists, the eval does not

Worth stating precisely, because it looks further away than it is.

All five dataset modes take a **single input**. There is no multi-turn mode, no conversational scorer,
and no `multi_turn` reference anywhere in `judge.py` or the eval-runner. But the **runtime** already has
full conversations — `ConversationStore`, `thread_id`, transcripts that persist across turns. Eval
simply never consumes them.

`eval-v2/research.md` §4.6 surveyed the field and recorded the finding that decides the design: **better
single-turn does not predict better multi-turn** (MT-Bench, MINT). So this cannot be approximated by
scoring turns independently.

**It depends on `tool_mocks`.** A multi-turn path is only deterministic if the tools return fixed values
across turns; otherwise turn 3 varies because turn 2's tool answered differently. Build the mock
threading first — it is bounded plumbing with the route already written down — and multi-turn becomes
tractable. Attempted in the other order, it will be flaky and nobody will trust it.

---

## 5. Traces: fully populated, entirely unread

Live DB, today:

```
tot=447   with_trace=447   failed=201   failed_with_trace=201
```

**Every** eval result row, including every failure, carries a `langfuse_trace_id`. Zero nulls. The
per-row click-through already ships (`EvalResultsPage.tsx:484` renders `trace_url`).

What does not exist is anything that **reads span content** to explain a failure — "the tool returned
500", "retrieval came back empty", "it looped three times then gave up". The planned triage
(`eval-ux-enrichment.md` Wave 2) infers from `dimension_scores` and `eval_detail` only, so it can only
report what a dimension already encodes.

If trace-based triage gets built, build it as a **fixed vocabulary of failure modes** (tool error, empty
retrieval, loop, timeout) rather than free-form LLM summarization — the same reasoning that made the
existing scorers deterministic, and the same reason `eval-ux-enrichment.md` rejected embedding-based
clustering.

---

## 6. What to work on — in this order

**Sequenced by what unblocks what, not by severity.** Three rules produced this order: fix the things
that make *other* work unsafe first; fix the harness before you lean on it; don't build on a foundation
you are about to change.

> **Slice 0 is DONE** — shipped 2026-07-27 as registry-api **0.2.234** / studio **0.1.167**. Per-request
> eval resolution + `eval_source` provenance, `lib/evalVerdict` as the single verdict owner, deny-by-default
> on two listing routes, and `suite-80 T-S80-000b` rewritten to discover its scope. Verified: `suite-89`
> 10/0, `suite-80` 15/15, API + browser `--group eval` green, Vitest 536, `CP3a` 6/6. It also closed E-6's
> open Studio tasks, so **Eval v2 is complete**. Details: `docs/plan/eval-slice0/`, Decisions 32-33.

---

### 1 · Deploy safety (~half a day)

**First because everything after it ships through this.** Slice 0's first deploy exited green, helm
reported `STATUS: deployed`, and the pod sat in `ImagePullBackOff` for seven minutes because the image
had never been built.

- **ECR preflight** in `scripts/deploy-eks.sh`: before `helm upgrade`, assert every tag in
  `values.yaml` exists (`aws ecr describe-images --image-ids imageTag=…`). Turns a seven-minute
  ImagePullBackOff into a one-second failure naming the tag, and works regardless of how many tag lists
  exist.
- **Derive the build tags from `values.yaml`** so the third list stops existing. There are currently
  three (`deploy-cpe2e.sh`, `values.yaml`, `deploy-eks.sh`) plus `studio/src/lib/build.ts`, and
  CLAUDE.md documents two. Bumping a third list is the fix that already failed.
- **Add a `startupProbe` to registry-api** in the same change. Two uvicorn workers under a 500m CPU
  limit exceed the liveness `initialDelaySeconds=15` on cold start, so the kubelet kills a *still-booting*
  container. Liveness answers "is it wedged?", not "has it finished booting?".

Detail: `docs/bugs/three-tag-sites-eks-build-vs-helm-deploy.md`.

### 2 · Test harness (~2-3 days)

**Before Wave 1, because Wave 1 is where you actually depend on it.**

- **Run the twelve unverified functional groups.** The pod-selector sweep touched 49 suites; only
  `tools`, `mcp`, `rbac` and `eval` were re-run. The rest is genuinely unknown, and unknown is the point.
- **Shared `api_pod()` helper.** `--field-selector=status.phase=Running` is copy-pasted 51 times and
  still admits a Running-but-**not-Ready** pod. Fix it in one place instead of a third 51-site sweep.
- **`embedding-sidecar`** is in ImagePullBackOff — never built by `deploy-eks.sh`, absent from ECR.
  Degrades KB/RAG on EKS.

### 3 · Decision 33 option B — team-scoped eval reads (~2-3 days)

**Deliberately ahead of Wave 1.** Without it, `list_eval_runs` returns only the caller's own runs, so an
approver reviewing someone else's agent sees an empty eval history — you would build the whole regression
story and then find that half its audience cannot see it. Cheaper before Wave 1 than retrofitted into it.

Collision to settle first: Decision 25 left `rbac.py: ENFORCE=False` platform-wide, so this cannot lean on
`has_artifact_role`. It must scope on `user_team_assignments` directly or wait for the enforcement flip.

### 4 · Wave 1 — the regression story (~1.5 weeks)

Eval-runs list page (the sidebar labels an item "Eval Runs" and points at `/playground`, which has none)
→ run-to-run diff → trend with the threshold as a `<ReferenceLine>`.

Two corrections already banked, both of which would have cost a day mid-flight:

- The migration is **0076**. The doc says 0073; that number is taken
  (`0073_credential_blobs_and_credential_ref.py`) and the head is 0075.
- **Two** Recharts components, not three. The heading said three from the first draft while the body
  always enumerated two; the sparkline stays hand-rolled.

The non-negotiable inside Slice 2 is **fingerprinting the join key**. UI-authored dataset items get no
stable `id`, so runs can only be joined positionally — without the fingerprint, a diff silently compares
two unrelated test cases and reports it as a regression.

### 5 · `adversarial_eval_passed` gets a real producer (~2-3 days)

**Not urgent — corrected 2026-07-28.** An earlier revision of this file called it "a hard gate with no
producer", which overstated it: `PlaygroundPage.tsx:199` ships a working "mark adversarial-eval passed"
button with clear 422 copy, so **nobody is blocked**. The gap is that it is an *attestation* where the
product implies an *evaluation* — the same shape as the `eval_passed` rubber stamp Decision 20 existed to
remove.

If built: an explicit `adversarial=true` run, ≥N probed items, ASR = 0 on all of them, and zero vetoes.
Never a side effect of another change, and **never auto-flipped from injection scores** — it is monotonic,
it gates production, and "ASR = 0 on the two probes this dataset happened to include" is not
"adversarially safe".

### 6 · Wave 2 + the engine gaps

Failure triage (`lib/failureReason.ts`, `clusterFailures`, `dimensionRollup`), the real eval-gate card on
`AgentDetailPage`, what-if re-scoring, traffic→cases, dataset quality.

**Folded in here: the observability score vocabulary.** `ObservabilityTracesPage.tsx:22` holds a **fourth**
`scoreColor`, hardcoding 0.8/0.5 over a trace's `judge_score`. It lands here not because Wave 2 edits that
file — it does not — but because Wave 2 is when observability score-rendering comes into scope at all
(`ObservabilityComparePage:70-82` for the diff vocabulary, `ObservabilityDashboardPage:204-217` for
`BarRow`, `:223` for the latency chart). Extracting one band for those three is worth doing once, at that
moment. **It cannot simply import `lib/evalVerdict`:** that `scoreColor` takes no threshold, and a trace has
no `pass_threshold` to grade against, so it needs its own explicit judge-score band rather than an invented
threshold.

Then the two engine gaps, **in this order**: `tool_mocks` threading, then multi-turn. That is a dependency,
not a preference — a multi-turn path is only reproducible once tools return fixed values across turns
(§4).

**Still not next:** custom evaluator authoring. It is P0 in the 2026-07-07 doc, but that ranking predates
the scorer library — seven dimensions now ship, which was the actual need behind it.

---

## 7. Known gaps in this ledger

- **not-yet-wired (debt)** — no automated check keeps this file honest. It was written by reading code
  once. The `--audit`-style guard that would work is a grep for scorer names in `judge.py` against the
  table in §1.
- **deferred (intentional)** — per-item cost/latency for eval runs. Only workflow mode has upstream
  data (`agent_runs` carries `cost_usd`/`latency_ms`; `playground_runs` carries neither).

# Eval v2 — Mode-Aware Agent Evaluation — Implementation Plan

**Slice:** Eval v2 of Execution Models v2 (cross-cuts WS-1…WS-6). **This plan covers Eval v2 ONLY.**
**Closes** the two gaps in playground-execution-modes.md §8: (1) datasets + batch eval are
reactive-text-only; (2) the judge scores `input→output` only — no trajectory, tool-call, or
side-effect evaluation. **Companion artifacts:** `data-model.md` (per-mode dataset schema + result
store), `research.md` (industry survey + citations).

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, per-mode eval semantics,
> the judge-upgrade shape, the sequencing against WS-1…WS-6, and the gap ledger are **stable and
> reviewable now** — that is what writing ahead buys. The execution specifics — `file:line`, migration
> numbers, image tags, orphan-greps, exact task order — are **indicative against the 2026-07-12 /
> execution-models-v2 tree** and WILL drift as the WS spine merges. **Re-ground every specific against
> live code when this slice is minted into its own `tasks.md`** (the just-in-time step). Never treat a
> `file:line` or migration number here as ground truth. (CLAUDE.md: design docs go stale — verify in
> code before relying.)

---

## 1. Goal

Turn evaluation from **response-only** into **mode-aware**: score each execution mode on what actually
matters for it, and let developers author per-mode golden datasets. Concretely, after Eval v2:

1. **Datasets are per-mode (OQ-C).** A dataset declares a `mode` and its items follow that mode's
   schema (a discriminated union — `data-model.md` §2): reactive `{input_message, expected_output}`,
   durable `{input_payload, expected_trajectory}`, scheduled `{job_spec, expected_side_effects}`,
   webhook `{trigger_payload, expected_match, injection_probe}`, workflow `{expected_member_path}`.
   The authoring UI validates on save; batch eval interprets items **by the agent's mode**.
2. **The judge is trajectory-aware.** Beyond response correctness, the judge scores the **step
   trajectory** (right tools, right order — against real `run_steps`), **tool-call correctness**
   (name + args, exact or semantic), **side-effects** (asserted against *recorded* tool calls, never
   real deliveries), **filter decisions** (webhook matched/filtered), and supports **reference-free
   rubric scoring** for cases with no golden answer. LLM-as-judge best practices applied (rubric,
   position/verbosity-bias mitigation — `research.md` §LLM-as-judge).
3. **Batch eval branches by mode.** The eval-runner reads `eval_runs.mode` and runs the mode-correct
   loop (chat / durable run+steps / job-spec fire / synthetic webhook event), scoring the mode's
   dimensions and writing a **composite** `judge_score` (so the existing `eval_passed` publish gate
   works unchanged).
4. **Side-effects are safe.** A `eval_mode=record` seam at the tool-governance boundary records
   `{tool,args}` and returns a mock/replay instead of invoking side-effecting downstreams — so
   evaluating a scheduled/webhook agent never sends a real email.
5. **The eval → publish gate stays the wire.** A passing mode-aware batch eval auto-sets
   `eval_passed` (unchanged mechanism, `eval_runner.py:299`), now with meaningful per-mode scores
   behind it. Regression/CI eval reuses the same runner headless.

**Alignment Check:** the ultimate goal is *trustworthy publish* — an agent shouldn't reach the catalog
until it's been judged on the axis that matters for its mode. Reactive-only eval silently degrades that
for durable/scheduled/webhook agents (a durable agent can pass a response check while calling the wrong
tools). Eval v2 restores the gate's meaning per mode. We do **not** weaken the gate to ship faster; we
sequence each mode's eval behind the workstream that makes that mode real (see §8).

**Out of scope (later / other slices):** making the modes themselves real (WS-1 durable engine, WS-3
scheduled, WS-4 webhook — Eval v2 *consumes* these); a standalone eval-authoring product surface beyond
the dataset editor; human-labeling/annotation queues (thumbs feedback already exists); judge
fine-tuning/calibration studies (we apply known best-practices, not new research); cross-agent
leaderboards.

---

## 2. Architecture

Eval v2 upgrades **three seams** that already exist, plus one new safety seam. Nothing greenfield.

```
 Authoring                 Interpretation                  Scoring                     Gate
 ─────────                 ──────────────                  ───────                     ────
 DatasetsPage (per-mode    eval-runner Job                 judge.py (mode-aware        eval_runner.py
 editor, discriminated  →  reads eval_runs.mode,        →  scorers: response |      →  auto-set
 union validated on save)  runs the mode-correct loop      trajectory | tool-call |     eval_passed
   │                         │  (chat / run+steps /         side-effect | filter |      (composite
   │ items JSONB + mode      │   job-fire / webhook)        rubric) → composite         judge_score)
   ▼                         ▼                              ▼                            ▼
 playground_datasets      run_steps (WS-1) ·            dimension_scores +           publish precondition
 .mode  (data-model §3)   AgentEvent (WS-4) ·           eval_detail JSONB            (unchanged)
                          recorded tool calls           (data-model §3.3)
                          (eval_mode=record seam)
```

**Seam 1 — dataset (authoring).** `playground_datasets.mode` discriminator + per-mode item editor in
`DatasetsPage`. Existing text datasets default to `reactive` — zero break.

**Seam 2 — eval-runner (interpretation).** `services/eval-runner/main.py` gains a `mode` branch. Today
it has exactly two branches (workflow-poll vs agent-playground-stream, `main.py:162`/`:187`); Eval v2
generalizes that into a **mode dispatch**: `reactive` (today's stream), `durable` (start run → poll
`run_steps` → collect trajectory), `scheduled` (fire with `job_spec` as `input_payload`), `webhook`
(POST the synthetic event through the real filter path), `workflow` (run tree + member path).

**Seam 3 — judge (scoring).** `services/registry-api/judge.py` grows from two prompts
(`_JUDGE_PROMPT` quality, `_EVAL_JUDGE_PROMPT` correctness) into a **scorer library**: response,
trajectory, tool-call, side-effect, filter, rubric. Deterministic scorers (trajectory/tool/filter/
side-effect) are **code, not LLM** where possible (exact/semantic match); LLM-as-judge is reserved for
response quality and rubric scoring. Composite = weighted mean.

**Seam 4 (NEW) — record/mock at the governance boundary.** An `eval_mode` flag threaded eval-runner →
run-create → tool-governance wrapper. When `record`, side-effecting tools record `{tool,args}` + return
a mock/replay. Explicit parameter, not a `context` sniff (data-model.md §4).

### 2.1 Judge upgrade — the scorer library

| Scorer | Kind | Technique (research.md) | Applies to modes |
|---|---|---|---|
| `score_response` | LLM-as-judge | rubric or reference-based; verbosity/position-bias guardrails | all |
| `score_trajectory` | code + optional LLM | trajectory-match modes: `exact\|ordered\|superset\|unordered`; golden trajectory | durable, scheduled, webhook, workflow |
| `score_tool_calls` | code (+ semantic fallback) | tool-name exact-match; args partial/semantic match | durable, scheduled, webhook |
| `score_side_effects` | code | assert recorded calls vs `expected_side_effects` (`occurs`/`count`) | scheduled, webhook |
| `score_filter` | code | `AgentEvent.status` vs `expected_match` + reason substring | webhook |
| `score_injection` | code + LLM | forbidden-tool-not-called + refusal check | webhook (`injection_probe`) |
| `score_member_path` | code | member-granularity trajectory over the run tree | workflow |

**Composite:** `judge_score = weighted_mean(dimension_scores, dimension_weights)`;
`passed = composite >= pass_threshold`. Weights default per mode (e.g. durable = 0.4 response / 0.4
trajectory / 0.2 tool-call). This keeps `overall_score` a single 0–1 the publish gate already consumes.

### 2.2 Reuse, don't fork (parity)

- **One judge module.** All scorers live in `judge.py`; the eval-runner calls a **single new endpoint**
  `POST /api/v1/playground/eval/score` (generalizes today's `POST /playground/judge`, `main.py:96`)
  that dispatches by `mode` and returns `{composite, dimension_scores, detail}`. The runner never
  re-implements scoring (kills the keyword-match fallback drift, `main.py:285`).
- **One trajectory source.** Trajectory scorers read `run_steps` (the same rows the StepTracker renders)
  — no parallel trace store.
- **One record seam.** The governance wrapper is the only side-effect interception point; the runner
  sets a flag, it doesn't mock tools itself.

---

## 3. Migration / Schema

Three small, additive migrations (`data-model.md` §5), numbers **indicative** (head `0057`; WS-0 =
`0058`; Eval v2 first = **≥`0059`** — confirm at impl):

1. `≥0059` — `playground_datasets.mode` + `schema_version`; `eval_runs.mode` + `dimension_weights` +
   `pass_threshold`. Backfill all to `reactive`. Guarded/idempotent/data-preserving.
2. `≥0060` — `eval_run_results` + `dimension_scores`, `eval_detail`, `trigger_payload`, `matched`,
   `run_id` (all nullable).
3. `≥0061` — `tools.side_effecting` (default from HTTP method) — only for the scheduled/webhook slice.

`judge_score`/`overall_score` semantics unchanged (now a composite). No destructive change.

---

## 4. Constitution / retro gates (condensed)

| Gate | How Eval v2 satisfies it |
|---|---|
| **DoD #1 — real user journey** | Playwright: author a per-mode dataset in `DatasetsPage` (durable + webhook variants), launch a batch eval, assert per-dimension scores render in `EvalResultsPage`. Not just the API. |
| **DoD #2 — save→reload→assert** | Dataset create with a `webhook` item → reload → the discriminated item survives with `kind`/`trigger_payload`. Bash suite re-GETs the dataset + the eval-run results. |
| **DoD #3 — no orphan code** | Every new column (`mode`, `dimension_scores`, `eval_detail`, `matched`, `side_effecting`), scorer, and the `/eval/score` endpoint has a shipped caller/reader in the same task (grep gate). |
| **DoD #4 — vertical slices** | Ship **reactive parity first** (dataset mode + composite plumbing, no behavior change), then durable, then scheduled, then webhook — each proven end-to-end (author → eval → score → gate) before the next. |
| **DoD #5 — honest gap ledger** | §7. Trajectory-before-WS-1, filter-before-WS-4 dependencies are explicit; anything stubbed is tagged deferred vs debt. |
| **DoD #6 — reason from running product** | This plan verified `items` is free-form JSONB, `EvalRunResult` is text-only, the runner has two branches, the judge has two prompts — before proposing changes. |
| **No-Bandaid** | Per-mode interpretation is an **explicit `mode` discriminator**, not item-key sniffing; the record seam is an **explicit `eval_mode` param**, not a `context=='playground'` sniff; scoring lives in one judge module (no keyword-match fork). |
| **Fail-closed governance** | The record seam **fails closed**: if a side-effecting tool can't be classified, it is **mocked (not invoked)** during eval, never allowed through. An eval that can't record a side-effect **fails the item**, never silently passes. |
| **Ship the gate's producer** | Trajectory scoring (reader) ships only alongside real `run_steps` (producer, WS-1); filter scoring only alongside the real filter path (WS-4). No gate without its producer. |

---

## 5. File Structure (created/modified — indicative)

### Backend — registry-api
| File | C/M | Responsibility |
|---|---|---|
| `alembic/versions/00NN_eval_v2_dataset_mode_and_run_dimensions.py` | **C** | Migration 1 (`data-model.md` §5.1). |
| `alembic/versions/00NN_eval_v2_result_dimensions.py` | **C** | Migration 2 (result store). |
| `alembic/versions/00NN_tools_side_effecting.py` | **C** | Migration 3 (side-effect flag; scheduled/webhook slice). |
| `models.py` | M | `PlaygroundDataset.mode/schema_version`; `EvalRun.mode/dimension_weights/pass_threshold`; `EvalRunResult` +5 cols; `Tool.side_effecting`. |
| `schemas.py` | M | Discriminated-union `DatasetItem` (Pydantic `Field(discriminator="kind")`); `PlaygroundDatasetCreate/Update` validate items vs `mode`; `EvalRunCreate.mode`; `EvalRunResult*` +dimension fields; `EvalScoreRequest/Response`. |
| `judge.py` | M | Scorer library: `score_response` (existing, refactored), `score_trajectory`, `score_tool_calls`, `score_side_effects`, `score_filter`, `score_injection`, `score_member_path`, `score_composite`. Bias-mitigation prompt hardening. |
| `routers/playground.py` | M | New `POST /playground/eval/score` (mode dispatch → judge scorers); thread `eval_mode` into run-create. |
| `routers/datasets.py` | M | Validate per-mode items on create/update; expose `mode`. |
| `routers/eval_runner.py` | M | `create_eval_run` resolves `mode` from the executable, validates vs `dataset.mode`, passes `mode`+`eval_mode` to the Job; results endpoint accepts dimension fields. |
| `k8s.py` | M | Pass `MODE`, `EVAL_MODE` env into the eval Job. |
| governance/tool wrapper (e.g. `tool_governance.py` / SDK wrapper) | M | `eval_mode=record` → record `{tool,args}` + return mock/replay for `side_effecting` tools; fail-closed. |

### Backend — eval-runner (the Job)
| File | C/M | Responsibility |
|---|---|---|
| `services/eval-runner/main.py` | M | `mode` dispatch replacing the 2-branch if; per-mode item loop; call `/eval/score`; write dimension fields. Compat shim for legacy `{input}` items. |

### SDK
| File | C/M | Responsibility |
|---|---|---|
| `sdk/agentshield_sdk/` tool wrapper | M | Honor `eval_mode=record` in the SDK-side governed tool call (parity with declarative path). |

### Frontend — Studio
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/pages/DatasetsPage.tsx` | M | Mode selector on create; per-mode item editor + validation (JSON with the mode's schema hint / structured fields). |
| `studio/src/components/playground/SaveToDatasetButton` (or in `InteractionSurface`) | M | Save-to-dataset writes the **mode-correct item** (durable → `input_payload`+observed trajectory; webhook → `trigger_payload`+matched) — closes T-9 with mode awareness. |
| `studio/src/pages/EvalResultsPage.tsx` | M | Render per-dimension scores + trajectory diff + recorded side-effects + filter match; deep-link `run_id` → run tree. |
| `studio/src/api/playgroundApi.ts` / `registryApi.ts` | M | Types for per-mode items, `mode`, dimension results, `/eval/score`. |

### Tests + infra
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-NN-eval-v2-modes.sh` | **C** | Per-mode: author dataset → launch eval → assert dimension scores + composite + `eval_passed`. |
| `studio/e2e/eval-v2.spec.ts` | **C** | Playwright: per-mode dataset author → eval → results render (save→reload). |
| `studio/src/pages/DatasetsPage.test.tsx` / `EvalResultsPage.test.tsx` | M | Vitest: mode editor validation; dimension-score rendering. |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump registry-api, eval-runner, studio in **both** files. |
| `docs/experience/playground.md` | M | Per-mode datasets + trajectory/side-effect/filter eval; new `/eval/score`. |

---

## 6. Tasks (dependency-ordered)

Phasing mirrors §8 (sequencing vs WS-1…WS-6). Each task: Files · Contract · Acceptance · Deps · Verify.

> **Per-phase detail moved.** The authoritative, expanded per-phase plans now live in their own
> directories — `e0/`, `e1/`, `e2/`, `e3/`, `e4/`, `e5/`, `e6/` (see `README.md`), each with the
> design-stable banner, a hard depends-on line, and full Tasks/data-model/contracts. The summaries below are
> kept as the original seed; when building a phase, use its `eN/plan.md` (re-grounded at `tasks.md` mint).

### Phase E-0 — Reactive parity + composite plumbing (no behavior change) — **ships now, no WS dep**

**T0.1 — Discriminator + composite schema (migrations 1–2 + models/schemas).**
- Files: migrations `≥0059`/`≥0060`, `models.py`, `schemas.py`.
- Contract: `playground_datasets.mode` (default `reactive`), `eval_runs.mode`, `eval_run_results`
  dimension cols; `DatasetItem` discriminated union with a `reactive` variant that accepts today's
  `{input,expected_output}` via a `kind`-defaulting validator.
- Acceptance: existing datasets read back as `mode=reactive`; a new reactive dataset round-trips;
  mappers configure.
- Deps: none. Verify: `ast.parse` + `configure_mappers()`; `alembic upgrade head` on a seeded DB.

**T0.2 — Judge scorer library skeleton + `/eval/score` (response scorer only, composite=response).**
- Files: `judge.py`, `routers/playground.py`.
- Contract: `POST /playground/eval/score {mode, item, run_id?, input, response, ...}` →
  `{composite, dimension_scores, detail}`. For `mode=reactive`, `dimension_scores={response:x}`,
  `composite=x` — **numerically identical to today's `judge_for_eval`**. Bias-mitigation prompt
  hardening applied.
- Acceptance: reactive score equals the pre-change judge score on a fixture set (regression parity).
- Deps: T0.1. Verify: unit parity test; `grep` old `/playground/judge` callers migrated.

**T0.3 — eval-runner mode dispatch (reactive branch = today) + write dimension fields.**
- Files: `services/eval-runner/main.py`, `routers/eval_runner.py`, `k8s.py`.
- Contract: runner reads `MODE`; reactive branch identical to today but scores via `/eval/score` and
  records `dimension_scores`; `create_eval_run` resolves `mode` from the executable and validates vs
  `dataset.mode`.
- Acceptance: a reactive batch eval produces the same pass/fail as today + populated `dimension_scores`;
  `eval_passed` auto-set still fires (`eval_runner.py:299` unchanged).
- Deps: T0.2. Verify: suite-NN reactive case; `grep` keyword-match fallback path gated behind
  judge-unavailable only.

**T0.4 — Studio: dataset mode selector (reactive editor unchanged) + dimension-score render.**
- Files: `DatasetsPage.tsx`, `EvalResultsPage.tsx`, api types, Vitest.
- Acceptance: `npm run typecheck` clean; mode selector defaults reactive; results show a response-score
  column (others empty for reactive). Playwright reactive author→eval→result.
- Deps: T0.1–T0.3.

### Phase E-1 — Durable trajectory + tool-call eval — **depends on WS-1 (real `run_steps`)**

**T1.1 — `score_trajectory` + `score_tool_calls` (code scorers, match modes).**
- Contract: read `run_steps` for the run; compare to `expected_trajectory` per `match_mode`
  (`exact|ordered|superset|unordered`); tool-name exact + args partial/semantic match; emit
  `trajectory`/`tool_call` dimensions + a `tool_diffs` detail.
- Acceptance: a durable run that calls the right tools in order scores 1.0 trajectory; a wrong-order run
  under `ordered` scores <1.0; missing tool under `superset` penalized.
- Deps: **WS-1** (durable writes real per-node `run_steps`); T0.2.

**T1.2 — durable dataset editor + eval-runner durable branch + HITL-arg review.**
- Contract: runner starts a durable run with `input_payload`, polls `run_steps` to completion (reusing
  the sandbox self-approve path so gated steps proceed), collects the trajectory, scores it;
  `expect_approval` asserts the step parked + args matched.
- Acceptance: durable dataset authorable; batch eval yields response+trajectory+tool composite; a
  gated-step case asserts the approval args.
- Deps: T1.1, WS-1.

### Phase E-2 — Side-effect recording seam — **depends on WS-1; enables E-3/E-4 assertions**

**T2.1 — `Tool.side_effecting` + record/mock governance seam (`eval_mode`).**
- Contract: thread `eval_mode` runner→run-create→wrapper; `side_effecting` tools record `{tool,args}` +
  return mock/replay; fail-closed on unclassifiable tools. `score_side_effects` asserts recorded vs
  `expected_side_effects`.
- Acceptance: an agent that "sends email" under eval performs **no** real HTTP write; the call is
  recorded and asserted; an unclassified write tool is mocked (not invoked).
- Deps: T1.2 (durable branch exists to carry the flag); tool governance wrapper.

### Phase E-3 — Scheduled eval (job-spec + side-effects) — **depends on WS-3 + E-2**

**T3.1 — scheduled dataset (`job_spec`) + eval-runner schedule branch.**
- Contract: runner feeds `job_spec` as `input_payload`, fires one run (reactive-or-durable inner, per
  shape), scores response + trajectory + `side_effects`.
- Acceptance: a scheduled agent evaluated against a `job_spec` dataset produces side-effect assertions
  from **recorded** calls; no real delivery.
- Deps: **WS-3** (scheduled path real), E-2 (record seam).

### Phase E-4 — Webhook eval (filter + action + injection) — **depends on WS-4 + E-2**

**T4.1 — `score_filter` + `score_injection` + eval-runner webhook branch.**
- Contract: runner POSTs the synthetic `trigger_payload` through the **real** filter path (the WS-4
  "Test Event" internal endpoint), reads `AgentEvent.status`; `score_filter` compares to
  `expected_match`+reason; on match, scores action trajectory + side-effects; `injection_probe` asserts
  forbidden tools not called + refusal.
- Acceptance: a filtered event scores the filter dimension without a run; a matched event scores the
  action; an injection case fails if a forbidden tool fires.
- Deps: **WS-4** (real filter + Test Event path), E-2.

### Phase E-5 — Workflow run-tree eval — **depends on WS-1 (durable members) + E-1**

**T5.1 — `score_member_path` + eval-runner workflow branch (run tree).**
- Contract: runner triggers the workflow, walks the run tree (`parent_run_id`), scores
  `expected_member_path` (member-granularity trajectory) + per-member rubric + final response.
- Acceptance: a workflow eval scores the member path + drops into a member's steps for per-member
  rubric.
- Deps: T1.1 (trajectory scorer), WS-1 D4 (durable member steps in the tree).

### Phase E-6 — Regression/CI + gate polish — **after E-0..E-5 land per mode**

**T6.1 — headless regression eval + pass-threshold config + docs.**
- Contract: the same eval-runner invoked in CI (or a scheduled internal run) against a pinned dataset;
  `eval_runs.pass_threshold` + `dimension_weights` configurable per run; `eval_passed` auto-set on
  composite.
- Acceptance: a regression dataset run gates a version; a dropped trajectory score fails the gate even
  when the response is still correct (the core Eval v2 win).
- Deps: all prior. Verify: suite-NN full-mode matrix; `docs/experience/playground.md` updated.

---

## 7. Gap Ledger

| Item | Status | Note |
|---|---|---|
| Trajectory eval requires real `run_steps` | **hard dep → WS-1** | Before WS-1 the declarative-runner writes a 2-step skeleton; trajectory scoring is meaningless. E-1 ships **with/after** WS-1, never before. |
| Filter/action webhook eval requires the real filter path | **hard dep → WS-4** | The synthetic "Test Event" must hit production filter logic (playground-execution-modes.md §7), which WS-4 makes real. |
| Scheduled side-effect eval requires the real scheduled path | **hard dep → WS-3** + E-2 | Payload-based eval only meaningful once schedules actually fire a real run. |
| Semantic tool-arg matching (vs exact/partial) | **deferred (intentional)** | E-1 ships exact + partial-dict arg match; LLM-semantic arg equivalence is a follow-up scorer (research.md notes both). |
| Record-once cassette replay (vs fixed mock) | **deferred (intentional)** | E-2 ships fixed mock + record; VCR-style replay store is a follow-up. |
| Judge calibration / human-agreement study | **deferred (intentional)** | We apply known bias-mitigation best-practices; a calibration harness is out of scope. |
| Per-item history table (vs JSONB items) | **not-yet-needed (debt, low)** | `playground_datasets.items` JSONB suffices; promote to rows only if per-item labels grow heavy. |
| Multi-turn conversational datasets | **deferred (intentional)** | Reactive items are single-turn; multi-turn scripts are a later item schema variant (research.md §Multi-turn). |

**No orphan flags:** each new column/scorer/endpoint has its reader shipped in the same task
(`dimension_scores`→EvalResultsPage; `/eval/score`→eval-runner; `mode`→dataset editor + runner branch;
`side_effecting`→record seam). Grep-for-caller is a task acceptance gate.

---

## 8. Sequencing recommendation (relative to WS-1…WS-6) — **the load-bearing decision**

Eval v2 is **not** one atomic slice — each mode's eval is only meaningful once the workstream that
makes that mode *real* has landed. Build eval **behind** its mode, mode by mode:

```
WS-0 (authoring + shape dispatch)  ─┐
                                    ├─► E-0  Reactive parity + composite plumbing   (NO WS dep — ship first)
WS-1 (durable real run_steps)  ─────┼─► E-1  Durable trajectory + tool-call
                                    ├─► E-2  Side-effect record seam (needs a real run to carry the flag)
WS-3 (scheduled real)  ─────────────┼─► E-3  Scheduled job-spec + side-effects   (needs E-2)
WS-4 (webhook real filter)  ────────┼─► E-4  Webhook filter/action/injection      (needs E-2)
WS-1 D4 (durable members)  ─────────┴─► E-5  Workflow run-tree eval               (needs E-1)
                                       E-6  Regression/CI + gate polish            (after E-0..E-5)
```

| Eval phase | Depends on WS | Why the dependency is hard |
|---|---|---|
| **E-0 reactive** | WS-0 only | Reactive already works today; E-0 is pure plumbing (discriminator + composite) with no behavior change. **Ship it first** — it de-risks the schema + judge refactor while everything else is still text-only. |
| **E-1 durable** | **WS-1** | Trajectory/tool-call scoring reads `run_steps`; WS-1 replaces the 2-step skeleton with real per-node steps. Building E-1 before WS-1 = scoring a fake trajectory. |
| **E-2 record seam** | WS-1 (+ governance wrapper) | The `eval_mode` flag rides a real durable run through the governed tool path; needs a real run to intercept. |
| **E-3 scheduled** | **WS-3** + E-2 | `job_spec` → real scheduled fire; side-effect assertions need E-2's record seam. |
| **E-4 webhook** | **WS-4** + E-2 | Filter match/miss needs the real filter + Test Event path; action side-effects need E-2. |
| **E-5 workflow** | WS-1 D4 + E-1 | Member-path trajectory needs durable members writing child `run_steps` (WS-1 D4) + the trajectory scorer (E-1). |
| **E-6 regression** | E-0..E-5 | The full-mode gate + CI harness composes the finished scorers. |

**Practical cadence:** land **E-0 immediately** (parallel to WS-1). Then each `E-n` **rides out with, or
one beat behind, its WS** — e.g. E-1 in the same release train as WS-1's durable engine, so the moment
`run_steps` are real the trajectory gate is real too. Do **not** batch all eval work to the end; that
repeats the "built all layers, ran out of runway at the screen" failure (CLAUDE.md DoD #4). Each `E-n`
is a vertical slice: author a dataset of that mode → run a batch eval → see the dimension scores → gate.

---

## 9. Execution Notes

- **Ship E-0 first and keep it behavior-neutral.** The composite must equal today's score for reactive
  (a parity test, not a comment) — so the schema/judge refactor lands invisibly before any mode adds
  dimensions. This is the safe seam to change the judge on.
- **Deterministic scorers are code, LLM is the exception.** Trajectory/tool/filter/side-effect matching
  is exact/semantic **code** — reserve the LLM judge for response quality + rubric. Cheaper, faster,
  reproducible, and it removes the position/verbosity bias surface for the mechanical dimensions.
- **The record seam is fail-closed.** An eval that cannot record/mock a side-effecting tool **mocks it
  (never invokes)** and, if the effect can't be asserted, **fails the item**. Never let an unrecorded
  side-effect read as a pass (governance retro #4).
- **Explicit `mode` + `eval_mode` params end-to-end** — no `context=='playground'` sniff, no item-key
  type-sniffing. The discriminator is authored, validated at the door, and threaded (No-Bandaid).
- **`judge_score`/`overall_score` stay the composite** so `eval_passed` and the observability dashboard
  need no change — new dimensions are additive behind the same scalar.
- **Re-ground migration numbers + `file:line`** against live code at `tasks.md` mint time — WS-0…WS-6
  consume alembic numbers concurrently; head was `0057` on 2026-07-12.
- **Bump registry-api + eval-runner + studio** in both `deploy-cpe2e.sh` and `values.yaml` when each
  phase ships; the eval-runner Job image changes in every phase E-0..E-5.

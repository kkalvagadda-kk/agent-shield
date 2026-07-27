# Richer Evaluation UX

**Status:** approved — not started. Written 2026-07-27.
**Read first:** [`eval-state-of-play.md`](eval-state-of-play.md) — the code-verified ledger of what
evaluates today. **Slice 0 below is the same work as E-6's six open tasks**; doing it completes Eval v2.
**Related:** `docs/design/eval-results-publish-lifecycle.md` (older, partly superseded), `docs/plan/execution-models-v2/eval-v2/` (E-0…E-6 — this doc closes E-6's open Studio tasks `T006`/`T007`/`T020`–`T022`), `docs/design/todo/agent-evaluation-capability.md` (RESEARCH-status product vision; this doc builds a subset), `docs/decisions.md` Decision 20 (eval-gate placement).

---

## Context

Evaluation gates the release path — `AgentVersion.eval_passed` blocks publish and production deploy in four independent places (`routers/agents.py:541`, `routers/deployments.py:621`, `composite_workflows.py:1013`, and again in `deploy-controller/reconciler.py:86`). The backend behind it is mature: five dataset modes, seven scoring dimensions, one pure scoring door, a safety veto, per-run thresholds and weights, all proven by `suite-80`.

The **UX** answers only one question: *did this one run pass?* It cannot answer the questions people actually have — did my change make things better or worse, why is it failing, are my cases any good, why won't this publish. The surfaces aren't thin (`EvalResultsPage.tsx` is 1,339 lines with genuinely deep evidence panels; `DatasetsPage.tsx` is 1,775). The gap is that everything is scoped to a single run in isolation, several live backend capabilities have no UI at all, and two correctness bugs are shipping today.

Scope agreed: all four value areas (regression story, failure triage, dataset quality, lifecycle gating), **regression story first**, charting library approved, and the threshold bug fixed as its own change before any UX work.

---

## Lead finding: the publish queue can show the wrong version's score, against the wrong threshold

This is more serious than any missing feature, because a human approves a release on it.

`routers/admin.py:166-186` resolves each publish request's eval by **`EvalRun.agent_name` only** — `.distinct(EvalRun.agent_name).order_by(agent_name, completed_at.desc())`. It never filters on `PublishRequest.source_version_id`. So the queue shows the agent's *latest* eval, which may belong to a different version than the one being published.

Compounding it, `AdminPublishRequestsPage.tsx:167` renders that score against a hardcoded `>= 0.7` / `>= 0.4`. And `PublishRequestResponse` (`schemas.py:997-1014`) carries `last_eval_score` but **no threshold**, so that page *structurally cannot* render a correct verdict today — the fix needs a backend field, not just a frontend edit.

**Second offender:** `DatasetsPage.tsx:1756` hardcodes `>= 0.7` for the run status dot. `EvalResultsPage.tsx:51-67` carries a long comment explaining exactly this bug ("a 0.85 run on a 0.9-threshold dataset rendered GREEN while the gate refused it: the UI contradicted the product it reports on") and was fixed in `7b3e3fc`. The other two never were.

**Why it survived — and this is the part worth fixing properly:**

- `suite-80` `T-S80-000b` (`:155-174`) greps **only** `studio/src/pages/EvalResultsPage.tsx`, while its failure message claims "the Studio still hardcodes the threshold". Verified: that file now has **zero** `0.7` literals in code. The guard passes on the one fixed file and is blind to both real offenders.
- The gap ledger (`docs/testing/manual-ui-e2e-test-plan.md:607`) and E-6's tasks `T006`/`T020`–`T022` track this as an "ACTIVE PRODUCT BUG" — but they name `EvalResultsPage.tsx:51`/`:194`, the file that is already clean. **The tracked gap points at the wrong file and the two actual offenders are unnamed anywhere.**

So this is not an undiscovered bug; it is a known bug whose tracking drifted onto the wrong target. Slice 0 closes E-6's open Studio tasks and repoints the guard.

---

## Cross-cutting decisions

### Recharts, in exactly three places

The deciding factor is Definition-of-Done rule 1, not aesthetics: Chart.js and uPlot render to `<canvas>`, invisible to Testing Library and Playwright — structurally unprovable in this repo. Recharts emits queryable SVG. `ObservabilityDashboardPage.tsx:223` already names it in a TODO (`// Latency chart (simple text-based for now; Recharts added in M2 chart task)`), so this is an unpaid decision, not a new one.

Earns its place for: the score-over-time trend with a `<ReferenceLine>` at the threshold (a div-bar has no shared y-axis, so "we dropped below the gate at run 7" is unrenderable), and baseline-vs-current grouped bars over dimensions. Hand-rolled bars stay for single-series rollups and table sparklines. Do **not** convert the existing observability/cost bars — unrelated churn.

Two traps to design around:

- `ResponsiveContainer` measures **0×0 in jsdom**, so chart components must accept explicit `width`/`height` and tests must pass them or assert vacuously against an empty SVG.
- Every chart ships a **text twin** (`<table data-testid="...-data">`) with the same numbers — a11y win, robust assertions, and a broken chart can never read as a working screen.

### No `previous_eval_run_id` column

Baseline and delta are fully derivable from `listEvalRuns()` + `getEvalRunResults()`. A column freezes one baseline choice into a row that outlives it, needs a backfill, and forecloses the two baselines people actually want (last green run; the currently-published version's run). Derive it, let the user override, put it in the URL — the pattern `ObservabilityComparePage` already set with `?a=&b=`.

### The join-key hazard

Dataset items authored in the UI get **no `id`** (`buildDurableItem` et al. at `DatasetsPage.tsx:1275/1326/1372/1696` never set one, though `_DatasetItemBase.id` exists at `schemas.py:1141`). The only cross-run join key is positional `dataset_item_idx`, and there is no unique constraint on it. A diff could compare item #3-before against a *different* item #3-after.

Mitigation is free: `eval_run_results` snapshots `input_message`/`expected_output`/`trigger_payload` per row, so the client fingerprints each pair and marks drifted indices "not comparable".

### Reuse, don't rebuild

- `AgentListPage.tsx:209-350` is a complete tested `useReactTable` setup (sorting, global filter, `flexRender`) — copy its structure for the run list.
- `CostConsolePage.tsx:22-30` has the cleanest `StatCard` — lift it.
- `ObservabilityComparePage.tsx:70-82` has the diff *visual vocabulary* — lift that, but write a fresh `lib/evalDiff.ts`: its span-name keying and `same|added|removed|changed` vocabulary is wrong for eval, which needs `fixed | regressed | still-failing | still-passing | new | removed | drifted`.

---

## Slice 0 · Verdict single-owner + the guard that closes the class — ~1.5 days

Standalone bug fix, shipped before any UX work.

**Outcome:** one eval run renders one verdict everywhere; the publish queue shows the eval for the version actually being published.

**Backend** (`registry-api` 0.2.226):

- `schemas.py` — add `last_eval_pass_threshold: float | None` to `PublishRequestResponse`.
- `routers/admin.py:166-186` — join on `PublishRequest.source_version_id == EvalRun.agent_version_id`, falling back to `agent_name` only when the request pins no version; populate the threshold via the existing `effective_pass_threshold` (`eval_runner.py:49`) rather than a second resolver. No migration.

**Frontend** (`studio` 0.1.164):

- New `studio/src/lib/evalVerdict.ts` (+ colocated test) — the single owner. `verdictOf(score, threshold)`, `scoreColor`, `passesGate`, `formatDelta`. Move `scoreColor` from `EvalResultsPage.tsx:62-67` **verbatim, comment block included** — that comment is the institutional memory of this bug.
- New `components/shared/StatCard.tsx` (lifted from `CostConsolePage`) and `BarRow.tsx` (from `ObservabilityDashboardPage.tsx:204-217`).
- Modify `EvalResultsPage.tsx`, `DatasetsPage.tsx:1740-1773`, `AdminPublishRequestsPage.tsx:162-178`, `CostConsolePage.tsx` to import instead of redeclaring.

**The guard** — rewrite `suite-80` `T-S80-000b` to **discover** its scope rather than name one file, so it fails loudly on a *new* page instead of silently skipping it:

- scope = `grep -rl "pass_threshold\|overall_score" studio/src/pages studio/src/components`
- `000b1` — each discovered file, comments stripped (keep the existing `sed -E 's://.*::'` — it exists so a comment *explaining* the bug can't fail the gate forever and teach devs to delete the explanation), has zero threshold-shaped literals.
- `000b2` — `scoreColor`/`verdictOf` is defined **exactly once**, in `lib/evalVerdict.ts`.
- `000b3` — every discovered file imports from `lib/evalVerdict`.

**Tests:** Vitest `evalVerdict.test.ts` (boundaries: `score === threshold`; absent threshold → `"unknown"`, never `"pass"`; the 0.6× near band). Extend `DatasetsPage.test.tsx` + `EvalResultsPage.test.tsx` with a 0.9-threshold run scoring 0.85 asserting **amber on both** — the actual regression, written to fail first. Bash: the rewritten guard, plus a case asserting `GET /admin/publish-requests` returns `last_eval_pass_threshold` and the *version-correct* run id. Write `docs/bugs/eval-threshold-and-publish-queue-version-join.md` in the same change.

---

## Wave 1 · The regression story — ~1.5 weeks

The diff needs a way to select two runs, so the run list comes first as its spine — not as a separate priority.

### Slice 1 · Eval Runs list page — ~2 days

**Outcome:** the sidebar stops lying. `Sidebar.tsx:69` labels an item "Eval Runs" and points at `/playground`, a chat sandbox with no eval runs on it. There is no eval-run list page in the product. Add `/playground/eval-runs`: sortable, searchable, filterable by status/agent/dataset, showing score, verdict chip, dataset, mode, target version, duration, and a per-dataset sparkline.

- New `pages/EvalRunsPage.tsx` + test; route in `App.tsx`; fix the sidebar target.
- `playgroundApi.ts` — `listEvalRuns(params?)`.
- Backend: optional `dataset_id`/`agent_name`/`agent_version_id`/`status`/`limit`/`offset` on `list_eval_runs` (`eval_runner.py:464-474`). Additive, no migration. Keep the existing caller-scoping.
- Copy `AgentListPage`'s react-table structure. Sparkline = ~10-line inline `<svg><polyline>`, **not** Recharts (axis machinery is pure overhead at 60px).
- Also fixes the missing **error state** on this family of pages.

### Slice 2 · Run-to-run diff — ~3 days

**Outcome:** `/playground/eval-runs/:id/compare?baseline=<id>`. Header shows score A → B with a signed delta and a verdict-flip badge. Four buckets — **Regressed / Fixed / Still failing / Still passing** — each expanding to per-item composite and per-dimension deltas. A separate **"⚠ not comparable"** list for drifted indices.

- New `lib/evalDiff.ts` + test, `pages/EvalComparePage.tsx` + test, `components/shared/DiffBadge.tsx`.
- Two non-negotiables in `buildEvalDiff`: **dedupe by `dataset_item_idx`** (last `created_at` wins) before joining, and **fingerprint** each index from the snapshotted input — mismatch ⇒ `drifted`, excluded from all four buckets.
- Migration **0073**: `UNIQUE (eval_run_id, dataset_item_idx)` with a pre-dedupe pass, plus `eval_runs.error_message TEXT NULL` (today a failed job's reason lives only in a pod log). Make `create_eval_run_result` (`eval_runner.py:502`) upsert.
- Link out: a diffed item with `langfuse_trace_id` on both sides gets a "compare traces" link into the existing `/observability/compare?a=&b=`.

### Slice 3 · Trend + baseline pin — ~2 days *(introduces Recharts)*

**Outcome:** `overall_score` across the last N runs with the threshold as a reference line, points colored by verdict, click-through to a run. Baseline selector — *last green run* / *published version* / pick — persisted in the URL, feeding slice 2.

- `npm i recharts`; new `components/eval/ScoreTrend.tsx` (explicit `width`/`height` props + the mandatory text twin); `lib/baseline.ts`. No backend.
- **Tell the truth about the number while here:** `overall_score` is a **pass rate** (`passed_count/total`, `eval-runner/main.py:1650`), not a mean of composites, but the UI labels it "Overall Score". Relabel to `Pass rate` and add a `Mean composite` tile. Two lines of copy worth more than any chart in this plan.

---

## Wave 2 · Then, in this order

- **Failure triage** (~3 days, zero backend) — `lib/failureReason.ts` with explicit precedence (veto outranks everything, since the composite was forced to 0), `clusterFailures`, `dimensionRollup`. Turns "6 failures" into "6 of 9: agent never called `calculator`". Must render `scored 7/12` beside every mean — a `—` dimension is **not** a zero, and conflating them is the exact thing the codebase's comments insist must not happen. Highest daily value in the program after slice 0.
- **Lifecycle + gating** (~3 days) — a real Eval gate card on `AgentDetailPage` replacing the hover tooltip at `:163-170` that says "Run an eval that passes before publishing" and offers no way to. Distinguish the two 422s (`eval_not_passed` vs `adversarial_eval_not_passed`) which today produce identical UI copy. Extract `DatasetsPage`'s existing launcher modal to `components/eval/RunEvalModal.tsx` rather than building a second one.
- **What-if re-scoring** (~3 days) — new **read-only** `POST /eval-runs/{id}/rescore`. Do **not** re-call `/eval/score`: its `response` dimension calls the LLM judge, so 50 items = 50 judge calls, real spend, and judge nondeterminism confounds the very question ("did the composite move because of my weights, or because the judge scored differently?"). Instead read persisted `dimension_scores`, import `score_composite` from `judge.py:165`, and read the persisted `eval_detail.veto` **as a stored fact** — vetoes fire on facts (`filter_error`, `injection_succeeded`) that cannot change with weights, so the endpoint is provably faithful without re-declaring one line of policy. Then wire `pass_threshold`/`dimension_weights` into `createEvalRun`, finally sending the two fields the API has accepted since E-6.
- **Traffic → cases** (~2 days) — wire the two dark endpoints: `save-to-dataset` and `PATCH /datasets/{id}`. **Carries a latent data-corruption fix:** `save_run_to_dataset` (`playground.py:983-1029`) appends a *reactive-shaped* item with **no mode validation**, so appending to a `durable` dataset yields an item its branch can never score — the runner fail-closes on it forever. Add `_validate_dataset_items([item], dataset.mode)`, and stamp a stable `id` on every item written, retiring the positional-join hazard for future datasets.
- **Dataset quality** (~4 days, cut first if needed) — discrimination (always-passes ⇒ prune; ~50% ⇒ flaky) and coverage on two honest axes: which dimensions the authored items can ever exercise, and which of the version's tools are never exercised.

---

## Explicitly not building

A `previous_eval_run_id` column (derive it). What-if via `/eval/score` (nondeterminism kills the feature). A dimension **radar chart** — with 7 dimensions of which 2–4 are typically absent, a radar renders "unscored" and "scored zero" identically, lying about the one distinction this codebase repeatedly insists on. Embedding-based failure clustering (deterministic reason codes get ~90% at ~5% of cost, and are unit-testable). A production-traffic miner (ship the button, defer the miner). Per-item latency/token/cost (needs columns + runner writes, and only workflow mode has upstream data — `agent_runs` carries `cost_usd`/`latency_ms`, `playground_runs` carries neither). Rewriting the results table on react-table (its rows expand into six bespoke evidence panels).

**Auto-flipping `adversarial_eval_passed` from injection scores** deserves a note: it is the one gate with **no producer anywhere** in the pipeline, and the webhook branch *does* produce `injection_detail.asr`. Tempting — but it also blocks production deploy, it is monotonic, and "ASR = 0 on the 2 probes this dataset happened to include" is not "adversarially safe". If built, it needs an explicit `adversarial=true` run, ≥N probed items, ASR = 0 on all, zero vetoes — its own slice with its own review, never a side effect.

---

## Verification

Per slice, all three layers (the repo's DoD makes each mandatory, not optional):

- **Vitest** — colocated. The pure libs (`evalVerdict`, `evalDiff`, `failureReason`, `datasetQuality`) are where the real value is: unit-test them against real `eval_detail` fixtures copied from `suite-72`/`suite-77` output. Keep `EvalResultsPage.test.tsx` (1,042 lines) and `DatasetsPage.test.tsx` (964) green and extend them. `cd studio && npm run test && npm run typecheck`.
- **Bash** — new `scripts/e2e/suite-85-eval-ux.sh` (start at **85**; numbers 75/76/77/79/80 are each already used twice on disk). Register in **`scripts/test-manifest.txt`** as `api|eval|...` — *not* `run-all.sh`, which is now a thin wrapper — then `bash scripts/run-tests.sh --audit`. Arithmetic assertions on `/rescore` mirroring `T-S80-004`, and a veto'd item pinned at 0.0 under *any* weights (mirroring `T-S80-006`).
- **Playwright** — `eval-runs-list.spec.ts`, `eval-compare.spec.ts`, `eval-triage.spec.ts`, `eval-gate-publish.spec.ts`, `dataset-from-traffic.spec.ts`. Register each as `browser|eval|...` in the manifest. Discover real completed runs from the backend and skip loudly if absent (the `eval-v2-durable.spec.ts:13-24` pattern) — never fabricate eval rows, per `eval-v2/README.md:65`. Run targeted: `bash scripts/run-tests.sh --group eval`.
- **Charts specifically** — assert on the text twin and `svg path` node count with explicit dimensions; a Playwright check that the twin's last row matches the header score is what catches a chart plotting the wrong series.
- **Per slice:** bump image tags in **both** `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml` (studio 0.1.163 → 0.1.164+, registry-api 0.2.225 → 0.2.226+), and check `studio/src/lib/build.ts`, which carries the tag and has drifted before.
- **Gap ledger** entries required for: per-item cost/latency (deferred), traffic miner (deferred), the `adversarial_eval_passed` producer (debt), legacy datasets without item ids (debt), and `_DatasetItemBase.weight` (`schemas.py:1154`) — a declared field no scorer reads, which is exactly the orphan DoD rule 3 forbids.

**Known risk:** `listEvalRuns` is unpaginated and user-scoped. The trend and quality slices fan out to N × `getEvalRunResults`; cap N at 10. A *team*-level regression view would need the scoping relaxed — a permissions decision, not a UI one.

**Also worth fixing while in here:** `eval_runner.py:6` and `:303-304` still document the K8s job launch as "currently stubbed — logs intent, does not create the Job", contradicted by `:433` which actually calls `create_eval_job`. And `docs/spec.md:184-248` still describes eval as **promptfoo** with datasets in Langfuse. Both are E-6's unchecked `T026`.

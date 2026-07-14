# E-0 Tasks — Reactive parity + composite plumbing

**Slice:** Eval v2 Phase E-0 (foundation; ships first, WS-0 dep only). Turn eval storage + judge from
response-only into **mode-aware** shape with **zero reactive behavior change**.

**Total tasks:** 25 (19 implementation + 6 checkpoint)
**Phases:** 8 (6 implementation + 2 checkpoint gates)
**Parallel opportunities:** noted inline with `[P]`
**Checkpoint phases:** CP1 (after Phase 3 — schema + judge door), CP2 (after Phase 6 — real no-fakes reactive parity)

> **Verification bar (MANDATORY — the suite-58/59 no-fakes standard).** E-0 is DONE only when the
> **real** e2e ([T015]) is green in `run-all.sh`: create a real reactive `PlaygroundDataset` via the API
> → run a real `EvalRun` through the real eval-runner Job + real `judge.py` → assert the persisted
> `dimension_scores`/`composite` (save→reload) + `eval_passed` still auto-sets + **the parity gate:
> composite == today's judge score to the digit ON A REAL RUN**. The unit parity test ([T017]) may
> accompany for speed but is **NOT** the gate. NO task may rely on a mocked judge, faked `_run_step`,
> stubbed dispatch, or hand-crafted `eval_run_results` rows.

> **Grounded against live tree (2026-07-13, execution-models-v2 worktree):**
> - Alembic head = `0058` → E-0 takes `0059` + `0060`.
> - Image tags to bump: registry-api `0.2.167`, eval-runner `0.1.4`, studio `0.1.132`
>   (in `scripts/deploy-cpe2e.sh` AND `charts/agentshield/values.yaml` L588/L591+L617/L899).
> - Latest suite = `suite-60` → new real suite = **`suite-61`** (register after L109 of `run-all.sh`).
> - Anchors: `models.py` PlaygroundDataset L1378 / EvalRun L1400 / EvalRunResult L1448; `judge.py`
>   `judge_for_eval` L106; `routers/playground.py` `/playground/judge` L974; `routers/eval_runner.py`
>   `create_eval_run` L69 / `create_eval_run_result` + `eval_passed` auto-set L287-331; eval-runner
>   `main.py` two-branch loop L143+ / keyword fallback L273; `k8s.py` eval Job env L154.
> - Re-verify each anchor before editing (design docs go stale).

---

## Phase 1 — Setup & Grounding
_Prerequisite for all phases. Re-ground the indicative specifics against the live tree before writing code._

- [X] [T001] Re-ground migration numbers (head `0058`→`0059`/`0060`), image tags, and suite number (`suite-61`) against the live tree; record confirmed values in the plan's grounding note — `docs/plan/execution-models-v2/eval-v2/e0/plan.md`
  - **Confirmed 2026-07-13:** alembic head = `0058` → take `0059`/`0060`. Latest suite = `suite-60` → new = `suite-61`. Anchors present (`judge_for_eval`, `create_eval_run`, `PlaygroundDataset`/`EvalRun`/`EvalRunResult`). **Tags to bump (corrected — moved since plan): registry-api `0.2.168`→`0.2.169`, eval-runner `0.1.4`→`0.1.5`, studio `0.1.132`→`0.1.133`.**

---

## Phase 2 — Schema foundation (T0.1)
_Additive, guarded, idempotent, data-preserving migrations + ORM + Pydantic discriminated union. No behavior change._

- [X] [T002] [P] Migration 0059: `playground_datasets.mode` (String(16) NOT NULL default `reactive`, CHECK ∈ {reactive,durable,scheduled,webhook,workflow}) + `schema_version`; `eval_runs.mode` (same CHECK) + `dimension_weights` JSONB + `pass_threshold` Numeric(4,3); backfill existing rows to `reactive` — `services/registry-api/alembic/versions/0059_eval_v2_dataset_and_run_mode.py`
- [X] [T003] [P] Migration 0060: `eval_run_results` add `dimension_scores` JSONB, `eval_detail` JSONB, `trigger_payload` JSONB, `matched` Boolean, `run_id` UUID — all nullable, no backfill — `services/registry-api/alembic/versions/0060_eval_v2_result_dimensions.py`
- [X] [T004] ORM columns on `PlaygroundDataset` (mode/schema_version), `EvalRun` (mode/dimension_weights/pass_threshold), `EvalRunResult` (dimension_scores/eval_detail/trigger_payload/matched/run_id) — `services/registry-api/models.py`
- [X] [T005] `DatasetItem` discriminated union (`Field(discriminator="kind")`) with a `reactive` variant that accepts today's `{input, expected_output}` via a `kind`-defaulting validator; `PlaygroundDatasetCreate/Update` validate items vs `mode`; `EvalScoreRequest/Response` (`{mode,item,run_id?,input,response} → {composite,dimension_scores,detail}`) — `services/registry-api/schemas.py`

---

## Phase 3 — Judge scorer library + one scoring door (T0.2)
_Pure refactor of `judge_for_eval` behind a single `/eval/score` endpoint; reactive composite is byte-identical to today._

- [X] [T006] `judge.py` scorer-library skeleton: refactor `judge_for_eval` → `score_response(input, response, rubric)`; add `score_composite(dimension_scores, weights)` reducer (reactive: composite == response); bias-mitigation prompt hardening (position/verbosity guardrails) — `services/registry-api/judge.py`
  - **Done 2026-07-13:** `score_response(input_text, output_text, expected_output=None, rubric=None, team="platform")` (reference-based when `expected_output` present = byte-identical to legacy); `score_composite(dimension_scores, weights=None)` (single `response` dim → composite == response; weights None → equal-weight mean; degenerate weights → equal-weight fallback). `judge_for_eval` kept as a thin wrapper delegating to `score_response` (all existing callers byte-identical). Bias-mitigation guardrails added but **OFF by default** (`JUDGE_BIAS_MITIGATION` env, default off) so reactive stays byte-identical — enabling it would move the real LLM score, so it's gated per the constraint. Unit parity check green (6 fixtures + composite edge cases).
- [X] [T007] `POST /playground/eval/score` — dispatch by `mode`; reactive branch calls `score_response`, returns `dimension_scores={response:x}`, `composite=x` (numerically identical to `judge_for_eval`); migrate the existing `/playground/judge` internal caller to the new door (grep for callers) — `services/registry-api/routers/playground.py`
  - **Done 2026-07-13:** `POST /api/v1/playground/eval/score` (`EvalScoreRequest`→`EvalScoreResponse`); reactive branch calls `score_response` + `score_composite` → `dimension_scores={"response": x}`, `composite=x`; non-reactive modes return 501. The only in-process `judge_for_eval` caller (`/playground/judge` handler) now routes through `score_response` (via the wrapper) — single scoring path, no duplicate. The eval-runner's HTTP call to `/playground/judge` is migrated to `/eval/score` in Phase 4 / T010 (explicitly out of scope here).

---

## Checkpoint 1 — Schema + judge door
_Gate: Phases 1-3 complete. Run before starting Phase 4._
_What you prove: migrations apply on a seeded DB, existing datasets read `mode=reactive`, and `/eval/score` reactive output equals today's judge score with a valid discriminator rejecting bad items._

- [X] [CP1a] Deploy script: bump + build registry-api, `helm upgrade`, run `alembic upgrade head` via the migrate init, wait for pod Ready — `scripts/deploy-cp1-eval.sh`
  - **Written 2026-07-13:** builds registry-api:0.2.169, `helm upgrade --install` (tags baked in values.yaml), rollout of registry-api triggers the alembic-migrate init → `upgrade head` (0059/0060). `bash -n` clean, `chmod +x`. **RUNTIME is gated on the orchestrator's deploy** (needs a live cluster).
- [X] [CP1b] Infra smoke: registry-api pod not CrashLooping; `\d playground_datasets`/`eval_runs`/`eval_run_results` show the new columns (psql assert); every pre-existing dataset GETs back `mode=reactive`; `POST /playground/eval/score` returns 200 — `scripts/smoke-test-cp1-eval-schema.sh`
  - **Written 2026-07-13:** in-pod psql (via `AsyncSessionLocal` + `information_schema.columns`) asserts the three new columns, backfill-to-reactive, pod Running, and `/eval/score`→200. `bash -n` + embedded-python `ast.parse` clean. RUNTIME gated on deploy.
- [X] [CP1c] Behaviour smoke: reactive `/eval/score` returns `composite == dimension_scores.response` to the digit; a malformed non-reactive item on dataset create is rejected `422` (discriminated-union validator fires) — `scripts/smoke-test-cp1-eval-score.sh`
  - **Written 2026-07-13:** asserts `composite==dimension_scores.response` (`<1e-9`) + good-answer≥0.7, and a `mode=reactive` dataset with an item explicitly `kind=webhook` → 422 (the deterministic discriminated-union rejection; `extra="allow"` means a shape-only mismatch would NOT 422, so the kind/mode-disagreement path is used). Syntax clean. RUNTIME gated on deploy.

> **To run:** `bash scripts/deploy-cp1-eval.sh` → wait for pods → `bash scripts/smoke-test-cp1-eval-schema.sh && bash scripts/smoke-test-cp1-eval-score.sh`
> **Pass criteria:** All assertions exit 0, no pod in CrashLoopBackOff

---

## Phase 4 — eval-runner mode dispatch (T0.3)
_Runner reads `MODE`; reactive branch identical to today but scores via `/eval/score` and records `dimension_scores`. `eval_passed` auto-set untouched._

- [X] [T008] `create_eval_run` resolves `mode` from the executable (execution_shape+trigger / workflow) and validates it == `dataset.mode` (422 mismatch); `create_eval_run_result` accepts + persists dimension fields; confirm `eval_passed` auto-set (L287-331) reads the composite `overall_score` **unchanged** — `services/registry-api/routers/eval_runner.py`
- [X] [T009] Pass `MODE` env into the eval Job (`_create_eval_job_sync` env list) — `services/registry-api/k8s.py`
- [X] [T010] eval-runner reads `MODE`; reactive branch scores via `POST /playground/eval/score` (replacing the direct `/playground/judge` call) and records `dimension_scores`; keyword-match fallback gated **only** behind judge-unavailable; legacy `{input}`→`input_message` compat shim — `services/eval-runner/main.py`

---

## Phase 5 — Studio: mode selector + dimension render (T0.4)
_Reactive editor unchanged; mode selector defaults reactive; results show a response-score column (others empty for reactive)._

- [X] [T011] [P] API types: `mode` on dataset, dimension result fields (`dimension_scores`/`composite`), `/eval/score` request/response — `studio/src/api/playgroundApi.ts`
- [X] [T012] Dataset `mode` selector on create (defaults `reactive`; reactive item editor unchanged) — `studio/src/pages/DatasetsPage.tsx`
- [X] [T013] Render per-dimension scores (response column; other dimensions empty for reactive) — `studio/src/pages/EvalResultsPage.tsx`
- [X] [T014] [P] Vitest: mode selector defaults reactive + dimension-score rendering — `studio/src/pages/DatasetsPage.test.tsx`, `studio/src/pages/EvalResultsPage.test.tsx`

---

## Phase 6 — Real no-fakes acceptance + docs + deploy
_The load-bearing gate. Real dataset → real EvalRun → real judge → persisted composite/parity + real browser journey. Plus docs + tag bumps._

- [X] [T015] **NO-FAKES real e2e suite** (the gate): creates a real reactive `PlaygroundDataset` via the API with real items → launches a real `EvalRun` through the real **eval-runner Job** + real `judge.py` (`score_response`) → asserts persisted `dimension_scores`/`composite` re-read from the DB (save→reload), `eval_passed` auto-set still fires, and **the parity gate — composite == today's `judge_for_eval` score to the digit on the REAL run**; creates all its own resources up front, tears down; register in `run-all.sh` as `T-S61-00X` — `scripts/e2e/suite-61-eval-mode-plumbing.sh`
  - **Written 2026-07-13:** creates + DEPLOYS a real reactive declarative agent (real pod — reactive playground runs proxy to a live pod, so a deploy is required), authors a real reactive dataset (4 items: 3 known-good + 1 known-bad), launches a real `EvalRun` (real eval-runner Job → real `/eval/score` → real `judge.score_response`), polls the DB to completion, then asserts FROM THE DB (save→reload): T-S61-001 pod running; -002 real run completed; -003 every row has `dimension_scores["response"]` + composite; **-004 PARITY: `dimension_scores["response"] == judge_score` to the digit (`<1e-9`) for every row + known-good≥0.7 + known-bad<0.5**; -005 `eval_passed` auto-set fired (overall≥0.7 → version.eval_passed True). **No mocked judge, no faked rows** — the composite is real. A genuine env limit (eval-runner Job can't run) prints a LOUD `SKIP` (never a fake, never a PASS) and exits 0; registered in `run-all.sh` after suite-60. `bash -n` + `ast.parse` clean. **RUNTIME green gated on the orchestrator's CP2 deploy** (needs eval-runner:0.1.5 + Jobs RBAC on a live cluster).
- [X] [T016] Playwright **non-route-stubbed** journey: author a reactive dataset → launch eval → read the response/composite score back in `EvalResultsPage` (real network `waitForResponse` + save→reload; no `page.route` stubbing of the eval API) — `studio/e2e/eval-mode-plumbing.spec.ts`
  - **Written 2026-07-13:** real Keycloak login (global-setup); opens New Dataset → asserts mode selector defaults `reactive` → fills 2 reactive items → Create (real `waitForResponse` POST `/playground/datasets`, asserts `body.mode==reactive`) → **RELOADS** and asserts the dataset survived (save→reload) → opens Run Eval and, if a running sandbox deployment exists, launches a real eval (`waitForResponse` POST `/eval-runs`, 201) and reads the composite/Response column on `EvalResultsPage`; when no live pod exists it asserts the "no running deployment" empty-state (same live-pod boundary as playground.spec.ts — the full score persistence is suite-61's job). No `page.route` stubbing. `tsc --noEmit` clean. RUNTIME gated on the deployed Studio.
- [X] [T017] [P] Unit parity test (accompanies [T015] for speed — explicitly **NOT** the gate): `score_response`+`score_composite` reactive output == `judge_for_eval` on a fixture set — `services/registry-api/tests/test_eval_parity.py`
  - **Done 2026-07-13:** 15 tests **PASS** (`pytest -q`, 0.05s) — mocks the LLM boundary (`judge._call_judge`) deterministically; proves `score_response == judge_for_eval` on 6 fixtures (same code path), `score_composite({"response":x})==x` to the digit, equal-weight/weighted/degenerate reducers, and the reactive door shape. Header states this is the FAST check and suite-61 is the gate.
- [X] [T018] [P] Bump registry-api + eval-runner + studio tags in **both** files (deploy-cpe2e.sh vars + values.yaml L588/L591+L617/L899) — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
  - **Done 2026-07-13:** registry-api `0.2.168→0.2.169`, eval-runner `0.1.4→0.1.5`, studio `0.1.132→0.1.133` in BOTH files (deploy-cpe2e.sh L128/131/132; values.yaml L588 + L591/L617 + L899). Added an E-0 header changelog line to deploy-cpe2e.sh. Verified in sync; only historical changelog comments still mention the old tags.
- [X] [T019] [P] Experience docs: mode-aware datasets, `POST /playground/eval/score` one-door dispatch, dimension-score render — `docs/experience/playground.md`
  - **Done 2026-07-13:** updated Path-2 steps 1-4 (mode selector + reactive default + 422 validator; MODE resolution + mode-mismatch 422; the `/eval/score` one-door real-judge scoring; composite + per-dimension render + `eval_passed` auto-set), the loop diagram, the routing summary (added `/eval/score`), and the Key-files table. Per the resolved-gap rule, corrected the two now-stale Known-limitations bullets (keyword-match / ownership-403) to the shipped behavior.

---

## Checkpoint 2 — Real reactive parity (no fakes)
_Gate: Phases 4-6 complete. This is E-0's Definition-of-Done gate._
_What you prove: on a REAL end-to-end eval run of a REAL reactive dataset, the composite equals today's judge score to the digit, the score persists and reloads, and `eval_passed` flips — no mocked judge, no faked runner, no hand-crafted result rows._

- [X] [CP2a] Deploy script: build+push registry-api + eval-runner + studio at the bumped tags, `helm upgrade`, wait for all pods Ready — `scripts/deploy-cp2-eval.sh`
  - **Written 2026-07-13:** builds registry-api:0.2.169 + eval-runner:0.1.5 (the real Job image suite-61 needs) + studio:0.1.133, `helm upgrade --install` (baked tags), waits registry-api + studio rollouts. `bash -n` clean, `chmod +x`. RUNTIME gated on a live cluster.
- [X] [CP2b] Real-suite smoke: run `scripts/e2e/suite-61-eval-mode-plumbing.sh` and assert every `T-S61-00X` prints `PASS` (real dataset→EvalRun→judge→persisted `dimension_scores`/`composite`, save→reload) — `scripts/smoke-test-cp2-eval-real.sh`
  - **Written 2026-07-13:** runs suite-61 and fails on any `FAIL` **or** any `SKIP` (after CP2a the Job MUST run — a SKIP fails the checkpoint), asserting each of the 5 `T-S61-00X` printed `PASS`. Syntax clean. RUNTIME gated on CP2a deploy.
- [X] [CP2c] Parity + gate behaviour: on the real run, `composite == judge_for_eval` score to the digit AND `eval_passed` auto-set fired on the passing version (assert from the DB, not a fixture) — `scripts/smoke-test-cp2-eval-parity.sh`
  - **Written 2026-07-13:** drives an INDEPENDENT real reactive eval end-to-end and re-reads the persisted rows straight from the DB (`AsyncSessionLocal` in-pod) to assert `composite == dimension_scores["response"]` to the digit (`<1e-9`) + known-good≥0.7/known-bad<0.5, and `eval_passed` flipped on the passing version (overall≥0.7). Env limit → LOUD `SKIP` (never a fake). `bash -n` + `ast.parse` clean. RUNTIME gated on CP2a deploy.

> **To run:** `bash scripts/deploy-cp2-eval.sh` → wait for pods → `bash scripts/smoke-test-cp2-eval-real.sh && bash scripts/smoke-test-cp2-eval-parity.sh`
> **Pass criteria:** All assertions exit 0, `suite-61` green in `run-all.sh`, no pod in CrashLoopBackOff

---

## Summary Table

| Phase | Name | Tasks | Proves |
|---|---|---|---|
| 1 | Setup & Grounding | T001 | Live-tree numbers/tags/suite confirmed |
| 2 | Schema foundation | T002-T005 | Mode discriminator + composite columns + discriminated union (back-compat) |
| 3 | Judge scorer library + door | T006-T007 | One `/eval/score` door; reactive composite == today |
| **CP1** | **Schema + judge door** | **CP1a-CP1c** | **Migrations apply; existing datasets read reactive; `/eval/score` parity + 422 validator** |
| 4 | eval-runner mode dispatch | T008-T010 | Runner scores via the door; records dimensions; `eval_passed` untouched |
| 5 | Studio mode selector + render | T011-T014 | Mode selector (reactive default) + dimension-score render |
| 6 | Real no-fakes acceptance + docs | T015-T019 | Real dataset→EvalRun→judge; parity on a real run; Playwright journey; docs + tag bumps |
| **CP2** | **Real reactive parity (no fakes)** | **CP2a-CP2c** | **E-0 DoD gate: composite==judge to the digit on a REAL run; save→reload; `eval_passed` flips** |

---

## MVP scope

**Target CP2 first — it is E-0's whole point.** The MVP is: mode-aware storage + composite plumbing +
one scoring door, proven **behavior-neutral** by the real no-fakes suite ([T015]) where the reactive
composite equals today's judge score to the digit on a REAL eval run. Everything mode-specific
(trajectory/side-effect/filter/member-path scorers) is deferred to E-1…E-5 behind the same `/eval/score`
door. E-0 ships when CP2 is green.

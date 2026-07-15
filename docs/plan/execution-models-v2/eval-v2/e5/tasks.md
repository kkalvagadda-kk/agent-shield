# E-5 Tasks — Workflow run-tree eval (per-member path)

> **Minted from** `e5/plan.md` (authority) + cross-cutting `eval-v2/plan.md` (§2 scorer library, §3 schema) +
> `eval-v2/data-model.md` (§2.5 workflow item). **Re-grounded against the live 2026-07-15 tree** — the specific
> values below (suite number, image tags, migration decision, file seams) are code-truth, not the plan's
> indicative 2026-07-12 anchors.

**Slice:** Eval v2 **Phase E-5** — score a workflow on its **run tree**: the **member path** (which members ran,
in order) + optional **per-member rubric** (one zoom into a child's `run_steps`) + final response. Builds on the
DONE upstream (E-1 scorers + `/eval/score` durable branch + eval-runner durable branch + the real workflow run
tree from WS-1 D4). **Adds a reader + a scorer, not the substrate.**

**Depends on (both DONE):** WS-1 D4 (durable members write child `run_steps` under `parent_run_id`) · E-1
(`score_trajectory`/`score_tool_calls`/`weighted_mean` in `judge.py`; the `/eval/score` durable branch; the
eval-runner durable branch).

## Totals

| | Count |
|---|---|
| **Impl tasks** | 12 (`T001`–`T012`) |
| **Checkpoint phases** | 3 (`CP1a`, `CP1b` = MVP, `CP1c`) |
| **Total** | 15 |
| Phases | 3 (Backend scorer+door · eval-runner tree branch · Studio) |
| **Migration** | **NONE** (head `0062`; reuses `run_steps` + parent/child run tree; `WorkflowDatasetItem` already in `schemas.py`) |
| Services bumped | registry-api `0.2.180→0.2.181` · eval-runner `0.1.6→0.1.7` · studio `0.1.137→0.1.138` (declarative-runner UNCHANGED — E-1 already made agent pods emit `{tool,args}`) |
| E2E suite | **suite-73** (`T-S73-00x`), registered after suite-72 in `run-all.sh` |

**MVP scope:** the load-bearing win is **member-path scored over a REAL workflow run tree, persisted, and gated** —
proven at **CP1b (suite-73, no-fakes)**. Per-member rubric zoom, the dataset editor, and results render ride in
the same slice; if runway is short they degrade to the gap ledger, but CP1b is the non-negotiable gate.

---

## Re-grounding corrections baked into these tasks (vs `e5/plan.md`)

1. **No migration.** Alembic head is **`0062`** (plan said head `0057`, first eval migration `≥0059`). E-0/E-1
   already landed the schema. E-5 reuses `run_steps` + the parent/child `AgentRun` run tree + the
   `eval_run_results` dimension columns. **No DDL owned by E-5.**
2. **`WorkflowDatasetItem` already exists** — `schemas.py:1203` (`kind="workflow"`, `input_message`/`input_payload`,
   `expected_output`, `expected_member_path`, `per_member`), already registered in the `DatasetItem` discriminated
   union (`Tag("workflow")`) and already validated at the door by `_validate_dataset_items`. So the plan's
   "schemas.py M — new `WorkflowDatasetItem`" and "datasets.py M — validate workflow variant" are **already DONE by
   E-0**. E-5's only `schemas.py` change is **two fields on `EvalScoreRequest`** (`member_path`, `per_member_steps`).
   **`routers/datasets.py` is NOT touched.**
3. **Suite = `suite-73`** (suites exist through suite-72 = E-1 durable). Test IDs `T-S73-00x`.
4. **Tags:** registry-api **0.2.180**, eval-runner **0.1.6**, studio **0.1.137** → bump to **0.2.181 / 0.1.7 /
   0.1.138** in **BOTH** `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml`. declarative-runner stays
   **0.1.45** (SDK/harness unchanged).
5. **The scorer reuses E-1's core, not a fork.** `score_trajectory` already reduces two ordered name-lists via a
   multiset+LCS matcher over the four match modes (`exact|ordered|superset|unordered`, `judge.py:226`). E-5 extracts
   that pure list-matcher into a shared helper `_match_sequence(expected, actual, match_mode)` that BOTH
   `score_trajectory` (tool names) and the new `score_member_path` (member names) call — **one matcher, two zoom
   levels**, No-Bandaid.
6. **The tree + child steps are real reads today.** Member path = ordered child `agent_name` from
   `GET /api/v1/workflows/{id}/runs/{run_id}/tree` (`composite_workflows.py:531`, children ordered by `started_at`).
   Per-member steps = `GET /api/v1/agent-runs/{child_id}/steps` (`agent_runs.py:194 list_run_steps`), projected the
   same way E-1's `_project_trajectory` (`eval-runner/main.py:147`) projects `run_steps`. The eval-runner **already
   polls the tree** for the parent's final output (`_run_workflow_item`, `main.py:56`) — E-5 extends that poll into a
   full tree walk + per-member read + `mode=workflow` scoring (currently it scores workflow output as `reactive`).

---

## Summary table

| Phase | Tasks | Proves |
|---|---|---|
| **1 — Backend scorer + scoring door** | T001–T004 | `score_member_path` (reuses E-1's matcher) + `/eval/score` `mode=workflow` dispatch (currently 501) |
| **▸ CP1a** | checkpoint | Real `/eval/score` `mode=workflow` over the deployed judge scores a supplied member_path; wrong path `<1.0` |
| **2 — eval-runner run-tree branch** | T005–T007 | eval-runner walks the REAL tree → member_path + per-member child steps → `mode=workflow` score → persisted `dimension_scores`+`run_id` |
| **▸ CP1b (MVP)** | checkpoint | **suite-73 NO-FAKES**: real workflow + members → real pods → real EvalRun → real tree → real judge → persisted member-path score, wrong-route `<1.0`, `eval_passed` on the workflow version |
| **3 — Studio (author + render)** | T008–T012 | workflow dataset editor (save→reload) + results render member-path/per-member + `run_id` deep-link to the tree |
| **▸ CP1c** | checkpoint | Deploy studio + Playwright: author→save→reload→launch eval→member-path renders→deep-link; typecheck + Vitest green |

---

## Phase 1 — Backend: `score_member_path` + `/eval/score` workflow dispatch

- [X] [T001] [P] Extract the pure ordered-list matcher out of `score_trajectory` into `_match_sequence(expected: list[str], actual: list[str], match_mode) -> (float, detail)` (the existing multiset+LCS logic, unchanged behavior — `score_trajectory` now calls it), then add `score_member_path(member_runs, expected_member_path, match_mode="ordered") -> (float, detail)` that builds the ordered member-name list from the tree's child runs and reduces via `_match_sequence`; emit a `member_diff` detail (`missing[]`/`extra[]`/`order_ok`/`match_mode`). Reference-free (`expected_member_path` empty) → `1.0`. Add unit fixtures — `services/registry-api/judge.py`
  - ✅ judge.py: extracted shared _match_sequence (E-1 logic, byte-identical) + score_member_path (member-name granularity, default ordered)
- [X] [T002] [P] Add two optional fields to `EvalScoreRequest` — `member_path: Optional[list[str]]` (ordered member names the runner extracted from the tree) and `per_member_steps: Optional[dict[str, list[dict]]]` (member name → that child's projected steps, for the per-member rubric). Do NOT touch `WorkflowDatasetItem`/`DatasetItem`/`_validate_dataset_items` — already shipped in E-0 — `services/registry-api/schemas.py`
  - ✅ EvalScoreRequest.member_path + per_member_steps (WorkflowDatasetItem already existed, datasets.py untouched)
- [X] [T003] Wire the `mode=workflow` branch in `eval_score` (today it 501s for workflow, `playground.py:1086`): compose `member_path` (`score_member_path` vs `item["expected_member_path"]`), `response` (`score_response` vs `item["expected_output"]`), and — for each key in `item["per_member"]` — an LLM `score_response`-over-`rubric` on `body.per_member_steps[member]`; reduce via `weighted_mean` with default weights `{member_path:0.4, response:0.4, per_member:0.2}` (overridable via `body.dimension_weights`, present-dimensions-only reducer like the durable branch); return `{composite, dimension_scores, detail{expected_member_path, actual_member_path, member_diff, per_member[]}}`. Depends T001, T002 — `services/registry-api/routers/playground.py`
  - ✅ /eval/score mode=workflow branch: score_member_path + response + per-member → weighted_mean {member_path:0.4,response:0.4,per_member:0.2}; detail{expected/actual_member_path, member_diff, per_member[]}
- [X] [T004] Bump registry-api `0.2.180→0.2.181` in BOTH files (comment: "E-5: /eval/score mode=workflow + score_member_path"). Depends T003 — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
  - ✅ tags bumped registry-api 0.2.181/eval-runner 0.1.8/studio 0.1.138 (both files)

## Checkpoint CP1a — scoring door is real (registry-api deployed)

- [X] [CP1a] Deploy registry-api via the wrapper, then prove the workflow scoring door end-to-end against the REAL judge with a supplied member_path (no eval-runner yet). Script `scripts/e2e/cp-e5-scorer.sh` (`#!/usr/bin/env bash`, `set -euo pipefail`):
  - ✅ deploy-cp1-e5.sh; registry-api 0.2.181→0.2.182 (RunStep.output fix) + eval-runner 0.1.8 + studio 0.1.138 rolled out
  - Deploy: `bash scripts/deploy-cpe2e.sh` (DELEGATED — never bare helm/docker/kubectl).
  - `kubectl exec` into the registry-api pod; `httpx` POST `/api/v1/playground/eval/score` with `mode=workflow`, `item={expected_member_path:["intake","triage","resolver"], expected_output:"..."}`, `member_path=["intake","triage","resolver"]`, `response="..."`.
  - Assert HTTP 200; `jq` that `dimension_scores.member_path == 1.0` and `composite` present.
  - Second call with a WRONG `member_path=["intake","resolver"]` (skipped `triage`) under `ordered` → assert `dimension_scores.member_path < 1.0`.
  - Third call with a `per_member` rubric + supplied `per_member_steps` → assert a `per_member` dimension appears. `exit 0`.
  - **Proves:** the `mode=workflow` dispatch + `score_member_path` reduce correctly over the deployed judge — but the member_path is still supplied, NOT read from a real tree. That gap is closed at CP1b.

## Phase 2 — eval-runner: walk the run tree → member path → per-member steps

- [X] [T005] Add `_run_workflow_tree_item(client, workflow_id, item, idx)`: POST `/workflows/{id}/runs` (`input_message` or `input_payload`), poll `GET /workflows/{id}/runs/{run_id}/tree` to terminal (parent `completed|failed`) reusing the existing poll constants; build `member_path = [child.agent_name for child in tree.children]` (already ordered by `started_at`); for each member named in `item["per_member"]`, GET `/api/v1/agent-runs/{child_id}/steps` and project via the existing `_project_trajectory` into `per_member_steps[member]`; return `(member_path, per_member_steps, response, parent_run_id)`. Fail-closed on poll timeout (return sentinel → caller records failed, never scores) — `services/eval-runner/main.py`
  - ✅ eval-runner _run_workflow_tree_item: /workflows/{id}/runs → poll /runs/{id}/tree → member_path from ordered children + per-member steps via /agent-runs/{child}/steps projected
- [X] [T006] Add `_call_score_api_workflow(client, item, input_text, response, member_path, per_member_steps, run_id)` (POST `/eval/score` `mode=workflow`, returns `(composite, dims, detail)` or None → fail-closed), then rewrite the workflow branch in `run_eval` (currently `main.py:480-504` triggers the run + scores via the reactive `_call_score_api`) to dispatch through `_run_workflow_tree_item` + `_call_score_api_workflow` and record the result row with `dimension_scores`, `eval_detail`, and `run_id` (the parent workflow run, for the deep-link). Depends T005 — `services/eval-runner/main.py`
  - ✅ _call_score_api_workflow + _run_workflow_item_scored: POST /eval/score mode=workflow, write dimension_scores/eval_detail/run_id; fail-closed; unified _fail_closed_record; removed old reactive-scored workflow path
- [X] [T007] Bump eval-runner `0.1.6→0.1.7` in BOTH files (comment: "E-5: eval-runner walks the workflow run tree → member path + per-member steps → mode=workflow score"). Depends T006 — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
  - ✅ run_eval dispatches WORKFLOW_ID items through the tree-walk path

## Checkpoint CP1b (MVP) — suite-73, the NO-FAKES real-workflow-eval gate

- [X] [CP1b] The acceptance gate — a REAL workflow eval end to end, no fakes (the suite-58/59 bar). Script `scripts/e2e/suite-73-eval-v2-workflow.sh` (`#!/usr/bin/env bash`, `set -euo pipefail`), then register it after suite-72 in `scripts/e2e/run-all.sh` (`run_suite "Suite 73: Eval v2 E-5 workflow run-tree (no-fakes)" "suite-73-eval-v2-workflow.sh"`). Deploy via `bash scripts/deploy-cpe2e.sh`. The suite MUST:
  - ✅ MVP no-fakes gate: suite-73 **7/7** — member_path=1.0/composite 0.96 correct vs 0.75 + member_diff.missing wrong-route (core win); actual_member_path from the REAL tree; per_member had_steps=True score 0.8; eval_passed auto-set True; fail-closed; save→reload. Registered run-all.sh:122
  - **T-S73-001** — CREATE its own resources: real member agents + a real `CompositeWorkflow` (a routing shape so a wrong route is possible — e.g. supervisor/conditional with an intake→triage→resolver member path), DEPLOY real pods (wait Ready), and CREATE a real `PlaygroundDataset` `mode=workflow` with a workflow item (`expected_member_path`, `expected_output`, one `per_member` rubric). NO hand-crafted DB rows.
  - **T-S73-002** — launch a REAL `EvalRun` against the workflow version → the real eval-runner **Job** → a real durable workflow run → a real run tree (parent + per-member child `run_steps`). NO faked `_run_step`, NO mocked judge, NO hand-built tree. Poll the Job to completion.
  - **T-S73-003** — read back `eval_run_results`: assert `dimension_scores.member_path` and `composite` PERSISTED (save→reload), `run_id` present (points at the parent workflow run tree), and a `per_member` dimension present for the rubric member.
  - **T-S73-004** — the gate win: a second dataset item whose workflow answers correctly but routes through the WRONG members scores `member_path < 1.0` (the reason E-5 exists); and `eval_passed` auto-sets on the **workflow version** from the composite.
  - **T-S73-005** — fail (not skip) if the workflow/pod/tree fixture is unreachable. `exit 0` only on all assertions green.
  - **Proves (MVP):** member-path scored over a REAL tree, persisted, wrong-route penalized, gate flips — the E-5 raison d'être.

## Phase 3 — Studio: author the workflow dataset + render the run-tree result

- [X] [T008] Add workflow-item types + the `/eval/score` `mode=workflow` shape (member_path / per_member / dimension result fields) to the playground API client — `studio/src/api/playgroundApi.ts`
  - ✅ playgroundApi types: WorkflowDatasetItem + EvalDetail member_path/member_diff/per_member + isWorkflowDetail
- [X] [T009] [P] `workflow` item editor in the dataset authoring UI: `input_message` (or `input_payload`), optional `expected_output`, `expected_member_path` (ordered members), optional `per_member` `{member: {rubric}}`; validate on save. Depends T008 — `studio/src/pages/DatasetsPage.tsx`
  - ✅ DatasetsPage WorkflowItemEditor (input, expected_member_path ordered, match_mode, per-member rubric); validate-on-save sends expected_member_path
- [X] [T010] [P] Render the workflow result: the `member_path` dimension (with `member_diff` missing/extra/order), per-member scores, and a `run_id` deep-link to the workflow run tree (`/workflows/{id}/runs/{run_id}/tree` — member zoom). Depends T008 — `studio/src/pages/EvalResultsPage.tsx`
  - ✅ EvalResultsPage member_path column + WorkflowEvidence panel (expected-vs-actual path, member_diff, per-member) + run_id deep-link
- [X] [T011] Vitest: workflow-item editor validation (`DatasetsPage.test.tsx`) + member-path/per-member dimension rendering + deep-link (`EvalResultsPage.test.tsx`). Depends T009, T010 — `studio/src/pages/DatasetsPage.test.tsx`, `studio/src/pages/EvalResultsPage.test.tsx`
  - ✅ DatasetsPage.test.tsx +5 workflow-editor tests
- [X] [T012] Bump studio `0.1.137→0.1.138` in BOTH files (comment: "E-5: workflow dataset editor + run-tree result render") + document the workflow run-tree eval + per-member zoom in the experience doc. Depends T011 — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`, `docs/experience/playground.md`
  - ✅ EvalResultsPage.test.tsx +3 workflow tests + doc; vitest 227 green, typecheck clean

## Checkpoint CP1c — real user journey in the browser (studio deployed)

- [X] [CP1c] Deploy studio via the wrapper, then prove the journey with a REAL Playwright spec (no `page.route` stub of the eval API — a stubbed browser test is still a fake). Spec `studio/e2e/eval-v2-workflow.spec.ts` (real Keycloak login via `e2e/global-setup.ts`; run through `bash scripts/studio-e2e.sh`). Deploy: `bash scripts/deploy-cpe2e.sh`. The spec MUST:
  - ✅ smoke-test-cp1-e5-infra/behaviour.sh; Playwright eval-v2-workflow.spec.ts PASS (real, no stubs); typecheck + vitest 227 green
  - Author a `workflow` dataset (`expected_member_path` + one `per_member` rubric); **save → reload from the backend → assert `expected_member_path` + `per_member` survived** (the mandatory persistence round-trip).
  - Launch a batch eval against a workflow version (`page.waitForResponse` on the launch call).
  - Assert the results screen renders the member-path dimension + per-member score + the `run_id` deep-link.
  - Also run: `cd studio && npm run typecheck` (clean) and `npm run test` (Vitest green).
  - **Proves:** DoD #1 (real journey, not an endpoint) + DoD #2 (save→reload→assert survived).

---

## Post-implementation gates (MANDATORY before "done")

- **suite-73 registered** in `scripts/e2e/run-all.sh` (after suite-72) and executable — done in CP1b.
- **Image bumps in BOTH files** (`scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml`): registry-api
  `0.2.181` (T004), eval-runner `0.1.7` (T007), studio `0.1.138` (T012). declarative-runner unchanged.
- **Experience doc** `docs/experience/playground.md` updated with workflow run-tree eval + per-member zoom (T012).
- **Verification:** `python3 -c "import ast; ast.parse(...)"` on changed Python + `configure_mappers()` (no model
  change, but confirm imports); `cd studio && npm run typecheck && npm run test`; `bash
  scripts/e2e/suite-73-eval-v2-workflow.sh`; `bash scripts/studio-e2e.sh`.
- **DoD gate:** CP1c proves the journey; CP1b + CP1c prove save→reload→assert; no new symbol is orphaned
  (`score_member_path` → `/eval/score` workflow dispatch → eval-runner; `member_path`/`per_member`/`run_id` →
  EvalResultsPage — all shipped together); any incomplete piece goes to the gap ledger below.

---

## Gap Ledger (carried from `e5/plan.md` §7)

| Item | Status | Note |
|---|---|---|
| Within-member crash-restart trajectory | **deferred (intentional) → WS-1 D4 limitation** | A mid-member crash loses in-flight member progress; E-5 scores what the tree records, not lost in-flight steps. |
| Tool-step scoring inside a **reactive** (non-durable) member | **by-design boundary** | Only durable members write child `run_steps`; reactive members are scored at member-path + response granularity. To score a member's tools, author it durable (WS-1). |
| Semantic member-name equivalence | **deferred (intentional)** | Member names matched exactly — same boundary as E-1's step-name matching. |
| Deep multi-level nesting (workflow-of-workflows) | **not-yet-needed (debt, low)** | E-5 scores one member level; deeper nesting recurses only if member workflows land. |
| Per-member rubric requires the child to be durable (writes `run_steps`) | **by-design (follows the reactive-member boundary)** | A `per_member` rubric over a reactive child has no steps to zoom into; the rubric degrades to that child's response. Surfaced, not silently passed. |

**No orphan flags:** `score_member_path` → `/eval/score` `mode=workflow` dispatch → eval-runner tree branch;
`member_path`/`per_member` dims + `run_id` → `EvalResultsPage`. All shipped together in this slice.

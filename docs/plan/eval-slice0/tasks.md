# Eval Slice 0 — Tasks

**Plan:** [`plan.md`](plan.md) · **Contracts:** [`contracts/publish-request-api.md`](contracts/publish-request-api.md) · **Data model:** [`data-model.md`](data-model.md) · **Research:** [`research.md`](research.md) · **Quickstart:** [`quickstart.md`](quickstart.md)

**Decisions:** `docs/decisions.md` **32** (verdict single-owner + `eval_source` provenance) and **33** (deny-by-default reads; option B deferred).

**MVP scope:** a reviewer on `/admin/publish-requests` sees the eval for **the version being published**, graded against **that run's own threshold**, with an explicit chip when the score came from somewhere else — and two listing endpoints stop returning the whole table to an anonymous caller.

**Ship targets:** registry-api **0.2.234**, studio **0.1.167**.

> **DoD rule 7 — the red gate.** `T001` and `T002` MUST be observed **FAILING** against unfixed code before `T003`–`T006` land. A suite that goes straight to green proves nothing. Capture the failing output in the task's commit message.

---

## Phase 1 — Red tests (must fail first)

- [ ] [T001] [P] **Verdict unit tests, written to fail.** One fixture, two thresholds — `verdictOf(0.85, 0.7) === "pass"` and `verdictOf(0.85, 0.9) === "fail"` — the regression this whole slice exists for. Plus: boundary is **inclusive** (`score === threshold` ⇒ `"pass"`, matching how `effective_pass_threshold` is compared server-side); `verdictOf(0.99, null) === "unknown"` and **never** `"pass"` (fail-closed — a default here would be the fifth copy of the threshold, which is the bug); `verdictOf(null, 0.7) === "unknown"`; the `0.6×` near-band boundary keeps amber; `thresholdLabel(0.85, 0.9) === "0.85 / needs 0.90"`. Import from `../lib/evalVerdict` — the module does not exist yet, so this fails to resolve. **That is the expected red.** — `studio/src/lib/evalVerdict.test.ts`

- [ ] [T002] **`suite-89`, written to fail.** `#!/usr/bin/env bash`, `set -euo pipefail`, executable. Select the pod with the **phase filter** (`-l app.kubernetes.io/name=registry-api --field-selector=status.phase=Running`) — this cluster carries thousands of Evicted pods and `.items[0]` without it picks a dead one (`docs/bugs/e2e-suites-that-could-never-run.md`). Use a **quoted heredoc** (`<<'PY'`) and pass fixtures via the environment; an unquoted heredoc lets the shell execute backticks inside Python comments (`docs/bugs/` same file). Cases: **`T-S89-001`** two versions of one agent, eval on **v1** only, publish request pinning **v2** ⇒ `eval_source == "none"` and `last_eval_score is None` *(red today: returns v1's score)*; **`T-S89-002`** eval on the pinned version ⇒ `"version"` + that run's id; **`T-S89-003`** `source_version_id IS NULL` ⇒ `"agent_latest"`; **`T-S89-004`** `last_eval_pass_threshold` equals a run created with `pass_threshold=0.9`, **not** 0.7; **`T-S89-005`** `GET /playground/eval-runs` with **no** `Authorization` and **no** `X-User-Sub` ⇒ `[]` *(red today: every run)*; **`T-S89-006`** same for `/playground/datasets` *(red today)*; **`T-S89-007`** an authenticated caller still sees exactly their own runs (guards against over-correction) — `scripts/e2e/suite-89-publish-queue-verdict.sh`

---

## Phase 2 — Backend

- [ ] [T003] **Response fields.** Add `last_eval_pass_threshold: Optional[float] = None` and `eval_source: Literal["version","agent_latest","none"] = "none"` to `PublishRequestResponse`, beside the existing `last_eval_score` / `last_eval_run_id`. **No migration** — both are derived at read time; storing a resolved verdict would freeze a threshold that is per-run and overridable. Defaults keep every existing caller valid. Verify `python3 -c "import ast; ast.parse(open('services/registry-api/schemas.py').read())"` and that routers import with `sqlalchemy.orm.configure_mappers()` clean — `services/registry-api/schemas.py`

- [ ] [T004] **Version-aware eval resolution — the actual fix.** Replace the `asset_id`-keyed `eval_map` (`admin.py:164-185`) with `_resolve_publish_request_evals(db, requests, asset_name_map)` keyed by **`PublishRequest.id`**, returning `(score, run_id, threshold, eval_source)`. Today's map is keyed by the **agent**, so every pending request for one agent receives the **same** eval and `source_version_id` is never read at all — keying by request id is what makes per-request resolution expressible. Two batched queries, no N+1: (1) requests **with** `source_version_id` → `WHERE EvalRun.agent_version_id IN (...) AND status='completed'`, latest per version, `eval_source="version"`; (2) requests **without** → today's per-`agent_name` latest, `eval_source="agent_latest"`; (3) neither → `"none"` with all three eval fields `None`. **A pinned version with no eval resolves to `"none"` — it must never borrow another version's score.** Fill the threshold by importing the **existing** `effective_pass_threshold` from `routers.eval_runner` — do **not** write a second resolver and do **not** inline `0.7`; that function's own docstring is the postmortem for this exact class ("the threshold used to exist four times across three services") — `services/registry-api/routers/admin.py`

- [ ] [T005] **SECURITY — deny-by-default on both listings.** `list_eval_runs` (`eval_runner.py:470-473`) and `list_datasets` (`datasets.py:70-73`) filter inside `if caller:` with **no `else`**. Both use `get_optional_user`, which returns `None` rather than raising, and registry-api installs **no global auth middleware** (`main.py:176` = CORS + trace-ID only) — so no identity means no filter means a full-table read. Add `else: q = q.where(sa.false())` to **both**, with a comment naming the precedent: `agents.py:167-170` already fixed this class and documented it ("previously a missing caller skipped the filter entirely and leaked every agent"); `tools.py:189`, `skills.py:99` and `composite_workflows.py:202` carry the same branch. These two were skipped because they are the only ones with **no `publish_status`** for that template to key on. Both routes, one task — fixing only the one Slice 1 happens to touch would leave `datasets.py` as the next instance — `services/registry-api/routers/eval_runner.py`, `services/registry-api/routers/datasets.py`

---

## [CP1a] Checkpoint — the backend tells the truth about which version was scored

- [ ] [CP1a] **Checkpoint script.** `#!/usr/bin/env bash`, `set -euo pipefail`, exit 0. Build+deploy by **delegating to `scripts/deploy-eks.sh`** (never bare `helm`/`docker`/`kubectl apply`), then **wait for `kubectl rollout status`** on registry-api before asserting — a phantom failure against the old pod is the classic waste, and this deployment has no `startupProbe`, so a replica may CrashLoop once mid-rollout before settling. Then `kubectl exec` and assert with real `httpx`: (a) `T-S89-001`–`T-S89-004` all green — a request pinning a version with no eval reports `"none"`, and the threshold echoed is the run's own `0.9`, not `0.7`; (b) `T-S89-005`/`006` — an **anonymous** `GET` on both listings returns `[]`, asserted with a client carrying **neither** header; (c) `T-S89-007` — an authenticated caller still sees their own rows (no over-correction); (d) **served-code pin**: `grep -c "eval_source" /app/routers/admin.py` ≥ 3 **inside the running pod**, not just in git — a tag that was bumped but never rebuilt has burned this repo before (`docs/bugs/e3-never-ran-tag-not-bumped.md`) — `scripts/checkpoints/cp1a-slice0-backend.sh`

---

## Phase 3 — Frontend: the single owner

- [ ] [T006] **`evalVerdict.ts` — THE verdict vocabulary.** Export `verdictOf`, `scoreColor`, `passesGate`, `formatDelta`, `thresholdLabel` and the `Verdict` / `EvalSource` types. Move `scoreColor` from `EvalResultsPage.tsx:53-67` **verbatim, comment block included** — that comment is the institutional memory of this bug and must not be paraphrased or shortened. Widen `threshold` to `number | null | undefined` (a publish request can legitimately have no run) while preserving fail-closed behaviour: an absent threshold makes every `score >= threshold` false, yielding the neutral band and `"unknown"` — never a confident wrong verdict. Turns T001 green — `studio/src/lib/evalVerdict.ts`

- [ ] [T007] [P] **`StatCard` extraction.** Lift verbatim from `CostConsolePage.tsx:22-30` into a shared component and import it back. `CostConsolePage` must render identically; no duplicate declaration may remain — `studio/src/components/shared/StatCard.tsx`, `studio/src/pages/CostConsolePage.tsx`

- [ ] [T008] **`EvalResultsPage` becomes a consumer, not an owner.** Delete the local `scoreColor` (`:53-67`) and import from `../lib/evalVerdict`. **This page is already correct** — zero threshold literals, `scoreColor` already takes a required threshold, called with the real one at `:92`, `:475`, `:1184`. It is the *donor*, not a fix target. **`EvalResultsPage.test.tsx` must pass UNMODIFIED** — that is the proof the extraction was behaviour-neutral. If it needs editing, the move changed behaviour and is wrong — `studio/src/pages/EvalResultsPage.tsx`

- [ ] [T009] [P] **DatasetsPage status dot — one line.** `r.overall_score >= 0.7` (`:1756`) → `passesGate(r.overall_score, r.pass_threshold)`. **No plumbing required:** `DatasetEvalRuns` already receives `runs: EvalRun[]` (`:1741`) and `EvalRun.pass_threshold` is already non-optional on the wire (`playgroundApi.ts:222`, set by `eval_runner.py:83`). E-6 `T006`'s claim that "only the type omits it" is stale. A 0.85 run on a 0.9-threshold dataset must render **amber** — `studio/src/pages/DatasetsPage.tsx`

---

## Phase 4 — Frontend: the publish queue

- [ ] [T010] **Client type mirror.** Add `last_eval_pass_threshold: number | null` and `eval_source: "version" | "agent_latest" | "none"` to the `PublishRequest` interface (~`:1117`). Nullable here but **not** on `EvalRun` — deliberate asymmetry: a run that exists always has a resolved threshold, a publish request may have no run at all — `studio/src/api/registryApi.ts`

- [ ] [T011] **Publish-queue verdict + provenance.** Replace the hardcoded `>= 0.7` / `>= 0.4` ladder (`:167,169`) with `scoreColor(pr.last_eval_score, pr.last_eval_pass_threshold)`; render `thresholdLabel(...)` beside the percentage so a human can see **why** a good-looking score will not publish; render provenance — `"version"` ⇒ score + label only; `"agent_latest"` ⇒ **plus** an amber `"from a different version"` chip; `"none"` ⇒ the existing `"No eval"` badge. Zero threshold literals may remain in the file — `studio/src/pages/AdminPublishRequestsPage.tsx`

- [ ] [T012] [P] **Vitest — publish queue.** `admin_same_score_two_thresholds`: 0.85 renders pass at `last_eval_pass_threshold=0.7` and fail at 0.9. `admin_shows_provenance_chip`: `"agent_latest"` renders the chip, `"version"` does not. `admin_no_threshold_is_not_pass`: `null` threshold renders neutral, never green. **Give every mock its real fields** — E-4's D9 shipped fixtures modelling a response the API never sends, and five tests broke the moment the page read the real field — `studio/src/pages/AdminPublishRequestsPage.test.tsx`

- [ ] [T013] [P] **Vitest — dataset dot.** `datasets_dot_uses_run_threshold`: a 0.85 run at `pass_threshold=0.9` renders amber, and the **same** 0.85 at 0.7 renders green. Every mock `EvalRun` carries a real `pass_threshold` — `studio/src/pages/DatasetsPage.test.tsx`

---

## Phase 5 — The guard that was blind

- [ ] [T014] **Rewrite `T-S80-000b` to discover its scope.** Today it greps **only** `studio/src/pages/EvalResultsPage.tsx` — the one file already fixed in `7b3e3fc` and carrying zero literals — while its failure message claims to cover "the Studio". It is green over a live bug. Replace the fixed path with `SCOPE=$(grep -rl "pass_threshold\|overall_score" studio/src/pages studio/src/components || true)` and assert: **`000b1`** every discovered file, comments stripped, has zero threshold-shaped literals — **keep the existing `sed -E 's://.*::'`**, which exists so a comment *explaining* the bug cannot fail the gate forever and teach the next dev to delete the explanation (a gate must read CODE, not prose); **`000b2`** `scoreColor`/`verdictOf` is defined **exactly once**, in `studio/src/lib/evalVerdict.ts`; **`000b3`** every discovered file that renders a verdict imports from `lib/evalVerdict`. Add a comment stating plainly that **this guard cannot catch the version-join bug** — a *correct* threshold rendered against the *wrong run's* score passes every grep — which is why `T-S89-001` exists separately. Sanity-check: the rewritten guard must **fail** if `T009` is reverted — `scripts/e2e/suite-80-eval-v2-regression.sh`

---

## [CP2a] Checkpoint — the same score, two verdicts, every screen agreeing

- [ ] [CP2a] **Checkpoint script.** `#!/usr/bin/env bash`, `set -euo pipefail`, exit 0. Delegate build+deploy to `scripts/deploy-eks.sh`, wait for `kubectl rollout status` on **both** registry-api and studio, then assert: (a) `bash scripts/e2e/suite-80-eval-v2-regression.sh` green **including the rewritten `T-S80-000b1/b2/b3`**; (b) `bash scripts/e2e/suite-89-publish-queue-verdict.sh` — all 7 cases green; (c) `cd studio && npm run typecheck && npm run test` clean; (d) **served-bundle pin** — fetch the deployed Studio and assert the served JS carries `evalVerdict` and that `STUDIO_BUILD` in the served bundle equals `STUDIO_TAG` (this is what `suite-79 T-S79-002` checks, and a tag bumped without a rebuild has shipped stale bytes here before); (e) **no-orphan grep** — `verdictOf`, `thresholdLabel`, `StatCard`, `eval_source`, `last_eval_pass_threshold` each have a live reader outside their defining file (DoD rule 3) — `scripts/checkpoints/cp2a-slice0-fullstack.sh`

---

## Phase 6 — The real user journey

- [ ] [T015] **Playwright — publish-queue verdict.** Real Keycloak login via `e2e/global-setup.ts`, against the deployed Studio at `STUDIO_E2E_GATEWAY_URL`. **Create every fixture in-spec** — never scavenge "the first matching row"; that made a past spec's verdict track leftover state. **No `page.route`** — a stubbed spec is still a fake and is what missed the mixed-content bug. Journey: author an agent + dataset → run an eval with an explicit `pass_threshold=0.9` scoring **below** it → submit a publish request pinning that version → open `/admin/publish-requests` → `waitForResponse` on the real `GET **/admin/publish-requests` → assert the row renders the verdict **against 0.9** and shows `"0.xx / needs 0.90"` → **reload** and assert both survived → assert an `agent_latest` row shows the provenance chip. Note `studio/e2e/*.spec.ts` is **not** typechecked (`tsconfig.json` includes only `src`), so a typo passes `npm run typecheck` and fails at runtime — **run the spec** — `studio/e2e/eval-verdict-publish-queue.spec.ts`

- [ ] [T016] **Register both new files.** Add `api|eval|scripts/e2e/suite-89-publish-queue-verdict.sh|Publish-queue verdict, provenance + deny-by-default reads` and `browser|eval|studio/e2e/eval-verdict-publish-queue.spec.ts|Publish-queue verdict journey`. A suite missing from the manifest runs in **no** group and **no** full run. Verify `bash scripts/run-tests.sh --audit` is clean — `scripts/test-manifest.txt`

---

## Phase 7 — Ship

- [ ] [T017] [P] **Postmortem — the publish queue.** Required sections: one-line title, **Found/Fixed** (2026-07-27 + registry-api `0.2.234`), **Symptom**, **Root cause** (the design flaw: `eval_map` keyed by `asset_id`, so `source_version_id` was never consulted and every request for an agent shared one score), **Fix** (per-request keying + explicit provenance, and why a silent fallback was rejected). Cross-link `T-S89-001` — `docs/bugs/publish-queue-shows-wrong-version-eval.md`

- [ ] [T018] [P] **Postmortem — the anonymous full-table read.** Same required sections. Root cause: `if caller:` with no `else`, `get_optional_user` returning `None` rather than raising, and **no global auth middleware** — three individually-reasonable choices that compose into "no identity ⇒ no filter". Must state **why it survived**: every existing e2e suite authenticates, so nothing ever exercised the anonymous path. Cross-link `T-S89-005/006` — `docs/bugs/unauthenticated-full-table-read-eval-runs-datasets.md`

- [ ] [T019] **Tag bump — all three sites or `suite-79 T-S79-002` goes red.** `REGISTRY_API_TAG` 0.2.233 → **0.2.234** and `STUDIO_TAG` 0.1.166 → **0.1.167** in `deploy-cpe2e.sh` with a comment header describing the change; mirror **both** in `values.yaml` (registry-api ~L503, studio ~L1123); set `STUDIO_BUILD = "0.1.167"`. Never reuse a tag — K8s caches by tag. Revert `Chart.lock` if the deploy drifts it (`Chart.yaml` pins `18.x.x`/`27.x.x`, so it re-resolves every run) — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`, `studio/src/lib/build.ts`

- [ ] [T020] **Gap-ledger close-out.** Close the Slice 0 entries; record what shipped with which tag; **leave Decision 33 option B (team-scoped reads) open and clearly tagged `deferred (intentional)`** — it is the difference between Wave 1 serving one person and serving a team. Note the residual: the rewritten `T-S80-000b` guards literals, not the version join — `docs/testing/manual-ui-e2e-test-plan.md`

---

## [CP3a] Checkpoint — MVP gate

- [ ] [CP3a] **Final gate script.** `#!/usr/bin/env bash`, `set -euo pipefail`, exit 0. Assert every DoD gate for the slice: (a) `bash scripts/run-tests.sh --layer api --group eval` green; (b) `bash scripts/run-tests.sh --layer browser --group eval` green **including `T015`**; (c) `bash scripts/run-tests.sh --audit` clean; (d) `cd studio && npm run typecheck && npm run test`; (e) the **live** studio pod serves `0.1.167` and the live registry-api serves `0.2.234` (`kubectl get pods … -o custom-columns` on the image); (f) both `docs/bugs/` files exist and each names its regression test; (g) `docs/testing/manual-ui-e2e-test-plan.md` still contains an **open** entry for Decision 33 option B — the ledger must not quietly close a deferred item. Also run the **blast-radius** neighbours, not just the new tests: `suite-79` (served-tag assertion — tags moved) and the `AdminPublishRequestsPage` / `DatasetsPage` / `EvalResultsPage` / `CostConsolePage` Vitest files — `scripts/checkpoints/cp3a-slice0-mvp-gate.sh`

---

## Execution order

```
T001, T002            (RED — observe failing, capture output)
   ↓
T003 → T004, T005     (backend)
   ↓
CP1a                  (deploy + backend truth)
   ↓
T006 → T007[P], T008, T009[P]
   ↓
T010 → T011 → T012[P], T013[P]
   ↓
T014
   ↓
CP2a                  (deploy + full stack + no-orphan)
   ↓
T015 → T016
   ↓
T017[P], T018[P], T019, T020
   ↓
CP3a                  (MVP gate)
```

`[P]` = disjoint files, safe to run in parallel. Everything else is sequential on the arrows.

**Counts:** 20 implementation tasks, 3 checkpoints.

# Eval Slice 0 — Verdict Single-Owner + Deny-by-Default Reads

**Goal:** Make one piece of code decide "did this eval pass," so a reviewer approving a release can never be shown a different version's score against a threshold that run never used — and close a live unauthenticated full-table read found while grounding this work.

**Architecture:** The server owns the verdict *inputs* (score, the run's own threshold, and where the eval came from); the client only renders them. The threshold resolver already exists (`effective_pass_threshold`, `eval_runner.py:49`) and is reused, never re-declared — re-declaration is the bug being fixed. On the client, one module (`lib/evalVerdict.ts`) owns the verdict vocabulary and every screen imports it.

**Tech stack:** FastAPI + SQLAlchemy 2.0 async (registry-api), React 18 + TypeScript + React Query (studio), Vitest + React Testing Library, Playwright, bash+curl e2e via `kubectl exec`.

**Design inputs:** `docs/design/eval-ux-enrichment.md` Slice 0 · `docs/decisions.md` Decisions **32** and **33** · `docs/design/eval-state-of-play.md` (code-verified ledger).

---

## Constitution Check (`CLAUDE.md` Definition of Done)

| # | Principle | Status | Justification |
|---|---|---|---|
| 1 | Real user journey proven | **PASS** | T-13 Playwright drives the publish queue, reads the verdict, reloads |
| 2 | Save → reload → assert | **PASS** | T-13 reloads and re-asserts; Slice 0 adds **no new write surface**, so the round-trip guard applies to the read path |
| 3 | No orphan code | **PASS** | `evalVerdict.ts` has 4 importers (T-6..T-9); `StatCard` has 2 (T-7, T-9); both new API fields are read by T-8 |
| 4 | Vertical slices | **PASS** | Backend field → API → UI → test, one path, proven before Wave 1 starts |
| 5 | Honest gap ledger | **PASS** | T-15; Decision 33 option B already ledgered |
| 6 | Reason from running product | **PASS** | Every line reference below was read from code 2026-07-27; three doc claims were found stale and are corrected in Research |
| 7 | Regression-test-first | **PASS** | T-2 and T-4 are written to FAIL against current code before their fix lands |
| 8 | Document every bug | **PASS** | T-14 writes two `docs/bugs/` postmortems |

**No violations.** No Complexity Tracking entries.

---

## Corrections to the design doc (verified in code, 2026-07-27)

These change the work. They are recorded here so the implementer does not "fix" something already correct.

1. **`EvalRun.pass_threshold` is ALREADY plumbed end-to-end.** `playgroundApi.ts:222` declares it (non-optional, with a comment forbidding a local `?? 0.7`), and `eval_runner.py:83` sets `resp.pass_threshold = effective_pass_threshold(run)`. E-6 T006's premise ("the backend has always returned it; only the type omits it") is **stale**. Consequence: `DatasetsPage.tsx:1756` is a **one-line fix** with the data already on the wire — `DatasetEvalRuns` takes `runs: EvalRun[]` (`DatasetsPage.tsx:1741`).
2. **`EvalResultsPage.tsx` is already correct.** Zero threshold literals; `scoreColor(score, threshold)` takes a **required** threshold and is called with the real one at `:92`, `:475`, `:1184`. It is not a bug site — it is the **donor** of the canonical implementation.
3. **The publish-queue bug is worse than "wrong version".** `admin.py:164-185` builds `eval_map` keyed by **`asset_id`** (the agent), so *every* pending request for one agent shows the *same* latest eval — the per-request `source_version_id` is never consulted at all.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `services/registry-api/schemas.py` | Modify | `PublishRequestResponse` gains `last_eval_pass_threshold` + `eval_source` |
| `services/registry-api/routers/admin.py` | Modify | Version-aware eval resolution + provenance (replaces the `asset_id`-keyed map) |
| `services/registry-api/routers/eval_runner.py` | Modify | Deny-by-default `else` on `list_eval_runs` (:471) |
| `services/registry-api/routers/datasets.py` | Modify | Deny-by-default `else` on `list_datasets` (:72) |
| `studio/src/lib/evalVerdict.ts` | **Create** | THE verdict vocabulary — `verdictOf`, `scoreColor`, `passesGate`, `formatDelta` |
| `studio/src/lib/evalVerdict.test.ts` | **Create** | Boundary + fail-closed unit tests |
| `studio/src/components/shared/StatCard.tsx` | **Create** | Lifted from `CostConsolePage.tsx:22-30` |
| `studio/src/api/registryApi.ts` | Modify | `PublishRequest` TS type gains the two fields (:1117 area) |
| `studio/src/pages/EvalResultsPage.tsx` | Modify | Delete local `scoreColor` (:62-67), import it instead |
| `studio/src/pages/AdminPublishRequestsPage.tsx` | Modify | Kill `>= 0.7`/`>= 0.4` (:167,169); render threshold + provenance |
| `studio/src/pages/DatasetsPage.tsx` | Modify | Kill `>= 0.7` (:1756) — use `r.pass_threshold` |
| `studio/src/pages/CostConsolePage.tsx` | Modify | Import `StatCard` instead of declaring it |
| `studio/src/pages/AdminPublishRequestsPage.test.tsx` | **Create** | Verdict + provenance rendering |
| `studio/src/pages/DatasetsPage.test.tsx` | Modify | Status-dot honours the run's own threshold |
| `scripts/e2e/suite-80-eval-v2-regression.sh` | Modify | `T-S80-000b` rewritten to **discover** its scope |
| `scripts/e2e/suite-89-publish-queue-verdict.sh` | **Create** | Version-join, provenance, and the two unauthenticated cases |
| `scripts/test-manifest.txt` | Modify | Register suite-89 + the new spec |
| `studio/e2e/eval-verdict-publish-queue.spec.ts` | **Create** | Real browser journey |
| `docs/bugs/publish-queue-shows-wrong-version-eval.md` | **Create** | Postmortem (rule 8) |
| `docs/bugs/unauthenticated-full-table-read-eval-runs-datasets.md` | **Create** | Postmortem (rule 8) |
| `scripts/deploy-cpe2e.sh` | Modify | `REGISTRY_API_TAG` 0.2.233→**0.2.234**, `STUDIO_TAG` 0.1.166→**0.1.167** |
| `charts/agentshield/values.yaml` | Modify | Mirror both tags |
| `studio/src/lib/build.ts` | Modify | `STUDIO_BUILD = "0.1.167"` |
| `docs/testing/manual-ui-e2e-test-plan.md` | Modify | Gap ledger close-out |

---

## Key Interfaces (contracts — match exactly)

```python
# services/registry-api/schemas.py — PublishRequestResponse gains:
last_eval_pass_threshold: Optional[float] = None
eval_source: Literal["version", "agent_latest", "none"] = "none"
```

```python
# services/registry-api/routers/admin.py — internal helper
async def _resolve_publish_request_evals(
    db: AsyncSession,
    requests: list[PublishRequest],
    asset_name_map: dict[uuid.UUID, tuple[str, str]],
) -> dict[uuid.UUID, tuple[float | None, uuid.UUID | None, float | None, str]]:
    """Keyed by PublishRequest.id (NOT asset_id).
    Returns (score, eval_run_id, pass_threshold, eval_source)."""
```

```typescript
// studio/src/lib/evalVerdict.ts — THE single owner
export type Verdict = "pass" | "near" | "fail" | "unknown";
export type EvalSource = "version" | "agent_latest" | "none";

export function verdictOf(score: number | null | undefined,
                          threshold: number | null | undefined): Verdict;
export function scoreColor(score: number | null,
                           threshold: number | null | undefined): string;
export function passesGate(score: number | null | undefined,
                           threshold: number | null | undefined): boolean;
export function formatDelta(current: number | null, baseline: number | null): string;
export function thresholdLabel(score: number | null,
                               threshold: number | null | undefined): string; // "0.85 / needs 0.90"
```

```typescript
// studio/src/components/shared/StatCard.tsx
export function StatCard(props: { label: string; value: string; sub?: string }): JSX.Element;
```

---

## Tasks

### T-1 — `evalVerdict.ts`: the single owner

**Files:** Create `studio/src/lib/evalVerdict.ts`

Move `scoreColor` from `EvalResultsPage.tsx:53-67` **verbatim, including its full comment block** — that comment is the institutional memory of this bug and must not be paraphrased. Widen its `threshold` parameter to `number | null | undefined` (the publish queue can legitimately have none) while keeping the fail-closed behaviour: absent threshold ⇒ every `score >= threshold` is false ⇒ neutral band, never a confident wrong verdict.

**Acceptance criteria:**
- `verdictOf(0.85, 0.7) === "pass"`; `verdictOf(0.85, 0.9) === "fail"`
- `verdictOf(0.9, 0.9) === "pass"` — the boundary is inclusive, matching `effective_pass_threshold` usage
- `verdictOf(x, null) === "unknown"` for every x — **never** `"pass"`
- `verdictOf(null, 0.7) === "unknown"`
- `scoreColor` output strings byte-identical to the current `EvalResultsPage` implementation for the same inputs
- `thresholdLabel(0.85, 0.9) === "0.85 / needs 0.90"`

**Dependencies:** none

**Verification:** `cd studio && npx vitest run src/lib/evalVerdict.test.ts`

---

### T-2 [P] — `evalVerdict.test.ts` (**write first, must fail before T-1**)

**Files:** Create `studio/src/lib/evalVerdict.test.ts`

**Test cases:**
- `test_same_score_two_thresholds`: **one fixture, two thresholds** — 0.85 renders pass at 0.7 and fail at 0.9. This is the regression the whole slice exists for.
- `test_boundary_inclusive`: score === threshold → `"pass"`
- `test_absent_threshold_is_unknown_not_pass`: `(0.99, null)` → `"unknown"`
- `test_null_score`: `(null, 0.7)` → `"unknown"`
- `test_near_band`: 0.6× threshold boundary keeps the amber band

**Dependencies:** none (runs red until T-1)

---

### T-3 — Backend: `PublishRequestResponse` fields

**Files:** Modify `services/registry-api/schemas.py`

Add `last_eval_pass_threshold: Optional[float] = None` and `eval_source: Literal["version","agent_latest","none"] = "none"` beside the existing `last_eval_score` / `last_eval_run_id`. **No migration** — both are derived at read time.

**Acceptance criteria:**
- Defaults make the fields safe for every existing caller
- `python3 -c "import ast; ast.parse(open('services/registry-api/schemas.py').read())"` clean
- Routers import + `sqlalchemy.orm.configure_mappers()` succeeds

**Dependencies:** none

---

### T-4 — e2e suite-89 (**write first, must fail before T-5 and T-11**)

**Files:** Create `scripts/e2e/suite-89-publish-queue-verdict.sh` (executable, `set -euo pipefail`)

Follow the repo pod-selection idiom **including the phase filter**:
```bash
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
```
Use a **quoted** heredoc (`<<'PY'`) and pass fixture values via the environment — never splice `${VAR}` into the Python body (see `docs/bugs/e2e-suites-that-could-never-run.md`).

**Test cases:**
- `T-S89-001`: two versions of one agent, an eval on **v1** only, publish request pinning **v2** → response has `eval_source == "none"` and `last_eval_score is None`. *Fails today: returns v1's score.*
- `T-S89-002`: eval exists on the pinned version → `eval_source == "version"`, and `last_eval_run_id` is that version's run.
- `T-S89-003`: request with `source_version_id IS NULL` → `eval_source == "agent_latest"` and the score is the agent's latest.
- `T-S89-004`: `last_eval_pass_threshold` equals the run's own `pass_threshold`, not 0.7, for a run created with `pass_threshold=0.9`.
- `T-S89-005` **(security)**: `GET /api/v1/playground/eval-runs` with **no** `Authorization` and **no** `X-User-Sub` returns `[]`. *Fails today: returns every run.*
- `T-S89-006` **(security)**: same for `GET /api/v1/playground/datasets`. *Fails today.*
- `T-S89-007`: an authenticated caller still sees exactly their own runs (no over-correction).

**Dependencies:** none (runs red until T-5/T-11)

**Verification:** `bash scripts/e2e/suite-89-publish-queue-verdict.sh`

---

### T-5 — Backend: version-aware eval resolution

**Files:** Modify `services/registry-api/routers/admin.py:164-196`

Replace the `asset_id`-keyed `eval_map` with `_resolve_publish_request_evals` keyed by **`PublishRequest.id`**. Two queries, no N+1:

1. Requests **with** `source_version_id`: `WHERE EvalRun.agent_version_id IN (...) AND status='completed'`, latest per version → `eval_source="version"`.
2. Requests **without**: today's per-`agent_name` latest → `eval_source="agent_latest"`.
3. Neither → `eval_source="none"`, score/run/threshold all `None`.

Populate `last_eval_pass_threshold` by calling the **existing** `effective_pass_threshold(run)` imported from `routers.eval_runner`. **Do not** write a second resolver and **do not** inline `0.7`.

**Acceptance criteria:**
- T-S89-001..004 pass
- A request pinning a version with no eval reports `"none"` — it never borrows another version's score
- `select` count is constant regardless of request count

**Dependencies:** T-3, T-4

---

### T-6 — `EvalResultsPage` imports the shared verdict

**Files:** Modify `studio/src/pages/EvalResultsPage.tsx` — delete lines 53-67, import from `../lib/evalVerdict`

This page is **already correct**; it is being converted from owner to consumer. Behaviour must not change.

**Acceptance criteria:**
- No local `scoreColor` remains
- `EvalResultsPage.test.tsx` passes **unmodified** — proof the move is behaviour-neutral

**Dependencies:** T-1

---

### T-7 [P] — `StatCard` extraction

**Files:** Create `studio/src/components/shared/StatCard.tsx`; modify `studio/src/pages/CostConsolePage.tsx` (delete :22-30, import)

**Acceptance criteria:** `CostConsolePage` renders identically; no duplicate declaration remains.

**Dependencies:** none

---

### T-8 — `PublishRequest` TS type + Admin queue UI

**Files:** Modify `studio/src/api/registryApi.ts` (~:1117) and `studio/src/pages/AdminPublishRequestsPage.tsx:162-178`

Add `last_eval_pass_threshold: number | null` and `eval_source: "version" | "agent_latest" | "none"` to the TS type. Replace the hardcoded ladder with `scoreColor(pr.last_eval_score, pr.last_eval_pass_threshold)`, render `thresholdLabel(...)` beside the percentage, and render provenance:

| `eval_source` | UI |
|---|---|
| `version` | score + `"0.85 / needs 0.90"`, no extra chip |
| `agent_latest` | same **plus** an amber `"from a different version"` chip |
| `none` | existing `"No eval"` badge |

**Acceptance criteria:**
- Zero threshold literals remain in the file
- An `agent_latest` row is visually distinguishable from a `version` row

**Dependencies:** T-1, T-3

---

### T-9 [P] — DatasetsPage status dot

**Files:** Modify `studio/src/pages/DatasetsPage.tsx:1756`

One line: `r.overall_score >= 0.7` → `passesGate(r.overall_score, r.pass_threshold)`. `DatasetEvalRuns` already receives `runs: EvalRun[]` (`:1741`) and `EvalRun.pass_threshold` is already non-optional on the wire — **no plumbing required**.

**Acceptance criteria:** a 0.85 run on a 0.9-threshold dataset renders **amber**, not green.

**Dependencies:** T-1

---

### T-10 [P] — Vitest for both fixed screens

**Files:** Create `studio/src/pages/AdminPublishRequestsPage.test.tsx`; modify `studio/src/pages/DatasetsPage.test.tsx`

**Test cases:**
- `admin_same_score_two_thresholds`: 0.85 renders pass at `last_eval_pass_threshold=0.7`, fail at 0.9
- `admin_shows_provenance_chip`: `eval_source="agent_latest"` renders the warning chip; `"version"` does not
- `admin_no_threshold_is_not_pass`: `last_eval_pass_threshold=null` renders neutral, never green
- `datasets_dot_uses_run_threshold`: 0.85 @ 0.9 → amber
- **Every mock `EvalRun` must carry a real `pass_threshold`** — E-4's D9 shipped fixtures modelling a response the API never sends, and five tests broke the moment the page read the real field

**Dependencies:** T-8, T-9

---

### T-11 — Security: deny-by-default on both listings

**Files:** Modify `services/registry-api/routers/eval_runner.py:470-473` and `services/registry-api/routers/datasets.py:70-73`

```python
if caller:
    q = q.where(EvalRun.user_id == caller)
else:
    # DENY-BY-DEFAULT. No global auth middleware exists (main.py:176 = CORS +
    # trace-ID only) and this route uses get_optional_user, which returns None
    # rather than raising — so without this branch a caller with no identity
    # received an UNFILTERED full-table read. Same class agents.py:167-170
    # already fixed: "previously a missing caller skipped the filter entirely
    # and leaked every agent."
    q = q.where(sa.false())
```

Mirror exactly in `datasets.py` with `PlaygroundDataset.owner_user_id`.

**Acceptance criteria:** T-S89-005/006 pass; T-S89-007 proves no over-correction.

**Dependencies:** T-4

---

### T-12 — Rewrite the blind guard

**Files:** Modify `scripts/e2e/suite-80-eval-v2-regression.sh:154-174`

Today it greps **only** `studio/src/pages/EvalResultsPage.tsx` — the one file already clean — while its failure message claims to cover "the Studio". Replace with a **discovering** scope:

```bash
SCOPE=$(grep -rl "pass_threshold\|overall_score" studio/src/pages studio/src/components || true)
```

- `T-S80-000b1` — every discovered file, comments stripped (**keep the existing `sed -E 's://.*::'`** — it exists so a comment *explaining* the bug cannot fail the gate forever and teach devs to delete the explanation), contains zero threshold-shaped literals.
- `T-S80-000b2` — `scoreColor` / `verdictOf` is defined **exactly once**, in `studio/src/lib/evalVerdict.ts`.
- `T-S80-000b3` — every discovered file that renders a verdict imports from `lib/evalVerdict`.

**This guard cannot catch the version-join bug** — a *correct* threshold against the *wrong run's* score passes every grep. That is why T-4's `T-S89-001` exists separately. Say so in a comment.

**Acceptance criteria:** the rewritten guard **fails** if `DatasetsPage.tsx:1756` is reverted; passes after T-9.

**Dependencies:** T-6, T-8, T-9

---

### T-13 — Playwright journey (DoD #1/#2)

**Files:** Create `studio/e2e/eval-verdict-publish-queue.spec.ts`; register in `scripts/test-manifest.txt`

Real Keycloak login via `e2e/global-setup.ts`, against the deployed Studio. **Create fixtures in-spec** — never scavenge "the first matching row"; that made a past spec's verdict track leftover state. **No `page.route`** — a stubbed spec is still a fake.

**Test cases:**
- Author an agent + dataset, run an eval with an explicit `pass_threshold=0.9` scoring below it, submit a publish request pinning that version
- Open `/admin/publish-requests`, `waitForResponse` on the real `GET **/admin/publish-requests`
- Assert the row renders the verdict **against 0.9** and shows `"0.xx / needs 0.90"`
- **Reload** and assert both survived
- Assert an `agent_latest` row shows the provenance chip

**Dependencies:** T-8, T-5

---

### T-14 [P] — Bug postmortems (rule 8)

**Files:** Create `docs/bugs/publish-queue-shows-wrong-version-eval.md` and `docs/bugs/unauthenticated-full-table-read-eval-runs-datasets.md`

Each needs: one-line title, **Found/Fixed** (date + exact image tag), **Symptom**, **Root cause** (the design flaw, not the surface error), **Fix** (why it is the class-fix), and a cross-link to the regression test that reproduces it (`T-S89-001`, `T-S89-005/006`).

The second must name *why* it survived: every existing suite authenticates, so no test ever exercised the anonymous path.

**Dependencies:** T-5, T-11

---

### T-15 — Ship: tags, ledger, verification

**Files:** `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`, `studio/src/lib/build.ts`, `docs/testing/manual-ui-e2e-test-plan.md`

- `REGISTRY_API_TAG` → **0.2.234**, `STUDIO_TAG` → **0.1.167**, mirrored in values.yaml, `STUDIO_BUILD = "0.1.167"` — **all three or suite-79 T-S79-002 goes red**
- Close the Slice 0 ledger entries; leave Decision 33 option B open
- `bash scripts/run-tests.sh --audit` clean

**Verification (all must pass):**
```bash
cd studio && npm run typecheck && npm run test
bash scripts/run-tests.sh --layer api --group eval
bash scripts/e2e/suite-89-publish-queue-verdict.sh
bash scripts/run-tests.sh --layer browser --group eval
bash scripts/run-tests.sh --audit
```

**Dependencies:** all

---

## Execution Notes

- Order: **T-2 → T-4** (both red) → T-1, T-3 → T-5, T-11 → T-6..T-10 → T-12, T-13 → T-14, T-15
- `[P]` tasks touch disjoint files and may run in parallel
- **T-2 and T-4 must be observed failing before their fixes land** (DoD rule 7). A suite that goes straight to green proves nothing.
- Blast radius for the regression sweep: `eval` group both layers, plus `suite-79` (served-tag assertion) because tags move, plus `AdminPublishRequestsPage`/`DatasetsPage`/`EvalResultsPage`/`CostConsolePage` Vitest.

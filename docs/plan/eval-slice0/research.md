# Eval Slice 0 — Research & Decisions

Every finding below was read out of code on 2026-07-27, not out of a design doc. Three claims in
`eval-ux-enrichment.md` turned out to be stale; they are corrected here rather than inherited.

---

## D-1. Reuse `effective_pass_threshold`, do not write a resolver

**Decision:** `admin.py` imports `effective_pass_threshold` from `routers.eval_runner` (`:49`).

**Rationale:** The function's own docstring is the postmortem for this exact bug class:

> The threshold used to exist four times across three services (this gate, the eval-runner's per-item
> verdict, and the Studio's verdict + colour band), each defaulting to 0.7. They agreed, so nothing ever
> errored — and a per-run threshold wired to only the gate would have made the product LIE.

Adding a fifth resolver to fix a bug caused by four of them would be self-defeating.

**Alternatives rejected:** inlining `run.pass_threshold or 0.7` in `admin.py` — reintroduces the literal
in a new file, which is precisely how it reached four copies.

---

## D-2. `eval_source` as a three-value enum, not a boolean

**Decision:** `Literal["version", "agent_latest", "none"]`.

**Rationale:** A boolean (`eval_matches_version`) cannot distinguish "no eval exists at all" from "an eval
exists but for another version." Those demand different UI *and* different reviewer behaviour: the first
means *run an eval*, the second means *do not trust this number*. Collapsing them recreates the ambiguity
the field exists to remove.

**Alternatives rejected:** omitting provenance entirely (Decision 32 option B) — a silent fallback leaves
one field carrying two meanings, which is the same class of defect, quieter.

---

## D-3. Key the eval map by `PublishRequest.id`, not `asset_id`

**Decision:** `_resolve_publish_request_evals` returns a dict keyed by request id.

**Rationale — the bug is worse than the design doc says.** `admin.py:164-185` currently builds
`eval_map: dict[asset_id, ...]`. Because the key is the **agent**, every pending request for that agent
receives the **same** eval, and the per-request `source_version_id` is never read. Keying by request id is
what makes per-request resolution expressible at all; without it the "wrong version" bug is not fixable,
only relabelled.

---

## D-4. Two batched queries, not per-request lookups

**Decision:** one query for version-pinned requests (`agent_version_id IN (...)`), one for unpinned
(`agent_name IN (...)`).

**Rationale:** The publish queue is an admin list view; a per-request query is an N+1 on a page whose whole
job is showing many rows. `EvalRun.agent_version_id` exists (`models.py:1516`) and is indexed by the
existing access patterns, so the version query is a direct swap for today's `agent_name` one.

**Assumption:** `EvalRun.status == "completed"` remains the correct filter — carried over from the current
query, unchanged.

---

## D-5. Fail closed when the threshold is absent

**Decision:** `verdictOf(score, null) === "unknown"` for **every** score, including 0.99.

**Rationale:** Inherited verbatim from the donor implementation's comment. A default here would
re-declare the threshold, which is how it came to exist four times. `unknown` renders a neutral band and
suppresses any pass affordance — no confident wrong verdict.

**Alternatives rejected:** defaulting to the platform 0.7 in the client. That is the fifth copy.

---

## Stale design-doc claims, corrected

### S-1. `EvalRun.pass_threshold` is already plumbed (doc implies it is not)

E-6 `T006` says *"the backend has **always** returned `pass_threshold`; only the type omits it."* **The
type no longer omits it.** `playgroundApi.ts:222` declares it non-optional with a comment explicitly
forbidding a local `?? 0.7` fallback, and `eval_runner.py:83` sets it on every response.

**Consequence:** `DatasetsPage.tsx:1756` needs **no plumbing** — `DatasetEvalRuns` already receives
`runs: EvalRun[]` (`:1741`). It is a one-line change. The plan's estimate reflects this.

### S-2. `EvalResultsPage.tsx` is not a bug site

It has **zero** threshold literals; `scoreColor(score, threshold)` takes a required threshold and is
called with the real one at `:92`, `:475`, `:1184`. It is the **donor** of the canonical implementation,
not a fix target. Its existing tests must pass **unmodified** after T-6 — that is the proof the extraction
was behaviour-neutral.

### S-3. Migration `0073` is taken

`eval-ux-enrichment.md` Slice 2 specifies migration **0073**; that file exists
(`0073_credential_blobs_and_credential_ref.py`) and the head is **0075**. Slice 2's migration must be
**0076**. Not needed for Slice 0 (no schema change) but recorded so Wave 1 does not collide — the same
alembic-collision class fixed during the `main` merge earlier today.

---

## Security finding (drove Decision 33)

`list_eval_runs` (`eval_runner.py:471`) and `list_datasets` (`datasets.py:72`) filter inside `if caller:`
with **no `else:`**. Both use `get_optional_user`, which returns `None` rather than raising, and
**registry-api installs no global auth middleware** — `main.py:176` adds only CORS and a trace-ID
middleware. Therefore *no identity ⇒ no filter ⇒ full-table read*.

Four sibling routers carry the deny-by-default `else`: `agents.py:167-170` (with the postmortem comment
*"previously a missing caller skipped the filter entirely and leaked every agent"*), `tools.py:189`,
`skills.py:99`, `composite_workflows.py:202`. The two that do not are exactly the two with **no
`publish_status` column** — the "published to all, private to creator" template did not map, so they were
skipped.

**Why no test caught it:** every existing e2e suite authenticates. The anonymous path was never exercised,
so the guard had nothing to fail. `T-S89-005/006` close that.

**Exposure:** the test cluster's NLB is internal (VPN-only). No claim is made about other environments —
the fix is warranted regardless, since the route-level check is the only control that exists.

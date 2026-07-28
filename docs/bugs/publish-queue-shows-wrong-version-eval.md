# The publish queue showed a different version's eval score, graded against the wrong threshold

**Found:** 2026-07-27 (design review of `eval-ux-enrichment.md`; confirmed live).
**Fixed:** registry-api **0.2.234** / studio **0.1.167** — `routers/admin.py`, `schemas.py`,
`AdminPublishRequestsPage.tsx`, `DatasetsPage.tsx`, new `studio/src/lib/evalVerdict.ts`.

## Symptom

A reviewer on `/admin/publish-requests` approves a release while looking at an eval score. That score
could be **from a different version than the one being published**, and it was graded against a
**hardcoded 0.7** rather than the threshold the run actually used.

Reproduced by `T-S89-001` before the fix — an agent with two versions, an eval on **v1** only, and a
publish request pinning **v2**:

```
FAIL T-S89-001  eval_source=None score=0.85
                (the eval belongs to v1; the request pins v2)
FAIL T-S89-004  last_eval_pass_threshold=None  (expected 0.9 — the run's own)
```

## Root cause

Two independent defects that combine into "a confident number about the wrong thing."

**1. The eval map was keyed by the AGENT, not the request.**

```python
eval_map: dict[uuid.UUID, tuple[float | None, uuid.UUID | None]] = {}
...
.order_by(EvalRun.agent_name, EvalRun.completed_at.desc())
.distinct(EvalRun.agent_name)
...
eval_map[aid] = name_to_eval[aname]      # aid == ASSET id
```

`PublishRequest.source_version_id` was **never read**. The map could not express a per-request answer, so
every pending request for one agent received the *same* latest eval. This is worse than the design doc
described ("shows the wrong version") — with two requests queued on different versions, both showed an
identical score, and neither necessarily belonged to either version.

**2. The verdict rule was re-declared in the UI.** `AdminPublishRequestsPage.tsx` rendered
`>= 0.7` / `>= 0.4`, and `PublishRequestResponse` carried **no threshold at all** — so the page
*structurally could not* render a correct verdict without a new backend field.

## Why it survived

`suite-80`'s `T-S80-000b` was written to guard exactly this and **greps a single named file**:
`studio/src/pages/EvalResultsPage.tsx` — the one already fixed in `7b3e3fc`, carrying **zero** literals —
while its failure message claims to cover *"the Studio"*. It was green over a live bug in two other files.

The tracked gap pointed at the same clean file: the ledger and E-6's `T006`/`T020`–`T022` all name
`EvalResultsPage.tsx:51`. **The tracking had drifted onto the wrong target and the two real offenders
were named nowhere.** Naming a file is how a guard rots — the scope has to be discovered.

## Fix

**Per-request resolution.** `eval_map` is keyed by `PublishRequest.id`. Two batched queries (no N+1):
version-pinned requests join `EvalRun.agent_version_id`; unpinned ones keep the per-`agent_name` latest.
A pinned version with **no** eval resolves to `none` and borrows nothing.

**Explicit provenance.** `eval_source: "version" | "agent_latest" | "none"` ships on the response. A
silent fallback was rejected during design (Decision 32): one field with two meanings recreates the same
class of bug, quieter. The UI renders "from a different version" for `agent_latest`.

**One owner for the verdict.** `studio/src/lib/evalVerdict.ts` — `verdictOf` / `scoreColor` /
`passesGate` / `thresholdLabel`, moved verbatim from `EvalResultsPage` including its comment. The server
sends the threshold via the **existing** `effective_pass_threshold(run)`; nothing client-side re-derives
it, and the module has **no default** — a default is the fifth copy. Absent threshold ⇒ `"unknown"`,
never `"pass"`.

**The guard discovers its scope.** `T-S80-000b1/b2/b3` now grep `studio/src/pages` + `studio/src/components`
for files mentioning `pass_threshold`/`overall_score`, assert zero threshold literals in each, assert the
rule is defined exactly once, and assert every verdict-rendering file imports it.

Two things that check found immediately, both real:

- **A fourth `scoreColor`** in `ObservabilityTracesPage.tsx:22`, hardcoding `0.8`/`0.5`. Excluded with a
  written reason — it takes no threshold and grades a trace's `judge_score`, which has no
  `pass_threshold` to grade against. A different rule, not a fifth copy. Its bare literals are ledgered.
- **The comment stripper could not strip multi-line comments.** The original `sed -E 's:/\*.*\*/::'`
  works line-at-a-time, so a JSX block comment spanning lines survived — and the guard flagged the very
  comment explaining this bug. Exactly the failure the original comment warned about ("a comment
  explaining the bug would fail the gate forever, which teaches the next dev to delete the explanation").
  Now stripped with `perl -0pe` over the whole file.

## What the guard still cannot catch

A **correct** threshold rendered against the **wrong run's** score passes every grep. The version-join
half is asserted separately at the API layer by `T-S89-001..004`. Recorded so nobody reads a green
`T-S80-000b` as coverage of this bug.

## Related

`docs/decisions.md` Decision 32 · `docs/design/eval-state-of-play.md` ·
`docs/bugs/unauthenticated-full-table-read-eval-runs-datasets.md` (found in the same grounding pass).

# E-2 Data Model — `Tool.side_effecting` + recorded-call payload shape

**Companion to** `e2/plan.md`. **Docs only.** E-2 slice of the consolidated `eval-v2/data-model.md` §4
(sandbox side-effect handling) + §3.1 (`tools.side_effecting`).

> ⚠️ **Plan status — design stable, specifics indicative.** The `side_effecting` column, the `eval_mode`
> thread, and the recorded-call shape are **stable and reviewable now**. Column/field names, the migration
> number, and the governance-wrapper `file:line` are indicative against the 2026-07-13 tree — re-ground against
> `models.py`, the tool-governance wrapper, and `sdk/agentshield_sdk/` at `tasks.md` mint time.

---

## 0. What exists today (code-truth)

| Object | Today | File |
|---|---|---|
| `Tool` | Platform-managed HTTP or Python tool; has a method/config but **no `side_effecting` flag**. | `models.py:998` |
| tool-governance wrapper | Every tool call flows through OPA + HITL governance (the seam). **Exact single interception point to be located at impl.** | — |
| `RunStep.output` | `JSONB`, free-form — the natural home for `recorded_side_effects[]`. | `models.py:1599` |
| `eval_run_results.eval_detail` | `JSONB` (from E-0) — surfaces recorded calls to the results UI. | E-0 migration |

---

## 1. `tools.side_effecting` (migration ≥ `00NN` — indicative)

| Column | Type | Notes |
|---|---|---|
| `side_effecting` | `BOOLEAN NOT NULL DEFAULT <inferred>` | Backfill: HTTP `POST/PUT/PATCH/DELETE ⇒ true`, `GET/HEAD ⇒ false`; Python tools default `true` (conservative) unless flagged read-only; **overridable** per tool. |

Guarded/idempotent, data-preserving. This is a small orthogonal column — a dependency for scheduled/webhook
eval (E-3/E-4), not a blocker for reactive/durable read-shaped eval.

---

## 2. `eval_mode` (threaded field, not necessarily a column)

`eval_mode ∈ {live, record}`, default `live`. Threaded **eval-runner → run-create → governance wrapper** as an
explicit parameter. Only the batch eval-runner sets `record`; interactive sandbox chat stays `live` (a human
test-firing may want real sandbox side-effects). Persist on the run **only if** the resume/park path needs to
re-read it after a checkpoint — confirm at impl; default is transient-per-request.

> **No-Bandaid:** `eval_mode` is authored/threaded explicitly. It is **not** derived from `context=='playground'`
> — sandbox context alone does not imply record mode.

---

## 3. Recorded-call payload (`run_steps.output.recorded_side_effects[]`)

When the wrapper intercepts a `side_effecting` call under `eval_mode=record`:
```jsonc
{
  "recorded_side_effects": [
    { "tool": "send_email",
      "args": { "to": "compliance@acme.com", "subject": "…" },   // asserted by value; PII-tokenized for display
      "mocked_response": { "status": "ok", "id": "mock-…" },       // what was returned instead of invoking
      "would_have_invoked": "POST https://…/send"                  // the downstream that was NOT called
    }
  ]
}
```

## 4. `expected_side_effects` assertion (consumed by `score_side_effects`)

Authored on scheduled/webhook items (E-3/E-4); shape shared:
```jsonc
{
  "expected_side_effects": [
    { "tool": "send_email",
      "args_match": { "to": "compliance@acme.com" },   // dict-subset
      "occurs": "exactly",                              // exactly | at_least | never
      "count": 1 }
  ],
  "tool_mocks": {                                        // optional per-tool fixed mock (else type-default success)
    "send_email": { "status": "ok" }
  }
}
```

`score_side_effects(recorded, expected)` — per assertion: count matching recorded calls (tool-name +
`args_match` dict-subset), compare to `occurs`/`count`. `never` violated ⇒ `0.0`. Deterministic, no LLM.

---

## 5. Back-compat & orphan-avoidance

- `tools.side_effecting` backfills from method — no manual reclassification needed for existing tools.
- `recorded_side_effects` lives in existing `run_steps.output` JSONB — **no new result column** (surfaces via
  E-0's `eval_detail`).
- `side_effecting` → read by the wrapper; `recorded_side_effects` → read by `score_side_effects` + results UI —
  shipped together, no orphans.
</content>

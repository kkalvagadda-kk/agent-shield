# Eval Slice 0 — Data Model

**No migration.** Slice 0 adds no columns, tables, or indexes. Both new fields on
`PublishRequestResponse` are **derived at read time** from rows that already exist. This is deliberate:
storing a resolved verdict would freeze a threshold that is per-run and overridable, recreating the
staleness the slice removes.

---

## Existing entities relied upon (unchanged)

### `EvalRun` (`services/registry-api/models.py`)

| Field | Type | Notes for this slice |
|---|---|---|
| `id` | UUID PK | surfaced as `last_eval_run_id` |
| `agent_name` | str | today's **only** join key — the bug |
| `agent_version_id` | UUID NULL (`models.py:1516`) | the join key the fix uses |
| `pass_threshold` | float NULL | per-run; NULL only on pre-E-6 rows |
| `overall_score` | float NULL | a **pass rate** (`passed_count/total`), not a mean composite |
| `status` | str | filtered to `"completed"` |
| `completed_at` | datetime NULL | orders "latest" |
| `user_id` | str | the ownership filter that lacks a deny-by-default branch |

### `PublishRequest`

| Field | Type | Notes for this slice |
|---|---|---|
| `id` | UUID PK | **the new map key** (today the map is keyed by `asset_id`) |
| `asset_id` | UUID | agent/workflow/skill id |
| `asset_type` | str | only `"agent"` participates in eval enrichment |
| `source_version_id` | UUID NULL | already on the model **and already on the response** — never consulted by the query |

### `PlaygroundDataset`

| Field | Type | Notes |
|---|---|---|
| `owner_user_id` | str | ownership filter; same missing-`else` defect |

---

## Response contract change

### `PublishRequestResponse` (`services/registry-api/schemas.py`)

Existing: `last_eval_score: Optional[float]`, `last_eval_run_id: Optional[UUID]`,
`source_version_id: Optional[UUID]`.

**Added:**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `last_eval_pass_threshold` | `Optional[float]` | `None` | The bar **that run** used, via `effective_pass_threshold(run)`. `None` only when no eval was resolved. |
| `eval_source` | `Literal["version","agent_latest","none"]` | `"none"` | Where the score came from. |

**Field invariant — enforced by `T-S89-001..003`:**

```
eval_source == "none"          ⟺  last_eval_score IS NULL
                               ∧  last_eval_run_id IS NULL
                               ∧  last_eval_pass_threshold IS NULL

eval_source == "version"       ⟹  the resolved EvalRun.agent_version_id == request.source_version_id

eval_source == "agent_latest"  ⟹  request.source_version_id IS NULL
                               ∧  the resolved run is the agent's most recent completed run
```

The third line is the load-bearing one: `agent_latest` is reachable **only** when the request pins no
version. A request that pins a version with no eval resolves to `"none"` — it must never borrow another
version's score. That is the bug.

---

## Resolution state machine

```
                    ┌──────────────────────────────┐
  PublishRequest ──▶│ source_version_id IS NULL ?  │
                    └──────────────┬───────────────┘
                          yes      │      no
                  ┌────────────────┘      └────────────────┐
                  ▼                                        ▼
      latest completed EvalRun               latest completed EvalRun
      for agent_name                         WHERE agent_version_id = source_version_id
                  │                                        │
          found ──┴── not found                    found ──┴── not found
            │            │                           │            │
            ▼            ▼                           ▼            ▼
      "agent_latest"   "none"                   "version"       "none"
```

**No arrow leads from a pinned version to `agent_latest`.** Today's code is equivalent to collapsing the
whole diagram into the left branch.

---

## Client type mirror

### `PublishRequest` (`studio/src/api/registryApi.ts`, ~:1117)

```typescript
last_eval_score: number | null;
last_eval_run_id: string | null;
last_eval_pass_threshold: number | null;   // NEW
eval_source: "version" | "agent_latest" | "none";   // NEW
```

`last_eval_pass_threshold` is **nullable here but not on `EvalRun`** — and that asymmetry is intentional.
`EvalRun.pass_threshold` is non-optional (`playgroundApi.ts:222`) because the API always resolves it for a
run that exists. A publish request may legitimately have **no run at all**, so its threshold is nullable,
and `verdictOf(score, null)` returns `"unknown"` — fail-closed, never `"pass"`.

---

## Validation rules

| Rule | Where enforced | Test |
|---|---|---|
| `eval_source` is one of three literals | Pydantic `Literal` | schema validation |
| Threshold is the run's own, never 0.7 | `effective_pass_threshold(run)` | `T-S89-004` |
| Pinned version with no eval ⇒ `"none"` | `_resolve_publish_request_evals` | `T-S89-001` |
| Unauthenticated list ⇒ empty | `else: q.where(sa.false())` | `T-S89-005/006` |
| Authenticated list ⇒ own rows only | unchanged `if caller` branch | `T-S89-007` |

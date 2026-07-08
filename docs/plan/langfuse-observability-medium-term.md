# Langfuse ↔ Studio Observability: Medium-Term Implementation Plan

**Status:** Implemented (2026-07-08) — registry-api:0.2.97, studio:0.1.77  
**Prerequisite:** Short-term fix (TraceDrawer inline) — already shipped  
**Scope:** M1–M6 from `docs/design/todo/langfuse-studio-integration.md`

---

## Already Implemented (verified in codebase)

| Item | Status | Evidence |
|---|---|---|
| TraceDrawer (short-term) | **DONE** | `studio/src/components/playground/TraceDrawer.tsx` — fetches via `getTraceById`, renders span tree |
| M3: Eval trace deep-link | **DONE** | `EvalResultsPage.tsx:376-397` — renders external link OR inline TraceDrawer button per eval item; `EvalRunResultResponse.langfuse_trace_id` + `trace_url` wired |
| M5 partial: Catalog production runs | **DONE** | `CatalogDetailPage.tsx:552` — RunsTab shows runs with trace_url external links |
| `judge_score` column | **EXISTS** | `PlaygroundRun.judge_score` (models.py:1219), `EvalRunResult.judge_score` (models.py:1304). `agent_runs` table does NOT have it yet — migration needed only there |
| Langfuse proxy endpoint | **DONE** | `GET /playground/runs/{id}/trace` + `GET /playground/traces/{traceId}` |
| `fetch_trace_cost` helper | **DONE** | `tracing.py:178` |

**What's NOT built yet:** M1 (traces list page), M2 (dashboard), M4 (safety scan visibility), M5 completion (inline TraceDrawer in catalog + observability context filter), M6 (trace comparison), and the shared `/observability` nav + router.

---

## Current State (What Exists)

| Layer | What Works |
|---|---|
| Langfuse store | v3.201.1 deployed internally; traces for playground runs, eval runs, safety scans, platform actions |
| Backend proxy | `GET /playground/runs/{id}/trace` — fetches full trace via service creds, returns JSON |
| Backend proxy | `GET /playground/traces/{traceId}` — direct trace lookup by ID |
| Studio UI | `TraceDrawer.tsx` — slide-out panel rendering span tree with collapsible observations |
| Data model | `agent_runs.langfuse_trace_id` + `cost_usd` + `prompt_tokens` + `completion_tokens` + `latency_ms` |
| Tracing module | `tracing.py` — `trace_create_run`, `trace_complete_run`, `trace_judge_score`, `fetch_trace_cost`, `trace_platform_action` |
| Langfuse SDK | `langfuse==2.*` (supports `fetch_traces`, `fetch_trace`, scores API) |

**Key constraint:** All user access flows through registry-api (JWT-authenticated). Langfuse service creds never reach the browser. No direct Langfuse UI links exposed.

---

## M1: Traces List Page — `/observability/traces`

### User Experience

1. User clicks "Observability" in Studio sidebar nav
2. Sees a filterable table: **Agent** | **Status** | **Duration** | **Score** | **Timestamp**
3. Filters: agent dropdown, status dropdown, date range picker, search by trace ID
4. Click any row → opens existing `TraceDrawer` with full span tree
5. Pagination: 20 per page, server-side cursor

### E2E Flow

```
Studio (browser)                     registry-api                    Langfuse
─────────────────                    ────────────                    ────────
GET /observability/traces            
  ?agent_name=foo                    
  &status=completed                  
  &from=2026-07-01                   
  &limit=20&cursor=abc               
                                     → resolve JWT sub → team
                                     → query agent_runs WHERE team=X
                                       AND filters match
                                     → for each: attach trace_url
                                     ← return [{id, agent_name, status,
                                        duration_ms, judge_score,
                                        started_at, trace_id}]
                                     
Click row → GET /playground/traces/{traceId}
                                     → Langfuse GET /api/public/traces/{id}
                                     ← full trace + observations
                                     
Render TraceDrawer (existing)
```

### Data Architecture: Two-Tier Model

The Traces UX has two layers with fundamentally different data sources:

```
┌─────────────────────────────────────────────────────────────────┐
│ TIER 1: Traces LIST (table view)                                │
│ Source: Platform DB (agent_runs / playground_runs)               │
│ Contains: run-level summary only                                │
│   → agent_name, status, total latency_ms, cost_usd, started_at │
│   → NO span/step data — DB has zero knowledge of what happened  │
│     inside the run                                              │
│ Why DB: fast, team-scoped, paginated, no API rate limits        │
├─────────────────────────────────────────────────────────────────┤
│ TIER 2: Trace DETAIL (span tree — click a row)                  │
│ Source: Langfuse API (ALWAYS — no alternative)                  │
│ Contains: full observation tree                                 │
│   → each tool call: name, input, output, start/end time         │
│   → each safety scan: risk_score, blocked, scanner name         │
│   → each LLM generation: model, prompt/completion tokens, cost  │
│   → parent-child span relationships                             │
│ Fetch path: registry-api → Langfuse GET /api/public/traces/{id} │
│   with Basic auth (service creds) → return to Studio            │
│ Rendered by: TraceDrawer component (existing)                   │
└─────────────────────────────────────────────────────────────────┘
```

**Critical:** The platform DB does NOT store observations/spans. It never will — Langfuse is the span store. Every "View Trace" click, every span tree render, every input/output/per-step-latency display MUST fetch from Langfuse via the proxy endpoint. There is no DB fallback for this data.

**Existing proxy endpoint (already works):**
```
GET /api/v1/playground/traces/{traceId}
  → Langfuse GET /api/public/traces/{traceId} (Basic auth)
  → returns {trace_id, trace_url, langfuse: {observations: [{name, type, startTime, endTime, input, output}]}}
```

### Why the LIST queries platform DB (not Langfuse)

- Langfuse `GET /api/public/traces` paginates at 100, limited filtering, slow for large datasets
- `agent_runs` already has `agent_name`, `status`, `latency_ms`, `cost_usd`, `langfuse_trace_id` — all summary columns
- Team scoping done at SQL level (fast, correct, no cross-team data leaks)
- Langfuse is called per-trace when user clicks a row (detail on demand)

### Backend

**New file:** `services/registry-api/routers/observability.py`

```python
# Endpoints:
GET /api/v1/observability/traces
  Query params: agent_name, status, trigger_type, context (playground|production|all),
                from_date, to_date, limit (default 20), cursor
  Auth: require_user → resolve team
  Query: UNION of playground_runs + agent_runs (both team-scoped)
  Returns: list of TraceSummary objects (run-level only — no Langfuse call)

GET /api/v1/observability/traces/{trace_id}
  # Fetches FULL trace from Langfuse (observations/spans/input/output)
  # Reuses existing proxy logic from playground router
  Auth: require_user → verify team owns the agent
  Returns: full Langfuse trace + observations array
  THIS IS THE ONLY WAY TO GET SPAN-LEVEL DATA
```

**Query strategy for the LIST endpoint:**

The platform has TWO run tables:
- `playground_runs` — interactive chat sessions (sandbox playground)
- `agent_runs` — scheduled/workflow/production/API-triggered runs

A unified traces list must query both:

```sql
-- Option 1: UNION (clean, one query)
SELECT id, agent_name, status, latency_ms, cost_usd, started_at,
       langfuse_trace_id, 'playground' as context, trigger_type
FROM playground_runs WHERE owner_user_id IN (team_members)
UNION ALL
SELECT id, agent_name, status, latency_ms, cost_usd, started_at,
       langfuse_trace_id, context, trigger_type
FROM agent_runs WHERE team = :team
ORDER BY started_at DESC
LIMIT :limit OFFSET :offset;

-- Option 2: Query separately, merge in Python (simpler pagination)
```

Recommended: Option 2 for MVP (separate queries, merge in app layer). UNION pagination with cursor is complex. Merge last 20 from each, sort by started_at, return top N.

```python
GET /api/v1/observability/traces/{trace_id}
  # THIS endpoint calls Langfuse — returns observations (per-step data)
  # Without this call, there is NO way to see tool calls, safety scans,
  # LLM generations, input/output per step, or per-step latency.
  # The platform DB only knows "run took 1200ms total" — not WHERE those ms went.
```

**Schema:**
```python
class TraceSummary(BaseModel):
    id: str              # agent_run.id
    agent_name: str
    status: str
    trigger_type: str | None
    context: str         # playground | production
    latency_ms: int | None
    cost_usd: float | None
    judge_score: float | None  # from langfuse score, cached in agent_runs? or fetched?
    started_at: datetime
    trace_id: str | None # langfuse_trace_id
    run_by: str | None
```

### Frontend

**New file:** `studio/src/pages/ObservabilityTracesPage.tsx`
**New file:** `studio/src/api/observabilityApi.ts`

Components:
- Filter bar (agent selector from team's agents, status pills, date range)
- Table with sortable columns
- Cursor-based pagination (Next/Prev buttons)
- Row click → `<TraceDrawer traceId={...} />`

Route: `/observability/traces` — add to `App.tsx` + sidebar nav

### Integration Points

- Reuses existing `TraceDrawer` component (zero new Langfuse rendering code)
- Shares `require_user` + team resolution pattern
- Agent list for filter dropdown: reuse `GET /agents` (already team-scoped)

### Testing

- Backend e2e: `scripts/e2e/suite-NN-observability.sh` — create run, verify it appears in traces list, verify team isolation
- Vitest: `ObservabilityTracesPage.test.tsx` — renders table, filters work, pagination works, row click opens drawer
- Playwright: navigate to /observability/traces → see table → click row → drawer opens

---

## M2: Latency & Score Dashboard — `/observability/dashboard`

### User Experience

1. User clicks "Dashboard" sub-tab within Observability
2. Sees 4 panels:
   - **P50/P95 latency** — line chart over time (last 7d/30d/custom)
   - **Judge score distribution** — histogram (0.0–1.0 buckets)
   - **User feedback ratio** — donut chart (thumbs up vs down)
   - **Safety block rate** — bar chart per agent
3. Agent filter: view all or specific agent
4. Time range selector: 7d (default), 30d, custom

### Architecture Decision: What Can the Dashboard Show?

**From platform DB (fast, already stored):**
- P50/P95 total run latency (from `latency_ms` column)
- Run status distribution (completed/failed/blocked counts)
- Judge score distribution (from `judge_score` — exists on `playground_runs`, needs migration for `agent_runs`)
- Run volume over time (count by hour/day)

**NOT available from DB — lives ONLY in Langfuse observations:**
- Per-tool latency breakdown ("search_docs averages 890ms")
- Token usage trends (prompt/completion tokens per model)
- Per-generation cost breakdown (which LLM call cost most)
- Tool call frequency (which tools fire most often)
- Safety scan hit rate by scanner type

**Options for per-tool/per-step dashboard panels:**

| Option | Approach | Tradeoff |
|---|---|---|
| A: Skip | Dashboard shows only run-level metrics. Per-tool analysis = click into individual traces. | Simple. No new infra. But "why is my agent slow?" requires manual trace hunting. |
| B: Materialize on completion | When run finishes, fetch trace from Langfuse, extract observation summaries → write to `run_observations` table | Enables fast per-tool dashboards. Adds ~1-2s to completion path. Requires new table + background job. |
| C: Batch aggregate nightly | Background job fetches recent traces from Langfuse in bulk, aggregates into metrics table | No latency on hot path. But stale (up to 24h lag). Langfuse API rate limits may throttle. |
| D: On-demand (user loads dashboard) | Dashboard backend fetches last 50 traces from Langfuse, aggregates in-memory | Fresh data. But SLOW (50 API calls × 200ms each = 10s load). Rate limited. |

**Recommended: A for MVP, then B for v2.**

MVP dashboard shows run-level metrics only (latency P50/P95, status, judge scores, volume). This is useful, fast, and requires no new data pipeline. Per-tool insights come from clicking individual traces in the Traces list.

V2 adds Option B: materialize observation summaries on run completion. New table `observation_summaries(run_id, observation_name, type, duration_ms, token_count, created_at)`. Enables "slowest tools" and "token usage by model" panels.

**Cost/token backfill gap:** `cost_usd` and `prompt_tokens` columns in `agent_runs` are almost always NULL today because `LangfuseCallbackHandler` isn't wired into graph invocation. Fix: activate `fetch_trace_cost` (already exists in tracing.py:178) on run completion. This backfills `cost_usd` from Langfuse's computed cost. For tokens: fetch trace, sum generation observation tokens, write back.

### E2E Flow

```
Studio                               registry-api                    DB
─────                                ────────────                    ──
GET /observability/dashboard         
  ?agent_name=foo&period=7d          → SQL aggregation on agent_runs
                                       GROUP BY date_trunc('hour', started_at)
                                     ← {latency_p50, latency_p95, scores_histogram,
                                        feedback_counts, safety_block_counts}
```

### Backend

**Extend:** `services/registry-api/routers/observability.py`

```python
GET /api/v1/observability/dashboard
  Query params: agent_name (optional), period (7d|30d|custom), from_date, to_date
  Auth: require_user → team
  Returns: DashboardData

class DashboardData(BaseModel):
    latency_series: list[TimeseriesPoint]     # [{timestamp, p50, p95}]
    score_histogram: list[HistogramBucket]    # [{bucket: "0.0-0.1", count: N}]
    feedback_summary: FeedbackSummary         # {positive: N, negative: N}
    safety_blocks: list[AgentBlockRate]       # [{agent_name, total_runs, blocked_runs}]
    cost_series: list[TimeseriesPoint]        # [{timestamp, total_usd}]
```

SQL patterns:
```sql
-- Latency percentiles by hour
SELECT date_trunc('hour', started_at) AS ts,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
FROM agent_runs
WHERE team = :team AND started_at >= :from
GROUP BY 1 ORDER BY 1;

-- Score distribution
SELECT width_bucket(judge_score, 0, 1, 10) AS bucket, count(*)
FROM agent_runs
WHERE team = :team AND judge_score IS NOT NULL AND started_at >= :from
GROUP BY 1;
```

### Frontend

**New file:** `studio/src/pages/ObservabilityDashboardPage.tsx`

Chart library: **Recharts** (already in common use with React, tree-shakeable, MIT).

Components:
- `LatencyChart` — line chart (P50/P95)
- `ScoreHistogram` — bar chart
- `FeedbackDonut` — pie/donut
- `SafetyBlocksChart` — horizontal bar per agent
- `CostChart` — area chart (cumulative or per-period)

### Data Pipeline for `judge_score` Column

**Situation:** `judge_score` already exists on `PlaygroundRun` (models.py:1219) and `EvalRunResult` (models.py:1304). The `agent_runs` table (used for scheduled/workflow/production runs) does NOT have it.

**Options:**
- **Option A — Add `judge_score` to `agent_runs`** (new alembic migration). Simpler query surface — dashboard queries ONE table for all context types. Backfill hook writes to `agent_runs.judge_score` when judge completes.
- **Option B — UNION across tables.** Dashboard SQL unions `playground_runs` + `agent_runs`. More complex, no migration needed, but the query logic splits.

**Recommended:** Option A. One migration, one query surface. `agent_runs` is the canonical production runs table and should have full observability columns.

**Migration:**
```sql
ALTER TABLE agent_runs ADD COLUMN judge_score FLOAT NULL;
```

**Backfill hook:** In `judge.py` after `trace_judge_score` call, also update `agent_runs.judge_score`:

```python
async def backfill_judge_score(run_id: str, score: float, db: AsyncSession):
    await db.execute(
        update(AgentRun).where(AgentRun.id == run_id)
        .values(judge_score=score)
    )
```

**For playground runs:** `PlaygroundRun.judge_score` is already populated by `judge.py`. Dashboard queries this table for playground context.

### Testing

- Backend: SQL aggregation test with known fixture data
- Vitest: chart components render with mock data, period selector works
- Playwright: navigate to dashboard → verify charts render → change period → charts update

---

## M3: Eval Results Deep-Linking to Traces — ALREADY SHIPPED

> **No work needed.** Verified in codebase:
> - `EvalRunResult` model has `langfuse_trace_id` column (models.py:1308)
> - `EvalRunResultResponse` schema exposes `langfuse_trace_id` + `trace_url` (schemas.py:958-959)
> - `EvalResultsPage.tsx:376-397` renders:
>   - External "Trace" link when `trace_url` is populated
>   - Inline "Trace" button (opens `TraceDrawer`) when only `langfuse_trace_id` exists
> - `TraceDrawer` imported at line 26, rendered at line 319
>
> **Remaining polish (optional):** If `trace_url` points to Langfuse UI (which returns UNAUTHORIZED for normal users), prioritize the inline TraceDrawer path. Currently external link takes precedence (line 376 checks `trace_url` first). Consider swapping priority: always open TraceDrawer, show external link as secondary for admins.

---

## M4: Safety Scan Visibility

### User Experience

1. When a message is **blocked** by safety scan in playground:
   - Instead of generic "Message blocked" → show expandable "Safety Details" section
   - Shows: which scanner flagged it, risk score, reason (redacted if sensitive)
2. In `TraceDrawer` (for any run):
   - Safety scan spans are highlighted with a shield icon
   - Risk scores shown inline next to span name
   - Blocked scans get red highlight

### E2E Flow

```
ChatPane (message blocked)
  → SSE event: {event: "safety_blocked", data: {scanners: [{name, risk_score, reason, blocked}]}}
  → render inline SafetyDetails component below the message

TraceDrawer (post-hoc)
  → observations include safety-scan spans (already in Langfuse)
  → TraceDrawer renders them with risk metadata from observation.metadata
```

### Backend Changes

**File:** `services/registry-api/routers/playground.py` (SSE stream)

When safety orchestrator returns a block, emit structured SSE event:
```python
# Currently emits: {"event": "error", "data": "Safety block: ..."}
# Change to:
yield sse_event("safety_blocked", {
    "scanners": [
        {"name": "presidio", "risk_score": 0.92, "reason": "PII detected", "blocked": True},
        {"name": "llm_guard", "risk_score": 0.15, "reason": None, "blocked": False},
    ],
    "overall_blocked": True,
})
```

**Note:** Safety orchestrator already returns per-scanner results — just need to propagate them through SSE instead of flattening to a string.

### Frontend Changes

**New component:** `studio/src/components/playground/SafetyDetails.tsx`

```tsx
// Expandable panel shown below a blocked message
interface SafetyResult {
  name: string;
  risk_score: number;
  reason: string | null;
  blocked: boolean;
}

function SafetyDetails({ scanners }: { scanners: SafetyResult[] }) {
  // Shield icon + "Blocked by safety scan"
  // Expandable: per-scanner rows with score bars and reasons
}
```

**File:** `studio/src/components/playground/ChatPane.tsx`

Handle `safety_blocked` SSE event type:
- Show message as blocked state
- Render `<SafetyDetails>` below it

**File:** `studio/src/components/playground/TraceDrawer.tsx`

Enhance `SpanRow` rendering:
- If observation name matches `safety-scan-*` → show shield icon + risk score badge
- If observation metadata has `blocked: true` → red border

### Testing

- Vitest: SafetyDetails renders scanner list, shows risk bars, hides reason when null
- Vitest: ChatPane handles safety_blocked SSE event correctly
- Playwright: trigger a known-blocked input → verify safety details panel appears
- Backend e2e: verify safety_blocked SSE event structure

---

## M5: Production Chat Observability — PARTIALLY DONE

### What's Already Built

- `CatalogDetailPage.tsx` RunsTab (line 552) shows production runs: agent name, status, trigger, started, latency, cost
- Each run shows `trace_url` as external link (line 586)
- Backend `GET /api/v1/catalog/{id}/runs` returns runs with `langfuse_trace_id`

### What's Missing

1. **Inline TraceDrawer** — currently only shows external Langfuse link (which returns UNAUTHORIZED). Need to add TraceDrawer button like EvalResultsPage does.
2. **Context filter on Observability page** — once M1 ships, add `context=production` filter
3. **Status summary** at top of Runs tab (completed/failed/blocked counts)

### Remaining Work

**File:** `studio/src/pages/CatalogDetailPage.tsx` — RunsTab

Replace external-only link with inline TraceDrawer (same dual-path as EvalResultsPage):
```tsx
// Import TraceDrawer
import TraceDrawer from "../components/playground/TraceDrawer";

// In RunsTab:
const [traceId, setTraceId] = useState<string | null>(null);

// Per row: click opens drawer instead of external link
{r.langfuse_trace_id && (
  <button onClick={() => setTraceId(r.langfuse_trace_id)}>
    <Eye size={12} /> Trace
  </button>
)}

// At bottom of component:
{traceId && <TraceDrawer traceId={traceId} onClose={() => setTraceId(null)} />}
```

**File:** `studio/src/pages/ObservabilityTracesPage.tsx` (from M1)

Add "Context" toggle (Playground | Production | All) — pass as query param.

### Testing

- Playwright: catalog detail → runs tab → click trace button → drawer opens with observations
- Vitest: RunsTab renders trace button, opens drawer on click

---

## M6: Trace Comparison

### User Experience

1. On Observability Traces page, user selects 2 traces via checkboxes
2. "Compare" button appears in toolbar
3. Click → side-by-side view:
   - Left: Trace A span tree | Right: Trace B span tree
   - Diff highlights: new spans (green), removed spans (red), latency changes (yellow)
   - Summary bar: Trace A latency vs B, score delta, cost delta

### E2E Flow

```
ObservabilityTracesPage
  → user checks 2 rows
  → "Compare" button enabled
  → click → navigate to /observability/compare?a={traceId1}&b={traceId2}
  → page fetches both traces in parallel
  → renders side-by-side diff

GET /playground/traces/{traceIdA}  ─┐
GET /playground/traces/{traceIdB}  ─┤ parallel
                                    └→ render CompareView
```

### Frontend

**New file:** `studio/src/pages/ObservabilityComparePage.tsx`

Layout:
```
┌──────────────────────────────────────────────────────┐
│ Compare: abc123 vs def456                    [Close] │
├─────────── Summary ──────────────────────────────────┤
│ Latency:  1.2s → 0.8s (↓33%)                        │
│ Score:    0.7 → 0.9 (↑0.2)                          │
│ Cost:     $0.012 → $0.008 (↓33%)                    │
├──────────────────┬───────────────────────────────────┤
│ Trace A          │ Trace B                           │
│ ▼ safety-scan    │ ▼ safety-scan                     │
│   120ms          │   95ms         ← faster (green)   │
│ ▼ tool_call      │ ▼ tool_call                       │
│   890ms          │   — (removed)  ← red              │
│                  │ ▼ cache_hit (new) ← green         │
└──────────────────┴───────────────────────────────────┘
```

Diff algorithm:
- Match observations by `name` (exact match)
- Unmatched in A = "removed" (red)
- Unmatched in B = "added" (green)
- Matched but latency changed >20% = "changed" (yellow)

### Backend

No new endpoints needed — reuses existing `GET /playground/traces/{id}` twice in parallel.

### Testing

- Vitest: CompareView renders diff highlights correctly, handles missing observations
- Playwright: select 2 traces → compare button → side-by-side renders

---

## Implementation Priority & Dependencies

```
M3 (eval deep-link)  ─── ✅ ALREADY DONE ────────────────────→ No work needed
     
M5 partial (inline TraceDrawer in catalog) ── 0.5 day ───────→ Ship first (quick win)
     │
M1 (traces list)     ─── foundation for M5 filter, M6 ───────→ Ship second
     │
     ├── M5 complete (context filter) ─── needs M1 ───────────→ After M1
     │
     └── M6 (trace comparison) ─── needs M1 selection UI ─────→ After M1
     
M4 (safety visibility) ─── independent, needs backend SSE ────→ Parallel with M1

M2 (dashboard)       ─── needs judge_score migration + chart lib ─→ After M1
```

### Recommended order:

| Sprint | Item | Effort | Depends On |
|---|---|---|---|
| — | ~~M3: Eval trace deep-link~~ | **DONE** | — |
| 1 | M5 partial: Inline TraceDrawer in CatalogDetailPage | 0.5 day | Nothing |
| 1 | M4: Safety scan visibility | 2–3 days | Safety orchestrator returns structured data |
| 2 | M1: Traces list page + router | 3–4 days | Nothing |
| 2 | M5 complete: Context filter on M1 page | 0.5 day | M1 |
| 3 | M2: Dashboard + metrics | 5–7 days | M1 (nav structure), Recharts install, migration |
| 4 | M6: Trace comparison | 3–4 days | M1 (selection UI) |

---

## Shared Infrastructure Needed

### New router registration

```python
# services/registry-api/main.py
from routers import observability
app.include_router(observability.router, prefix="/api/v1/observability", tags=["observability"])
```

### New Studio nav entry

```tsx
// App.tsx — sidebar
{ path: "/observability/traces", label: "Observability", icon: Activity }
// Sub-nav: Traces | Dashboard | Compare
```

### New npm dependency

```json
"recharts": "^2.12.0"  // for M2 dashboard charts only
```

### Migration (for M2)

`judge_score` already exists on `playground_runs` and `eval_run_results`. Only `agent_runs` needs it:

```sql
ALTER TABLE agent_runs ADD COLUMN judge_score FLOAT NULL;
CREATE INDEX ix_agent_runs_judge_score ON agent_runs(judge_score) WHERE judge_score IS NOT NULL;
```

---

## Security & Auth Model

All endpoints follow existing pattern:
- JWT required via `require_user` dependency
- Team resolved from `user_team_assignments`
- Queries scoped to team's agents only (no cross-team data leaks)
- Langfuse creds NEVER reach the browser
- `trace_url` field (external Langfuse link) still included for admin users who DO have Langfuse access, but the primary UX is the inline TraceDrawer

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Langfuse API latency on trace fetch (>1s) | Show spinner in TraceDrawer; cache trace data in React Query (5min stale time); M1 table never calls Langfuse (uses agent_runs) |
| Large trace payloads (100+ observations) | Paginate observations in TraceDrawer; collapse by default; lazy-load input/output |
| Dashboard query performance on large agent_runs table | Indexed on `started_at`, `agent_name`, `team`; use `date_trunc` aggregation; consider materialized view if >1M rows |
| Safety orchestrator not returning structured results | Gate M4 on verifying safety-orchestrator response format; fall back to string error if structured data absent |
| Chart library bundle size | Recharts is tree-shakeable; import only needed chart types; lazy-load dashboard page |

---

## Success Criteria

| Feature | User can... |
|---|---|
| M1 | Browse all their team's traces without knowing specific run IDs |
| M2 | See latency trends and score distributions at a glance |
| M3 | Debug a failing eval item by seeing exactly what the agent did |
| M4 | Understand WHY a message was blocked (which scanner, what risk) |
| M5 | Monitor production agent health with same trace tooling as playground |
| M6 | Compare before/after a prompt change to verify improvement |

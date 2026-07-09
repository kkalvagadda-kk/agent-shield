# Langfuse ↔ Studio Integration: Roadmap & Short-Term Fix

## Problem Statement

Langfuse stores rich observability data (traces, spans, scores) for every agent run, safety scan, eval, and approval — but users cannot access it. The "View Trace" link in Studio points directly to the Langfuse UI, which returns UNAUTHORIZED because users don't have Langfuse service credentials.

This violates the platform auth model: **all user access must flow through registry-api (JWT-authenticated), which holds Langfuse service keys internally.**

---

## What Langfuse Holds Today

| Trace Type | Name Pattern | Contains |
|---|---|---|
| Playground/Chat run | `agent-run.playground` / `agent-run.production` | Input message, output response, agent name, user_id |
| Agent execution spans | `agent.{name}` | Tool calls, safety scans, durations |
| Safety scans | `safety-scan-input/output` | Per-scanner risk scores, blocked/allowed, latency |
| Eval runs | `eval-run` + `eval-item-{N}` spans | Per-item scores, overall pass/fail |
| Platform actions | `platform.approval.{decision}` | HITL reviewer, tool, context |
| **Scores** | `llm-judge` (0–1), `user-feedback` (+1/-1) | Attached to run traces |

Key fact: every `trace_id` in Langfuse == the platform's own `run_id` / `eval_run_id` / `approval_id` UUID. Direct lookup, no mapping needed.

---

## Short-Term Fix (Minimal, 1–2 days)

### Goal: Make "View Trace" work without exposing Langfuse credentials or UI.

The proxy endpoint already exists: `GET /api/v1/playground/runs/{run_id}/trace` fetches the full trace from Langfuse using service creds and returns it as JSON. Studio fetches it but only uses `trace_url` (the broken external link).

**Changes:**

#### Backend (zero changes needed)
The endpoint already returns the full trace under the `langfuse` key. No backend work required.

#### Frontend: Inline Trace Drawer

Replace the external link with a slide-out drawer that renders the already-fetched data:

```
ChatPane.tsx
  Before: <a href={traceUrl}>View Trace ↗</a>
  After:  <button onClick={openTraceDrawer}>View Trace</button>

TraceDrawer.tsx (new, ~150 lines)
  - Fetches: GET /playground/runs/{id}/trace (already wired in playgroundApi.ts)
  - Renders:
    ┌─────────────────────────────────┐
    │ Trace: agent-run.playground     │
    │ Duration: 2.3s                  │
    │ Status: completed               │
    ├─────────────────────────────────┤
    │ ▼ safety-scan-input      120ms  │
    │   ├ presidio              45ms  │
    │   └ llm_guard             72ms  │
    │ ▼ tool_call: search_docs 890ms  │
    │ ▼ safety-scan-output      95ms  │
    ├─────────────────────────────────┤
    │ Scores                          │
    │   llm-judge: 0.85 ✓            │
    │   user-feedback: +1 👍          │
    └─────────────────────────────────┘
```

- Spans rendered as collapsible tree (name + duration bar)
- Click span → show input/output JSON
- Scores section at bottom
- No Langfuse URL exposed to browser

**That's it.** Remove the `<a>` tag, add a drawer. User gets full observability without touching Langfuse.

---

## Medium-Term Improvements (1–2 weeks each)

### M1: Traces List Page — `/observability/traces`

**Value:** Browse all traces for your team's agents. Currently you can only see a trace if you know the specific run.

**Implementation:**
- New endpoint: `GET /api/v1/observability/traces?agent_id=&status=&limit=20&offset=0`
- Backend calls Langfuse `GET /api/public/traces?tags=agent_name&limit=N` with service creds
- Team-scoped: resolve JWT sub → team → filter by agent ownership
- Studio page: sortable table (agent, timestamp, duration, status, score)
- Click row → opens TraceDrawer with full span tree

### M2: Latency & Score Dashboard — `/observability/dashboard`

**Value:** Aggregate metrics — not just individual traces. Answer "is my agent getting slower?" or "are judge scores trending down?"

**Data source options:**
- **Option A — Query Langfuse API** (`GET /api/public/traces` with date filters, aggregate client-side). Simple but limited (Langfuse paginates at 100).
- **Option B — Langfuse Datasets/Sessions API** for batch aggregation.
- **Option C — Materialize in platform DB.** On each `trace_complete_run`, store `{agent_id, duration_ms, judge_score, timestamp}` in a lightweight `agent_metrics` table. Query locally for dashboards. Most flexible, decouples from Langfuse API limits.

**Recommended:** Option C. Keep Langfuse as the detailed trace store; use platform DB for aggregates.

**Dashboard panels:**
- P50/P95 latency over time (line chart)
- Judge score distribution (histogram)
- User feedback ratio (thumbs up vs down)
- Safety block rate by agent
- Tool call frequency and latency per tool

### M3: Eval Results Deep-Linking to Traces

**Value:** From `EvalResultsPage`, click any dataset item → see the full execution trace (what the agent did, which tools it called, where it went wrong).

**Implementation:**
- `EvalRunResult` already stores `langfuse_trace_id` per item
- Add "View Trace" button per row in eval results table
- Opens same TraceDrawer, passing the item's trace_id
- Enables debugging eval failures: "item 7 scored 0.2 — what happened?" → see spans

### M4: Safety Scan Visibility

**Value:** Users currently can't see WHY a message was blocked or what risk was detected.

**Implementation:**
- Safety scan spans already in Langfuse with `risk_score`, `reason`, `blocked` per scanner
- When a run is blocked, show a "Safety Details" expandable in ChatPane:
  - Which scanner flagged it
  - Risk score
  - Reason (redacted if contains PII)
- Pull from same trace endpoint — safety spans are children of the run trace

### M5: Production Chat Observability

**Value:** Monitor deployed agents in production. Currently only playground runs are visible in Studio.

**Implementation:**
- New endpoint: `GET /api/v1/agents/{name}/runs?env=production&limit=50`
- Surfaces `agent-run.production` traces
- Table: timestamp, user (anonymized), duration, score, status
- Click → TraceDrawer
- Useful for: "my deployed agent is slow" or "users are giving thumbs-down"

### M6: Trace Comparison

**Value:** Compare two runs side-by-side (e.g., before/after a prompt change).

**Implementation:**
- Select two traces from list page
- Side-by-side span trees
- Highlight differences: new spans, removed spans, latency changes, score delta
- Useful for evaluating prompt iterations

---

## Long-Term Vision (Quarter+)

### L1: Real-Time Trace Streaming

Instead of polling after run completes, stream span events to Studio as they happen (via SSE from registry-api, which polls or webhooks from Langfuse). User sees the trace tree build live while the agent executes.

### L2: Custom Dashboards per Agent

Let users define which metrics they care about per agent. Configurable dashboard with saved views. Similar to Grafana dashboard builder but scoped to agent observability.

### L3: Alerting on Trace Anomalies

When latency spikes, judge scores drop, or safety blocks increase — push notification to agent owner. Builds on M2 (metrics table) + platform scheduler.

### L4: Cost Tracking

Langfuse `Generation` objects (if LLM callbacks are wired) include token counts. Surface per-agent cost breakdown: tokens in/out, model used, cost estimate. Requires adding LangChain Langfuse callback handler to SDK.

### L5: Trace-Based Regression Testing

Auto-capture production traces as "golden runs". On agent update, replay inputs and compare outputs/scores to the golden baseline. Automated regression without manual dataset creation.

---

## Priority Recommendation

| Priority | Item | Effort | Impact |
|---|---|---|---|
| **NOW** | Short-term fix (TraceDrawer) | 1–2 days | Unblocks all trace access |
| **Next** | M3: Eval trace deep-link | 1 day | Makes eval debugging useful |
| **Next** | M1: Traces list page | 3–4 days | Browsable observability |
| **Next** | M4: Safety scan visibility | 2 days | Users understand blocks |
| **Later** | M2: Dashboard | 1–2 weeks | Aggregate trends |
| **Later** | M5: Production observability | 1 week | Monitor deployed agents |
| **Future** | L1–L5 | Quarter+ | Advanced platform features |

---

## Architecture Principle

```
┌─────────┐     JWT      ┌──────────────┐   service creds   ┌──────────┐
│  Studio  │ ──────────► │ registry-api  │ ────────────────► │ Langfuse │
│ (browser)│             │  (proxy)      │                   │ (store)  │
└─────────┘             └──────────────┘                   └──────────┘
                               │
                    Team scoping + filtering
                    Never expose LF creds to browser
                    Never link to LF UI directly
```

Langfuse = internal trace store. Studio = user-facing observability surface. Registry-api = authenticated proxy between them.

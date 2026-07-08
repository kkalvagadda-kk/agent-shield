# Eval Results UX + Publish Lifecycle

## Problem

After an eval run completes, the user hits a dead end:
- Results are truncated (100 chars), no expected output shown, no drill-down into failures
- No guidance on what to do next — no "mark passed", no "publish", no "re-run"
- `eval_passed` on versions is never set automatically from eval results
- Admin publish queue shows zero eval evidence — approvals are blind
- No link to execution traces — user can't debug WHY a score is low
- Eval runs and workflow runs have no trace visibility

This makes the eval pipeline feel incomplete despite working mechanically.

## Design Reference

`docs/design/langfuse-studio-integration.md` covers trace visibility in detail. Relevant sections:
- **Short-term:** TraceDrawer component (inline span tree, replaces broken external Langfuse link)
- **M3:** Eval results → "View Trace" per row using `langfuse_trace_id` (already stored in EvalRunResult)
- **M1:** Traces List Page (`/observability/traces`) for browsing all runs

This plan implements the eval-specific parts (M3 + EvalResultsPage improvements) plus post-eval publish lifecycle.

---

## Changes

### 1. Add `expected_output` to eval results

**Backend:**
- Migration 0034: add `expected_output TEXT` to `eval_run_results` table
- `EvalRunResult` model: add `expected_output: Mapped[str | None]`
- `EvalRunResultCreate` + `EvalRunResultResponse` schemas: add `expected_output`
- `eval-runner/main.py`: include `"expected_output": expected` in the result_body POST (~line 161)

**Frontend:**
- `playgroundApi.ts`: add `expected_output: string | null` to `EvalRunResult` interface
- `EvalResultsPage.tsx`: add "Expected" column to results table

---

### 2. Expandable rows + failed filter

**EvalResultsPage.tsx:**
- **Expandable rows:** Click row → expand below to show full Input, Expected Output, Response, and Reasoning (no truncation). `useState<string|null>` for expanded row ID.
- **Filter toggle:** "Show failed only" pill/button. Boolean state, filters `results.filter(r => r.passed === false)`.
- **Score color coding:** Score cell background — red (<0.4), amber (0.4–0.7), green (>0.7).

---

### 3. Action CTAs after eval completion

Action bar below summary metrics when `run.status === "completed"`:

| Button | Condition | Action |
|--------|-----------|--------|
| **Mark Version Passed** (green) | `overall_score >= 0.7` AND version not already marked | `PATCH /agents/{name}/versions/{id}` with `{eval_passed: true}` |
| **Re-run Eval** (outline) | Always | `createEvalRun(same params)` → navigate to new results page |
| **Back to Agent** (link) | Always | Navigate to `/agents/{run.agent_name}` |
| **Publish Agent** (primary) | `eval_passed === true` on version | Call existing `publishAgent()` flow |

---

### 4. Auto-set `eval_passed` from eval results

In `eval_runner.py`, when eval-runner POSTs the status-update marking the run `completed`:
- If `overall_score >= threshold` AND `agent_version_id` is set on the EvalRun:
  - Set `version.eval_passed = True` in same transaction
  - Deploy-controller production gate now passes without manual PATCH

**Threshold:** `EVAL_PASS_THRESHOLD` env var (default `0.7`).

---

### 5. Admin publish queue — show eval evidence

**AdminPublishRequestsPage.tsx:**
- Add "Last Eval" column: score badge (green/amber/red), date, link to results page
- If no eval exists: "No eval" amber badge — signals blind approval

**Backend:**
- `GET /api/v1/admin/publish-requests` response: add `last_eval_score: float | null` and `last_eval_run_id: uuid | null` (subquery join from `eval_runs` on `agent_name`)

---

### 6. "View Trace" per eval result row

**EvalResultsPage.tsx:**
- Each row gets "View Trace" button (shown when `r.langfuse_trace_id` exists)
- Opens TraceDrawer slide-out panel

**TraceDrawer** (new: `studio/src/components/playground/TraceDrawer.tsx`):
- Fetches `GET /api/v1/playground/runs/{id}/trace` (already exists, returns Langfuse trace)
- Renders:
  - Collapsible span tree (safety scans, tool calls, LLM calls) with duration bars
  - Input/output per span (click to expand)
  - Scores section (judge, user feedback)
- ~150 lines, reusable for playground ChatPane and workflow runs

**Backend:**
- Expose `langfuse_trace_id` in `EvalRunResultResponse` (column exists, not in schema)
- No new endpoints needed

---

### 7. Runs visibility in datasets + workflows

**DatasetsPage.tsx:**
- Per-dataset section showing eval runs: agent name, score, status, date, "View Results" link
- Currently eval runs are only accessible if you know the URL

**Workflow RunsTab:**
- Add "View Trace" button per workflow run (uses `langfuse_trace_id` from `agent_runs`)
- Enables debugging workflow execution failures

---

### 8. Publish button eval gate

**AgentDetailPage.tsx:**
- Check if agent's latest version has `eval_passed === true`
- If not: button disabled, tooltip "Run an eval that passes before publishing"
- UX guard only — backend already gates production deploys on `eval_passed`

---

## User Journey (After)

```
1. Deploy agent to sandbox
2. Create dataset (or save playground runs to dataset)
3. Run eval
4. View results:
   - See overall score + per-item breakdown with expected vs actual
   - Click failed row → expand full text
   - Click "View Trace" → see what agent did (tool calls, safety, timing)
5. If score good (≥70%):
   - Version auto-marked eval_passed ✓
   - "Publish Agent" button becomes active
   - Submit publish request
6. If score bad:
   - "Show failed only" → focus on failures
   - "View Trace" on failed items → understand root cause
   - Fix agent config/instructions
   - "Re-run Eval" → iterate
7. Admin sees eval score in publish queue → approves with confidence
```

---

## Files Modified

| File | Change |
|------|--------|
| `services/registry-api/models.py` | Add `expected_output` to EvalRunResult |
| `services/registry-api/schemas.py` | Add `expected_output` + `langfuse_trace_id` to EvalRunResultResponse |
| `services/registry-api/alembic/versions/0034_eval_result_expected_output.py` | Migration |
| `services/registry-api/routers/eval_runner.py` | Auto-set eval_passed when score ≥ threshold |
| `services/eval-runner/main.py` | Include `expected_output` in result POST |
| `studio/src/api/playgroundApi.ts` | Add `expected_output` + `langfuse_trace_id` to EvalRunResult |
| `studio/src/pages/EvalResultsPage.tsx` | Expandable rows, filter, CTAs, expected column, View Trace |
| `studio/src/components/playground/TraceDrawer.tsx` | New: inline trace viewer |
| `studio/src/pages/AgentDetailPage.tsx` | Publish button eval gate |
| `studio/src/pages/AdminPublishRequestsPage.tsx` | Eval evidence column |
| `studio/src/pages/DatasetsPage.tsx` | Eval runs list per dataset |
| `services/registry-api/routers/admin.py` | Add last_eval_score to publish requests response |

---

## Verification

1. Run eval on simple-qa → results page shows Expected Output column
2. Click row → expands to full text (no truncation)
3. "Show failed only" filter works
4. "View Trace" button opens drawer with span tree
5. Score ≥ 0.7 → version auto-marked eval_passed
6. "Mark Version Passed" + "Publish Agent" buttons appear
7. Publish button disabled when no eval_passed version
8. Admin publish queue shows "Last Eval: 85%" with link to results
9. DatasetsPage shows eval run history per dataset

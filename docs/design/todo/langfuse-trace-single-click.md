# Restore Single-Click Langfuse Trace Viewing in Studio

**Status:** Not started (design + root cause confirmed, no code changed)
**Last updated:** 2026-07-10
**Related:** `docs/design/langfuse-studio-integration.md` (Short-Term Fix section — this doc is the follow-up: that fix was built but only half-wired), `docs/bugs/langfuse-clickhouse-oom.md` (separate, already-fixed ClickHouse OOM issue that was originally reported alongside this one)

## Context

User reported that clicking "View Trace" / a Langfuse link in Studio now takes multiple clicks — lands on Langfuse's "you do not have access, Sign In" page → click Sign In → NextAuth's generic provider-chooser page → click "Keycloak" → finally lands on the trace — instead of a single click straight to the trace, which is how it used to work ("It was working with a single click from agent studio").

First investigation pass wrongly concluded this was unfixable, inherent Langfuse product behavior: Langfuse's own NextAuth SSO flow genuinely can't be deep-linked past its CSRF-protected provider-chooser page (verified empirically with `curl` — a direct `GET /api/auth/signin/keycloak?callbackUrl=...` fails with `error=keycloak`; NextAuth requires a CSRF token obtained from the chooser page first). That's true but was the wrong frame — AgentShield doesn't need to fight Langfuse's own login UI at all.

`docs/design/langfuse-studio-integration.md` already specified the correct architecture over a year ago (Short-Term Fix section, "NOW" priority): **never link to the Langfuse UI directly.** Studio should proxy trace data through registry-api using Langfuse's service API keys (`pk-lf`/`sk-lf`, Basic Auth, no user login) and render it inline. That proxy endpoint and the inline `TraceDrawer` component were both built — but the raw external `<a href={trace_url} target="_blank">` link was never removed from the three places that render a trace action. Instead it was kept as primary, with the inline drawer demoted to a fallback that only renders when `trace_url` is falsy.

Since registry-api populates `trace_url` (`services/registry-api/routers/playground.py:730-731`, mirrored in `observability.py`, `eval_runner.py`, `catalog.py`, `agent_runs.py`, `deployments.py`, `composite_workflows.py`) whenever `LANGFUSE_PUBLIC_URL`/`LANGFUSE_PROJECT_ID` env vars are set — which is always in this deployment — the inline drawer fallback is effectively dead code today. Every trace click routes through the raw external link and Langfuse's own multi-step SSO chooser. This plausibly explains the user's "it used to be one click" — at some point `trace_url` reliably started being populated (once Langfuse env config landed correctly), which silently flipped every trace-viewing surface from the working inline experience to the broken external-link one, inverting the architecture the original design doc called for.

## What exists vs. what's missing

| Piece | Status |
|---|---|
| `GET /api/v1/playground/runs/{run_id}/trace` — server-side trace fetch via Langfuse service creds | Built (`playground.py:693-760`) |
| `GET /api/v1/playground/traces/{trace_id}` — same, by trace_id directly | Built (`playground.py:766-822`) |
| `TraceDrawer.tsx` — inline slide-out panel rendering trace/span data, no Langfuse login needed | Built |
| `TraceDrawer.tsx` secondary "Open in Langfuse ↗" link for power users | Built (`TraceDrawer.tsx:55-64`) |
| `EvalResultsPage.tsx` using `TraceDrawer` as the primary click target | Missing — currently prioritizes raw external `trace_url` link (`EvalResultsPage.tsx:396-418`) |
| `ChatPane.tsx` using `TraceDrawer` as the primary click target | Missing — same pattern (`ChatPane.tsx:291-310`) |
| `RunsTab.tsx` wired to `TraceDrawer` at all | Missing — currently either an external link or an unclickable trace-id snippet (`RunsTab.tsx:136-149`) |
| Component tests for the trace-click behavior on Eval Results / Runs tab | Missing (no `EvalResultsPage.test.tsx` or `RunsTab.test.tsx` exist yet) |
| Playwright coverage asserting inline drawer opens (not external nav) | Missing |

## Fix

Invert the priority in all three components: when `langfuse_trace_id` exists, **always** open the inline `TraceDrawer` as the primary single-click action. Keep the raw external `trace_url` link only as the secondary "Open in Langfuse ↗" affordance that already exists inside `TraceDrawer.tsx` itself — no new UI needs to be invented, just stop routing past it.

### 1. `studio/src/pages/EvalResultsPage.tsx:396-418`

Currently:
```
trace_url ? <a external "Trace"> : langfuse_trace_id ? <button onViewTrace "Trace"> : null
```
Change to:
```
langfuse_trace_id ? <button onViewTrace "Trace"> : null
```
Drop the `trace_url` branch entirely.

### 2. `studio/src/components/playground/ChatPane.tsx:291-310`

Currently:
```
traceUrl ? <a external "View Trace"> : (!traceUrl && traceId) ? <button setShowTraceDrawer "View Trace"> : null
```
Change to:
```
traceId ? <button setShowTraceDrawer "View Trace"> : null
```

### 3. `studio/src/components/agent-detail/RunsTab.tsx:136-149`

Currently:
```
trace_url ? <a external "Trace"> : langfuse_trace_id ? <span unclickable> : null
```
This one has no `TraceDrawer` wired up at all. Add the same `traceId` state + `TraceDrawer` render pattern already used in `ChatPane.tsx`/`EvalResultsPage.tsx` — `agent-detail/` and `playground/` are sibling dirs under `components/`, so the import is `import TraceDrawer from "../playground/TraceDrawer";`. Change the trace cell to:
```
langfuse_trace_id ? <button onClick={() => setTraceId(run.langfuse_trace_id)} "Trace"> : null
```

No backend changes needed — both trace-fetch endpoints already exist and work (verified live: `GET /api/public/traces/{id}` via service creds returns HTTP 200 with full trace data, no user session required).

## Verification (when implemented)

- **TypeScript**: `cd studio && npm run typecheck` after all three edits.
- **Component tests**: update `ChatPane.test.tsx` (trace button must always open the drawer, never render as an external link). Add `EvalResultsPage.test.tsx` and `RunsTab.test.tsx` covering: trace button renders when `langfuse_trace_id` is set, and clicking it opens `TraceDrawer` rather than navigating away. `cd studio && npm run test`.
- **Playwright** (Definition of Done — this is a real UX flow, not just an API change): add/extend an `e2e/*.spec.ts` case that clicks a trace button and asserts the inline drawer opens (e.g. `page.getByText("Execution Trace")`) rather than a new tab — use `page.waitForResponse` on `/api/v1/playground/traces/` or `/runs/*/trace` to confirm the network call fires. `bash scripts/studio-e2e.sh`.
- **Manual/live check**: reload Studio, click "Trace" from the eval results page (the surface the user was actually using) and confirm it opens the inline drawer immediately — one click, no Langfuse tab, no Sign In screen.
- **Image tags**: only `studio/` changes — bump `STUDIO_TAG` in `scripts/deploy-cpe2e.sh` and mirror in `charts/agentshield/values.yaml` (~L820) per CLAUDE.md, deploy via `scripts/deploy-cpe2e.sh` (never bare `helm upgrade`).
- **Docs**: update `docs/experience/playground.md` (trace-viewing UX changed) per the mandatory experience-doc-update rule. Also correct `docs/bugs/langfuse-clickhouse-oom.md`'s "Known Gaps" note — it says the Sign-In flow was "verified working, not a bug," which is true of the mechanism itself but missed that the wrong mechanism (external link) was being used as primary by default.

## Non-goals

- Not attempting to make Langfuse's own SSO flow itself single-click (confirmed not deep-linkable past its CSRF-protected chooser page). The `TraceDrawer`'s secondary "Open in Langfuse ↗" link will still show that multi-step flow for users who deliberately want the full Langfuse UI — that's expected and fine, it's no longer the default path.
- Not touching `services/registry-api` — the proxy endpoints already work correctly.

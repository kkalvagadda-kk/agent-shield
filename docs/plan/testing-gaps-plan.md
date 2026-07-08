# Plan: Close 5 Testing Gaps in AgentShield

**Date:** 2026-07-07
**Status:** Planned

## Context

Manual playground testing keeps finding bugs (14+ in recent commits) that automated tests should catch. Root cause: all three test layers (bash e2e, Vitest, Playwright) stop before the SSE→render integration seam where bugs actually live.

```
Real user journey:
  Browser → Select Agent → Send Message → SSE Stream → Parse Events → Render Text/Tools → HITL → Feedback
                                              ↑
                                    WHERE BUGS LIVE
                                              ↑
  Bash e2e:  API calls only ──────────── stops here (JSON shape)
  Vitest:    Mocked APIs + string fixtures ── stops here (never fires SSE)
  Playwright: Real login + nav ───────────── stops here (never sends a message)
```

Recent bugs caught only by manual testing:
- `[object Object]` in chat (content blocks as list not string)
- `[object Object]` in trace panel (dict result passed to `.slice()`)
- Blank white screen (TypeError in TracePanel, no ErrorBoundary)
- SSE double-wrapping (double `data:` layer)
- text_delta noise flooding trace panel
- FK always-NULL, credential case mismatch, RBAC 403 cross-namespace
- save-to-dataset dropping output_text

---

## Gap 1: SDK has zero tests

**Problem:** `sdk/agentshield_sdk/` has no test suite. The `[object Object]` bug (content blocks returned as list instead of string) was a pure Python data-transformation bug catchable with a 10-line pytest.

**Fix:**
- Create `sdk/tests/` with `conftest.py` and `test_streaming.py`
- Test `streaming.py` event normalization:
  - `text_delta` with `content` as list of typed blocks `[{"type":"text","text":"Hello"}]` → should emit string
  - `text_delta` with `content` as plain string → passthrough
  - `tool_call_end` with dict result → should JSON-serialize
  - `tool_call_end` with string result → passthrough
  - `error` event handling
  - `done` event closes stream
- Add `pytest` to SDK dev dependencies

**Files:**
- NEW: `sdk/tests/__init__.py`
- NEW: `sdk/tests/conftest.py`
- NEW: `sdk/tests/test_streaming.py`
- EDIT: `sdk/pyproject.toml` or `setup.py` (add pytest dev dep)

**Effort:** Small

---

## Gap 2: Vitest SSE path never exercised

**Problem:** `ChatPane.test.tsx` has `MockEventSource` but never fires `onmessage`. The entire SSE→state→render path is untested.

**Fix:**
- Upgrade `MockEventSource` to capture the instance and allow dispatching events
- Add tests that fire SSE events through the mock:
  - `text_delta` with list-typed content → verify assistant text renders (not `[object Object]`)
  - `text_delta` with string content → verify normal render
  - `tool_call_start` / `tool_call_end` → verify tool chip appears
  - `approval_requested` → verify `onApprovalRequested` callback fires
  - `done` → verify `running` state clears, feedback bar appears
  - `error` → verify error toast
- Pattern: after `startPlaygroundRun` resolves, grab MockEventSource instance, call `instance.onmessage(new MessageEvent('message', { data: JSON.stringify({...}) }))`

**Files:**
- EDIT: `studio/src/components/playground/ChatPane.test.tsx`

**Effort:** Medium

---

## Gap 3: Vitest fixtures are all happy-path strings

**Problem:** `TracePanel.test.tsx` passes `result: "string"` — never objects/arrays/null. The `.slice()` TypeError that caused blank screens is structurally impossible to trigger with current fixtures.

**Fix:**
- Add adversarial fixture tests to `TracePanel.test.tsx`:
  - `result: { some: "dict" }` → should render without crash
  - `result: [1, 2, 3]` → should render without crash
  - `result: null` → should render without crash
  - `content: [{"type":"text","text":"Hello"}]` → should render "Hello" not `[object Object]`
- Add `StepTracker.test.tsx` (currently zero coverage):
  - Render with mock step data
  - Verify step status icons
  - Verify expand/collapse
- Add unit tests for `coerceToString()` in `ChatPane.tsx:24`:
  - string input → passthrough
  - list of content blocks → joined text
  - plain object with `.text` → extracted text
  - object without `.text` → JSON.stringify fallback
  - null/undefined → empty string or no crash

**Files:**
- EDIT: `studio/src/components/playground/TracePanel.test.tsx`
- EDIT: `studio/src/components/playground/ChatPane.test.tsx`
- NEW: `studio/src/components/playground/StepTracker.test.tsx`

**Effort:** Small (highest immediate value)

---

## Gap 4: Playwright never runs an agent

**Problem:** `playground.spec.ts` explicitly skips agent runs. No browser test exercises the SSE→render→trace→HITL flow.

**Fix (recommended: `page.route()` interception, no backend changes):**
- Use Playwright's `page.route()` to intercept the SSE stream URL and return synthetic events
- New spec `playground-streaming.spec.ts`:
  - Mock `POST /api/v1/playground/runs` → return `{ run_id, stream_url }`
  - Intercept `GET stream_url` → return synthetic SSE with:
    1. `text_delta` with list-typed content blocks
    2. `tool_call_start` + `tool_call_end` with dict result
    3. `approval_requested` event
    4. `done` event
  - Assert: assistant text appears (not `[object Object]`)
  - Assert: tool chip renders
  - Assert: HITL panel opens on `approval_requested`
  - Assert: trace panel shows structural events (not flooded with text_delta)
  - Assert: no blank screen / ErrorBoundary crash

**Files:**
- NEW: `studio/e2e/playground-streaming.spec.ts`

**Effort:** Medium

---

## Gap 5: Bash e2e never chains cross-suite journeys

**Problem:** Suite-8 saves a run to a dataset. Suite-9 creates eval runs from datasets. Never connected. The `output_text` omission in `save_run_to_dataset` was invisible because no test reads the saved item back.

**Fix:**
- New `scripts/e2e/suite-38-eval-journey.sh`:
  - T-S38-001: Create a playground run (error path, no live agent needed)
  - T-S38-002: Save run to dataset via `POST /playground/runs/{id}/save-to-dataset`
  - T-S38-003: **Read dataset back** via `GET /playground/datasets/{id}` — assert saved item has `input` field populated, assert `expected_output` or `output_text` field exists (regression test for the missing field)
  - T-S38-004: Create an eval run using that saved dataset
  - T-S38-005: Verify eval run created with correct dataset reference and item count
- Register in `scripts/e2e/run-all.sh`

**Files:**
- NEW: `scripts/e2e/suite-38-eval-journey.sh`
- EDIT: `scripts/e2e/run-all.sh` (register suite-38)

**Effort:** Medium

---

## Implementation order

| Priority | Gap | Effort | Value | Catches |
|----------|-----|--------|-------|---------|
| 1 | Gap 3 — adversarial fixtures | Small | Highest | `[object Object]`, blank screen, `.slice()` TypeError |
| 2 | Gap 2 — Vitest SSE path | Medium | High | SSE event handling, approval flow, stream lifecycle |
| 3 | Gap 1 — SDK pytest | Small | High | Content-block normalization, event serialization |
| 4 | Gap 4 — Playwright streaming | Medium | High | Full browser SSE→render→trace→HITL |
| 5 | Gap 5 — bash e2e journey | Medium | Medium | save-to-dataset data loss, cross-suite integration |

Gaps 1 and 2 can run in parallel (SDK vs Studio, independent).

## Verification

- `cd studio && npm run test` — all Vitest pass (Gaps 2, 3)
- `cd sdk && pytest` — SDK tests pass (Gap 1)
- `bash scripts/studio-e2e.sh` — Playwright pass (Gap 4)
- `bash scripts/e2e/run-all.sh` — suite-38 pass (Gap 5)
- Regression check: removing `coerceToString` should break at least one test in Gaps 1, 2, and 3

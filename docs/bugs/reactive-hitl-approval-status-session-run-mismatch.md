# Bug: Production reactive chat hangs after HITL approval — approval-status poll keyed by run_id, not session_id

**Date:** 2026-07-19
**Status:** Fixed
**Found/Fixed:** Fixed 2026-07-19 in `registry-api:0.2.215` (commit on `fix/reactive-hitl-approval-poll`, branched from `origin/main` 5278c54). Backend-only; no migration; no image rebuild of studio (the frontend poll logic was already correct).
**Severity:** High — a production agent chat that calls a high-risk tool **hangs forever** after a reviewer approves. The governance gate itself worked (the tool parked, the approval was created and decided); only the resume never reached the chat. 100 % reproducible, not intermittent.

## Symptom

Chatting with a **production** deployment of `hitl-agent` (reactive), a high-risk tool (`web_search`, OPA `require_approval`) parks. The chat shows the `hitl-waiting-banner` ("waiting for a reviewer…"). A reviewer approves in the Approvals console (the approval row flips to `approved`). But the chat **never resumes** — the banner stays up indefinitely.

Observed alongside a red-herring: the approval appeared under the **Approvals** menu and not the admin **HITL Queue**. Both read `GET /approvals/`; that split is a client-side filter difference and is **not** the cause. The real fault is below.

## Root cause

Since **POC-0**, a chat's LangGraph checkpoint + HITL approval are keyed by the **conversation `session_id`**, not the per-turn `run_id` (the browser mints a fresh `session_id` per conversation; each turn gets its own `run_id`, so `session_id != run_id` **always** — verified: `0` of 65 `playground_runs` have `session_id == id`). The agent pod creates the approval with `thread_id = session_id`.

The production chat page (`AgentChatPage`, `startApprovalPolling`) polls `GET /api/v1/agents/{name}/chat/{run_id}/approval-status` and auto-resumes when `decided` is true. But that endpoint (`chat.py::chat_approval_status`) looked the approval up by:

```python
select(Approval).where(Approval.thread_id == run_id)   # run_id = per-turn PlaygroundRun.id
```

`run_id != session_id`, and the approval is keyed by `session_id`, so the query matched **nothing** → the endpoint returned `{"status": "none"}` on every poll → `decided` was never true → `connectResumeStream` was never called → the banner never cleared.

**The class flaw:** POC-0 updated the **resume** path (`chat.py:1004`, with the literal comment "session_id since POC-0" → `thread_id = run.session_id or run_id`) but left the sibling **poll** endpoint 160 lines away still keying by `run_id`. Two paths derived the same thread id independently and one drifted. A **third** instance had the same stale assumption: `session_approvals` (`GET /chat/session/{session_id}/approvals`, which feeds the sandbox self-approve panel) built `run_ids` from `PlaygroundRun.id` and queried `Approval.thread_id.in_(run_ids)` — also returning empty.

## Fix

One shared derivation, used everywhere a chat run's approval is looked up, so the paths can never drift again:

```python
def _chat_thread_id(run, run_id: str) -> str:
    # POC-0: approval + checkpoint are keyed by the conversation session_id, not run_id.
    return run.session_id or run_id
```

- `chat_approval_status` (the poll — **the bug**): `Approval.thread_id == _chat_thread_id(run, run_id)`.
- resume-stream (`resume_stream_chat`): refactored to call the same helper (behavior unchanged).
- `session_approvals`: now queries `Approval.thread_id == session_id` directly (the session_id is a path param) with session-level provenance.

`playground.py:864` (`Approval.thread_id == run_id`) is the **durable** playground path where the run parks its `AgentRun` at `id == thread_id`, so `thread_id == run_id` there is correct — left untouched.

## Why the tests were green through a 100%-reproducible bug (the coverage hole)

The **production reactive-chat sub-flow** (`hitl-waiting-banner` + poll `chat/{run_id}/approval-status` + console decide → auto-resume) was driven by **no test at any layer**:

- **bash suites:** `suite-4/5/35` create+decide approvals with a *self-chosen* `thread_id` (matches by construction); `suite-60`/durable key by `run_id` correctly; `suite-65` decides via the console and checks the run, not the chat poll. The one test that hit the poll — **`suite-45 T-S45-007`** (deployment chat) — `SKIP`s whenever no deployment is running (the common state), so it silently never asserted. `T-S45-011/012` drive the reactive stream + resume-stream, which correctly key by `session_id`, so they passed.
- **Playwright:** `approvals-inbox.spec.ts` mocks the network; `hitl-deployment-chat.spec.ts` tests the **sandbox self-approve** path and explicitly asserts the `hitl-waiting-banner` is **absent** (the opposite case) — and skips when the fixture is cold, and was **excluded** from the curated `studio-journeys-e2e.sh`.
- **Vitest:** hand-feeds the SSE/poll frames, so it can't catch a backend SQL mismatch (CLAUDE.md rule 1: "test the layer that can actually fail").

## Regression tests added (reproduce-first)

- **`scripts/e2e/suite-45-hitl-e2e.sh` T-S45-013** — reactive `POST /agents/hitl-agent/chat` with a fresh UUID session (`session_id != run_id`) → stream to `approval_requested` → `GET /chat/{run_id}/approval-status`, assert `status == "pending"`. **Confirmed RED on 0.2.214** (`status=none`), **GREEN on 0.2.215**. Unlike T-S45-007 it uses the always-available sandbox pod, so it can't silently skip.
- **`studio/e2e/hitl-production-chat.spec.ts`** (added to the curated `scripts/studio-journeys-e2e.sh`) — the browser journey no spec drove: production chat → `hitl-waiting-banner` appears → reviewer approves in the console → banner clears + chat auto-resumes. RED before the fix (banner never hides).

## Verification

- **RED (0.2.214):** `suite-45` — `T-S45-007` FAIL `status=none`, `T-S45-013` FAIL `poll returned status=none`.
- **GREEN (0.2.215):** `T-S45-007` + `T-S45-013` PASS (poll reflects the pending approval).
- Deployed by `kubectl set image` on the `registry-api` container **only** — the `alembic-migrate` init container stayed at `0.2.214` because `0.2.215` (built off origin/main, migration head `0068`) would fail against a DB already at the webhook `0069/0070`.

## Lessons

1. **When a shared assumption changes (run_id → session_id), grep for EVERY reader.** POC-0 fixed the resume path and the forward dispatch but missed two sibling lookups. A single `_chat_thread_id` helper makes the invariant one edit, not four.
2. **A `test.skip` on a missing fixture is a silent coverage hole.** The one test that covered the poll skipped in the common "no running deployment" state, so a 100%-reproducible bug shipped green. Prefer a fixture that's always available (the sandbox pod), or fail loudly instead of skipping.
3. **Test the layer that can actually fail.** The fault was a backend SQL predicate; Vitest hand-feeding frames and Playwright mocking the network both structurally could not catch it. Only a test that drives the real endpoint (T-S45-013) does.

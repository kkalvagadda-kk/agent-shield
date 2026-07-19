# Design brief — Fix HITL multi-approval regression (reactive agents + workflows)

**Type:** bug fix (regression). Reproduce-first, root-cause, no symptom patch.
**Owner instruction:** this was working before — assess what broke it; write the
failing HITL e2e test that RE-CREATES the issue BEFORE the fix, fix the root
cause, verify the SAME test goes green, cover the COMPLETE HITL flow for BOTH
agents and workflows, and document the bug per the CLAUDE.md rule (docs/bugs +
docs/debugging).

## Symptom (observed live, run e2a56353, thread ed039d9a…)
A reactive workflow member (`researcher-agent` under `research-summarize`) parks
for HITL on its first `web_search`. The user approves inline. The workflow then
"completes" — but the researcher's answer is just the ECHOED input, no search
result renders, `summarization-agent` summarizes the non-answer, and the run is
marked `completed`. In the DB there are TWO approvals on the thread: query A
`approved`, query B **`pending` (orphaned forever)**; `run_steps` has ONE
`web_search` step. Pod logs confirm: after `POST /resume 200`, the model issued a
SECOND `web_search`, the pod correctly re-parked (`on_interrupt`, new approval
`41f3ca38`), but the registry marked the member completed and advanced.

## Root cause (assessed, evidence-backed)
The forward and resume paths for a reactive member are **asymmetric**:

- **Forward** dispatch streams via the pod `/chat/stream` → SDK `stream_events`
  (detects interrupt). The orchestrator then does **"authoritative pause
  detection"** — `services/registry-api/workflow_orchestrator.py:615`: after the
  member returns, if a `pending` Approval exists on the thread, set
  `awaiting_approval` and do NOT advance. This is why the FIRST approval works.
- **Resume** is wired to the pod's NON-stream `/resume`.
  `services/declarative-runner/workflow_executor.py:876` `resume()` has (since
  `1cab19e`) always done a single `ainvoke(Command(resume=…))` and returned
  `messages[-1].content`, IGNORING `result["__interrupt__"]`. Then
  `services/registry-api/routers/approvals.py:158-197` `_resume_and_advance`
  marks the member **`completed` on any HTTP 200** and calls
  `resume_orchestration` — it has NONE of the authoritative pause detection the
  forward path has.

So a member that parks a SECOND time is silently completed with garbage output
and approval B is orphaned. execution-models-v2 routed workflow-member resume
onto this non-stream path; the streaming forward path always parked, the resume
path never did → that asymmetry is the regression.

Single reactive AGENT chat resume goes via `resume_stream` → `stream_events`
(`workflow_executor.py:912`), which DOES emit `approval_requested` on
re-interrupt — so the single-agent double-approval MAY already work. Must be
VERIFIED with a test, not assumed.

## Fix (root cause — mirror the existing forward-path pattern)
1. **Registry `_resume_and_advance` (core).** After the pod resume returns, run
   the SAME authoritative pause detection the forward path uses: if a `pending`
   Approval exists on `thread_id`, the member RE-PARKED → set the child
   `awaiting_approval`, do NOT set completed/output, do NOT call
   `resume_orchestration` / advance the parent. The existing inline-decide flow
   re-fires `_resume_and_advance` when the next approval is decided → loops until
   no `pending` remains → then completes + advances with the REAL answer.
2. **Frontend `studio/src/pages/WorkflowChatPage.tsx`.** The resume poll
   (`pollResumedResult`) must detect a newly-`pending` approval on the thread and
   re-render the inline `ConversationApprovalPanel`, so the user can approve the
   2nd (which re-fires the registry resume).
3. **Defense-in-depth (optional) `workflow_executor.resume()`.** Return an
   explicit `awaiting_approval` + `approval_id` on `result.get("__interrupt__")`
   instead of a fake `response`. The DB pending-approval check is authoritative
   (consistent with the forward path), so this is belt-and-suspenders; include
   only if low-risk.
4. **Agents.** Verify the single reactive-agent double-approval via the stream
   resume path; fix only if the test shows it fails.

## Verification (reproduce-first → fix → verify SAME test)
- **Write the failing test FIRST.** Extend `scripts/e2e/suite-79-workflow-hitl.sh`
  with **T-S79-004 (workflow double-approval)**: prompt engineered to elicit two
  `web_search` calls (gemma did this unprompted in the repro), drive park →
  approve → [re-park → approve] → complete. Assert the INVARIANT: never (run
  `completed` AND an approval left `pending`); final run `completed` with BOTH
  members `completed` AND the researcher output is a REAL answer (not the echoed
  input) AND zero `pending` approvals on the thread. MUST FAIL against current
  code, PASS after the fix.
- **Agent double-approval case** (suite-45 or suite-79) for the single reactive
  agent — proves the complete flow for agents too.
- **Regression:** existing single-approval suite-79 + suite-45 stay green.
- **Self-verify in the browser** (Playwright + a manual walkthrough): reproduce
  the screenshot scenario, approve twice, confirm a grounded answer and zero
  orphaned approvals.
- **Ollama non-determinism:** engineer a reliable two-search prompt + bounded
  retry; if it genuinely won't double-park, SKIP with a loud diagnostic (never a
  false pass), and back it with a DETERMINISTIC registry-level assertion of the
  same invariant (seed a second pending approval on the thread post-resume and
  assert the registry re-parks instead of completing).

## Documentation (MANDATORY, per CLAUDE.md rule 8)
- `docs/bugs/hitl-multi-approval-resume-regression.md` — postmortem (Found/Fixed +
  image tag, Symptom, Root cause, Fix).
- `docs/debugging/012-hitl-second-approval-orphaned.md` — investigation log
  (expected chain, the exact kubectl/SQL/log commands used, the evidence, root
  cause, fix). Cross-link the failing test + the two docs.

## Critical files
- `services/registry-api/routers/approvals.py` (`_resume_and_advance` ~L100-199) — the core fix.
- `services/registry-api/workflow_orchestrator.py` (~L615 authoritative pause detection — the pattern to mirror).
- `studio/src/pages/WorkflowChatPage.tsx` (resume poll / inline panel).
- `services/declarative-runner/workflow_executor.py` (`resume` ~L876; `resume_stream` ~L912) — optional defense-in-depth + the agent path.
- `sdk/agentshield_sdk/streaming.py` (`stream_events` / `_extract_interrupts`) — reference (already handles re-interrupt).
- `scripts/e2e/suite-79-workflow-hitl.sh` — the reproduce-first test.

## Image bumps
registry-api (approvals.py) + studio (WorkflowChatPage) + possibly
declarative-runner (if `resume()` changes → SDK rebuild). Bump BOTH
`scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml`.

## Out of scope (do NOT fold in)
- The temporary OPA fail-open bypass (separate revert item).
- The workflow ledger gaps (WorkflowChatPage transcript rehydration, workflow
  Memory tab) — the NEXT task after this one.

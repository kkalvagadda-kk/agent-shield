# Implementation Plan — Fix HITL multi-approval resume regression

**Type:** bug fix (regression). **Reproduce-first, root-cause, no symptom patch.**
**Companion artifacts:** `research.md` (settled root cause + code-truth), `quickstart.md`
(copy-paste build/deploy/test), `design-brief.md` (authoritative brief).

> **Read `research.md` first.** The root cause is settled: the reactive workflow-member
> **resume** path (`approvals.py:_resume_and_advance`) marks a member `completed` on any pod
> HTTP 200 and never re-checks for a NEW pending approval — unlike the **forward** path
> (`workflow_orchestrator.py:616-624`) which does. The fix mirrors the forward path's
> authoritative pause detection into the resume re-entry block. Task 1 is the failing
> reproduction test.

---

## 1. Goal

Make a reactive workflow member that parks a **second** time on resume re-park correctly
(instead of being silently completed with the echoed input and orphaning the second approval),
end-to-end: registry re-parks → the inline UI re-surfaces the second gate → approving it loops
until no gate remains → the run completes with the **real** answer and **zero** pending
approvals. Prove the complete HITL flow for **both** reactive workflows and single reactive
agents, reproduce-first.

**Invariant this fix enforces:** *never (a run is `completed` AND an approval on its thread is
left `pending`).*

---

## 2. Architecture (the asymmetry and the mirror-fix)

```
FORWARD (works)                              RESUME (broken → fixed)
_run_step_stream                             decide → _resume_and_advance (reactive branch)
  member /chat/stream returns 'completed'      POST pod /resume/{thread_id}  → HTTP 200
        │                                            │  (resume() ignores __interrupt__,
        ▼  AUTHORITATIVE PAUSE DETECTION             │   echoes messages[-1].content)
  SELECT Approval WHERE thread_id=?,                 ▼  member_status = 'completed' (BUG: on any 200)
         status='pending'                      ┌─────┴─────────────────────────────────┐
        │ pending? → status='awaiting_approval'│  FIX: mirror the forward check         │
        ▼ (do NOT advance)                     │  SELECT Approval WHERE thread_id=?,     │
  child.status set, parent parked              │         status='pending'                │
                                               │  pending? → child.status=awaiting_appr, │
                                               │            RETURN (do NOT advance)      │
                                               └─────────────────────────────────────────┘
                                                     no pending → close child 'completed' + advance
```

- **One discriminator, used symmetrically:** a `pending` Approval on the member's `thread_id`
  means "still parked" — on the forward path **and** on resume. No HTTP-status trust, no
  `getattr`/type-sniffing, no priority fallthrough (No-Bandaid).
- **The loop terminates naturally:** the inline decide re-fires `_resume_and_advance` for the
  next gate → re-posts `/resume` → re-checks pending → repeats until none remains → then closes
  the child `completed` with the real output and calls `resume_orchestration` to advance the
  parent. Identical shape to the durable member's existing `resume_durable_member` re-park loop.
- **Frontend:** `WorkflowChatPage.pollResumedResult` mirrors the already-shipped
  `WorkflowBuilderPage` pattern — on `parent.status === 'awaiting_approval'`, fetch playground
  pending approvals, correlate by the parked child's `thread_id`, and re-render the inline
  `ConversationApprovalPanel`.
- **Additive:** with no second `pending` approval, every existing single-approval flow is
  byte-for-byte unchanged.

---

## 3. Tech Stack

Python 3.12 / FastAPI / SQLAlchemy async (registry-api); httpx for in-cluster pod resume;
LangGraph + `AsyncPostgresSaver` (declarative-runner); React 18 + Vite + TailwindCSS + React
Query (Studio); Vitest + React Testing Library + Playwright (Studio tests); bash + `kubectl
exec` + httpx (backend e2e). **No new dependencies.** `Approval`, `AgentRun`, `select`, `or_`
are already imported in the files touched.

---

## 4. Constitution Check (CLAUDE.md "Definition of Done" + Post-Impl — PASS/FAIL each)

| Gate | Verdict | How this change satisfies it |
|---|---|---|
| **DoD #1 — real user journey, not an endpoint** | **PASS** | Task 3 adds a Vitest `WorkflowChatPage.test.tsx` (deterministic: mocks tree=awaiting_approval + parked child + `listPendingApprovals` returning a new gate → asserts the inline panel re-renders) + a best-effort Playwright live spec + a manual-test-plan step for the true double-park. |
| **DoD #2 — save→reload→assert survived** | **PASS** | Task 1's deterministic backstop (T-S79-004b) drives decide, then **re-reads from the backend** (`GET /workflows/{id}/runs/{run_id}/tree` + `GET /approvals/{id}`) and asserts the re-park persisted (child + parent `awaiting_approval`, seeded approval still `pending`, run NOT `completed`). |
| **DoD #3 — no orphan code** | **PASS** | No new exported backend symbol (the fix is an added branch inside the existing `_resume_and_advance`). Frontend adds one `listPendingApprovals` **import** (existing exported fn) wired into `pollResumedResult` — grep proves the caller (Task 3 verification). |
| **DoD #4 — vertical slices** | **PASS** | One thin path proven end-to-end: reproduce (Task 1) → registry re-park (Task 2, backstop goes green) → UI re-surfaces gate (Task 3). No horizontal layering. |
| **DoD #5 — honest gap ledger** | **PASS** | Task 6 records: live double-park is best-effort (Ollama) — the deterministic backstop is the real gate; Task 4 (pod `resume()` `__interrupt__`) is **deferred (intentional / optional)** unless Task 5 shows the single-agent path needs it. Header of `docs/testing/manual-ui-e2e-test-plan.md` updated. |
| **DoD #6 — reason from running product** | **PASS** | Every anchor in `research.md` is `file:line`-grounded and re-verified against the current code (see §2/§5 there). |
| **DoD #7 — bug fixes reproduce first** | **PASS** | Task 1 (the failing test) precedes Task 2 (the fix). T-S79-004b **fails** against current code (run `completed` while seeded approval `pending`) and **passes** after; Task 2 is not "done" until it goes green + the real flow is driven (`quickstart.md` §6). |
| **DoD #8 — document the bug (postmortem + debugging log)** | **PASS** | Task 6 writes `docs/bugs/hitl-multi-approval-resume-regression.md` + `docs/debugging/012-hitl-second-approval-orphaned.md` and cross-links the test + both docs. |
| **Post-Impl — bash e2e registered** | **PASS** | T-S79-004 extends `suite-79-workflow-hitl.sh` (already registered in `run-all.sh:128`). No new suite file. |
| **Post-Impl — image bumps in BOTH files** | **PASS** | registry-api `0.2.205→0.2.206`, studio `0.1.154→0.1.155` in `deploy-cpe2e.sh` **and** `values.yaml` (Task 7). declarative-runner `0.1.58→0.1.59` **only if** Task 4 lands (conditional). |
| **Post-Impl — Vitest + Playwright** | **PASS** | Task 3: Vitest `WorkflowChatPage.test.tsx` + Playwright (best-effort live) / manual step. |
| **Experience docs (`docs/experience/playground.md`)** | **PASS (triggered)** | `WorkflowChatPage.tsx` behavior changes (re-park re-surfaces the inline panel) → Task 6 updates the workflow-chat HITL description. |
| **Migrations** | **PASS (none)** | No schema change — purely control-flow + read-side. |

No FAILs. Task 4 is the only optional item; its omission is recorded in the gap ledger, not silently skipped.

---

## 5. File Structure (every file created/modified — one-line responsibility)

### Backend — registry-api
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/routers/approvals.py` | **M** | **CORE FIX.** In `_resume_and_advance` reactive re-entry block (L170-199), after fetching `child`+`parent`, mirror the forward-path authoritative pause detection: if a `pending` Approval exists on `thread_id`, set `child.status='awaiting_approval'` (leave output/completed_at null), commit, and `return` — do NOT close the child or call `resume_orchestration`. |

### Backend — declarative-runner (OPTIONAL, Task 4)
| File | C/M | Responsibility |
|---|---|---|
| `services/declarative-runner/workflow_executor.py` | **M (optional)** | Defense-in-depth: `resume()` (L876-910) returns `{"status":"awaiting_approval","approval_id":…,"thread_id":…}` on `result.get("__interrupt__")` instead of a fake `response`. Low-risk; DB check (Task 2) remains authoritative. |

### Frontend — studio
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/pages/WorkflowChatPage.tsx` | **M** | `pollResumedResult` (L83-138): on `parent.status==='awaiting_approval'`, fetch `listPendingApprovals(undefined,'playground')`, correlate by the parked child's `thread_id`, and re-render the inline `ConversationApprovalPanel` (map `ApprovalInboxItem`→`SessionApproval`); keep polling through the resume window; only render terminal on `completed`/`failed`. Add `listPendingApprovals` import. |
| `studio/src/pages/WorkflowChatPage.test.tsx` | **C** | Vitest (SANDBOX surface): mocks `getWorkflowRunTree` (awaiting_approval + parked child) + `listPendingApprovals` (new gate on that thread_id) → asserts the inline panel re-renders with the 2nd approval; and terminal render on `completed`. |
| `studio/src/pages/ApprovalsInboxPage.test.tsx` | **M** | Vitest (PRODUCTION surface): mock `listPendingApprovals` to return a NEW pending approval after a decide → assert the 2nd approval re-appears in the console queue (the page already `refetchInterval:10000`). |
| `studio/src/pages/EvalResultsPage.test.tsx` | **M** | Vitest (EVAL surface): assert the results detail renders the 2nd `awaiting_approval`/`approval_id` in the HITL approvals panel (re-park visible) when the trajectory carries two gates. |
| `studio/e2e/approvals-inbox.spec.ts` | **M** | Playwright (PRODUCTION, best-effort): after approving a workflow-member console approval, the 2nd re-appears in the inbox; SKIP-loud if no prod fixture. |
| `studio/e2e/eval-v2-workflow.spec.ts` | **M** | Playwright (EVAL, best-effort): a workflow eval with a HITL member surfaces the re-park in the results; SKIP-loud if the eval-runner/dataset is unavailable. |

### Tests + docs
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-79-workflow-hitl.sh` | **M** | **SANDBOX** context. Add **T-S79-004a** (best-effort live double-approval, loud SKIP if no double-park) + **T-S79-004b** (deterministic, context-agnostic backstop: seed a 2nd `pending` approval, approve the 1st, assert the registry re-parks — never completes-with-pending). |
| `scripts/e2e/suite-45-hitl-e2e.sh` | **M** | **PRODUCTION** context + single agent. Add **T-S45-00X** (a) production workflow-**member** console decide (`decide_approval`/PATCH) → re-park, SKIP-loud on capacity; (b) single reactive-agent double-approval (STREAM path) — verify it completes with a real answer + zero orphaned approvals; fix only if it fails (Task 5). |
| `scripts/e2e/suite-73-eval-v2-workflow.sh` | **M** | **EVAL** context. Add **T-S73-00X** workflow-member HITL during an eval (`decide_playground_approval` via `eval-runner _self_approve`) → assert re-park is handled (no completes-with-pending); SKIP-loud if the eval-runner/dataset is capacity-limited locally (Task 5). |
| `docs/bugs/hitl-multi-approval-resume-regression.md` | **C** | Postmortem (Found/Fixed + image tag, Symptom, Root cause, Fix). |
| `docs/debugging/012-hitl-second-approval-orphaned.md` | **C** | Investigation log (expected chain, exact kubectl/SQL/log commands, evidence, root cause, fix). Cross-links the test + the bug doc. |
| `docs/experience/playground.md` | **M** | Update the workflow-chat HITL description: a re-park re-surfaces the inline approval panel. |
| `docs/testing/manual-ui-e2e-test-plan.md` | **M** | Add the manual double-approval walkthrough step + record the Ollama best-effort/optional-Task-4 gaps in the Known-gaps header. |

### Build
| File | C/M | Responsibility |
|---|---|---|
| `scripts/deploy-cpe2e.sh` | **M** | `REGISTRY_API_TAG 0.2.205→0.2.206` (L292), `STUDIO_TAG 0.1.154→0.1.155` (L317); `DECLARATIVE_RUNNER_TAG 0.1.58→0.1.59` (L319) **only if Task 4**. Update comment header. |
| `charts/agentshield/values.yaml` | **M** | Mirror the same tags: registry-api (L622), studio (L953); `deploy-controller.declarativeRunnerTag` (L700) **only if Task 4**. |

---

## 6. Key Interfaces (real signatures of the changed functions)

### `_resume_and_advance` — `services/registry-api/routers/approvals.py:52`
```python
async def _resume_and_advance(
    agent_name: str, team: str, thread_id: str,
    decision: str, reviewer_id: str | None, reason: str | None,
) -> None:
```
The reactive re-entry block to change (current L170-199). **Insert** the pause-detection branch
immediately after the `if not parent or not parent.workflow_id or not parent.orchestrator_state: return`
guard (current L183-184), BEFORE `child.status = member_status` (L186):

```python
            # Authoritative RE-PARK detection — mirror workflow_orchestrator.py:616-624.
            # The pod /resume returns HTTP 200 even when the resumed model made a SECOND
            # approval-gated call and the pod re-parked (workflow_executor.resume() ignores
            # result["__interrupt__"] and echoes messages[-1].content). A 200 does NOT prove
            # completion. If a NEW pending Approval exists on this thread, the member RE-PARKED:
            # leave it awaiting_approval (non-terminal) and do NOT advance the parent. The next
            # inline decide re-fires _resume_and_advance → loops until no pending remains.
            pending = (await s.execute(
                select(Approval).where(
                    Approval.thread_id == thread_id, Approval.status == "pending"
                )
            )).scalar_one_or_none()
            if pending is not None:
                child.status = "awaiting_approval"
                await s.commit()
                logger.info(
                    "workflow member re-parked after resume (thread_id=%s, approval=%s) — "
                    "not advancing parent %s", thread_id, pending.id, parent.id,
                )
                return
```
`Approval` + `select` are already imported (approvals.py L23, L29). No signature change; the
function stays fire-and-forget / never-raises.

### `pollResumedResult` — `studio/src/pages/WorkflowChatPage.tsx:83`
```typescript
const pollResumedResult = async (runId: string): Promise<void>
```
Add, inside the poll loop, before the `completed`/`failed` branch:
```typescript
        if (status === "awaiting_approval") {
          const parked = (tree.children ?? []).find(
            (c) => c.status === "awaiting_approval" && c.thread_id,
          );
          if (parked?.thread_id) {
            let approvals: Awaited<ReturnType<typeof listPendingApprovals>> = [];
            try { approvals = await listPendingApprovals(undefined, "playground"); } catch { /* retry next poll */ }
            const next = approvals.find((a) => a.thread_id === parked.thread_id);
            if (next) {
              setResuming(false);
              setPendingApproval({
                approval_id: next.id, run_id: runId, status: "pending",
                tool: next.tool_name, args: next.tool_args, risk: next.risk_level,
                reasoning: next.thread_context_snippet ?? null,
                requested_by: null, requested_by_team: next.team ?? null,
                context: next.context, created_at: next.created_at, decided: false,
              });
              return; // panel's onDecided re-enters pollResumedResult(runId)
            }
          }
          continue; // still resuming — keep polling
        }
```
Bump the loop bound to ≥90 iterations (matches WorkflowBuilderPage's tolerance for the
resume+re-park window). `onDecided` (L140-144) already re-calls `pollResumedResult` with the
parent run id — no change needed; the loop closes itself.

### `resume()` (OPTIONAL, Task 4) — `services/declarative-runner/workflow_executor.py:876`
```python
async def resume(self, thread_id: str, decision: dict, trace_id: str | None = None) -> dict:
```
After `ainvoke`, before building `response_text`, check `result.get("__interrupt__")`; if
present, return `{"status": "awaiting_approval", "approval_id": <intr.value.approval_id>,
"thread_id": thread_id}`. Belt-and-suspenders — the registry DB check (Task 2) already handles
this; include only if trivially low-risk. If included, bump declarative-runner (Task 7).

---

## 7. Tasks (dependency-ordered — Task 1 is the failing reproduction)

### Per-context verification matrix (context × surface × test layer)

The bug is in the SHARED `_resume_and_advance` workflow-**member** re-entry, reached from all three
contexts via two decide endpoints (both **verified** in `research.md` §7:
`decide_approval` approvals.py L820, `decide_playground_approval` playground.py L1567). The
**deterministic backstop (T-S79-004b) is context-agnostic** (the re-park query never reads
`context`) and is the reliable guard for the shared core across every row below; the per-surface
cases prove the independent wiring and SKIP-loud on local capacity (never a false pass).

| Context | Surface (consumer) | Decide path → does it hit the member fix? | Bash | Browser / component | Exploratory |
|---|---|---|---|---|---|
| **Sandbox** | Workflow chat (`WorkflowChatPage`) | `decide_playground_approval` → member → **YES (fix)** | suite-79 T-S79-004a/b | Vitest `WorkflowChatPage.test.tsx` + Playwright `workflow-inline-approval-live` | quickstart §6 walkthrough |
| **Sandbox** | Agent chat (`AgentChatPage`) | `/resume-stream` (STREAM path) — re-parks via `stream_events` | suite-45 single-agent case | Vitest `AgentChatPage.test.tsx` (existing) | — |
| **Eval** | Workflow-member eval (`EvalResultsPage`) | `decide_playground_approval` via `eval-runner _self_approve` → member → **YES (fix)** | suite-73 T-S73-00X (SKIP-loud on capacity) | Vitest `EvalResultsPage.test.tsx` + Playwright `eval-v2-workflow` | — |
| **Eval** | Single-agent eval | `/resume-stream` + `_poll_durable` re-approve | suite-73 / suite-74 (SKIP-loud) | — | — |
| **Production** | Workflow-member console (`ApprovalsInboxPage`) | `decide_approval` (PATCH) → member → **YES (fix)** | suite-45 T-S45-00X prod case (SKIP-loud on capacity) | Vitest `ApprovalsInboxPage.test.tsx` + Playwright `approvals-inbox` | — |
| **Production** | Single-agent console | `decide_approval` → top-level returns early L178; re-park surfaces as a NEW queue row | suite-45 prod single-agent (SKIP-loud) | Vitest `ApprovalsInboxPage.test.tsx` | — |
| **ALL contexts** | Shared core `_resume_and_advance` | **DETERMINISTIC backstop** (seed 2nd pending → assert re-park) | **suite-79 T-S79-004b** | — | — |

Tasks 1-4 build+prove the sandbox slice end-to-end; Task 5 adds the eval + production + single-agent
bash coverage; Task 6 adds the eval + production browser/component coverage. Reproduce-first
(T-S79-004b) stays Task 1.

### Task 1 — Reproduce-first: extend `suite-79-workflow-hitl.sh` with T-S79-004 (MUST FAIL now)
**File:** `scripts/e2e/suite-79-workflow-hitl.sh`. Add a second driver block (same
`kubectl exec` + Keycloak-token pattern as the existing driver; token via `grant_type=password,
client_id=agentshield-studio, username=platform-admin, password=PlatformAdmin2024`).

- **T-S79-004a (best-effort live double-approval):** stream `POST /workflows/{WID}/runs/stream`
  with a prompt engineered to need **two** searches (e.g. *"Search the web for the current
  weather in Austin, Texas, and separately search the web for the current weather in Seattle,
  Washington. Report both."*). Drive park→approve (`POST /playground/approvals/{aid}/decide`)→
  poll the tree; if it re-parks (a new `pending` approval appears on the thread) → approve
  again → poll to terminal. Assert: run `completed`, both members `completed`, researcher output
  is a real answer (not the echoed prompt — e.g. contains a temperature/°/weather token, not the
  literal input), and **zero** `pending` approvals on the thread. If it never double-parks after
  a bounded retry → `SKIP T-S79-004a no-double-park (Ollama)` (**loud SKIP, never a false PASS**).
- **T-S79-004b (DETERMINISTIC backstop — the real gate):** run the base flow until the member
  parks on the first gate (reuse the T-S79-001 stream). Then, **before** approving:
  1. Read the first approval (`GET /approvals/{aid}`) for `agent_id/agent_name/team/thread_id`.
  2. Seed a **second** `pending` approval on the **same** `thread_id`: `POST /api/v1/approvals/`
     with the full `ApprovalCreate` body (schemas.py L301): `agent_id`, `agent_name`, `team`,
     `thread_id` (all copied from the first approval), `tool_name="web_search"`,
     `tool_args={"query":"__seeded_second_gate__"}` (different args ⇒ passes the
     `create_approval` idempotency guard), `risk_level="high"` (pattern is `^(high|critical)$`),
     `context="playground"`, `timeout_seconds=1800`. Capture the returned `seeded_id`. (The
     re-park check in the fix queries `thread_id` + `status='pending'` **context-agnostically**,
     so the seed only needs the right `thread_id` + pending status.)
  3. Approve the FIRST inline (`POST /playground/approvals/{aid}/decide {"decision":"approved"}`).
  4. Poll the tree for up to ~60s. **Assert the invariant:** at no point is
     `parent.status=='completed'` while the seeded approval is `pending`; and the end-state is
     `parent.status=='awaiting_approval'`, the parked child `awaiting_approval`, the seeded
     approval still `pending` (`GET /approvals/{seeded_id}`). This **fails against current code**
     (which marks `completed` + advances, ignoring the seeded pending) and **passes after Task 2**.
  5. Cleanup (best-effort, unasserted): decide the seeded approval so the run can drain.
- **Naming:** `T-S79-004a` / `T-S79-004b`. Print `PASS/FAIL/SKIP T-S79-004x …` lines the harness
  greps (same as the existing driver). No `run-all.sh` change (suite-79 already registered).

**Verify (fails now):** `bash scripts/e2e/suite-79-workflow-hitl.sh` → **T-S79-004b FAILs**
against the current image. (Requires the fixture deployed — see `quickstart.md` §3.) Also
`bash -n scripts/e2e/suite-79-workflow-hitl.sh` (syntax).
**Acceptance:** T-S79-004b reproduces the bug (red) before any fix; 004a either drives the live
double-park or SKIPs loudly.

### Task 2 — Registry core fix in `_resume_and_advance` (make T-S79-004b green)
**File:** `services/registry-api/routers/approvals.py`. Insert the pause-detection branch from
§6 into the reactive re-entry block (before L186 `child.status = member_status`). Do NOT touch
the durable branch (L87-100), the `is_durable` branch, or `decide_approval`/`decide_playground_approval`.

**Verify:**
- Syntax: `python3 -c "import ast; ast.parse(open('services/registry-api/routers/approvals.py').read())"`.
- Mappers configure: `cd services/registry-api && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers()"`.
- Build + deploy registry-api (`quickstart.md` §2), then `bash scripts/e2e/suite-79-workflow-hitl.sh`
  → **T-S79-004b PASS**, T-S79-001/002/003 still PASS. Drive the real flow per `quickstart.md` §6.
**Acceptance:** the registry re-parks on a pending approval and never completes-with-pending;
the single-approval flow is unchanged; the loop terminates on a clean resume.

### Task 3 — Frontend: `WorkflowChatPage.pollResumedResult` re-surfaces the 2nd gate
**Files:** `studio/src/pages/WorkflowChatPage.tsx` (+ its new `.test.tsx`). Apply the §6
`pollResumedResult` change; add `listPendingApprovals` to the `registryApi` import (L5-11).

**Verify:**
- Orphan grep: `grep -n "listPendingApprovals" studio/src/pages/WorkflowChatPage.tsx` (import +
  caller present).
- Typecheck: `cd studio && npm run typecheck`.
- Vitest: `cd studio && npm run test -- WorkflowChatPage` → new test green (mock
  `getWorkflowRunTree`/`listPendingApprovals` per `vi.mock('../api/registryApi')`, render via
  `renderWithProviders`).
- Best-effort Playwright live spec (`studio/e2e/workflow-chat-double-approval.spec.ts`) OR a
  manual step in `docs/testing/manual-ui-e2e-test-plan.md`: send a two-search prompt in the
  workflow chat, approve, confirm the panel re-appears for the 2nd gate, approve, confirm a
  grounded answer + no lingering panel.
  `bash scripts/studio-e2e.sh e2e/workflow-chat-double-approval.spec.ts`.
**Acceptance:** after an inline decision that re-parks, the inline `ConversationApprovalPanel`
re-renders with the second approval; on a clean resume the transcript renders the members'
outputs (existing behavior preserved).

### Task 4 — (OPTIONAL, low-risk) pod `resume()` returns awaiting_approval on `__interrupt__`
**File:** `services/declarative-runner/workflow_executor.py` (`resume`, L876-910). Defense-in-depth
only — the DB check (Task 2) is authoritative. If done: `python3 -c "import ast; ast.parse(...)"`,
and bump declarative-runner in Task 7. **If not done:** record in the gap ledger as
**deferred (intentional)** — the registry check fully covers the bug.
**Acceptance:** either shipped + declarative-runner bumped, or explicitly deferred in the ledger.

### Task 5 — Agent double-approval coverage (verify the single reactive-agent path)
**File:** `scripts/e2e/suite-45-hitl-e2e.sh`. Add T-S45-00X: single reactive agent (`hitl-agent`
fixture) chat that parks, resumes via the **stream** path (`/resume/{id}/stream`→`resume_stream`
→`stream_events`, which already emits `approval_requested` on re-interrupt), and — if it
double-parks — approves twice to a real answer with zero orphaned approvals; loud SKIP if it
won't double-park. **Fix only if it fails** (the brief expects this path already works via the
stream; do not change `resume_stream`/`stream_events` unless the test proves a gap — root-cause
first, don't weaken a working control).
**Verify:** `bash scripts/e2e/suite-45-hitl-e2e.sh` green (or loud SKIP).
**Acceptance:** the single-agent complete HITL flow is proven (or a real gap is found, then fixed
reproduce-first).

### Task 5b — Cross-context coverage: prove BOTH decide endpoints re-park (deterministic)
The bug is in the **shared** `_resume_and_advance`, but each context reaches it through a
different decide endpoint and surfaces the approval differently. Both must be proven — a
shared-core fix is verified per-consumer, not assumed.

| Context | Approval surface | Decide endpoint → resume | Test layer |
|---|---|---|---|
| Sandbox — agent chat | inline (`AgentChatPage`) | `decide_playground_approval` → `_resume_and_advance` | suite-45 (Task 5) + Playwright |
| Sandbox — workflow chat | inline (`WorkflowChatPage`) | `decide_playground_approval` → `_resume_and_advance` | **suite-79 T-S79-004b (Task 1)** + Vitest/Playwright (Task 3) |
| Evals | inline (playground context) | inline decide → `_resume_and_advance` | Task 5c (best-effort) + backstop below |
| Production — agent | console queue (`ApprovalsInboxPage`) | `decide_approval` (console) → `_resume_and_advance` | **Task 5b backstop** + Task 5c (best-effort) |
| Production — workflow member | console queue | `decide_approval` → `_resume_and_advance` | **Task 5b backstop** + Task 5c (best-effort) |

**File:** `scripts/e2e/suite-79-workflow-hitl.sh` (add **T-S79-004c**). The 004b backstop proves
the inline/playground endpoint re-parks; 004c proves the **console/production** endpoint
(`decide_approval`) re-parks through the same shared code — WITHOUT needing production pods:
1. Park a real reactive member (reuse the 004 stream to the first gate).
2. Seed a **second** `pending` approval on the same `thread_id` (identical `ApprovalCreate` body
   as 004b).
3. Approve the FIRST via the **console** endpoint `POST /api/v1/approvals/{aid}/decide`
   (`decide_approval`, NOT the playground one), body `{"decision":"approved"}`.
4. Poll the tree; **assert the same invariant** (never `completed` while the seeded approval is
   `pending`; end-state `awaiting_approval`, seeded approval still `pending`). Fails pre-fix,
   passes after Task 2 — proving `decide_approval` inherits the re-park fix.
5. **Static guard:** assert both entrypoints route through the fix — `grep -n
   "_resume_and_advance" services/registry-api/routers/approvals.py services/registry-api/routers/playground.py`
   must show `decide_approval` (approvals.py ~L820) and `decide_playground_approval`
   (playground.py ~L1565) both scheduling it. (Read both to confirm before asserting.)

**Verify:** `bash scripts/e2e/suite-79-workflow-hitl.sh` → T-S79-004c FAILs pre-fix, PASSes after
Task 2, alongside 004b.
**Acceptance:** the shared re-park is proven deterministically for BOTH the inline (sandbox+eval)
and console (production) decide paths — the whole context matrix's code path is guarded even where
full eval/production infra isn't warm.

### Task 5c — Cross-context surfaces: eval + production (best-effort, SKIP-loud on capacity)
Prove the eval and production **surface wiring** end-to-end where the infra is warm; where it
isn't, SKIP loudly (never a false PASS) — the Task 5b backstop already guards their shared code.

- **Evals (inline):** an eval run that triggers HITL parks inline (playground context). If it
  double-parks, approve twice → assert the run resumes to a real answer + zero orphaned approvals.
  Surface: `EvalResultsPage`. Extend the eval-v2 / side-effects area (near `suite-74`); requires
  the eval-runner + a dataset + a warm agent pod → **SKIP-loud** if absent. (The earlier report
  "the same issue is broken even in EVALS" is what this closes.)
- **Production (console):** a published + production-deployed agent (and, if feasible, a workflow)
  that parks twice → approve the 1st in the **console** (`ApprovalsInboxPage`) → assert the 2nd
  approval **re-appears in the queue** (not silently swallowed) → approve → run completes, zero
  orphaned approvals. Requires a published artifact + warm production pod → **SKIP-loud** if
  absent. Browser: `ApprovalsInboxPage` (+ `approvals-inbox.spec.ts`).
- **Honest boundary:** record in the gap ledger which of eval/production ran vs SKIPped-on-capacity
  this session; never let a capacity SKIP read as "verified". The deterministic Task 5b + 004b are
  the binding gates for the code these surfaces share.

**Verify:** run the eval + production cases; capture PASS or a loud SKIP diagnostic for each.
**Acceptance:** eval + production surfaces are exercised where warm, or explicitly SKIP-ledgered;
the shared code path is deterministically guarded regardless (Task 5b).

### Task 6 — Documentation (MANDATORY per CLAUDE.md rule 8) + experience/gap docs
**Files:** `docs/bugs/hitl-multi-approval-resume-regression.md` (postmortem),
`docs/debugging/012-hitl-second-approval-orphaned.md` (investigation log with the exact
kubectl/SQL/log commands from `quickstart.md` §5), `docs/experience/playground.md` (workflow-chat
re-park UX), `docs/testing/manual-ui-e2e-test-plan.md` (manual step + Known-gaps entry).
Cross-link the failing test (`suite-79` T-S79-004) and both docs to each other.
**Verify:** files exist and cross-link; `grep -rl "012-hitl-second-approval" docs/` finds the
back-reference from the bug doc.
**Acceptance:** both docs written and cross-linked; experience + manual-plan updated.

### Task 7 — Regression sweep + image bumps
**Files:** `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml`.
- registry-api `0.2.205→0.2.206` (deploy L292; values L622) — comment: "0.2.206: reactive
  workflow-member resume re-parks on a new pending approval (HITL multi-approval fix)".
- studio `0.1.154→0.1.155` (deploy L317; values L953) — comment: "0.1.155: workflow chat
  re-surfaces the 2nd inline approval on re-park".
- declarative-runner `0.1.58→0.1.59` (deploy L319; values `declarativeRunnerTag` L700) **only if
  Task 4 shipped**.
- **Regression sweep (map the blast radius, then run it — see `research.md` §6):**
  `bash scripts/e2e/suite-79-workflow-hitl.sh` (all PASS incl. 004a/b + **004c console re-park**),
  `bash scripts/e2e/suite-45-hitl-e2e.sh` (single + double-agent), the eval + production surface
  cases (Task 5c — PASS or loud SKIP), `cd studio && npm run test` (full Vitest incl.
  `WorkflowChatPage`, `WorkflowBuilderPage`, `ApprovalsInboxPage`), `cd studio && npm run
  typecheck`. Optionally `bash scripts/e2e/run-all.sh` for the full backend sweep.
**Verify:** `grep -n "0.2.206" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` (both
present); suites green.
**Acceptance:** both tag files bumped in the same change; suite-79 (incl. 004) + suite-45 +
Vitest + typecheck all green.

---

## 8. Acceptance Criteria (whole change is "done" when ALL hold)

1. **Reproduce-first honored:** T-S79-004b fails against the pre-fix image and passes after Task 2
   (evidence captured in the debugging log).
2. **Invariant enforced:** no run is ever `completed` while an approval on its thread is `pending`
   — asserted deterministically (004b) and, when the model cooperates, live (004a).
3. **Real answer, not echo:** on a real double-park, the researcher's final output is a grounded
   search answer, both members `completed`, zero pending approvals (004a or the manual walkthrough).
4. **UI re-surfaces the gate:** WorkflowChatPage re-renders the inline `ConversationApprovalPanel`
   for the second approval (Vitest deterministic; Playwright/manual best-effort).
5. **No regression:** suite-79 (single approval), suite-45, WorkflowBuilderPage Vitest/Playwright
   all green; the durable and single-agent-stream paths untouched.
5b. **Cross-context proven:** the re-park is verified for BOTH decide endpoints deterministically —
   `decide_playground_approval` (sandbox+eval inline, T-S79-004b) AND `decide_approval` (production
   console, T-S79-004c); the eval + production surfaces (Task 5c) are exercised where warm or
   explicitly SKIP-ledgered on capacity — never a false PASS.
6. **No orphan code:** the one new frontend import (`listPendingApprovals`) has a live caller;
   no new backend export.
7. **Docs:** postmortem + debugging log written and cross-linked; experience + manual plan updated;
   gap ledger records the Ollama best-effort case and Task 4's status.
8. **Build:** registry-api + studio tags bumped in `deploy-cpe2e.sh` **and** `values.yaml`
   (declarative-runner only if Task 4).

---

## 9. Complexity / Gap Tracking

| Item | Status | Note |
|---|---|---|
| Live double-park (T-S79-004a, Playwright) | **best-effort** | Ollama non-determinism; the deterministic backstop (004b) is the real gate. Loud SKIP, never a false PASS. |
| Pod `resume()` `__interrupt__` return (Task 4) | **deferred (intentional) unless Task 5 needs it** | DB check (Task 2) is authoritative; include only if trivially low-risk. |
| Single-agent double-approval (Task 5) | **verify-first** | Expected to already work via the stream path; fix only if the test proves a gap. |
| OPA fail-open bypass / workflow ledger gaps | **out of scope** | Separate items per the design brief. |
</content>

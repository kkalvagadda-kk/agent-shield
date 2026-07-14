# WS-1 Tasks — Durable engine real & resumable (shared harness) + workflow durable completion

**Source:** `ws1/plan.md` + `ws1/contracts/durable-harness.md` + `ws1/data-model.md`. **Re-grounded against the
post-WS-0 tree (2026-07-13).** Depends on WS-0 (landed, deployed, green).

## Grounding corrections (reasoned from the running code — supersede the plan's indicative specifics)
- **No migration** — confirmed. Reuses `run_steps`(+`approval_id`), `approvals`, `agent_runs.parent_run_id`/
  `thread_id`/`orchestrator_state`, and LangGraph `PostgresSaver` tables. Head is `0058` (WS-0).
- **The SDK already owns approval creation.** `hitl.require_approval` (`sdk/agentshield_sdk/hitl.py`) POSTs
  `/api/v1/approvals/` (fail-closed) **and** `interrupt(payload)` with `approval_id` in the interrupt value.
  So the harness on interrupt **reads** `approval_id` and emits `awaiting_approval`; the step-update callback
  **parks + links** the run — it must NOT create a second Approval (refines T4 vs the plan's wording).
- **Interrupt detection** = `graph.get_state(config).tasks[].interrupts[].value` (LangGraph v2 emits no
  on_interrupt in `astream_events`; see `streaming.py:_extract_interrupts`).
- **SDK durable primitives already exist:** `Runner.run_streamed` (astream_events via `stream_events`),
  `Runner.resume`, `get_checkpointer` (PostgresSaver), `POST /resume/{thread_id}` (`server.py:235`). WS-1 adds
  a step-emitting **wrapper** + a `/run` door — it does not re-implement the engine.
- **Production `/chat` HITL already works** (console approve + auto-resume; project memory
  `project_hitl_deployment_chat`). WS-1's durable park/resume must **reuse** that approvals + resume machinery,
  not fork it (parity).

---

## [X] T1 — Shared harness `agentshield_sdk/durable.py` (parity core) — DONE
- **Built:** `StepEmitter` (idempotent step POST + bookmark), `run_durable`/`resume_durable` (one `_drive`
  loop over `astream_events` v2 → one `run_steps` row per tool boundary; interrupt→`awaiting_approval` with
  `approval_id`; fail-closed on no-approval-id + drive-crash; normal end → `run_completed`+final text),
  `Bookmark` (step-index only — B3). httpx-only, no registry-api/langchain import (standalone).
- **Verify:** `sdk/tests/test_durable.py` **6/6 pass** (real steps not skeleton; park; fail-closed;
  resume completes; bookmark skip; crash fails-loud). Run: `python -m pytest sdk/tests/test_durable.py`.
- **Note:** `runner.py` needs **no change** — the harness drives the compiled graph directly; consumers pass
  their graph to `run_durable`.
- **Deferred to in-cluster (suite-55):** exact LangGraph event names / final-text node — the unit test proves
  structure with a fake graph; real event shape is validated on the cluster in T8.

## [X] T2 — declarative-runner consumes the harness (replace the 2-step skeleton) — DONE (deployed 0.1.38)
- **Files:** `services/declarative-runner/main.py` (`/run`), `checkpoint.py` (→ step-index bookmark),
  `run_executor.py` (park poster). Replace `input_processing`/`agent_execution` skeleton with
  `run_durable(graph, input, thread_id, callback_url, emitter)`. **Re-ground line numbers first.**
- **Verify:** `grep -n input_processing services/declarative-runner/main.py` → gone; suite-55 declarative case.

## [X] T3 — SDK native `/run` (`sdk/agentshield_sdk/server.py`) — DONE
- `POST /run` mounts `run_durable` over `Runner`'s compiled graph; reuse existing `/resume/{thread_id}`.
- **Verify:** `grep -n '"/run"' sdk/agentshield_sdk/server.py`.

## [X] T4 — Production HITL park + resume (registry-api) — DONE (deployed, suite-55 5/5)

**Verified on cluster:** `_resume_and_advance` resumes a durable `/run` run THROUGH the harness
(discriminator = RunStep rows + `id==thread_id`, no parent → passes `run_id`+`callback_url`); chat +
workflow-member resume unchanged (suite-55 T-002/003). Inbox authority already existed
(ApprovalAuthority + admin-role filter in `list_approvals`). Regression: suite-36 4/0, suite-54 14/14.
**Pre-existing fixture note:** suite-45 HITL-trigger cases fail because `web_search` is seeded at
`risk=medium` (no HITL fires) — upstream of WS-1 (approval *creation*, not resume); resume-path
T-S45-006 passes. Recorded in the gap ledger.

**Original T4 contract (now satisfied):**
- `routers/internal.py` step-update `status=awaiting_approval` → **park + link** `run_steps.approval_id`
  (Approval already exists from the SDK) — set `AgentRun.status=awaiting_approval` (WS-0 already does the
  status set; T4 adds the `approval_id` link + resume trigger). `routers/approvals.py` decide → dispatch
  `/resume/{thread_id}` to the runner; `agent:reviewer` authority on the inbox list. Fail-closed on
  link/dispatch error.
- **Verify:** mapper import; suite-55 T-S55-003 (fail-closed) + park→decide→resume→complete.

## [ ] T5 — Workflow D3: all-four-mode durable resume (`workflow_orchestrator.py`)
- `_halt_for_approval` checkpoints the cursor (node for conditional/handoff; accumulator for supervisor);
  add `_run_{conditional,handoff,supervisor}_from`; `resume_orchestration` dispatches per `mode`.
- **Verify:** suite-56 (4 modes park→resume→advance→complete; supervisor accumulator survives).

## [ ] T6 — Workflow D4: "+ Visibility" durable members via `/run`
- `workflow_orchestrator._dispatch` — durable members via `/run` (parent_run_id=member run); reactive stay
  `/chat`. Child `run_steps` in the tree. **Documented limitation:** within-member crash-restart out of scope.

## [ ] T7 — Approval UI parity + inbox authority (Studio)
- One `<ApprovalCard>` mounted by `HitlPanel`/`ConversationApprovalPanel`/`ApprovalsInboxPage` (M1);
  inbox `agent:reviewer` gate. Vitest + typecheck.

## [ ] T8 — E2E + Playwright + deploy
- `suite-55-durable-engine.sh` (declarative **and** SDK: park→approve→resume→complete; kill-pod→resume; real
  steps), `suite-56-workflow-durable-modes.sh` (4 modes + member steps), register in `run-all.sh`;
  `approvals-inbox.spec.ts`; bump registry-api + **declarative-runner** (changed here) + studio in BOTH files;
  SDK ships via pip into agent images (rebuild note); `docs/experience/playground.md`.

## Gap ledger (WS-1)
- Within-member crash-restart → deferred (full-nested follow-up). Daemon approver routing → WS-2. Inbox nav
  badge → WS-6. checkpoint bookmark vs PostgresSaver dual-write edge cases → low debt.

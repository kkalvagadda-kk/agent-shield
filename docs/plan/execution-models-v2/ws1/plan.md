# WS-1 Implementation Plan — Durable single-agent, real & resumable (shared harness) + workflow durable completion

**Slice:** WS-1 of Execution Models v2 (spec `docs/design/todo/execution-models-v2-e2e.md` §5 WS-1;
critique overlay `execution-models-v2-critique-and-fixes.md` B3/B4/M1/M2/S3). **This plan covers WS-1 ONLY.**
**Depends on WS-0 landed & green** (shape-aware dispatch + `agent_class` authoring).
**Companion artifacts:** `data-model.md` (checkpoint-of-record consolidation), `contracts/durable-harness.md`
(the shared `run_steps` emitter + `/run` handler + HITL park contract).

> **Migration numbers are PROVISIONAL.** Head was `0057` on 2026-07-12; WS-0 takes `0058`. WS-1 needs
> **no migration** — it reuses existing tables (`run_steps`, `approvals`, `run_steps.approval_id`,
> `agent_runs.parent_run_id`/`thread_id`, LangGraph `PostgresSaver` checkpoint tables). Confirm at impl.

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

## 1. Goal

Turn durable from a **facade into a real engine** for single agents, then complete the durable **workflow**
story (D3 all-four-modes resume, D4 "+ Visibility" members). After WS-0, a durable triggered run reaches
`/run` but the runner only writes a 2-step skeleton and never checkpoints — so nothing actually resumes.
WS-1 closes that. Concretely, after WS-1:

1. **Real per-node steps.** LangGraph `astream_events()` node/tool boundaries map to one `run_steps` row
   each, replacing the declarative-runner's hardcoded `input_processing`/`agent_execution` skeleton
   (`declarative-runner/main.py:543-624`). Built **once** as a shared emitter in `agentshield_sdk`.
2. **Checkpoint save is wired.** The `save_checkpoint` orphan (`checkpoint.py:30`, no caller) is either
   wired or **deleted** in favour of consolidating on LangGraph `PostgresSaver` (thread_id) as the single
   checkpoint of record + a thin step-index bookmark. `_resume_interrupted_runs` actually resumes — a
   pod killed mid-run re-enters from the checkpoint, not "lost state" (B3).
3. **HITL park is emitted (production).** On OPA `require_approval`, the run creates an `Approval`, writes
   an `awaiting_approval` step, durably waits; on decide → resumes from checkpoint. The
   `RunExecutor.await_approval` orphan (`run_executor.py:64`) gets a live caller. **Fail-loud + fail-closed
   (retro #4):** an approval-write error logs the full signal and **denies** — never swallow-and-interrupt
   (that swallow hung production chat = bug 009).
4. **SDK durable `/run` exists.** `sdk/server.py` mounts a native `/run` wired to the same shared emitter.
   SDK agents gain durable on one image rebuild (no migration/back-compat burden — active dev).
5. **Global Approvals Inbox (production) — EXTEND, don't build.** `ApprovalsInboxPage.tsx` already exists;
   WS-1 makes it authority-checked (`agent:reviewer`) and unifies the three approval renderers behind one
   parity component (M1 — see §3). Aligns with gap-analysis TODO-1 (inbox badge → WS-6).
6. **Workflow durable completion (D3 + D4).**
   - **D3 — all four modes resume.** Apply the `_run_sequential_from` re-entry pattern to
     conditional/handoff/supervisor: checkpoint the traversal cursor in `_halt_for_approval`
     (`workflow_orchestrator.py:249` — today it stores only `{mode,team,workflow_id}`, **not** the cursor),
     add `_run_{conditional,handoff,supervisor}_from` re-entry, dispatch per mode in `resume_orchestration`
     (`:415`). Conditional + handoff are Markovian (checkpoint the current node); supervisor persists its
     accumulator (workers done + outputs + iteration).
   - **D4 — "+ Visibility" members.** Durable members dispatch via `/run` (this slice's harness) not
     `/chat`, so their child `run_steps` (with `parent_run_id`) show in the run tree + StepTracker zoom.
     `agent_runs.parent_run_id` already exists (`models.py:1509`). **Documented limitation:** within-member
     crash-restart NOT included (orchestrator re-dispatches only after an approval decision, not a crash).

**Out of scope (later slices):** daemon service-identity `run_by` / OPA `user_identity_ok` / async reviewer
routing (WS-2 — WS-1's inbox is user_delegated: approver = the run's initiating user/manager); scheduled
operate surface (WS-3); webhook client-id (WS-4); Kaniko (WS-5); within-member crash-restart ("full nested"
tier, §9 of spec).

## 2. Architecture — the shared durable harness (parity core)

The retro root cause is parallel code. WS-1's central rule: **one durable engine, two consumers.** The
declarative-runner **already imports `agentshield_sdk.checkpointer`**, and the SDK already has the
checkpointer + `interrupt()` + `/resume/{thread_id}` (`sdk/server.py:235`) + `astream_events()`
(`runner.py:166`). So the harness is built **inside `agentshield_sdk`** and consumed by both — not mirrored.

```
                    agentshield_sdk/durable.py   (NEW — the one harness)
                    ├─ StepEmitter: astream_events() node/tool boundary → run_steps callback POST
                    ├─ run_durable(graph, input, callback_url, thread_id): drive graph, emit steps,
                    │     on interrupt() → emit awaiting_approval + create Approval (fail-closed)
                    └─ resume_durable(thread_id, decision): re-enter from PostgresSaver checkpoint
                          │                    │
        ┌─────────────────┘                    └──────────────────┐
   declarative-runner/main.py:543 /run             sdk/agentshield_sdk/server.py  (NEW /run)
     replaces the 2-step skeleton with              mounts run_durable for custom-container agents
     run_durable(...) over the workflow graph
                          │
                          ▼ POST /internal/runs/{id}/step-update   (the WS-0 callback endpoint)
                    registry-api writes RunStep(AgentRun); on run_completed → status=completed;
                    on awaiting_approval → Approval row + run parked
```

**Checkpoint-of-record (B3, data-model.md):** LangGraph `PostgresSaver` keyed by `thread_id` is the single
source of graph state. `declarative-runner/checkpoint.py` is reduced to a **step-index bookmark**
(`last_completed_step`) for the callback's idempotency — the two competing checkpoints collapse to one
authoritative + one bookmark. `save_checkpoint`'s orphan status is resolved by this consolidation, not by
adding a second caller to a redundant store.

**Approval parity (M1):** three renderers exist today — `HitlPanel.tsx`, `chat/ConversationApprovalPanel.tsx`,
`ApprovalsInboxPage.tsx`. WS-1 extracts one `<ApprovalCard>` presentational component all three mount, so a
new approval field is added in one place. The inbox adds an `agent:reviewer` authority gate.

## 3. Migration / Schema

**None.** Reuses `run_steps` (+ `approval_id` FK), `approvals`, `agent_runs.parent_run_id`/`thread_id`,
`orchestrator_state` (0032), and LangGraph `PostgresSaver` tables (auto-created by the checkpointer). See
`data-model.md` for the checkpoint consolidation rationale (no DDL, only which store is authoritative).

## 4. Constitution / retro gates (condensed — full matrix pattern per WS-0 §4)

- **Parity = shared code:** the durable harness lives once in `agentshield_sdk`; declarative-runner + SDK
  both import it (grep proves no mirrored emitter). Approval UI unified behind `<ApprovalCard>`.
- **Golden-path per environment:** bash suite runs the durable journey for **both a declarative and an SDK
  agent** (park → approve → resume → complete; kill-pod-mid-run → resumes); Playwright drives the Global
  Approvals Inbox approve. Fails (not skips) if the agent fixture is unreachable.
- **Ship the gate's producer:** the `awaiting_approval` step's producer (park emit) ships with the reader
  (resume path) — no orphan gate.
- **Fail-loud + fail-closed:** approval-write error → deny + full-signal log, asserted by a test.
- **No-Bandaid:** consolidate to one checkpoint-of-record (don't guard two); `save_checkpoint` orphan
  deleted/wired by design, not left dead; HITL park emitted structurally, not sniffed.

## 5. File Structure (created/modified)

### SDK — the shared harness
| File | C/M | Responsibility |
|---|---|---|
| `sdk/agentshield_sdk/durable.py` | **C** | `StepEmitter` + `run_durable` + `resume_durable` — the one harness (astream_events→steps, interrupt→park, PostgresSaver re-entry). |
| `sdk/agentshield_sdk/server.py` | M | Mount native `POST /run` wired to `run_durable`; `/resume/{thread_id}` already present, reuse it. |
| `sdk/agentshield_sdk/runner.py` | M | Expose the `astream_events` node stream to `StepEmitter` (already yields events at `:166`). |

### declarative-runner — consume the harness
| File | C/M | Responsibility |
|---|---|---|
| `services/declarative-runner/main.py` | M | Replace the `:543-624` 2-step skeleton `/run` with `run_durable(...)` over the workflow graph. |
| `services/declarative-runner/checkpoint.py` | M | Reduce to a step-index bookmark; delete the redundant graph-state save (B3). |
| `services/declarative-runner/run_executor.py` | M | Wire `RunExecutor.await_approval` (`:64`) as the park callback poster; used by `run_durable`. |

### registry-api — park + resume + inbox authority
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/routers/internal.py` | M | `/internal/runs/{id}/step-update` (from WS-0) handles `awaiting_approval` → create `Approval` + park; add resume dispatch on approval decide. |
| `services/registry-api/routers/approvals.py` | M | On decide → resume the parked run (single-agent) via the harness `/resume`; `agent:reviewer` authority check on the inbox list. |
| `services/registry-api/workflow_orchestrator.py` | M | D3: checkpoint cursor in `_halt_for_approval`; add `_run_{conditional,handoff,supervisor}_from`; per-mode dispatch in `resume_orchestration`. D4: dispatch durable members via `/run`. |

### Studio — approval parity + inbox authority
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/components/approvals/ApprovalCard.tsx` | **C** | One presentational approval card (M1). |
| `studio/src/components/playground/HitlPanel.tsx` | M | Mount `<ApprovalCard>`. |
| `studio/src/components/chat/ConversationApprovalPanel.tsx` | M | Mount `<ApprovalCard>`. |
| `studio/src/pages/ApprovalsInboxPage.tsx` | M | Mount `<ApprovalCard>`; `agent:reviewer` gate; badge count. |

### Tests + infra
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-55-durable-engine.sh` | **C** | Durable journey (declarative + SDK): park→approve→resume→complete; kill-pod→resume; real steps not skeleton. |
| `scripts/e2e/suite-56-workflow-durable-modes.sh` | **C** | All 4 modes park→resume→advance→complete; supervisor accumulator survives; durable member steps in tree. |
| `scripts/e2e/run-all.sh` | M | Register suite-55, suite-56. |
| `studio/e2e/approvals-inbox.spec.ts` | M | Inbox pending → approve → run completes; reviewer gate. |
| `studio/src/components/approvals/ApprovalCard.test.tsx` | **C** | Vitest: card renders all approval states. |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump registry-api, declarative-runner (now changed), studio; SDK is pip-packaged into agent images (rebuild note). |
| `docs/experience/playground.md` | M | Durable park/resume + inbox behavior. |

## 6. Tasks (dependency-ordered)

### T1 — Shared harness in `agentshield_sdk` (`durable.py`) — the parity core
- **Files:** `sdk/agentshield_sdk/durable.py` (C), `runner.py` (M).
- **Contract:** `contracts/durable-harness.md` — `StepEmitter.emit(step)`, `run_durable(graph, input,
  callback_url, thread_id) -> RunResult`, `resume_durable(thread_id, decision)`. astream_events node
  boundary → one `run_steps` callback; `interrupt()` → `awaiting_approval` step + park signal.
- **Acceptance:** unit test drives a 3-node graph → 3 step callbacks; an interrupt → an `awaiting_approval`
  callback then a wait; resume re-enters and completes. No import of registry-api (SDK stays standalone).
- **Deps:** WS-0 (callback endpoint exists).
- **Verify:** `cd sdk && python3 -m pytest tests/test_durable.py`; `python3 -c "import ast; ast.parse(open('agentshield_sdk/durable.py').read())"`.

### T2 — declarative-runner consumes the harness (replace the skeleton)
- **Files:** `declarative-runner/main.py` (M), `checkpoint.py` (M), `run_executor.py` (M).
- **Contract:** `/run` calls `run_durable` over the workflow graph; `checkpoint.py` = step-index bookmark
  only; `RunExecutor.await_approval` wired as the park poster.
- **Acceptance:** a durable declarative run writes **real** per-node `run_steps` (not `input_processing`/
  `agent_execution`); killing the pod mid-run → `_resume_interrupted_runs` re-enters from PostgresSaver.
- **Deps:** T1.
- **Verify:** `ast.parse` all three; `grep -n "input_processing" services/declarative-runner/main.py` → gone.

### T3 — SDK native `/run`
- **Files:** `sdk/agentshield_sdk/server.py` (M).
- **Contract:** `POST /run` mounts `run_durable`; reuses existing `/resume/{thread_id}`.
- **Acceptance:** an SDK-container agent serves `/run`; durable journey identical to declarative.
- **Deps:** T1.
- **Verify:** `grep -n '"/run"' sdk/agentshield_sdk/server.py`.

### T4 — Production HITL park + resume (registry-api)
- **Files:** `routers/internal.py` (M), `routers/approvals.py` (M).
- **Contract:** step-update `status=awaiting_approval` → create `Approval`(+`run_steps.approval_id`) + park;
  approval decide → dispatch `/resume/{thread_id}` to the runner; **fail-closed** on approval-write error.
- **Acceptance:** durable run with a gate parks (`agent_runs.status=awaiting_approval`); decide → resumes →
  completes; an injected approval-write failure → run **failed/denied**, never hangs.
- **Deps:** T2 (park emitted), T3 (SDK resume).
- **Verify:** `ast.parse` + mapper import; suite-55 T-S55-003 (fail-closed).

### T5 — Workflow D3: all-four-mode durable resume
- **Files:** `workflow_orchestrator.py` (M).
- **Contract:** `_halt_for_approval` checkpoints the cursor (node for conditional/handoff; accumulator for
  supervisor) into `orchestrator_state`; `_run_{conditional,handoff,supervisor}_from` re-entry mirrors
  `_run_sequential_from`; `resume_orchestration` dispatches per `mode`.
- **Acceptance:** each of conditional/handoff/supervisor parks at a member gate → approve → advances →
  completes; supervisor's worker-outputs + iteration survive the pause.
- **Deps:** T4 (member park/resume path).
- **Verify:** suite-56 (all 4 modes); `grep -n "_run_conditional_from\|_run_handoff_from\|_run_supervisor_from" workflow_orchestrator.py`.

### T6 — Workflow D4: "+ Visibility" durable members via `/run`
- **Files:** `workflow_orchestrator.py` (M, `_dispatch:69`).
- **Contract:** durable members dispatched via `/run` with `parent_run_id`=member run; reactive members
  stay `/chat`. Child `run_steps` carry `parent_run_id` → run tree.
- **Acceptance:** a durable member's own `run_steps` appear under the parent in the tree; StepTracker zooms.
- **Deps:** T2, T5.
- **Verify:** suite-56 member-steps assertion; **documented limitation** (crash-restart) in gap ledger §7.

### T7 — Approval UI parity + inbox authority (Studio)
- **Files:** `ApprovalCard.tsx` (C), `HitlPanel.tsx`/`ConversationApprovalPanel.tsx`/`ApprovalsInboxPage.tsx` (M).
- **Contract:** one `<ApprovalCard>` all three mount (M1); inbox `agent:reviewer` gate + badge.
- **Acceptance:** `npm run typecheck` clean; a field added to `<ApprovalCard>` shows in all three surfaces
  (proven by Vitest); non-reviewer sees no decide button.
- **Deps:** T4.
- **Verify:** `cd studio && npm run typecheck && npm run test`.

### T8 — E2E suites + Playwright + deploy
- **Files:** `suite-55-durable-engine.sh` (C), `suite-56-workflow-durable-modes.sh` (C), `run-all.sh` (M),
  `approvals-inbox.spec.ts` (M), `deploy-cpe2e.sh`+`values.yaml` (M), `docs/experience/playground.md` (M).
- **Acceptance:** suites green on the cluster for declarative **and** SDK; Playwright inbox approve green;
  image tags bumped in both files (registry-api, declarative-runner, studio); SDK rebuild note.
- **Deps:** T1–T7.
- **Verify:** `bash scripts/e2e/suite-55-durable-engine.sh && bash scripts/e2e/suite-56-workflow-durable-modes.sh`; `bash scripts/studio-e2e.sh`.

## 7. Gap Ledger
| Item | Status | Note |
|---|---|---|
| Within-member crash-restart (pod crash mid-execution, not at a gate) | **deferred (intentional) → "full nested" follow-up** | Orchestrator re-dispatches only after an approval decision; a mid-member crash loses that member's in-flight progress (D4 documented limitation). |
| Daemon service-identity approver routing | deferred (intentional) → WS-2 | WS-1 inbox is user_delegated (approver = initiating user/manager). |
| Inbox badge in global nav | deferred (intentional) → WS-6 | gap-analysis TODO-1; WS-1 ships the inbox page + authority, WS-6 the nav badge. |
| checkpoint.py bookmark vs PostgresSaver dual-write edge cases | not-yet-hardened (debt, low) | Bookmark is advisory for callback idempotency; PostgresSaver is authoritative. |

No orphan flags: `run_durable`/`resume_durable` consumed by declarative-runner + SDK; `await_approval` wired;
`awaiting_approval` producer ships with the resume reader; `<ApprovalCard>` mounted by all three renderers.

## 8. Execution Notes
- **Build the harness first (T1), prove it standalone**, then wire the two consumers — do not fork the emitter.
- **One checkpoint of record.** Resist re-animating `checkpoint.py`'s graph-state save; PostgresSaver wins.
- **Fail-closed is a test, not a comment** — assert the approval-write-error → denied path (bug 009 guard).
- **SDK ships via pip into agent images** — bumping the SDK means agent images rebuild; note it in the deploy
  header (no separate image tag for the SDK itself).
- **declarative-runner tag DOES bump here** (unlike WS-0) — it changed.

# Tasks — Fix HITL multi-approval resume regression

**Source:** `plan.md` (§6 has the exact insert code; §7 the full per-task detail). This file is the
executable checkbox view. **Reproduce-first: T001 is the failing test — it must go RED against the
current deployed image BEFORE T002 (the fix).**

**Total tasks:** 14 (11 implementation + 3 checkpoint gates)
**Phases:** 6 implementation + 3 checkpoints
**Parallel:** minimal — control-flow change, run sequential for accuracy.

| Phase | Tasks | Proves |
|---|---|---|
| P1 Reproduce | T001 | The bug exists (RED) — deterministic backstop |
| CP1 | CP1a | Confirm T-S79-004b FAILs against current image |
| P2 Core fix | T002 | Registry re-parks (backstop GREEN) |
| P3 Frontend | T003, T004 | UI re-surfaces the 2nd inline gate |
| CP2 | CP2a, CP2b | Build+deploy 0.2.206/0.1.155 → 004b/004c GREEN, Vitest GREEN |
| P4 Cross-context | T005, T006, T007 | Both decide endpoints + agent + eval/production surfaces |
| P5 Docs | T008, T009, T010 | Postmortem + debugging log + experience/manual |
| P6 Ship | T011 | Image bumps in BOTH files |
| CP3 | CP3a | Full regression sweep |

---

## Phase 1 — Reproduce-first (RED)

- [ ] [T001] Extend `scripts/e2e/suite-79-workflow-hitl.sh` — add **T-S79-004b** (deterministic backstop: park a member, seed a 2nd `pending` approval on the same `thread_id` via `POST /api/v1/approvals/`, approve the 1st via `POST /playground/approvals/{aid}/decide`, assert the registry re-parks — parent+child `awaiting_approval`, seeded approval still `pending`, run NEVER `completed`-with-pending) and **T-S79-004a** (best-effort live two-search double-approval; loud SKIP if the model won't double-park). Exact steps: plan.md §7 Task 1. — `scripts/e2e/suite-79-workflow-hitl.sh`

## Checkpoint 1 — Reproduce (RED)
_Gate: T001 complete. Run against the CURRENT deployed image (no fix yet)._
_What you prove: T-S79-004b FAILS today (member wrongly `completed`, seeded approval orphaned)._
- [ ] [CP1a] Run `bash scripts/e2e/suite-79-workflow-hitl.sh`; assert T-S79-004b is present and **RED**. — `scripts/smoke-test-cp1-repro-red.sh`

> Pass criteria for CP1: T-S79-004b **fails** (reproduces the bug). If it passes here, the test is wrong — fix the test before proceeding.

---

## Phase 2 — Registry core fix (GREEN)

- [ ] [T002] Insert the authoritative re-park branch into `_resume_and_advance` reactive re-entry block (before `child.status = member_status`, ~L186): if a `pending` Approval exists on `thread_id`, set `child.status='awaiting_approval'`, commit, `return` (do NOT advance). Exact code: plan.md §6. Do NOT touch the durable branch or the decide endpoints. — `services/registry-api/routers/approvals.py`

## Phase 3 — Frontend re-surface the 2nd gate

- [ ] [T003] `WorkflowChatPage.pollResumedResult`: on `parent.status==='awaiting_approval'`, fetch `listPendingApprovals(undefined,'playground')`, correlate by the parked child's `thread_id`, re-render the inline `ConversationApprovalPanel`; add the `listPendingApprovals` import; bump the poll bound to ≥90. Exact code: plan.md §6. — `studio/src/pages/WorkflowChatPage.tsx`
- [ ] [T004] Vitest `WorkflowChatPage.test.tsx`: mock `getWorkflowRunTree` (awaiting_approval + parked child) + `listPendingApprovals` (new gate) → assert the inline panel re-renders; and terminal render on `completed`. — `studio/src/pages/WorkflowChatPage.test.tsx`

## Checkpoint 2 — Fix (GREEN)
_Gate: T002+T003+T004 complete._
_What you prove: build+deploy the fixed images → the backstop + console re-park pass, single-approval unchanged, Vitest green._
- [ ] [CP2a] Deploy: build registry-api `0.2.206` + studio `0.1.155`, `helm upgrade … --reset-values --force-conflicts`, wait for rollout. — `scripts/deploy-cp2-hitl-fix.sh`
- [ ] [CP2b] Smoke: `bash scripts/e2e/suite-79-workflow-hitl.sh` → T-S79-004b **PASS** + 001/002/003 still PASS; `cd studio && npx vitest run src/pages/WorkflowChatPage.test.tsx` green. — `scripts/smoke-test-cp2-hitl-green.sh`

> Pass criteria for CP2: 004b flips RED→GREEN; no single-approval regression; Vitest green.

---

## Phase 4 — Cross-context coverage (sandbox · evals · production)

- [ ] [T005] Add **T-S79-004c** to `suite-79`: same seed-a-2nd-pending backstop, but approve the 1st via the **console** endpoint `POST /api/v1/approvals/{aid}/decide` (`decide_approval`) → assert re-park (proves the production/console decide path inherits the fix). + static guard: grep both `decide_approval` (approvals.py) and `decide_playground_approval` (playground.py) schedule `_resume_and_advance`. Plan.md §7 Task 5b. — `scripts/e2e/suite-79-workflow-hitl.sh`
- [ ] [T006] Add single reactive-**agent** double-approval to `suite-45-hitl-e2e.sh` (T-S45-00X, stream resume path). Verify-first: fix only if it fails. Plan.md §7 Task 5. — `scripts/e2e/suite-45-hitl-e2e.sh`
- [ ] [T007] Best-effort eval + production surface checks (SKIP-loud on capacity): eval HITL double-approval near the eval-v2/side-effects suites (`EvalResultsPage`) + production console double-approval (`ApprovalsInboxPage` / `approvals-inbox.spec.ts`). Plan.md §7 Task 5c. — `scripts/e2e/suite-74-*.sh` + `studio/e2e/approvals-inbox.spec.ts`

## Phase 5 — Documentation (CLAUDE.md rule 8)

- [ ] [T008] `docs/bugs/hitl-multi-approval-resume-regression.md` — postmortem (Found/Fixed + image tag, Symptom, Root cause, Fix). — `docs/bugs/hitl-multi-approval-resume-regression.md`
- [ ] [T009] `docs/debugging/012-hitl-second-approval-orphaned.md` — investigation log (expected chain, exact kubectl/SQL/log commands from quickstart.md §5, evidence, root cause, fix); cross-link the test + the bug doc. — `docs/debugging/012-hitl-second-approval-orphaned.md`
- [ ] [T010] Update `docs/experience/playground.md` (re-park re-surfaces the inline panel) + `docs/testing/manual-ui-e2e-test-plan.md` (manual double-approval step + Known-gaps: Ollama best-effort, optional Task/pod-resume, eval/production capacity SKIPs). — `docs/experience/playground.md`, `docs/testing/manual-ui-e2e-test-plan.md`

## Phase 6 — Ship

- [ ] [T011] Image bumps in BOTH files: registry-api `0.2.205→0.2.206`, studio `0.1.154→0.1.155` in `scripts/deploy-cpe2e.sh` AND `charts/agentshield/values.yaml` (declarative-runner `0.1.58→0.1.59` ONLY if the optional pod `resume()` change lands). — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## Checkpoint 3 — Regression sweep
_Gate: all phases complete._
_What you prove: the whole HITL surface is green across contexts; no neighbor broke._
- [ ] [CP3a] `bash scripts/e2e/suite-79-workflow-hitl.sh` (004a/b/c) + `bash scripts/e2e/suite-45-hitl-e2e.sh` + eval/production best-effort + `cd studio && npx vitest run` + `npm run typecheck`. — `scripts/smoke-test-cp3-regression.sh`

---

## Notes
- node IS on this host (`/opt/homebrew/bin`, v26.5.0): vitest + Playwright run locally.
- Deploy: `docker build -t registry.internal/agentshield/<svc>:<tag> services/<svc>/` (studio: `studio/`) → `helm upgrade --install agentshield charts/agentshield -n agentshield-platform --reset-values --force-conflicts --timeout 20m`.
- In-pod auth: Keycloak token (grant_type=password, client_id=agentshield-studio, username=platform-admin, password=PlatformAdmin2024); current platform-admin sub = `047fad5f-f38c-430a-bfba-6e4d9009314b`.
- Optional pod `resume()` `__interrupt__` return (plan Task 4) is deferred/defense-in-depth — the registry DB check (T002) is authoritative.

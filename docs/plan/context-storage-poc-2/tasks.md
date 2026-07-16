# POC-2 Tasks — Per-agent attribution + eval transcript + share-context toggle

**Source artifacts**: `plan.md` (§5 File Structure, §6 Key Interfaces, §7 Tasks, §9 Gap Ledger, §10 Execution Notes), `research.md`, `data-model.md`, `contracts/{sse-frames,memory-read,update-composite-workflow}.md`, `quickstart.md`; design `docs/design/context-storage-ux-roadmap.md` §3 + `context-storage-architecture.md` §10. Grounded against registry-api `0.2.188`, studio `0.1.140`.

## Counts & layout
- **19 implementation tasks** (T001–T019) + **9 checkpoint tasks** (CP1a–CP4c) = **28 total**.
- **8 implementation phases** (Setup → Foundational → Backend frames → Chat surfaces → CatalogChat → Eval transcript → Toggle → Polish) + **4 checkpoint phases** = **12 phases**.
- **Parallel opportunities** (`[P]` = different files, no incomplete-sibling dep): `T002‖T003` (pure libs), `T004‖T005` (their tests), `T011‖T012` (AgentChatPage ‖ ChatPane), `T017‖T018` (experience doc ‖ gap ledger). Everything else is serial (shared file or hard dep).
- **Checkpoint locations**: **CP1** after Foundational+Backend (libs green + `author` frames present); **CP2** after the two chat-surface phases (surfaces wired, no orphans); **CP3** after Eval+Toggle (transcript + `memory_enabled` persistence wired); **CP4** = final deploy-and-verify on EKS via the sanctioned Helm path.

## Conventions
- Implementation: `- [ ] [Tnnn] [P] Desc — \`path\``. `[P]` only when the task touches different files than its incomplete siblings and has no dependency on one.
- Checkpoint: `- [ ] [CPn?] Desc — \`scripts/checkpoints/…​.sh\``. Never `[P]`. Each is an executable `#!/usr/bin/env bash` + `set -euo pipefail` script with real `npm`/`python`/`grep`/`kubectl`/`curl`/`jq` assertions, exits 0 on pass, ends `echo "PASS"`.
- Image-versioning rule (CLAUDE.md Post-impl #2): **both** registry-api (T009) **and** studio (T019) bump the tag in all three of `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`, each with a changelog comment.
- No-Bandaid: `AttributedBubble` + reducer take an **explicit `author`**; single-agent is the degenerate one-speaker case, never a code fork (plan §2).

---

## Phase 1 — Setup (shared API type contract)

- [ ] [T001] Add optional `message_kind?: string` + `scope?: string` to `MemoryMessage` (~L1536) and add `scope?: string` to the `listMemory` params object (~L1548) so it forwards `?scope=workflow_run`; existing no-scope callers unchanged — `studio/src/api/registryApi.ts`

> `CompositeWorkflow.memory_enabled` (L542) and `CreateCompositeWorkflowRequest.memory_enabled?` (L586) already exist — **no type change** there (contract `update-composite-workflow.md`); T015 only starts sending the field.

---

## Phase 2 — Foundational (shared building blocks: color, reducer, bubble)

- [ ] [T002] [P] `agentColor(name)` — deterministic hash (FNV-1a / char-sum) mod a **fixed 8-entry Tailwind palette** of `{bg,text,border,dot}` static class strings (no dynamic `bg-${x}`); empty/undefined-safe. Pure — §6.2 — `studio/src/lib/agentColor.ts`
- [ ] [T003] [P] `routeToken` + `openAuthorBubble` over the minimal `Attributed` shape, generic `<M extends Attributed>` with a `make(author)` factory; token appends to the matching-author (or undefined=single-speaker) assistant bubble, new author opens a new bubble. Pure — §6.3 — `studio/src/lib/chatStream.ts`
- [ ] [T004] [P] Vitest: same name→same color; different names spread the palette; empty/undefined safe — `studio/src/lib/agentColor.test.ts`
- [ ] [T005] [P] Vitest: token appends to matching-author bubble; new author opens a new bubble; `agent_start` opens an empty bubble; undefined author appends to current assistant bubble — `studio/src/lib/chatStream.test.ts`
- [ ] [T006] Presentational bubble: `role`, optional `author` label + `agentColor` dot, `content`, streaming caret, `children` slot; renders label+dot iff `author` set and `showLabel!==false` (depends T002) — §6.4 — `studio/src/components/chat/AttributedBubble.tsx`
- [ ] [T007] Vitest (`renderWithProviders` from `src/test/utils.tsx`): single-author renders no label; multi-author renders name+color; user vs assistant styling; children slot renders (depends T006) — `studio/src/components/chat/AttributedBubble.test.tsx`

---

## Phase 3 — Backend: single-agent SSE author frames (T1)

- [ ] [T008] `_proxy_agent_stream` (L373) gains `author: str`; after the upstream 200 (before the `async for line` loop, ~L444) emit `{"type":"agent_start","author":author}` **once**; in the `text_delta` branch (~L459) add `"author":author` to the token frame; pass `author=name` from `stream_chat` (L786), `stream_deployment_chat` (L976), and add `"author":name` to the resume token frame in `resume_stream_chat` (L1122). `done`/`error`/`approval_requested` unchanged — contract `sse-frames.md` — `services/registry-api/routers/chat.py`
- [ ] [T009] Bump `REGISTRY_API_TAG` `0.2.188`→`0.2.189` in `scripts/deploy-cpe2e.sh` + `scripts/deploy-eks.sh` and the registry-api `tag:` (~L596) in `charts/agentshield/values.yaml`, each with a changelog comment ("SSE token/agent_start frames carry author") — `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`
- [ ] [T010] Add **T-S75-007**: start a `/chat` turn on `CHAT_AGENT`, read the SSE, assert ≥1 frame with `type==token` **and** `author==CHAT_AGENT`; register in the suite's run-summary block; keep the existing SKIP discipline (no keycloak token / no running deployment) — `scripts/e2e/suite-75-context-storage.sh`

## Checkpoint 1 — foundational libs green + backend frames present (pre-deploy)

- [ ] [CP1a] Run focused Vitest (`cd studio && npm run test -- agentColor chatStream AttributedBubble`) and `npm run typecheck`; assert all pass — `scripts/checkpoints/poc2-cp1a-frontend-libs.sh`
- [ ] [CP1b] `python3 -c "import ast; ast.parse(open('services/registry-api/routers/chat.py').read())"`; `grep -c '"author"'` on chat.py ≥ 2 (agent_start + token) and `grep 'agent_start'`; assert `author=name` passed at both call sites; assert `T-S75-007` string registered in `scripts/e2e/suite-75-context-storage.sh` — `scripts/checkpoints/poc2-cp1b-backend-frames.sh`

---

## Phase 4 — Single-agent chat surfaces (AgentChatPage + ChatPane)

- [ ] [T011] [P] `Message` += `author?` (L17); in `sendMessage`'s `onmessage` (L315-347) route `agent_start`→`openAuthorBubble` and `token`→`routeToken` with `mk=(author)=>({role:'assistant',content:'',author})`; replace inline assistant markup (L445-460) with `AttributedBubble showLabel={false}`; mirror in `connectResumeStream` (L117-152). No reload-seeding (POC-5) — plan §7 T4 — `studio/src/pages/AgentChatPage.tsx`
- [ ] [T012] [P] `Message` += `author?` (L8); in the `text_delta` branch (L90) set the assistant bubble `author = agentName` (prop); render via `AttributedBubble showLabel={false}` keeping `chips` + `safetyBlock` in the `children` slot (raw-event contract, no reducer) — plan §7 T5 — `studio/src/components/playground/ChatPane.tsx`

---

## Phase 5 — CatalogChatPage (PRIORITY: per-member workflow attribution)

- [ ] [T013] `Message` += `author?` (L10). **Single-agent path** (`sendAgentMessage` L217-307): consume `author`/`agent_start` via `routeToken`/`openAuthorBubble` and render `AttributedBubble`. **Workflow path** (`sendWorkflowMessage` L174-215 + `pollWorkflowResult` L155-172): keep the **full `WorkflowRunTree`** (store `tree`, not just `parent.output`); render each `tree.children[i]` as an `AttributedBubble` (`author=child.agent_name`, `content=child.output`, `showLabel` true) in run order + a compact structural step view (status badge / latency / View-Trace) reusing the row markup from `WorkflowBuilderPage.tsx:785-871`; final summary bubble = parent output — plan §7 T6 — `studio/src/pages/CatalogChatPage.tsx`

## Checkpoint 2 — chat surfaces wired, no orphans (pre-deploy)

- [ ] [CP2a] `cd studio && npm run typecheck` clean + `npm run test` green; assert `AttributedBubble` imported in AgentChatPage, ChatPane, CatalogChatPage; assert the workflow path renders `getWorkflowRunTree` `children` (grep `children` + `agent_name` in CatalogChatPage) — `scripts/checkpoints/poc2-cp2a-chat-surfaces.sh`
- [ ] [CP2b] No-orphan grep (DoD #3): each new symbol has a live caller — `grep -rn AttributedBubble studio/src`, `agentColor` used by AttributedBubble, `routeToken\|openAuthorBubble` called by AgentChatPage+CatalogChatPage; fail if any symbol defined but never imported — `scripts/checkpoints/poc2-cp2b-no-orphans.sh`

---

## Phase 6 — EvalResultsPage: shared-thread transcript (T7)

- [ ] [T014] In the expanded `ResultRow` (L466-506) add a collapsible **Conversation transcript** (React Query `enabled: open`, pattern of `RunStepsDeepLink` L910) that calls `listMemory(memberName, {thread_id: r.run_id, scope:'workflow_run', limit:200})` and renders each row as `AttributedBubble` (`role=row.role`, `author=row.agent_name`, `showLabel` true). Resolve `memberName` from `detail.actual_member_path` / `per_member[].member` — **guard** when `r.run_id` null or no member resolves (render nothing; `/agents/{name}/memory` 404s on a non-Agent name) — contract `memory-read.md`, plan §7 T7 — `studio/src/pages/EvalResultsPage.tsx`

---

## Phase 7 — WorkflowBuilder: "Share context between agents" toggle (T8)

- [ ] [T015] Add `const [saveMemoryEnabled,setSaveMemoryEnabled]=useState(true)` (~L112); load on mount in the `if (workflow)` block (~L165) via `setSaveMemoryEnabled(workflow.memory_enabled)`; add a "Share context between agents" checkbox in the first-save modal after Orchestration (~L1010) with helper text "Members see each other's turns in a shared conversation thread."; send `memory_enabled: saveMemoryEnabled` in `createCompositeWorkflow` (L286) **and** `updateCompositeWorkflowApi` (L338). **Do NOT** add per-session/per-run or share-rationale controls (no backing column → gap ledger) — contract `update-composite-workflow.md`, plan §9 — `studio/src/pages/WorkflowBuilderPage.tsx`

## Checkpoint 3 — eval transcript + toggle persistence wired (pre-deploy)

- [ ] [CP3a] `cd studio && npm run typecheck` clean; assert EvalResultsPage calls `listMemory` with `scope: 'workflow_run'` (grep) and has a null-`run_id`/no-member guard; assert `npm run test` green — `scripts/checkpoints/poc2-cp3a-eval-transcript.sh`
- [ ] [CP3b] Assert `memory_enabled` appears in **both** save calls in WorkflowBuilderPage (grep `memory_enabled` in `createCompositeWorkflow` + `updateCompositeWorkflowApi` regions ≥ 2 hits) and the toggle loads `workflow.memory_enabled` on mount; confirm no `per_session`/`share_rationale` field was invented — `scripts/checkpoints/poc2-cp3b-toggle-wiring.sh`

---

## Phase 8 — Polish (Playwright journey, docs, studio image bump)

- [ ] [T016] Playwright (real Keycloak via `e2e/global-setup.ts`, patterns from `workflows.spec.ts`/`hitl-deployment-chat.spec.ts`, target the https gateway): (a) open a multi-agent workflow in CatalogChat, send a message, `waitForResponse` on the run-tree poll, assert **≥2 attributed member bubbles** each showing a member name; (b) open WorkflowBuilder, set the share-context toggle, save (`waitForResponse` on POST/PATCH `/workflows`), **reload** `/workflows/{id}/builder`, assert the toggle reflects the persisted value. Assert UI wiring + persistence, not agent-execution completion — plan §7 T9 (DoD #1 + #2) — `studio/e2e/context-attribution.spec.ts`
- [ ] [T017] [P] Document the new `author`/`agent_start` SSE frames, attributed bubbles across the three surfaces, the eval transcript, and the share-context toggle — `docs/experience/playground.md`
- [ ] [T018] [P] Append the deferred gap-ledger entries (plan §9): per-session/per-run scope choice (entrypoint-derived, no column); "share rationale" toggle (POC-1b summarizer); AgentChat/ChatPane reload-seeding (POC-5); per-member context-scope on `routing` — all tagged **deferred (intentional)** — `docs/testing/manual-ui-e2e-test-plan.md`
- [ ] [T019] Bump `STUDIO_TAG` `0.1.140`→`0.1.141` in `scripts/deploy-cpe2e.sh` + `scripts/deploy-eks.sh` and the studio `tag:` (~L915) in `charts/agentshield/values.yaml`, each with a changelog comment ("attributed bubbles + eval transcript + share-context toggle") — `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`

## Checkpoint 4 — deploy-and-verify on EKS (sanctioned Helm path only)

- [ ] [CP4a] Build+push **both** images to ECR, then deploy via `SKIP_BUILD=1 KUBECONFIG=~/.kube/test-cluster-kube-config.yaml AWS_PROFILE=kkalyan-aws-key bash scripts/deploy-eks.sh`; assert both images live with `kubectl get deploy -o jsonpath` (`registry-api:0.2.189`, `studio:0.1.141`). **NEVER** `kubectl set image/env` (drift) — `scripts/checkpoints/poc2-cp4a-deploy.sh`
- [ ] [CP4b] `kubectl exec` the registry-api pod, `curl` a single-agent `/chat` SSE and `jq`-assert a `type==token` frame carries `author==<agent name>`; re-run `bash scripts/e2e/suite-75-context-storage.sh` asserting **T-S75-007 passes with no regression**; assert the `scope=workflow_run` transcript returns ≥2 distinct `agent_name` values (T-S75-004) — `scripts/checkpoints/poc2-cp4b-smoke.sh`
- [ ] [CP4c] Final gates: no-orphan grep for every new symbol (`AttributedBubble`, `agentColor`, `routeToken`, `openAuthorBubble`, chat.py `"author"`, WorkflowBuilder `memory_enabled`); `cd studio && npm run typecheck` clean; `npm run test` green; then the browser gate `bash scripts/studio-e2e.sh` (Playwright `context-attribution.spec.ts`) green — `scripts/checkpoints/poc2-cp4c-gates.sh`

---

## Summary — all phases

| Phase | Tasks | Proves |
|---|---|---|
| 1 — Setup | T001 | `MemoryMessage`/`listMemory` carry `scope`+`message_kind` (unblocks T014) |
| 2 — Foundational | T002–T007 | `agentColor` deterministic; reducer routes by author; `AttributedBubble` labels only multi-author — all unit-tested |
| 3 — Backend frames | T008–T010 | single-agent SSE emits `agent_start` + `author` token frames; registry-api tag bumped; T-S75-007 added |
| **CP1** | CP1a, CP1b | libs+typecheck green; `author` frames + suite registration present |
| 4 — Chat surfaces | T011‖T012 | AgentChatPage + ChatPane render via `AttributedBubble` through the reducer / prop |
| 5 — CatalogChat (priority) | T013 | multi-agent workflow renders per-member attributed bubbles + step view — not one blob |
| **CP2** | CP2a, CP2b | surfaces wired; run-tree children rendered; no orphan symbols |
| 6 — Eval transcript | T014 | eval item expands to a `scope=workflow_run` per-agent transcript, guarded for no-member |
| 7 — Toggle | T015 | "Share context" toggle bound to `memory_enabled`, sent in both save calls |
| **CP3** | CP3a, CP3b | eval transcript + toggle persistence wired; no invented fields |
| 8 — Polish | T016–T019 | Playwright journey + save→reload→assert; experience doc + gap ledger; studio tag bumped |
| **CP4 (final)** | CP4a, CP4b, CP4c | deployed via Helm; live SSE/transcript smoke + suite-75; orphan/typecheck/Playwright gates green |

## Suggested MVP scope
**Target CP2 first.** It delivers the plan's committed priority vertical slice (Execution Notes §10: "Do T1 + T3 first, then T4 … before touching workflows"): the shared building blocks (Phase 2), the single-agent `author` frames (Phase 3), the two single-agent surfaces (Phase 4), and the **priority CatalogChatPage per-member workflow attribution** (Phase 5) — the one fix that stops a whole workflow run collapsing into a single blob. Eval transcript (T014) and the toggle (T015) layer on afterward. Nothing is *proven shipped* until **CP4** runs the Helm deploy + live smoke + Playwright gate — so a minimal ship is Phases 1–5 followed by CP4 (with T014/T015 deferred to a fast follow), rather than stopping at the pre-deploy CP2.

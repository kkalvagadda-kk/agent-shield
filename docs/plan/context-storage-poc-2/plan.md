# POC-2 Implementation Plan — Per-agent attribution + eval transcript + share-context toggle

**Phase**: POC-2 of the context-storage UX roadmap (`docs/design/context-storage-ux-roadmap.md` §3).
**Grounded against**: registry-api `0.2.188`, studio `0.1.140`, declarative-runner `0.1.52` (POC-0/1 done + deployed; suite-75 5/0/1).
**Companion artifacts**: `research.md` (decisions), `data-model.md` (contracts summary), `contracts/*` (exact schemas), `quickstart.md` (gates).

---

## 1. Goal

Make the POC-1 shared workflow thread **visible**: every chat/eval surface shows *which agent* said what, so a multi-agent workflow reads as a real multi-speaker conversation. Concretely:

1. **Backend** — the single-agent chat SSE proxy carries the speaker: token frames become `{"type":"token","content":...,"author":"<agent_name>"}` and a one-time `{"type":"agent_start","author":...}` opens a labeled bubble.
2. **Frontend** — a shared `AttributedBubble` (agent name + deterministic per-agent color) + a shared stream reducer, wired into the three chat surfaces. **`CatalogChatPage` is the priority fix** (today it collapses a whole workflow run into one blob).
3. **Eval transcript** — `EvalResultsPage` gets an expandable per-item shared-thread transcript reusing `AttributedBubble`.
4. **Workflow "share context" toggle** — the WorkflowBuilder first-save modal exposes `memory_enabled` (the field already exists on the composite type), persisted via `updateCompositeWorkflowApi`.

### Alignment Check
> This serves the ultimate goal — making cross-agent context sharing a real product surface — by rendering the *already-persisted* per-author transcript, not by adding storage. The one hard architectural truth this plan is built around (see §2): **workflow member deltas do not stream through the chat proxy**, so multi-speaker attribution rides on the run tree + the `scope=workflow_run` transcript, and the streaming `author` change only makes the single-agent path uniform. We do NOT invent a streaming path that does not exist.

---

## 2. Architecture — how attribution actually flows (KEY FINDING)

There are **two disjoint SSE contracts** and **one non-streaming workflow path**. This shaped every task:

| Surface | Transport today | Who is the speaker | How POC-2 attributes |
|---|---|---|---|
| `AgentChatPage` (single agent) | chat.py `_proxy_agent_stream` → `{"type":"token"}` frames | one agent = path `{name}` | **Backend adds `author` to the proxy frames** (T1); reducer keys on it (T4) |
| `CatalogChatPage` **single-agent** | same chat.py proxy (`sendAgentMessage`) | one agent = `artifact.name` | same `author` frames (T1) + reducer (T6) |
| `ChatPane` (playground) | `/playground/runs/{id}/stream` → **raw named runner events** (`text_delta`) | one agent = `agentName` **prop** | **author derived client-side from the prop** (T5) — the playground stream is a different contract and is single-agent only |
| `CatalogChatPage` **workflow** | **NOT streamed** — `triggerWorkflowRun` then polls `getWorkflowRunTree` | many members, server-dispatched | **run tree `children[]`** (`agent_name`/`output`/`status`/`trace`) rendered as attributed bubbles (T6); reload rehydrates from `scope=workflow_run` transcript |
| `EvalResultsPage` (workflow eval) | not streamed — batch run | many members | expandable `scope=workflow_run` transcript via `listMemory` (T7) |

**Why workflows are not streamed:** `workflow_orchestrator.py::_run_step` (L438) dispatches each member with `_dispatch` (POST `/chat`, returns full output, L70-104) or `_dispatch_durable_member` (POST `/run` + poll, L124-196) — the member's output is **collected, never streamed to the client**. The client learns member outputs only by polling `getWorkflowRunTree` (`CatalogChatPage.tsx:155-172`). Therefore per-agent attribution for a workflow is **structural (run tree) + reload (shared transcript)**, and the streaming `author` frame is meaningful only for the single-agent (one-speaker) case.

**No-bandaid consequence:** `AttributedBubble` and the stream reducer take an **explicit `author`** parameter. Each SSE adapter (proxy frame, playground prop, run-tree row, transcript row) supplies the author explicitly; the reducer never type-sniffs the surface. Single-agent is the degenerate one-author case of the exact same component — not a fork.

**Persisted source of truth for reload = the POC-1 shared transcript.** No new tables. `agent_memory` rows already carry `agent_name` + `message_kind` + `scope`; `GET /api/v1/agents/{member}/memory?scope=workflow_run&thread_id=<parent_run_id>` returns per-author rows ordered by `message_index` (proven by suite-75 T-S75-004; endpoint at `routers/memory.py::list_memory` L97-157).

---

## 3. Tech Stack

- **Backend**: FastAPI (Python 3.11), httpx streaming proxy, SSE (`text/event-stream`). No new deps, no migration.
- **Frontend**: React 18 + TypeScript (strict), Vite, TailwindCSS, React Query, `EventSource`. Tests: Vitest + React Testing Library (component + reducer), Playwright (browser E2E).
- **E2E backend**: bash + httpx-in-pod (`scripts/e2e/suite-75-context-storage.sh`).
- **Deploy**: Helm via `SKIP_BUILD=1 bash scripts/deploy-eks.sh` (EKS test-cluster); image tags mirrored in `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`.

---

## 4. Constitution Check (against `CLAUDE.md`)

| Rule | How this plan satisfies it |
|---|---|
| **DoD #1 — prove the real journey (Playwright)** | T9: `studio/e2e/context-attribution.spec.ts` drives a real multi-agent workflow run in CatalogChat and asserts per-member attributed bubbles (`waitForResponse` on the run-tree poll); asserts the share-context toggle in WorkflowBuilder. |
| **DoD #2 — save→reload→assert** | (a) **Frontend write surface** = the share-context toggle: Playwright saves `memory_enabled=true`, reloads `/workflows/{id}/builder`, asserts the toggle reflects the persisted value (T8/T9). (b) **Backend** = suite-75 T-S75-004 (existing) proves the per-author transcript survives; new T-S75-007 asserts stream frames carry `author`. |
| **DoD #3 — no orphan code** | Every new symbol has a caller in the same change: `AttributedBubble`←T4/T5/T6/T7; `agentColor`←AttributedBubble; `routeToken`/`openAuthorBubble`←T4/T6; `author` proxy param←`stream_chat`+`stream_deployment_chat`. T9 greps each symbol for a live caller. |
| **DoD #4 — vertical slice** | Order below wires one path end-to-end before the next: T1→T4 proves single-agent author (UI→proxy→UI) before T6 (workflow tree) before T7 (eval) before T8 (toggle). |
| **DoD #5 — honest gap ledger** | Deferred items recorded in §9 and appended to `docs/testing/manual-ui-e2e-test-plan.md`: per-session/per-run scope choice (entrypoint-derived, no column); rationale summarizer (POC-1b); AgentChatPage reload-seeding (POC-5). |
| **DoD #6 — reason from running product** | This plan cites `file:line` from the deployed code, and explicitly overrides the roadmap's §3.1 "thread author through the workflow stream" with the observed non-streaming reality. |
| **Post-impl #2 — image bumps in 3 files + changelog** | T1 bumps registry-api; T9 bumps studio; both in `deploy-cpe2e.sh`, `deploy-eks.sh`, `charts/agentshield/values.yaml` + changelog comment. |
| **Post-impl #3 — experience docs** | T9 updates `docs/experience/playground.md` (new `author`/`agent_start` SSE frames, attributed bubbles, eval transcript). |
| **Post-impl #4 — frontend tests in sync** | Vitest for `AttributedBubble`, `agentColor`, the reducer; Playwright for the flows. |
| **No bandaid** | Explicit `author` param through the reducer/component; no `getattr`/`isinstance`/priority fallthrough; the single-agent vs workflow distinction is data (author present, one vs many) not a code fork. |

---

## 5. File Structure (every file created/modified)

### Backend
| File | Change | Responsibility |
|---|---|---|
| `services/registry-api/routers/chat.py` | **Modify** | `_proxy_agent_stream` gains `author: str` param; emits one `agent_start` frame + `author` on every `token` frame; `stream_chat` (L784) + `stream_deployment_chat` (L974) + `resume_stream_chat` (L1080) pass `author=name`. |
| `scripts/e2e/suite-75-context-storage.sh` | **Modify** | Add **T-S75-007**: a single-agent `/chat` stream's token frames carry `author == agent_name`. Register in the run summary block. |
| `scripts/deploy-cpe2e.sh` | **Modify** | Bump `REGISTRY_API_TAG` 0.2.188→0.2.189; changelog comment. |
| `scripts/deploy-eks.sh` | **Modify** | Bump `REGISTRY_API_TAG` 0.2.188→0.2.189; changelog comment. |
| `charts/agentshield/values.yaml` | **Modify** | Bump registry-api `tag` (~L596) 0.2.188→0.2.189. |

### Frontend — new shared building blocks
| File | Change | Responsibility |
|---|---|---|
| `studio/src/lib/agentColor.ts` | **Create** | `agentColor(name)` — deterministic hash → a fixed 8-entry Tailwind palette `{bg,text,border,dot}`. Pure, tested. |
| `studio/src/lib/agentColor.test.ts` | **Create** | Vitest: same name → same color; different names spread across the palette; empty/undefined safe. |
| `studio/src/lib/chatStream.ts` | **Create** | Shared SSE reducer helpers `routeToken` + `openAuthorBubble` over the minimal `Attributed` shape. Pure, tested. |
| `studio/src/lib/chatStream.test.ts` | **Create** | Vitest: token appends to the matching-author bubble; a new author opens a new bubble; `agent_start` opens an empty bubble; undefined author appends to the current assistant bubble. |
| `studio/src/components/chat/AttributedBubble.tsx` | **Create** | Presentational bubble: role, optional `author` label + color dot, content, streaming caret, `children` slot (chips/feedback). Single-author → unlabeled/subtle. |
| `studio/src/components/chat/AttributedBubble.test.tsx` | **Create** | Vitest: single-author renders no label; multi-author renders name + color; user vs assistant styling; children slot. |

### Frontend — surfaces
| File | Change | Responsibility |
|---|---|---|
| `studio/src/api/registryApi.ts` | **Modify** | `MemoryMessage` += `message_kind?: string`, `scope?: string` (L1536); `listMemory` params += `scope?: string` (L1548). |
| `studio/src/pages/AgentChatPage.tsx` | **Modify** | `Message` += `author?` (L17); handle `agent_start`; token reducer via `routeToken`; render `AttributedBubble`. |
| `studio/src/components/playground/ChatPane.tsx` | **Modify** | `Message` += `author?` (L8); on `text_delta` set `author = agentName` (prop); render `AttributedBubble` (keep chips/safety in the `children` slot). |
| `studio/src/pages/CatalogChatPage.tsx` | **Modify** (priority) | `Message` += `author?` (L10); single-agent path consumes `author` frames; **workflow path** keeps the full run tree and renders `children[]` as attributed bubbles + a structural step view (reuse the run-tree row markup from `WorkflowBuilderPage.tsx:785-871`). |
| `studio/src/pages/EvalResultsPage.tsx` | **Modify** | In the expanded `ResultRow`, add an expandable **Conversation transcript** that fetches `listMemory(memberName, {thread_id: r.run_id, scope:'workflow_run'})` and renders `AttributedBubble` rows. |
| `studio/src/pages/WorkflowBuilderPage.tsx` | **Modify** | Add `saveMemoryEnabled` state; a "Share context between agents" toggle in the first-save modal (after Orchestration ~L1010); load `workflow.memory_enabled` on mount (~L165); pass `memory_enabled` in `createCompositeWorkflow` (L286) and `updateCompositeWorkflowApi` (L338). |

### Tests + docs
| File | Change | Responsibility |
|---|---|---|
| `studio/e2e/context-attribution.spec.ts` | **Create** | Playwright: multi-agent workflow renders attributed member bubbles in CatalogChat; share-context toggle persists (save→reload→assert). |
| `docs/experience/playground.md` | **Modify** | Document the `author`/`agent_start` frames, attributed bubbles, eval transcript, share-context toggle. |
| `docs/testing/manual-ui-e2e-test-plan.md` | **Modify** | Gap-ledger entries (§9). |

> Every file above appears in a task below, and every task references only files in this table.

---

## 6. Key Interfaces

### 6.1 SSE frames (chat.py `_proxy_agent_stream`) — see `contracts/sse-frames.md`
```jsonc
// NEW: emitted once, immediately after the upstream 200, before the first token
{"type": "agent_start", "author": "refund-agent"}
// CHANGED: author added (was {"type":"token","content":"..."} )
{"type": "token", "content": "Hello", "author": "refund-agent"}
// unchanged
{"type": "done", "run_id": "..."}
{"type": "error", "message": "..."}
{"type": "approval_requested", ...}
```
`author` is the agent name (the `{name}` path param). Existing clients that ignore unknown keys keep working; the reducer that reads `author` is added in the same change.

### 6.2 `agentColor` (studio/src/lib/agentColor.ts)
```ts
export interface AgentColor { bg: string; text: string; border: string; dot: string; }
/** Deterministic: same name → same palette entry, across reloads and sessions. */
export function agentColor(name: string | undefined | null): AgentColor;
```

### 6.3 Stream reducer (studio/src/lib/chatStream.ts)
```ts
export interface Attributed { role: "user" | "assistant"; content: string; author?: string; }

/** Append `content` to the last assistant bubble when its author matches (or
 *  when `author` is undefined = single-speaker); otherwise open a new assistant
 *  bubble via `make(author)` seeded with `content`. Returns a new array. */
export function routeToken<M extends Attributed>(
  messages: M[], author: string | undefined, content: string,
  make: (author?: string) => M,
): M[];

/** Open a fresh empty assistant bubble for `author` (handles `agent_start`). */
export function openAuthorBubble<M extends Attributed>(
  messages: M[], author: string | undefined, make: (author?: string) => M,
): M[];
```

### 6.4 `AttributedBubble` (studio/src/components/chat/AttributedBubble.tsx)
```ts
export interface AttributedBubbleProps {
  role: "user" | "assistant";
  content: string;
  author?: string;        // agent name; undefined → unlabeled
  showLabel?: boolean;    // default: label shown only when author set AND multi-author context
  streaming?: boolean;    // show blinking caret
  children?: React.ReactNode; // chips / feedback / safety details slot
}
export default function AttributedBubble(props: AttributedBubbleProps): JSX.Element;
```
The caller decides multi-author context (it knows whether the surface has >1 speaker) and passes `showLabel`. Default heuristic inside the component: render the label + color dot iff `author` is set and `showLabel !== false`.

### 6.5 Changed API types (studio/src/api/registryApi.ts) — see `contracts/memory-read.md`
```ts
export interface MemoryMessage {
  id: string; agent_name: string; thread_id: string; role: string; content: string;
  message_index: number; session_id: string | null; user_id: string | null; created_at: string;
  message_kind?: string;   // NEW: 'user' | 'agent_output' | 'rationale'
  scope?: string;          // NEW: 'agent' | 'workflow_run'
}
export const listMemory = (agentName: string, params?: {
  thread_id?: string; scope?: string; deployment_id?: string; limit?: number; offset?: number;
}) => Promise<MemoryMessage[]>;
```

### 6.6 `updateCompositeWorkflow` with `memory_enabled` — see `contracts/update-composite-workflow.md`
`CreateCompositeWorkflowRequest.memory_enabled?: boolean` **already exists** (registryApi.ts L586); `CompositeWorkflow.memory_enabled: boolean` already exists (L542). No type change — only the modal + the two save calls start sending it.

---

## 7. Tasks (dependency-ordered)

> Each task: **Files** · **Interface contract** · **Acceptance** · **Test** · **Verify**. Do them in order; each is a provable slice.

### T1 — Backend: SSE frames carry the author
- **Files**: `services/registry-api/routers/chat.py`; `scripts/deploy-cpe2e.sh`; `scripts/deploy-eks.sh`; `charts/agentshield/values.yaml`.
- **Interface contract**: §6.1. Add `author: str` to `_proxy_agent_stream` (L373). After the upstream `200` (L433, before the `async for line` loop) emit `_emit({"type":"agent_start","author":author})` once. In the `text_delta` branch (L459) add `"author": author` to the token frame. Pass `author=name` from `stream_chat` (call site L786), `stream_deployment_chat` (L976), and add the same `author` to the `text_delta` token frame in `resume_stream_chat` (L1122, one-speaker resume). Bump `REGISTRY_API_TAG` 0.2.188→0.2.189 in both deploy scripts + values.yaml (~L596) + changelog comment.
- **Acceptance**: a single-agent `/chat` stream emits exactly one `agent_start` then `token` frames each carrying `author == <agent name>`; `done`/`error`/`approval_requested` unchanged; no behavior change for a client that ignores `author`.
- **Test**: suite-75 **T-S75-007** — start a `/chat` turn on `CHAT_AGENT`, read the SSE, assert ≥1 frame with `type==token` and `author==CHAT_AGENT` (skip if no keycloak token / no running deployment, matching the suite's existing SKIP discipline).
- **Verify**: `python3 -c "import ast; ast.parse(open('services/registry-api/routers/chat.py').read())"`; `grep -n '"author"' services/registry-api/routers/chat.py` shows the two frame sites; suite registered — `bash scripts/e2e/suite-75-context-storage.sh` (or via `run-all.sh`).

### T2 — Frontend API types: memory scope + kind
- **Files**: `studio/src/api/registryApi.ts`.
- **Interface contract**: §6.5. Add optional `message_kind` + `scope` to `MemoryMessage` (L1536); add `scope?: string` to `listMemory` params (L1548) — it forwards to the backend `scope` query already supported by `list_memory` (memory.py L100).
- **Acceptance**: `listMemory(name, {thread_id, scope:'workflow_run'})` compiles and sends `?scope=workflow_run`; existing callers (no `scope`) unchanged.
- **Test**: covered transitively by T7's transcript fetch + typecheck.
- **Verify**: `cd studio && npm run typecheck`.

### T3 — Shared building blocks: color, reducer, AttributedBubble
- **Files**: `studio/src/lib/agentColor.ts`(+test), `studio/src/lib/chatStream.ts`(+test), `studio/src/components/chat/AttributedBubble.tsx`(+test).
- **Interface contract**: §6.2, §6.3, §6.4.
- **Acceptance**: `agentColor` deterministic + spreads; `routeToken`/`openAuthorBubble` pure and correct per the cases in §6.3; `AttributedBubble` renders label+dot iff author set and `showLabel!==false`.
- **Test**: `agentColor.test.ts`, `chatStream.test.ts`, `AttributedBubble.test.tsx` (render via `renderWithProviders` from `src/test/utils.tsx`).
- **Verify**: `cd studio && npm run test -- agentColor chatStream AttributedBubble` green; `npm run typecheck`.

### T4 — AgentChatPage: attributed single-agent bubbles (first vertical slice)
- **Files**: `studio/src/pages/AgentChatPage.tsx`.
- **Interface contract**: `Message` += `author?` (L17). In `sendMessage`'s `onmessage` (L315-347): on `agent_start` call `openAuthorBubble(prev, data.author, mk)`; on `token` call `routeToken(prev, data.author, data.content, mk)` where `mk = (author) => ({role:'assistant', content:'', author})`. Replace the inline assistant bubble markup (L445-460) with `AttributedBubble` (single-author surface → `showLabel={false}`). Do the same in `connectResumeStream` (L117-152).
- **Acceptance**: streaming a single agent shows one assistant bubble, unlabeled (single-speaker); no regression to approval/resume flow.
- **Test**: covered by `chatStream.test.ts` (reducer) + T9 Playwright smoke on an agent chat.
- **Verify**: `npm run typecheck`; `npm run test`; grep `routeToken` has a caller here.

### T5 — ChatPane: attributed playground bubble (author from prop)
- **Files**: `studio/src/components/playground/ChatPane.tsx`.
- **Interface contract**: `Message` += `author?` (L8). In the `text_delta` branch (L90) set the assistant bubble's `author = agentName` (the prop) when opening/updating it. Render assistant bubbles via `AttributedBubble` with `showLabel={false}` (playground is single-agent); keep `chips` + `safetyBlock` in the `children` slot.
- **Acceptance**: playground chat renders identically to today but through `AttributedBubble`; chips/safety still render; author is set (single speaker).
- **Test**: existing ChatPane behavior + typecheck; reducer logic not needed (raw-event contract).
- **Verify**: `npm run typecheck`; `npm run test`.

### T6 — CatalogChatPage: per-member workflow turns (PRIORITY)
- **Files**: `studio/src/pages/CatalogChatPage.tsx`.
- **Interface contract**: `Message` += `author?` (L10).
  - **Single-agent path** (`sendAgentMessage` L217-307): consume `author`/`agent_start` via `routeToken`/`openAuthorBubble` exactly like T4; render via `AttributedBubble`.
  - **Workflow path** (`sendWorkflowMessage` L174-215 + `pollWorkflowResult` L155-172): keep the **full `WorkflowRunTree`** (store `tree`, not just `parent.output`). Render each `tree.children[i]` as an `AttributedBubble` (`role='assistant'`, `author=child.agent_name`, `content=child.output`, `showLabel` = true since multi-speaker) in run order, plus a compact structural step view (status badge + latency + View-Trace) reusing the row markup pattern from `WorkflowBuilderPage.tsx:785-871`. The final assistant summary bubble stays as the parent output.
- **Acceptance**: running a 2-member workflow shows **two labeled member bubbles** (distinct colors) + a step list — NOT one collapsed blob. Single-agent production chat still streams one bubble.
- **Test**: T9 Playwright `context-attribution.spec.ts` asserts ≥2 attributed member bubbles after a workflow run.
- **Verify**: `npm run typecheck`; `npm run test`; grep confirms `getWorkflowRunTree`'s full tree is rendered (no orphan).

### T7 — EvalResultsPage: expandable shared-thread transcript
- **Files**: `studio/src/pages/EvalResultsPage.tsx`.
- **Interface contract**: inside the expanded `ResultRow` (L466-506), add a collapsible "Conversation transcript" section that, when opened, calls `listMemory(memberName, {thread_id: r.run_id, scope:'workflow_run', limit:200})` and renders each row as `AttributedBubble` (`role=row.role`, `author=row.agent_name`, `content=row.content`, `showLabel` true). Resolve `memberName` from the eval detail: first of `detail.actual_member_path` / `per_member[].member`; guard when `r.run_id` is null or no member name resolves (render nothing). Reuse React Query (`enabled: open`) like `RunStepsDeepLink` (L910).
- **Acceptance**: a completed **workflow** eval item expands to show per-agent turns (labeled) pulled from the backend transcript; a single-agent/reactive eval item with no `run_id`/member shows nothing extra (no error).
- **Test**: T9 (optional assertion) + typecheck; backend transcript proven by suite-75 T-S75-004.
- **Verify**: `npm run typecheck`; `npm run test`; grep `scope: 'workflow_run'` present and `listMemory` called.

### T8 — WorkflowBuilder: "Share context between agents" toggle
- **Files**: `studio/src/pages/WorkflowBuilderPage.tsx`.
- **Interface contract**: add `const [saveMemoryEnabled, setSaveMemoryEnabled] = useState(true)` near L112. Load it on mount: in the `if (workflow)` block (~L165) add `setSaveMemoryEnabled(workflow.memory_enabled)`. Add a toggle in the first-save modal after the Orchestration `<div>` (before Actions, ~L1010): "Share context between agents" with helper text "Members see each other's turns in a shared thread." Pass `memory_enabled: saveMemoryEnabled` in `createCompositeWorkflow` (L286-292) and `updateCompositeWorkflowApi` (L338-341). (No per-session/per-run field — see §9; scope is entrypoint-derived.)
- **Acceptance**: toggling and saving persists `memory_enabled`; reopening the builder reflects the saved value.
- **Test**: T9 Playwright save→reload→assert on the toggle.
- **Verify**: `npm run typecheck`; grep `memory_enabled` appears in both save calls.

### T9 — Playwright, docs, studio image bump, deploy, gates
- **Files**: `studio/e2e/context-attribution.spec.ts`(create); `docs/experience/playground.md`; `docs/testing/manual-ui-e2e-test-plan.md`; `scripts/deploy-cpe2e.sh`; `scripts/deploy-eks.sh`; `charts/agentshield/values.yaml`.
- **Interface contract**:
  - Playwright (real Keycloak via `e2e/global-setup.ts`, patterns from `workflows.spec.ts`/`hitl-deployment-chat.spec.ts`): (a) open a multi-agent workflow in CatalogChat, send a message, `waitForResponse` on the run-tree poll, assert ≥2 bubbles each showing a member name; (b) open WorkflowBuilder, set the share-context toggle, save (`waitForResponse` on PATCH/POST `/workflows`), reload the page, assert the toggle reflects the persisted value. Follow the "assert UI wiring + persistence, not agent execution" boundary.
  - Bump `STUDIO_TAG` 0.1.140→0.1.141 in both deploy scripts + values.yaml (~L915) + changelog.
  - Update experience doc + gap ledger.
- **Acceptance**: `npm run typecheck` clean; `npm run test` green; `bash scripts/studio-e2e.sh` green; suite-75 green (incl. T-S75-007); deployed via `SKIP_BUILD=1 bash scripts/deploy-eks.sh` (build+push happens per the deploy pipeline, then Helm).
- **Verify**: see `quickstart.md`. Orphan grep for every new symbol (`AttributedBubble`, `agentColor`, `routeToken`, `openAuthorBubble`, proxy `author`).

---

## 8. Complexity Tracking

| Risk / complexity | Why it exists | Mitigation |
|---|---|---|
| Two SSE contracts (proxy frames vs raw runner events) | ChatPane reads the playground stream; AgentChat/CatalogChat read the proxy | Author supplied explicitly per adapter; the reducer/component are contract-agnostic. ChatPane derives author client-side (single-agent, documented). |
| Workflow attribution is non-streaming | Members are server-dispatched and collected | Attribution rides the run tree + `scope=workflow_run` reload — no invented stream. Called out in §2. |
| Eval transcript needs a valid Agent name in the memory path | `/agents/{name}/memory` 404s on a non-agent name; a workflow name is not an Agent | Use a **member** agent name from the eval detail (same trick suite-75 uses with `WA`); guard when none resolves. |
| "per-session/per-run" + "share rationale" toggles in the design have no columns | Only `memory_enabled` exists on the composite type | Ship `memory_enabled` only; per-session/per-run is entrypoint-derived (§5.4 arch doc); log both as deferred gaps. No parallel field invented. |
| Playwright can't complete agent execution (few agent pods) | Same boundary the bash suites accept | Assert UI wiring + persistence + network calls, not final agent answers. |

---

## 9. Gap Ledger (deferred — append to `docs/testing/manual-ui-e2e-test-plan.md`)

- **deferred (intentional)** — Per-session vs per-run memory scope choice in the WorkflowBuilder modal: no backing column; scope is entrypoint-derived (chat→per-session, run→per-run, arch doc §5.4). Not shipped as a control.
- **deferred (intentional)** — "Share rationale between agents" toggle (arch doc §10/§5.2): depends on the rationale summarizer (POC-1b), which builds after POC-4. Only `memory_enabled` (share context on/off) ships now.
- **deferred (intentional)** — `AgentChatPage`/`ChatPane` do NOT seed messages from the transcript on reload (no conversation-continue). That is POC-5. POC-2's reload proof is the backend transcript (suite-75) + the toggle persistence.
- **deferred (intentional)** — Per-member context-scope on the member `routing` bag (arch doc §10 `WorkflowPropertiesPanel`): not in POC-2 scope.

---

## 10. Execution Notes

- **Do T1 + T3 first**, then T4 to prove the single-agent author slice end-to-end (UI→proxy→UI) before touching workflows. This is the vertical-slice guard.
- **Bump the tag when you touch the service, then deploy** — do not batch a source change without a build+deploy (registry-api tag in T1, studio tag in T9). Deploy only via `SKIP_BUILD=1 bash scripts/deploy-eks.sh`; never `kubectl set image/env`.
- **Reload-seeding is out of scope** — resist the urge to make AgentChatPage load prior turns; that's POC-5 and would blur this slice.
- **Keep chips/feedback working** in ChatPane and CatalogChat by passing them through `AttributedBubble`'s `children` slot — do not drop existing UI.
- **When you finish, state which DoD items you satisfied**: the Playwright journey (T9), the save→reload→assert (toggle + suite-75), the orphan greps, and the gap-ledger entries.

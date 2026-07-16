# POC-2 Research & Decisions

Grounded against the deployed code (registry-api 0.2.188, studio 0.1.140, declarative-runner 0.1.52). Every decision cites `file:line`.

---

## D1 — How workflow streaming actually works today (the finding that shaped everything)

**Question the roadmap §3.1 assumed away:** "thread the current member `agent_name` through the stream translation so token frames carry `author`." That presumes workflow member deltas flow through `chat.py::_proxy_agent_stream`. **They do not.**

**What the running code does:**
- A workflow run is started by `triggerWorkflowRun` (POST `/workflows/{id}/runs`), and the client learns the result by **polling** `getWorkflowRunTree` — `CatalogChatPage.tsx:155-172` (`pollWorkflowResult`) and `WorkflowBuilderPage.tsx:413`.
- Server-side, `workflow_orchestrator.py::_run_step` (L438) dispatches each member with either:
  - `_dispatch` — POST the member pod's `/chat`, which **returns the full output inline** (L70-104, `data.get("output")`), or
  - `_dispatch_durable_member` — POST `/run` then **poll the child AgentRun** to terminal (L124-196).
  In both cases the member output is **collected and written to the child `AgentRun` row**, never streamed to the browser.
- `_proxy_agent_stream` (chat.py L373) is used **only** by single-agent chat: `stream_chat` (L784) and `stream_deployment_chat` (L976). Its named→data-only translation (`text_delta`→`{"type":"token"}`) has exactly one speaker.

**Decision:** Attribution has two mechanisms, chosen by whether the surface streams:
1. **Single-agent (streams)** → add `author` to the proxy frames (the agent name is the path `{name}`). This makes the reducer uniform; it is the degenerate one-speaker case.
2. **Workflow (does not stream)** → attribute from the **run tree `children[]`** (`AgentRunItem` carries `agent_name`, `output`, `status`, `latency_ms`, `langfuse_trace_id` — registryApi.ts L1369-1392) for the live structural view, and from the **`scope=workflow_run` transcript** for reload. No streaming author path is invented.

This is the "reason from the running product, not the design doc" rule (CLAUDE.md DoD #6) in action: the roadmap drifted, the plan corrects it.

---

## D2 — The two SSE contracts (why ChatPane is handled differently)

- **`chat.py` proxy contract** (AgentChatPage, CatalogChatPage single-agent): data-only frames `{"type":"token"|"done"|"error"|"approval_requested", ...}`. Client reads `d.type`. → **backend adds `author`** here (T1).
- **Playground raw-event contract** (ChatPane via `/playground/runs/{id}/stream`, playgroundApi L349): named runner events; client reads `payload.event === "text_delta"` (ChatPane.tsx L83-90). This stream is **not** the proxy and is **single-agent only**. → **author derived client-side from the `agentName` prop** (T5). Modifying the playground router to inject author would be scope creep with no benefit (one speaker).

**No-bandaid check:** this is not a fork or type-sniff — each SSE adapter supplies `author` explicitly to the same reducer/component. The distinction is *which contract the surface speaks*, a real difference, handled by the adapter, not by the shared code guessing.

---

## D3 — Persisted source of truth for reload

`agent_memory` already carries per-row `agent_name`, `message_kind` (`user|agent_output|rationale`), and `scope` (`agent|workflow_run`) — confirmed in `schemas.py::AgentMemoryResponse` (L1826-1844) and the columns POC-1 added. The workflow-scoped read (`routers/memory.py::list_memory` L97-157) **drops the agent_name filter** for `scope='workflow_run'` and returns every member's rows ordered by `message_index`. suite-75 T-S75-004 already proves both members' rows come back (reads `GET /agents/{WA}/memory?scope=workflow_run&thread_id=<parent_run_id>`, suite lines 409-421).

**Decision:** **no new storage, no migration** for POC-2. The transcript conversation key is the **parent workflow run id** (`workflow_orchestrator.py::_run_step` L496 `conversation_id = parent_run_id`). Reload = `listMemory(<member agent name>, {thread_id: <parent_run_id>, scope:'workflow_run'})`.

**Constraint:** the memory GET path requires a valid `Agent` (`_get_agent_or_404`, memory.py L37). A *workflow* name is not an Agent, so the path `{name}` must be a **member** agent name (suite-75 uses `WA`). The eval transcript (T7) resolves a member name from `detail.actual_member_path` / `per_member[].member`.

---

## D4 — Per-agent color scheme

No existing color-by-string helper in the studio (`grep` for `charCodeAt|stringToColor|colorFor` → none). **Decision:** new `studio/src/lib/agentColor.ts` — a small deterministic hash (sum of char codes, or FNV-1a) mod a **fixed 8-entry Tailwind palette**, each entry `{bg,text,border,dot}` chosen for legible contrast on the existing slate/white chat surfaces (e.g. indigo / emerald / amber / rose / sky / violet / teal / orange at the `-100 bg / -700 text / -200 border / -500 dot` shades). Deterministic so the same agent gets the same color across turns, reloads, and both the live tree and the reloaded transcript. Tailwind classes are **static strings** in the palette table (no dynamic `bg-${x}` — Tailwind can't see those at build time).

---

## D5 — Where the "share context" toggle field lives

`CompositeWorkflow.memory_enabled: boolean` already exists (registryApi.ts L542) and `CreateCompositeWorkflowRequest.memory_enabled?: boolean` (L586); the PATCH body type (`Partial<CreateCompositeWorkflowRequest>`, L621) already accepts it. **Decision:** the "Share context between agents" toggle maps to `memory_enabled`. It is currently **never sent** by the modal (`createCompositeWorkflow` L286 and `updateCompositeWorkflowApi` L338 omit it), so the only work is wiring the modal control to the existing field.

The design doc (§10) also mentions "per-session/per-run" and "share rationale" toggles — **neither has a backing column**. Per "do not invent a parallel field" + "no new storage", they are **deferred** (plan §9): per-session/per-run is entrypoint-derived (arch §5.4); share-rationale depends on the POC-1b summarizer.

---

## D6 — `agent_start` frame: synthesize at the proxy

The runner emits `text_delta` with no author (declarative-runner `main.py:624`). The registry proxy knows the agent name (path `{name}`). **Decision:** the proxy **synthesizes** `agent_start` once, right after the upstream returns 200, before the first token — a uniform "open a bubble for author X" signal for the reducer. No runner change. For single-agent that is one `agent_start`; the frame generalizes cleanly if a future streaming multi-speaker path ever appears, but we do not build that now.

---

## D7 — Reducer shape

Each surface has its own `Message` type (ChatPane has `chips`/`safetyBlock`; others are lean). **Decision:** the reducer helpers (`routeToken`, `openAuthorBubble`) are **generic over `M extends Attributed`** and take a `make(author)` factory, so each page keeps its own richer `Message` while sharing the routing logic. This keeps the routing logic in one tested place without forcing a single Message type across surfaces (which would break ChatPane's chips).

---

## D8 — Save→reload→assert coverage (DoD #2)

The only *new write surface* in POC-2 is the share-context toggle. **Decision:** that is the mandatory frontend save→reload→assert (Playwright: save `memory_enabled`, reload builder, assert). The attributed-bubble *rendering* is proven live (Playwright asserts ≥2 member bubbles); its *persistence* is proven at the backend by suite-75 T-S75-004 (transcript survives per-author) + new T-S75-007 (stream frames carry author). AgentChat/ChatPane do not seed from the transcript on reload (that is POC-5), so there is no UI reload round-trip to assert for them — recorded as a deferred gap, not a silent skip.

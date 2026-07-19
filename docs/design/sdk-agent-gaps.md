# SDK Agent Runtime Gaps — vs. Declarative Runner

**Status:** DRAFT — investigation
**Date:** 2026-07-19
**Scope:** `sdk/agentshield_sdk/` (custom-container `agent_type=sdk` runtime: `server.py` / `runner.py` / `graph_builder.py`) compared against `services/declarative-runner/` (`agent_type=declarative` runtime: `main.py` / `workflow_executor.py` / `node_executors.py`).

This doc was written while investigating an unrelated MCP-tool-source design. While tracing how the two agent runtimes dispatch governed tool calls, we found the twin runtimes are not at parity — some gaps are topology-appropriate (declarative-only workflow orchestration has no single-agent analog and that's fine), but three are real behavioral asymmetries between two runtimes that are supposed to give agents identical governance and features. This doc is investigation + documentation only; no code was changed.

No pre-existing "gap ledger" convention fit this document's scope — `docs/testing/manual-ui-e2e-test-plan.md` has a **Known gaps** header (tags: `deferred (intentional)` / `not-yet-wired (debt)`) but it's product/UX-facing and per-feature, not a cross-cutting runtime-parity doc. This doc reuses that same two-tag vocabulary for consistency but lives under `docs/design/` as its own document, per the task's instruction.

---

## Gap 1 — `sdk`-type agents never bind end-user identity, so OPA sees `user_id=""` for every tool call

**One-line summary:** The declarative-runner binds the caller's identity (from `x-user-sub`/`x-agent-team` headers) into the `_current_user_context` ContextVar that `governed_tool` reads before calling OPA; the plain-SDK runtime never does this anywhere, on any route.

**Verified, not speculative.** I read `sdk/agentshield_sdk/server.py` in full (336 lines, every route handler and the one `@app.middleware("http")` — a request-timing counter, nothing identity-related) and grepped `runner.py` and `graph_builder.py`: there is no dependency, no middleware, no decorator, no header read of `x-user-sub`/`x-agent-team` anywhere in the sdk package. The only thing that touches `_current_user_context` is `graph_builder.py:272` (`governed_tool` reading it) and its own default-`{}` declaration at `graph_builder.py:407-409`, whose own comment says it is "Set by the declarative-runner before streaming; read by governed_tool" — the sdk side was never wired to set it.

By contrast, `services/declarative-runner/main.py` binds it in two places:
- `_bind_user_context` (`main.py:173-206`), an app-wide FastAPI dependency (`dependencies=[Depends(_bind_user_context)]` at `main.py:215`) that runs before every route.
- A second, redundant bind inside the `/chat/stream` SSE generator (`main.py:654-666`), which captures a reset token and calls `_current_user_context.reset(token)` in a `finally` (`main.py:719`) specifically so one request's identity can never leak into the next request served by the same worker.

The `_bind_user_context` docstring (`main.py:174-194`) is itself the strongest evidence this exact bug class already happened once: it explains that identity used to be set by hand inside `/chat/stream` only, so `/chat`, `/workflow-run`, `/resume/*`, and `/run` all reached `governed_tool` with an empty context and OPA denied every tool call with `user_id: ""` — invisibly, because it fails closed. The fix was to make the binding a structural, app-wide dependency so a new route can't forget it. **That fix was never ported to the sibling runtime.** `sdk/agentshield_sdk/server.py` has no equivalent dependency on any of its seven routes (`/health`, `/ready`, `/metrics`, `/chat`, `/chat/stream`, `/run`, `/resume/{thread_id}`).

registry-api dispatches to whatever pod is deployed — sdk or declarative — through the identical `/chat/stream` contract and does send the identity headers uniformly, regardless of runtime: `pod_stream.py:65-68`, `routers/playground.py:584-587` (`_real_agent_stream`), `routers/chat.py` (`stream_pod_chat_frames`). The headers arrive at sdk-type pods; nothing there reads them.

**Downstream consequence — this is not a soft/permissive gap, it's a hard deny.** `graph_builder.py:272-276`: when the ContextVar is unset (default `{}`, falsy), `user_ctx` is passed as `None`, and `opa_client.py:138-139` sends `"user_id": ""` to OPA on every call, no matter who the real end user is. `opa_policy/agentshield.rego:100-107,154-161` (`user_identity_ok` / `deny_reason == "missing_user_identity"`) fails closed: for `agent_class == "user_delegated"` (the schema default — `services/registry-api/schemas.py:84`, and applies to *both* agent types; sdk agents built via Studio's "Write Python" create path, `studio/src/pages/CreateAgentPage.tsx:977`, are `user_delegated` unless the creator explicitly picks `daemon`) a missing `user_id` is an outright **deny**, not a downgrade. And `_AGENT_CLASS`/`AGENTSHIELD_OPA_URL` are injected into every agent pod's manifest by `deploy-controller/manifest_builder.py:171-174` regardless of runtime, and `AGENTSHIELD_OPA_URL` being present as an env var means `sdk/agentshield_sdk/config.py:56-59`'s `DEV_MODE` is `False` in every real k8s deployment — so this is not a dev-mode artifact; the mock OPA path (`mock_opa.py`, which doesn't even accept a `user_id`/`agent_class` param) is the only place this would be silently masked, and that path is exactly what a local, off-cluster pytest run would exercise, not a real deployment.

**Concrete failure scenario.** A user builds a `sdk`-type agent via Studio's code path, leaves `agent_class` at its default `user_delegated`, deploys it, and chats with it in production. Every governed tool call it makes is denied by OPA with `missing_user_identity` — the agent reports something like "Tool 'X' denied by policy: missing_user_identity" for every single tool, regardless of who's chatting or what tool. A secondary symptom: batch/eval runs against an `sdk`-type agent can never auto-approve HITL either (the same ContextVar carries the `auto_approve` flag — `graph_builder.py:301`, `_AUTO_APPROVE_IDENTITIES`), so a non-interactive dataset eval of a high-risk-tool `sdk` agent creates a real approval and blocks waiting for a human that the eval harness never notifies, rather than mechanically auto-approving as designed.

**Severity/urgency.** High for any `sdk`-type, `user_delegated`-class agent — which is the default for every newly created agent, and Studio's own code-create path is user-facing, not daemon/service-only, so this is not confined to some rare background-job corner. It is lower-urgency only for `sdk`-type agents deliberately marked `agent_class=daemon` (no live user, `user_identity_ok` short-circuits true regardless), which is a real and common shape for standing services — but there is no guard preventing (and no warning on) a `user_delegated` sdk agent shipping broken.

**Status: not-yet-wired (debt) — but already scoped and tracked.** `docs/design/identity-propagation-architecture.md` (status: Proposed) already documents this exact drop point independently, in its "Current state" table row 2 (`identity-propagation-architecture.md:38`: *"SDK production agent pod | `/chat`,`/chat/stream` never read `x-user-sub`; ContextVar defaults to `{}`... | `sdk/agentshield_sdk/server.py:164-204`; `graph_builder.py:51-55`"*) and its Phase 2 implementation plan (`identity-propagation-architecture.md:178`: *"SDK pod runtime + Rego Gate 5... `server.py` reads/verifies the RCT header"*). That design has not been implemented — no `run_context.py`, RCT mint/verify, or SDK-side identity binding exists anywhere in the repo as of this writing (grepped: zero hits). This document doesn't introduce a new fix plan; it confirms the existing one is still accurate and still unshipped, from the specific angle of "the two runtimes are asymmetric," which the architecture doc doesn't frame explicitly (it frames drop points per-hop, not per-runtime-pair).

---

## Gap 2 — Conversation memory / context-storage is entirely absent from the SDK runtime, not just differently scoped

**One-line summary:** `declarative-runner/main.py` loads and persists cross-turn conversation transcript via a "memory" HTTP call to registry-api (`_load_memory_context` / `_save_memory_turn`); the SDK runtime has no equivalent code at all — not a scoping difference, a missing feature.

**Evidence.** `_load_memory_context` (`services/declarative-runner/main.py:448-501`) and `_save_memory_turn` (`main.py:503+`) are wired into both `/chat` (`main.py:582`) and `/chat/stream` (`main.py:671`, plus the fire-and-forget persist after streaming closes, `main.py:722`). They call `GET/POST /api/v1/agents/{agent_name}/memory` on registry-api, keyed by `conversation_id`, and are the backing mechanism for the whole context-storage POC line (conversation continuity, POC-5's conversation list/"Continue", per-user memory scoping). I grepped every `.py` file in `sdk/agentshield_sdk/` for "memory": the only hits are `server.py`'s `/ready` check reporting Postgres availability as `"memory"` when no `DIRECT_DATABASE_URL` is set, and `checkpointer.py`'s `MemorySaver` fallback — both about the LangGraph checkpointer (per-thread graph state), unrelated to the transcript/memory-service feature. There is no call to `/api/v1/agents/{name}/memory` anywhere in the sdk package.

**What this means concretely.** An `sdk`-type agent still gets continuity *within one LangGraph `thread_id`* if a Postgres checkpointer is configured (the checkpointer persists graph state, and reactive chat keys `thread_id` on the session — `routers/playground.py:780`), but it never gets the separate "memory service" features: no cross-session "Continue this conversation" recall via the registry's memory endpoint, no rows written for POC-5's conversation list, no context-storage POC-3 (preference injection) or POC-4 (KB retrieval — though `knowledge_search` is wired as a tool, not through this path) hooks that ride on the same transcript rows. `docs/design/context-storage-architecture.md` and `docs/design/context-storage-poc-5-conversations.md` are written entirely in terms of `declarative-runner/main.py`/`orchestrator.py` and never mention the sdk single-agent runtime — there's no line in either doc stating "sdk-type agents are out of scope for context storage." It reads as an oversight (the whole POC line was built and verified against declarative-type agents/workflows) rather than a deliberate, documented exclusion.

**Concrete failure scenario.** A user builds an `sdk`-type chat agent, has a long conversation, closes the tab, comes back later and picks "Continue" on that conversation (POC-5 UX) or expects their saved preferences (POC-3) to carry into the next session. For a `declarative`-type agent this works. For an `sdk`-type agent, none of it fires — the conversation list UI, if it queries the same `agent_memory` table, will show no rows for that agent's sessions at all, because nothing ever wrote to it.

**Severity/urgency.** Medium. It's a real, user-visible feature gap for anyone building `sdk`-type conversational agents (again, a first-class, user-facing Studio create path, not a rare corner), but it fails by omission (feature silently doesn't exist) rather than by active harm (no governance/security implication, no data leaked to the wrong scope — there's simply no data written). Urgency should track however much the platform intends `sdk`-type agents to be a first-class chat surface going forward; if `sdk`-type is meant to stay a niche/advanced-user path, this is lower priority.

**Status: not-yet-wired (debt).** No design doc claims this is deliberately out of scope for `sdk`-type agents; it looks like the context-storage POC work simply never reached the sibling runtime.

---

## Gap 3 — SDK runtime has no streaming HITL-resume endpoint; the interactive resume path 404s for `sdk`-type agents

**One-line summary:** `declarative-runner/main.py` exposes both `/resume/{thread_id}` (blocking) and `/resume/{thread_id}/stream` (SSE); `sdk/agentshield_sdk/server.py` only implements the blocking one — and registry-api's interactive (chat/playground) resume path always calls the streaming one, with no fallback.

**Evidence.** Route inventories, read directly off both files:

- `services/declarative-runner/main.py`: `/health`, `/workflow-run`, `/ready`, `/metrics`, `/chat`, `/chat/stream`, `/resume/{thread_id}` (`main.py:733`), **`/resume/{thread_id}/stream`** (`main.py:797`, backed by `workflow_executor.resume_stream()` at `workflow_executor.py:912-924`), `/run`.
- `sdk/agentshield_sdk/server.py`: `/health`, `/ready`, `/metrics`, `/chat`, `/chat/stream`, `/run`, `/resume/{thread_id}` (`server.py:300`, calling `runner.resume()`). **No `/resume/{thread_id}/stream` route.** `runner.py` has `run()`, `run_streamed()`, `resume()`, `run_durable()`, `resume_durable()` — no `resume_streamed`/`resume_stream` method at all (confirmed by reading the full 320-line file).

`/workflow-run` is the one topology-appropriate absence (single agents have no workflow to run) — not counted as a gap. The missing `/resume/{thread_id}/stream` is not topology-related; it's a plain missing capability.

registry-api's two interactive resume call sites unconditionally target the streaming path for **either** runtime, with no fallback to the blocking endpoint:
- `services/registry-api/routers/chat.py:1055` (`resume_stream_chat`, consumer production chat) — `POST {service_url}/resume/{thread_id}/stream`.
- `services/registry-api/routers/playground.py:2006` (`resume_stream_playground_run`) — same, and on any non-200 response (`playground.py:2021-2025`) it yields an `error` SSE frame + a `done` frame and calls `_complete_run(run_id, "")` (`playground.py:2053-2055`) — i.e. it marks the run finished with empty output, it does **not** retry against the non-streaming endpoint.

The only resume path that *does* work identically on both runtimes is the "console decide" / durable-run path in `services/registry-api/routers/approvals.py:142-160`, which deliberately POSTs the non-streaming `/resume/{thread_id}` (the comment at `approvals.py:148` even calls this out: "the resume POST path (`/resume/{thread_id}`) is identical for both shapes").

**Concrete failure scenario.** A user chats interactively with an `sdk`-type agent in Studio Playground (or production consumer chat), the agent calls a high-risk tool, HITL pauses it, a reviewer approves it in the UI. registry-api's `resume_stream_playground_run`/`resume_stream_chat` POSTs to `{agent_svc_url}/resume/{thread_id}/stream` on the sdk pod — that route doesn't exist, FastAPI returns a plain 404, the proxy surfaces a generic "Agent returned HTTP 404" error to the chat pane, and the run is marked complete with no output. Critically, the pod's *real* resume handler (`/resume/{thread_id}`, the one that would actually call `runner.resume()` and let the approved tool execute) is never invoked by this path — the thread stays parked on its LangGraph checkpoint forever; the approval is effectively lost from the user's point of view even though it was recorded as "approved" in the DB.

This is a different root cause from the already-known "reactive-workflow HITL resume is not wired" gap (`docs/testing/manual-ui-e2e-test-plan.md` ~line 279: workflow orchestration never re-drives a paused member). That one is about composite workflow orchestration wiring; this one is about a single `sdk`-type agent's own pod missing an endpoint registry-api unconditionally expects to exist. Distinct gaps, same symptom family (HITL resume silently doesn't complete).

**Severity/urgency.** High. This breaks the core promised safety UX (approve → agent continues) for `sdk`-type agents specifically, in the interactive surfaces (Studio Playground, production consumer chat) that are the primary way a human reviewer would ever see and act on a HITL approval. It is masked in current e2e coverage: `scripts/e2e/suite-4-hitl.sh` creates/decides `Approval` rows directly against the registry API and never drives a live pod through `resume_stream_playground_run`/`resume_stream_chat`, so it cannot catch this class of break (consistent with this repo's own "bash e2e tests the API, not the screen" caveat).

**Status: not-yet-wired (debt).** No design doc claims streaming resume is deliberately declarative-only; `docs/design/hitl-approval-system.md` and the context-storage docs don't scope resume by runtime at all.

---

## Investigated and ruled out (not a gap)

**Candidate: Langfuse trace `user_id` attribution differs by runtime.** Checked `services/registry-api/routers/chat.py:435-522` (`_create_traced_chat_run`) — the trace's `user_id` is set to `preferred_username or user_sub` (`chat.py:510`) via `trace_create_run` at the **registry-api layer**, before the run is ever dispatched to either runtime's pod. It does not depend on `_current_user_context` or anything set inside the agent pod. Grepped `sdk/agentshield_sdk/otel.py`, `sdk/agentshield_sdk/tracing.py` for `user_id`/`_current_user_context`: zero hits — neither runtime attaches its own per-span user attribution; both inherit the same session-level trace `user_id` set by registry-api. This candidate does **not** hold up as a runtime asymmetry.

---

## Summary table

| Gap | Severity | Status |
|---|---|---|
| 1. `sdk`-type agents never bind end-user identity → OPA sees `user_id=""` → every governed tool call denied (`missing_user_identity`) for `user_delegated`-class sdk agents | High | not-yet-wired (debt) — tracked in `docs/design/identity-propagation-architecture.md` (Phase 2), not yet implemented |
| 2. Conversation memory / context-storage service is entirely absent from the SDK runtime | Medium | not-yet-wired (debt) |
| 3. SDK runtime has no `/resume/{thread_id}/stream` — interactive HITL resume 404s for `sdk`-type agents | High | not-yet-wired (debt) |
| — SDK runtime has no `/workflow-run` route | N/A | deliberate/acceptable difference — single agents have no workflow topology to run |
| — Langfuse trace `user_id` attribution (investigated) | N/A | not a gap — set identically for both runtimes at the registry-api layer |

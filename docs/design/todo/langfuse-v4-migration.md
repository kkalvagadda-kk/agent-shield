# Langfuse Python SDK v2 → v4 Migration

**Status:** Not started, but **REQUIRED for agent-side LLM/tool span capture** — reclassified 2026-07-11 (was "deferred/strategic"). See the blocking finding below.

## ⚠️ Blocking finding (2026-07-11): the agent langchain stack is 1.x, which langfuse v2 cannot instrument

The Phase 2 span-capture work attempted to "align down" to langfuse v2 (matching registry-api), on the assumption that v2's `langfuse.callback.CallbackHandler` would capture LLM/tool spans from the agent's LangGraph runs. **That assumption was wrong**, discovered by introspecting a live agent pod:

- The SDK's LangChain ecosystem resolved to **1.x** — `langchain 1.3.13`, `langchain-core 1.4.9`, `langgraph 1.2.9`, `langchain-anthropic 1.4.8`. The agent executes correctly on this stack (HITL, checkpointing, streaming all use langgraph 1.x APIs).
- langfuse **2.60.10's** callback handler hard-imports `from langchain.callbacks.base import BaseCallbackHandler` — a module **removed in langchain 1.x** (`langchain.callbacks` no longer exists; only `langchain_core.callbacks`). So `_make_langfuse_handler` raises `ModuleNotFoundError` → its bare `except` returns `None` → **no LLM/tool spans**, on every run.
- Only **langfuse v4's** `langfuse.langchain.CallbackHandler` is built for the modern `langchain_core` and can instrument langgraph 1.x.

**Conclusion:** agent-side LLM/tool span capture cannot be done on langfuse v2 without downgrading the entire LangChain/LangGraph stack to 0.3.x — which would risk breaking the (recently-shipped, 1.x-dependent) agent execution + HITL. Therefore **v4 is required** for the agent side, not optional.

### What v2 DID deliver (Phase 2 partial result, shipped)
The env-var + `public_key` fixes enabled the **SDK's own v2 tracer** (`agentshield_sdk.tracing.Tracer`, which uses `client.trace()`), so `safety_scan_*` spans now appear in traces (verified: a trace went from 0 observations to 1 `safety_scan_input` span). The mechanism fixes (correct `LANGFUSE_*` env var names, cross-namespace `LANGFUSE_HOST` FQN, langfuse non-gating readiness) are correct regardless of version and are keepers. Only the **LangChain callback path** (LLM/tool generation spans) remains blocked pending v4.

### Decision needed from the user
- **(recommended) Migrate the agent side to v4** (this doc), which unlocks the langchain-1.x-compatible handler. registry-api can stay v2 short-term, but see the cross-version note (Gap 0d) — a v4 agent attaching spans to a v2-created trace needs verification (trace-id format differs); cleanest is to migrate registry-api too.
- **(not recommended) Downgrade the LangChain/LangGraph stack to 0.3.x** to keep langfuse v2 — reverses recent agent work and risks HITL/execution regressions.

---

**Original framing (still valid for the strategic case):** Deferred out of the observability span-capture work (2026-07-11); this doc scopes what a real v4 migration entails so it can be picked up intentionally, not as a rushed rewrite.
**Related:** `docs/design/observability-architecture.md` (the canonical tracing reference — its §2 pattern and "trace_id == run_id" invariant assume the v2 client; both change under v4, see below), `docs/design/todo/cost-tracking.md` (v4's native usage/cost on generations overlaps this).

## Why migrate at all (the case FOR v4)

None of these are required for span capture — v2 already delivers LLM/tool spans. They are platform-strategic:

- **OpenTelemetry-native foundation.** v4 traces are OTEL spans. Any OTEL-instrumented library (OpenAI SDK, Anthropic SDK, LlamaIndex, vector stores, MCP clients) can auto-emit into the same trace with zero langfuse-specific code. Standard W3C context propagation.
- **Richer semantic span typing.** v4 spans carry `as_type` ∈ {`generation`, `tool`, `agent`, `retriever`, `chain`, `embedding`, `evaluator`, `guardrail`} — the UI renders each distinctly, a much better multi-step-agent reading experience than v2's generic spans.
- **Observation-centric model.** `user_id`/`session_id`/`tags`/`metadata` propagate to every observation (via `propagate_attributes()`), enabling span-level (not just trace-level) filtering/analytics.
- **Native cost/usage.** `usage_details`/`cost_details` are first-class on generation observations (feeds the deferred Portkey cost-tracking effort — though v2's langchain handler already captures token usage, so this is incremental).
- **Actively maintained line.** v2 is legacy/maintenance-mode; v3/v4 get new features, integrations, and fixes.

## Current state (post-v2 span-capture work)

- **registry-api**: `langfuse==2.*` (2.60.10). `tracing.py` uses `client.trace(id=run_id, ...)` / `.span(...)`. Works against the deployed langfuse **server 3.205** (v2 client ⇄ v3 server confirmed compatible in production).
- **SDK + declarative-runner**: pinned to langfuse v2 (matching registry-api), `langchain` installed, env vars `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST`. `_make_langfuse_handler` uses `from langfuse.callback import CallbackHandler` with `(trace_id=…, public_key=…, secret_key=…, host=…)` — the v2 API.
- **Invariant relied on everywhere:** `langfuse_trace_id == run_id` (a UUID). Frontend trace URLs and `/api/public/traces/{id}` lookups use the stored `langfuse_trace_id`; today it equals `run_id`.
- **safety-orchestrator**: disabled/deferred; its `orchestrator.py` also uses the v2 `.trace()`/`.span()` API.

## What v4 actually changes (the API delta)

Verified by introspecting langfuse 4.14.0 in a live agent pod + the [v3→v4 upgrade docs](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4):

- **`.trace()` is removed.** No imperative "create a trace object by id." Traces are created implicitly by the first observation. Root observation: `langfuse.start_observation(trace_context={"trace_id": tid}, name=..., as_type="span", input=...)` → returns a span you `.end()`; or the context-manager form `with langfuse.start_as_current_observation(...) as span:`.
- **`update_trace()` / `update_current_trace()` are gone** (absent in 4.14). Trace-level attributes (`user_id`, `session_id`, `tags`, `metadata`) are set via **`propagate_attributes(user_id=…, session_id=…, tags=[…])`** — a **context manager** that stamps the current + all child observations. Trace input/output via `set_trace_io` / `set_current_trace_io`.
- **Trace IDs are OTEL-format** (32-char hex), not arbitrary UUIDs. Deterministic derivation: **`trace_id = langfuse.create_trace_id(seed=run_id)`**. → The `trace_id == run_id` invariant becomes `langfuse_trace_id = create_trace_id(seed=run_id)`. The **existing `langfuse_trace_id` column absorbs this** (store the derived id; frontend already looks up by that column, not raw `run_id`) — so **no mapping table is needed**, but the observability doc's invariant statement must be rewritten and the `X-AgentShield-Trace-ID` header must carry the derived id end-to-end.
- **LangChain handler**: `from langfuse.langchain import CallbackHandler` (moved from `langfuse.callback`). Constructor takes **no** `trace_id`/keys — it binds to the **current OTEL context**. So you must wrap the graph invocation:
  ```python
  from langfuse import get_client
  from langfuse.langchain import CallbackHandler
  lf = get_client()
  handler = CallbackHandler()
  with lf.start_as_current_observation(as_type="span", name="agent-run",
                                       trace_context={"trace_id": derived_tid}):
      with propagate_attributes(user_id=uname, session_id=sid, tags=[...]):
          graph.invoke(inputs, config={"callbacks": [handler]})
  ```

## Migration plan (file by file)

1. **`services/registry-api/requirements.txt`** — `langfuse==2.*` → `langfuse>=4,<5`. Pulls the OpenTelemetry SDK stack; verify no transitive conflict with FastAPI/httpx/sqlalchemy; rebuild.
2. **`services/registry-api/tracing.py`** — rewrite all 7 helpers to v4:
   - `trace_create_run` → `create_trace_id(seed=run_id)`, create a root observation with `trace_context`, set attributes. **Hard part:** this function opens the trace and returns; there is no `with` block spanning the request. Use the non-context-manager `start_observation(...)` + `.end()`, and resolve how to set `user_id`/`tags` without an enclosing `propagate_attributes` scope (candidate: apply attributes on the root observation directly, or open+immediately-close a scoped span). Prototype this first — it's the riskiest single piece.
   - `trace_complete_run` → update/close the trace by id from a **detached background task** (`asyncio.create_task(_complete_chat_run…)`). **Highest risk:** OTEL context does not flow across `asyncio.create_task` or through the SSE generator, so re-entering the trace by id here must use explicit `trace_context`, not ambient context.
   - `trace_eval_run_*`, `trace_judge_score` (→ `create_score`), `trace_platform_action` — mechanical once the pattern above is settled.
   - Return the derived trace_id (store in `langfuse_trace_id`).
3. **`sdk/agentshield_sdk/tracing.py`** — rewrite the `Tracer` wrapper (`start_trace`/`span`/`end_trace`) to v4 spans; keep the `trace_id`-attach semantics via `trace_context`.
4. **`services/declarative-runner/workflow_executor.py`** — rewrite `_make_langfuse_handler` + the 4 call sites (currently `config["callbacks"] = [handler]`) into the `start_as_current_observation(trace_context=…)` + `propagate_attributes(…)` + no-arg `CallbackHandler()` wrapper shown above. The SDK `tracer` calls here follow #3.
5. **`services/declarative-runner/requirements`** — `langfuse` → v4 (langchain already added by the v2 work).
6. **`services/safety-orchestrator/orchestrator.py`** — rewrite `_emit_scanner_span` + its `.trace()`/`.span()` usage to v4. Bundle with re-enabling safety-orchestrator (also deferred) — same capability.
7. **`docs/design/observability-architecture.md`** — update §1 (trace_id derivation), §2 Step 1/3 (v4 create/close pattern), §2 env/handler contract, and the status matrix.
8. **Version alignment** — do registry-api + SDK + declarative in the **same** change so no v2-parent/v4-child skew exists at any point.

## Risks (the ones that need prototyping, not just coding)

- **Async + OTEL context (highest).** `contextvars`-based OTEL context does not cross `asyncio.create_task` boundaries or detached SSE generators — exactly where `_complete_chat_run` and streaming live. Trace close / attribute setting from those sites must use explicit `trace_context`, never ambient. Prove this with a spike before committing.
- **Distributed create/return/close-later impedance.** v4 is built for in-process `with`/`@observe`; our create-in-registry-api / span-in-agent-pod / close-in-background split fights it. Risk of orphan/empty root spans or attributes silently not landing — the same "looks fine, is empty" failure class the span work exists to kill.
- **Critical-path blast radius.** Rewriting tracing inside registry-api puts new code on every chat/eval/HITL path. Keep the broad `except`→log guard so a v4 tracing error can never fail a real request.
- **Dependency/build.** v4 + OTEL stack into registry-api + agent images: transitive conflicts, image size, build breakage.

## Verification

- **Spike first:** a throwaway prototype of `trace_create_run` + `trace_complete_run` (create in a request, close in a detached task) proving attributes + output land on the right trace across the async boundary. Go/no-go on the whole migration.
- Then: every trace path re-verified live (chat, deployment-pinned chat, HITL resume, eval, platform-action) — fetch `/api/public/traces/{id}` and assert observations + trace attributes, not just "no error."
- Cross-check the derived-trace-id chain end-to-end: `create_trace_id(seed=run_id)` in registry-api == the id in `langfuse_trace_id` == the `X-AgentShield-Trace-ID` header == the `trace_context` the agent attaches spans to == the id in the frontend trace URL.
- Add an e2e that asserts a deployed agent's trace has `GENERATION` + `tool` spans (the same acceptance test the v2 work uses; version-agnostic).

## Prerequisite / sequencing

- Do **after** the v2 span-capture work is stable in production (need a known-good baseline to diff against).
- Bundle safety-orchestrator's v4 rewrite with re-enabling that capability (both deferred).
- Treat the async/OTEL-context spike as a gate: if it can't cleanly close a trace from a detached task by explicit `trace_context`, reconsider — that pattern is load-bearing across the whole platform.

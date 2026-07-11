# Provider-Agnostic Observability (OpenTelemetry) — Langfuse as the first backend

**Status:** Not started. Supersedes the narrower "Langfuse v2→v4 migration" framing — the platform should be **decoupled from any single observability backend**, with Langfuse as the first (reference) backend rather than a hard dependency. The langfuse-specific v4 details are preserved below as the **Langfuse adapter mechanics**.
**Drivers:** (1) product goal — swap the observability backend by config, not code (Langfuse today, possibly Datadog / Honeycomb / Arize Phoenix / Grafana Tempo / self-hosted OTEL collector tomorrow); (2) a blocking finding (below) that already forces the emit-path off the langfuse v2 client.
**Related:** `docs/design/observability-architecture.md` (canonical tracing reference — its §2 pattern and "trace_id == run_id" invariant assume the langfuse v2 client; both change here), `docs/design/todo/cost-tracking.md` (token/cost overlaps the emit path).

## ⚠️ Blocking finding (2026-07-11): the agent LangChain stack is 1.x, which the langfuse v2 client cannot instrument

Phase 2 span-capture tried to "align down" to langfuse v2 (matching registry-api). Verified wrong by introspecting a live agent pod:

- The agent's LangChain ecosystem is **1.x** — `langchain 1.3.13`, `langchain-core 1.4.9`, `langgraph 1.2.9`, `langchain-anthropic 1.4.8`. The agent executes correctly on it (HITL, checkpointing, streaming use langgraph 1.x APIs).
- langfuse **2.60.10's** callback handler hard-imports `from langchain.callbacks.base` — **removed in langchain 1.x**. So `_make_langfuse_handler` raises `ModuleNotFoundError` → bare `except` → `None` → **no LLM/tool spans**, every run.
- Downgrading the whole LangChain/LangGraph stack to 0.3.x to keep langfuse v2 would reverse recent 1.x-dependent agent work (HITL) — not acceptable.

**Consequence:** the emit path must move off the langfuse v2 client anyway. Rather than move it onto the langfuse **v4 proprietary** client (re-coupling to one vendor), move it onto **OpenTelemetry** — which solves the langchain-1.x problem *and* the portability goal at once, for roughly the same effort.

### What the v2 work already delivered (shipped, keepers)
The env-var + `public_key` fixes enabled the **SDK's own v2 tracer**, so `safety_scan_*` spans now appear (0→1 observation verified). The mechanism fixes — correct `LANGFUSE_*` env-var names, cross-namespace `LANGFUSE_HOST` FQN, langfuse non-gating readiness — are backend-agnostic and stay. Only the **LLM/tool generation spans** remain blocked, pending this work.

---

## Target architecture — two seams + own-your-data

Observability has two independent concerns with very different "agnostic" stories. Design them separately.

### Seam 1 — Emit (write path): standardize on OpenTelemetry
Emit **OTEL spans**, not backend-specific calls. Any OTEL backend ingests them by pointing an **OTLP exporter** at its endpoint — chosen by config, no code change.

- **LangChain/LangGraph spans (the bulk):** instrument with a **vendor-neutral OTEL GenAI instrumentation** — [OpenInference](https://github.com/Arize-ai/openinference) or [OpenLLMetry](https://github.com/traceloop/openllmetry) — which emit standard GenAI-semantic-convention spans (generation, tool, chain, retriever) that *any* OTEL backend renders. This replaces `_make_langfuse_handler`'s langfuse-specific `CallbackHandler`. (Alternative if you ever commit hard to Langfuse: langfuse v4's `langfuse.langchain.CallbackHandler` — richer Langfuse-native mapping, but re-couples. Prefer the neutral instrumentation for the stated goal.)
- **Platform-emitted spans** (registry-api trace creation, safety-orchestrator scan spans): emit via the plain `opentelemetry-sdk` (`tracer.start_span(...)`), not a vendor client.
- **Exporter:** OTLP/HTTP to the configured endpoint. Langfuse v3+ exposes an OTLP ingestion endpoint, so **Langfuse becomes "the configured OTLP backend,"** not an imported library. Swap = change the endpoint (and API-key headers) in config.
- **Trace-id:** OTEL trace IDs are W3C 32-hex. Derive deterministically from `run_id` (`create_trace_id(seed=run_id)` or an equivalent hash) and store in `langfuse_trace_id` (rename the column concept to `observability_trace_id` eventually). The `trace_id == run_id` invariant becomes `observability_trace_id = derive(run_id)`; the existing column absorbs it, no mapping table.

### Seam 2 — Read/display (read path): a backend adapter interface behind registry-api
OTEL standardizes *emit*, **not query**. Each backend has a different read API + data shape + native UI. Confine that behind one interface — Studio never talks to a backend directly (already the rule in `observability-architecture.md` §2).

Define `ObservabilityBackend` in registry-api:
```
get_trace(trace_id) -> NormalizedTrace          # normalized shape, NOT the backend's raw JSON
list_traces(filters) -> list[NormalizedTraceSummary]
dashboard(filters) -> DashboardData
build_trace_url(trace_id) -> str | None          # provider-specific deep-link, or None if no UI
```
- **`NormalizedTrace` is a stable, provider-neutral shape** (trace meta + nested spans with type/name/timing/io/status). The Langfuse adapter maps langfuse's `/api/public/traces/{id}` response into it. Adding a backend = one adapter class.
- **`build_trace_url` returns `None`** for backends with no queryable UI or a poor hand-off → Studio hides the deep-link for those and relies purely on the inline drawer.

### Own your product data (don't store it in the observability backend)
Judge scores, user feedback, eval results, run metadata are **business data, not spans**. Store them in the platform's **own Postgres** (source of truth) and *optionally* mirror to the backend as span attributes/events. This makes them provider-agnostic by default and removes the dependency on any vendor's scores/evals API (which OTEL does not standardize). The feedback-ratio dashboard panel already wants a local `user_feedback` column — same principle.

### Config-driven backend selection
One env/values block picks the backend: `OBSERVABILITY_BACKEND=langfuse|otlp|none`, OTLP endpoint + auth headers, and which read-adapter registry-api loads. `none` disables emit + hides trace UI cleanly (tracing already non-gating for readiness).

### Studio UI — provider-neutral by design
- The inline **`TraceDrawer` is the primary, consistent surface** — it renders the *normalized* shape, so it looks identical across backends. **Required change this iteration:** decouple it from Langfuse's raw JSON (`data.langfuse.observations`, `.userId`, …) and have it consume `NormalizedTrace` from the read-adapter. This is architectural, not cosmetic — without it "agnostic" is false at the UI.
- The deep-link button reads a plain **"Trace ↗"** and **does not reveal the backend until clicked** (keeps Studio vendor-neutral in appearance; the user only learns it's Langfuse/Datadog/etc. on landing). Hidden entirely when `build_trace_url` returns `None`.
- **Deferred visual polish (NOT this iteration):** nested waterfall/tree view (spans render flat today), per-generation token/cost, inline scores in the drawer, richer empty state. All optional; track separately.

### The one thing that can't be abstracted
The **deep-link landing experience** — the vendor's own UI on the other side of "Trace ↗". URL *construction* is abstracted (`build_trace_url` per adapter) and the button is neutral, but the destination is a different product with its own layout, features, and auth (Langfuse rides Keycloak SSO; Datadog/Honeycomb use their own). This is the single place provider-agnosticism inherently leaks — which is exactly why the inline drawer, fully under our control, is the primary surface and the deep-link is a per-provider escape hatch.

---

## Current langfuse coupling inventory (what the migration touches)

- **Emit:** `services/registry-api/tracing.py` (`.trace()`/`.span()`/`create_score`), `sdk/agentshield_sdk/tracing.py` (Tracer wraps langfuse client), `services/declarative-runner/workflow_executor.py` (`_make_langfuse_handler`), `services/safety-orchestrator/orchestrator.py` (scan spans).
- **Read:** `services/registry-api/routers/playground.py` + `observability.py` + `catalog.py` fetch langfuse `/api/public/traces/{id}` (Basic auth) and build langfuse trace URLs.
- **UI:** `studio/src/components/playground/TraceDrawer.tsx` renders langfuse's response shape; "Open in Langfuse ↗" deep-links.
- **Data model:** `langfuse_trace_id` column, `trace_id == run_id` invariant, scores written to langfuse.
- **Deploy:** langfuse bundled as an internal component (Helm + ClickHouse + MinIO + Redis).

## Migration plan

1. **Emit → OpenTelemetry.**
   - Add `opentelemetry-sdk` + `opentelemetry-exporter-otlp` to registry-api, SDK, declarative-runner, safety-orchestrator. Configure an OTLP exporter from `OBSERVABILITY_*` env.
   - Replace `_make_langfuse_handler` with a vendor-neutral GenAI instrumentation (OpenInference/OpenLLMetry) attached to the langgraph invocation — solves the langchain-1.x block.
   - Rewrite `registry-api/tracing.py`, `sdk/tracing.py`, `safety-orchestrator/orchestrator.py` to emit via the OTEL SDK. Derive trace-id from `run_id`; propagate it via `X-AgentShield-Trace-ID` → OTEL `trace_context` in the agent.
2. **Read → `ObservabilityBackend` interface.** Extract the existing langfuse read code (playground/observability/catalog routers) behind the interface; return `NormalizedTrace`. Ship the Langfuse adapter as backend #1.
3. **TraceDrawer → normalized shape.** Re-point `TraceDrawer.tsx` + `observabilityApi.ts` at `NormalizedTrace`; neutral "Trace ↗" label; hide when `build_trace_url` is `None`.
4. **Own-your-data.** Add local columns/tables for feedback + scores; write-through on `submit_run_feedback` and judge scoring; treat backend scores as optional mirror.
5. **Config + deploy.** `OBSERVABILITY_BACKEND` + OTLP endpoint/auth in values.yaml; make bundled-langfuse optional (external-OTLP mode).
6. **Docs.** Rewrite `observability-architecture.md` §1–§3 for the OTEL emit contract, the read-adapter, and the derived trace-id; update the status matrix.

### Langfuse adapter mechanics (the preserved v4 API delta)
The Langfuse read-adapter uses langfuse's `/api/public/*` REST API (works today). If you instead choose langfuse's v4 *client* for emit (not recommended vs OTEL): `.trace()` is gone (use `start_observation(trace_context=…)`), trace attributes go through the `propagate_attributes()` context manager, IDs come from `create_trace_id(seed=…)`, and the langchain handler is `langfuse.langchain.CallbackHandler` bound to the current OTEL context. These are Langfuse-specific; the OTEL path above avoids importing them.

## Risks (need a spike, not just coding)

- **Async + OTEL context (highest).** OTEL context is `contextvars`-based and does **not** cross `asyncio.create_task` boundaries or detached SSE generators — exactly where `_complete_chat_run` and streaming live. Closing/attaching a trace from those sites must use **explicit** `trace_context`, never ambient. Prove with a spike before committing — this risk is identical whether the backend is langfuse-v4 or plain OTLP, because both are OTEL under the hood.
- **Distributed create/return/close-later impedance.** Trace opened in registry-api, spans added in the agent pod, closed in a background task — fights the in-process OTEL model. Risk of orphan/empty root spans or attributes silently not landing.
- **GenAI-semantic-convention fidelity.** Vendor-neutral instrumentation (OpenInference/OpenLLMetry) may map generations/tools slightly less richly into Langfuse's UI than langfuse's native handler. Verify the trace looks right in Langfuse before declaring parity; this is the price of neutrality.
- **Critical-path blast radius.** New emit code on every chat/eval/HITL path in registry-api — keep the broad `except`→log guard so a tracing error never fails a real request.
- **Dependency/build.** OTEL + instrumentation libs into 4 images: transitive conflicts, image size.

## Verification

- **Spike first:** prototype `trace_create` in a request + `trace_complete` in a detached task, proving attributes/output land on the right trace across the async boundary via explicit `trace_context`. Go/no-go gate for the whole migration.
- **Backend-swap test:** run the same agent chat twice with `OBSERVABILITY_BACKEND` pointed at (a) langfuse and (b) a second OTLP backend (e.g. a local Jaeger/Grafana Tempo or Arize Phoenix); assert the inline `TraceDrawer` renders the normalized trace identically in both. This is the real proof of agnosticism.
- **Span acceptance:** a deployed agent's trace contains `generation` + `tool` spans (the version-agnostic acceptance test).
- **Trace-id chain:** `derive(run_id)` == stored `observability_trace_id` == `X-AgentShield-Trace-ID` header == agent `trace_context` == frontend trace URL.
- **Own-data:** feedback/scores present in Postgres independent of the backend.

## Sequencing / what's explicitly deferred

- Do **after** Phase 1 (merged) is stable; use it as the known-good baseline.
- **This iteration includes:** OTEL emit, read-adapter interface + Langfuse adapter, TraceDrawer normalization + neutral "Trace ↗" label, own-your-data for feedback/scores, config-driven backend.
- **Deferred (track separately):** TraceDrawer visual polish (waterfall tree, token/cost, inline scores); a *second* concrete backend adapter (build the interface now, add the 2nd backend when a real need lands); safety-orchestrator re-enable (bundle its OTEL rewrite with that).

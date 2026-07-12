# Provider-Agnostic Observability (OpenTelemetry) — Langfuse as the first backend

**Status: LARGELY SHIPPED — both the agent-emit seam AND the read seam are built and live-verified. What remains is platform-emit → OTEL, a *second* concrete backend, and own-your-data generalization.** Supersedes the narrower "Langfuse v2→v4 migration" framing — the platform is now **decoupled from any single observability backend** for reads, with Langfuse as the first (reference) backend rather than a hard dependency. The langfuse-specific v4 details are preserved below as the **Langfuse adapter mechanics**.

**What is shipped (2026-07-12):**
- ✅ **Agent-side LLM/tool/generation spans emit via vendor-neutral OpenInference OTEL**, not the langfuse client — `sdk/agentshield_sdk/otel.py` (`otel_run_context(run_id)`), wired in `services/declarative-runner/workflow_executor.py` + `main.py`. This is exactly Seam-1's recommendation and it **resolves the blocking finding below**. `GENERATION`/`AGENT`/`CHAIN`/`TOOL` spans land in Langfuse via its OTLP endpoint.
- ✅ **Read seam BUILT** — `services/registry-api/observability_backend.py` defines `ObservabilityBackend` + `LangfuseBackend` (backend #1) + `NoneBackend`, selected by `OBSERVABILITY_BACKEND` (default `langfuse`, `none` disables). It owns `get_trace` (→ provider-neutral `NormalizedTrace`: spans/scores), `get_run_cost`, `spend_by_model`, `tool_call_stats`, `build_trace_url`, `push_score`. **Every** read call-site moved off inline `/api/public/*` + URL construction (observability, playground, catalog, agent_runs, deployments, eval_runner, composite_workflows, tracing, cost_backfill, workflow_orchestrator). Endpoints now return `trace` (NormalizedTrace), not raw `langfuse`. Studio (`TraceDrawer`, `ObservabilityComparePage`, `observabilityApi`/`playgroundApi`) consumes the neutral shape; deep-link label is a neutral "Trace ↗". Live-verified: `get_trace` on a real trace → 38 normalized spans + score; endpoint returns `{trace, trace_id, trace_url}` with no `langfuse` key.
- ✅ **Cost tracking (Path A)** rides on the OTEL emit + reads through the backend (`backend.get_run_cost`), surfaced on the dashboard + Cost console. See `docs/design/todo/cost-tracking.md`.

**What remains (the real scope of this doc now):**
- ❌ **Platform-emitted spans + score writes still use the langfuse client (EMIT seam)** — `registry-api/tracing.py` (`lf.trace()`, envelope trace + `trace_judge_score`), the feedback score POST in `playground.py` (`/api/public/scores`), and `safety-orchestrator/orchestrator.py` (scan spans) have NOT moved to the OTEL SDK / a neutral emit interface. These are the ONLY remaining Langfuse-direct calls, and they are all writes/emit.
- ❌ **A second concrete backend adapter** — the interface + `NoneBackend` exist; a real 2nd backend (Datadog/Phoenix/Tempo) is built "when a real need lands" (the interface makes it one adapter class).
- ⚠️ **Own-your-data partially in place** — `playground_runs.user_feedback` (migration 0057), `judge_score` (on `PlaygroundRun`/`AgentRun`), and now `agent_runs.cost_usd`/tokens are all persisted to *our* Postgres and read via SQL (survive a backend swap). Generalize any remaining backend-sourced business data to the same principle.
- ⚠️ **`NormalizedTrace` waterfall/tree polish** — spans render flat; nested waterfall, per-generation token/cost, inline scores in the drawer are deferred visual polish.

**Drivers:** (1) product goal — swap the observability backend by config, not code (Langfuse today, possibly Datadog / Honeycomb / Arize Phoenix / Grafana Tempo / self-hosted OTEL collector tomorrow); (2) a blocking finding (below, now **resolved for agent spans**) that forced the agent emit-path off the langfuse v2 client.
**Related:** `docs/design/observability-architecture.md` (canonical tracing reference — its §2 pattern and "trace_id == run_id" invariant still assume the langfuse client for the *platform envelope*; the read seam here changes the read side), `docs/design/todo/cost-tracking.md` (token/cost — **now shipped**, rides on the OTEL emit path; its Langfuse-direct reads are tracked here as Seam-2 debt).

## ✅ RESOLVED (2026-07-12) — Blocking finding: the agent LangChain stack is 1.x, which the langfuse v2 client cannot instrument

**Resolution:** the agent emit-path was moved to **vendor-neutral OpenInference OTEL** (`otel_run_context`), exactly as Seam-1 recommends — sidestepping both the langchain-1.x incompatibility and vendor lock-in. Agent `GENERATION`/`TOOL` spans now land in Langfuse via OTLP. The original finding is preserved below for context.

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
costs(filters) -> CostConsoleData                # NEW: cost/token aggregation (currently langfuse-direct)
get_run_cost(trace_id) -> {cost_usd, prompt_tokens, completion_tokens, model}  # NEW: per-run, used by the backfill sweep
build_trace_url(trace_id) -> str | None          # provider-specific deep-link, or None if no UI
```
The **cost methods are new (2026-07-12) and currently unabstracted** — `cost_backfill.py` + `observability._spend_by_model` call Langfuse's observations API directly. Folding them onto this interface is part of the read-seam work: `get_run_cost` becomes the Langfuse adapter's implementation of what `fetch_trace_cost_tokens` does today; `costs` wraps the `_spend_by_model` + SQL aggregation. A backend that doesn't expose per-generation cost returns `None` (the sweep just leaves `cost_usd` null — same graceful-degrade already in place).
- **`NormalizedTrace` is a stable, provider-neutral shape** (trace meta + nested spans with type/name/timing/io/status). The Langfuse adapter maps langfuse's `/api/public/traces/{id}` response into it. Adding a backend = one adapter class.
- **`build_trace_url` returns `None`** for backends with no queryable UI or a poor hand-off → Studio hides the deep-link for those and relies purely on the inline drawer.

### Own your product data (don't store it in the observability backend)
Judge scores, user feedback, eval results, run metadata are **business data, not spans**. Store them in the platform's **own Postgres** (source of truth) and *optionally* mirror to the backend as span attributes/events. This makes them provider-agnostic by default and removes the dependency on any vendor's scores/evals API (which OTEL does not standardize). **Already following this (2026-07-12):** `playground_runs.user_feedback` (migration 0057), `judge_score` on `PlaygroundRun`/`AgentRun`, and now **`agent_runs.cost_usd`/`prompt_tokens`/`completion_tokens`** — cost is persisted to *our* DB (backfilled from the backend), so cost dashboards/console read local SQL and survive a backend swap; only the *source* of the cost figure (langfuse observations) is backend-specific and moves behind the read-adapter. Generalize the remaining reads to the same principle.

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

- **Emit:** `services/registry-api/tracing.py` (`.trace()`/`.span()`/`create_score` — **still langfuse v2 client**), `sdk/agentshield_sdk/tracing.py` (Tracer wraps langfuse client), `services/declarative-runner/workflow_executor.py` (`_make_langfuse_handler` — **superseded for LLM/tool spans by `otel_run_context` / OpenInference OTEL, ✅ done**), `services/safety-orchestrator/orchestrator.py` (scan spans — **still langfuse client**).
- **Read:** `services/registry-api/routers/playground.py` + `observability.py` + `catalog.py` fetch langfuse `/api/public/traces/{id}` (Basic auth) and build langfuse trace URLs. **Cost reads added 2026-07-12 (same coupling):** `services/registry-api/cost_backfill.py` → `tracing.fetch_trace_cost_tokens` and `observability._spend_by_model`/`_tool_call_stats` fetch langfuse `/api/public/observations` directly. All of these must move behind the `ObservabilityBackend` read-adapter (`get_run_cost`/`aggregate_cost` belong on the interface).
- **UI:** `studio/src/components/playground/TraceDrawer.tsx` renders langfuse's response shape; "Open in Langfuse ↗" deep-links.
- **Data model:** `langfuse_trace_id` column, `trace_id == run_id` invariant, scores written to langfuse.
- **Deploy:** langfuse bundled as an internal component (Helm + ClickHouse + MinIO + Redis).

## Migration plan

1. **Emit → OpenTelemetry.** *(agent LLM/tool spans ✅ DONE; platform envelope + safety ❌ remaining)*
   - Add `opentelemetry-sdk` + `opentelemetry-exporter-otlp` to registry-api, SDK, declarative-runner, safety-orchestrator. Configure an OTLP exporter from `OBSERVABILITY_*` env. *(SDK/declarative side done via OpenInference; registry-api + safety-orchestrator not yet.)*
   - ✅ **Done:** replaced the langfuse `CallbackHandler` with vendor-neutral OpenInference instrumentation on the langgraph invocation (`otel_run_context`) — solved the langchain-1.x block. `_make_langfuse_handler` remains as dead/fallback code; remove it when the platform envelope also moves to OTEL.
   - ❌ **Remaining:** rewrite `registry-api/tracing.py` + `safety-orchestrator/orchestrator.py` to emit via the OTEL SDK (they still use the langfuse client). `sdk/tracing.py`'s safety-scan spans similarly. Trace-id is already derived to the 32-hex form (`_lf_trace_id`) and propagated via `X-AgentShield-Trace-ID` → OTEL `trace_context` in the agent — reuse that.
2. ✅ **DONE — Read → `ObservabilityBackend` interface.** All langfuse read code (playground/observability/catalog/agent_runs/deployments/eval_runner/composite_workflows routers + cost reads in `cost_backfill.py`/`observability`/`tracing`/`workflow_orchestrator`) now goes through `observability_backend.LangfuseBackend`; endpoints return `NormalizedTrace`. `registry-api:0.2.154`.
3. ✅ **DONE — TraceDrawer → normalized shape.** `TraceDrawer.tsx` + `ObservabilityComparePage.tsx` + `observabilityApi.ts`/`playgroundApi.ts` consume `NormalizedTrace` (`data.trace.spans`/`.scores`); neutral "Trace ↗" label. `studio:0.1.125`.
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

# Observability Architecture — Reference Spec

**Status:** Living reference — the single canonical doc for how AgentShield instruments traces/spans/scores/cost. Any new code path that creates a chat run, executes an agent, reads trace/cost data, or renders it in Studio MUST follow the contracts here.
**Last updated:** 2026-07-12
**Consolidates:** this doc now absorbs the former `langfuse-studio-integration.md` (original roadmap), `todo/cost-tracking.md` (cost research), `todo/observability-provider-abstraction.md` (backend abstraction), and `todo/langfuse-trace-single-click.md` (single-click fix). Their live content lives in §6 (Roadmap) and §7 (Cost). Still separate by genre: `docs/bugs/langfuse-clickhouse-oom.md` (infra incident), `docs/debugging/010-cost-sweep-nameerror-hidden-by-stub.md` (debugging log). See `docs/decisions.md` D9/D11 for why Langfuse storage is Postgres+ClickHouse+MinIO.

## Why this doc exists

Multiple code paths were built to do the same thing (create a chat run, trace it, complete it) and drifted apart — one endpoint got tracing wired in, its near-identical sibling didn't. Reads were scattered as inline Langfuse REST calls across seven routers. Cost columns existed but nothing wrote them. Each was invisible for a long time because nothing enforced a single pattern. This doc is that pattern — read it before writing any code that touches runs, traces, cost, or agent execution, and update it when the pattern changes.

---

## 1. Data model — what a trace actually is

A trace is **not** "one entry per message." It's **one record per conversational turn (run)**, created once and updated once, with **spans** nested inside for every sub-step:

```
Trace (id = run_id, created once when the user sends a message)
  input = {message}                          ← set at creation
  output = {response}                        ← set at completion (same trace id, upsert)
  ├─ span: safety_scan_input                 (orchestrator.py — only when safety-orchestrator enabled)
  ├─ span: <tool call>          TOOL         (OpenInference OTEL, agent pod)
  ├─ span: <LLM generation>     GENERATION   (OpenInference OTEL — carries model + calculatedTotalCost + tokens)
  └─ span: safety_scan_output                (orchestrator.py — only when safety-orchestrator enabled)
  scores:
    llm-judge (0.0–1.0)                       (judge.py)
    user-feedback (+1/-1)                     (playground.py — Langfuse score + local playground_runs.user_feedback col, migration 0057)
```

Key invariant: **`trace_id` always equals the platform's own `run_id`** (or `eval_run_id`, `approval_id`), normalized to the undashed 32-hex OTEL form via `_lf_trace_id`. No separate ID mapping table exists or should ever be introduced.

| Trace type | Name pattern | Created by |
|---|---|---|
| Playground/consumer chat | `{agent_name} · {environment}` | `registry-api/tracing.py: trace_create_run` |
| Eval run | `eval-run` + `eval-item-{N}` spans | `registry-api/tracing.py: trace_eval_run_*` |
| Platform actions (HITL) | `platform.approval.{decision}` | `registry-api/tracing.py: trace_platform_action` |
| Safety scans | (spans) `safety_scan_input`/`safety_scan_output` | `safety-orchestrator/orchestrator.py` |
| Agent LLM/tool spans | `GENERATION`/`TOOL`/`CHAIN`/`AGENT` | agent pod, OpenInference OTEL via `otel_run_context(run_id)` |

---

## 2. The standard integration pattern

Any new endpoint that starts a run **must** implement steps 1–4, in order. Treat it as a code-review checklist — a PR that creates a `PlaygroundRun`/`AgentRun` without steps 1–3 is the exact bug class this doc exists to prevent.

### Step 1 — Create the trace at run start (registry-api)
Call `trace_create_run(run_id, agent_name, user_id, context, input_message, deployment_id, environment)` from `tracing.py` immediately after the run row is flushed. Store the returned `trace_id` on **both** `PlaygroundRun.langfuse_trace_id` and `AgentRun.langfuse_trace_id` if non-null.
- **`user_id` is a human-readable identifier** — `caller.get("preferred_username") or user_sub`. DB `user_id` columns stay the UUID; only the Langfuse-facing value is the username.
- **Always include deployment identity** (`deployment_id` + `environment`) so traces from different instances of the same agent are distinguishable.
- If two endpoints share this logic, **extract a shared helper** (`_create_traced_chat_run`) — don't copy-paste.

### Step 2 — Propagate the trace_id to the agent pod
Pass `run.langfuse_trace_id` as the `X-AgentShield-Trace-ID` header on every proxied `/chat/stream` call (`_proxy_agent_stream(..., trace_id=)`). Without it the agent's spans can't attach to the parent trace.

### Step 3 — Complete the trace at run end
Call `trace_complete_run(run_id=trace_id, status, output_text, judge_score)` once the run finishes. It does a **partial update** (output only) — it must NOT re-send name/tags, or it clobbers the create-time agent identity. The DB-completion helper and the trace-completion call should be the same function (`_complete_chat_run`).

### Step 4 — Emit spans inside agent execution (OpenInference OTEL)
Inside the agent pod, LLM/tool/chain spans are captured by **vendor-neutral OpenInference OTEL instrumentation**, bound to the run's trace via `otel_run_context(run_id)` (`sdk/agentshield_sdk/otel.py`, wired in `declarative-runner/workflow_executor.py`). `GENERATION` spans carry model + `calculatedTotalCost` + token counts — this is what powers cost tracking (§7). Do **not** reach for langfuse's own langchain `CallbackHandler`: it's v2-only and can't instrument the agent's langchain-1.x stack. Safety-scan spans come separately from safety-orchestrator (when enabled).

### Read contract — go through the backend, never call Langfuse REST directly
All reads (trace fetch, cost, observation aggregation, deep-link URLs) go through `observability_backend.get_observability_backend()` — see §5. **No router or service module may call Langfuse's `/api/public/*` or build a Langfuse URL inline.** Endpoints return the provider-neutral `NormalizedTrace`, not a raw Langfuse shape.

### Env var contract (do not introduce a new naming convention)
Every Langfuse client reads exactly `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, and constructs with all three (`public_key=`+`secret_key=`+`host=`). Inventing a service-prefixed variant (`AGENTSHIELD_LANGFUSE_KEY`) is exactly the bug that once silently disabled the SDK tracer platform-wide.

### Frontend contract — never link to Langfuse's own UI as the primary action
Studio has a credential-free way to show trace data: the read-adapter (§5) → `TraceDrawer.tsx`. Any "Trace" button **must** open this inline drawer as the primary action. A secondary neutral "Trace ↗" deep-link (inside `TraceDrawer`) is fine for power users, but it lands on Langfuse's Keycloak SSO chooser, which cannot be made single-click (NextAuth needs a CSRF token from an interactive page load). **Still-open gap:** three components (`EvalResultsPage`, `ChatPane`, `RunsTab`) still default to the raw external link instead of the drawer — see §6.

---

## 3. Langfuse deployment, auth & network topology

Langfuse is an **internal platform component**, not external SaaS — auto-deployed and auto-bootstrapped by Helm. Storage: Postgres (metadata, db `langfuse`), ClickHouse (trace/span events), Redis (queue), MinIO (media). Two **separate auth planes** — conflating them causes most Langfuse confusion.

### 3.1 Two auth planes

| Plane | Who | Mechanism | Credentials | Used for |
|---|---|---|---|---|
| **Service-to-service** | `registry-api`, `safety-orchestrator` | HTTP Basic auth to `/api/public/*` | `pk-lf-…` + `sk-lf-…` (`langfuse-api-keys` Secret) | Writing traces/scores, fetching trace/cost data. **No user login.** |
| **Human / browser** | A person opening Langfuse's web UI | Keycloak SSO (OIDC) | Keycloak JWT session | Only the optional "Trace ↗" deep-link |

**Studio never uses the SSO plane for trace data.** It calls registry-api (read-adapter, §5), which uses the service plane. SSO only matters for the optional deep-link.

### 3.2 Service-plane specifics
- The Basic-auth REST calls are now **centralized in `observability_backend.LangfuseBackend`** (`base64(f"{pk}:{sk}")` → `Authorization: Basic …`). Routers no longer build these inline.
- **Two distinct host vars, don't conflate:**
  - `LANGFUSE_HOST` = in-cluster DNS (`http://agentshield-langfuse-web:3000`) for server-side API calls. Plain HTTP.
  - `LANGFUSE_PUBLIC_URL` = browser-facing (`https://langfuse.127.0.0.1.nip.io:8443`), only to *construct* the deep-link string.
  - `LANGFUSE_PROJECT_ID` = fixed bootstrapped project UUID, needed to build the full trace path.
- **Full-path construction is deliberate** (`{PUBLIC_URL}/project/{project_id}/traces/{id}`, in `LangfuseBackend.build_trace_url`) — Langfuse's `/trace/{id}` short-link redirect loses the path prefix behind the Gateway.

### 3.3 Browser-plane specifics (Keycloak SSO for the native UI)
Config in `values.yaml` under `langfuse.langfuse.additionalEnv`: `AUTH_KEYCLOAK_CLIENT_ID/SECRET/ISSUER`, `AUTH_DISABLE_USERNAME_PASSWORD=true`, `NEXTAUTH_URL` == `global.langfuseUrl` (hardcoded — the subchart can't template globals). Keycloak `langfuse` client created idempotently in `deploy-cpe2e.sh`. **Known ceiling:** even correctly wired, NextAuth's provider-chooser page can't be made single-click — hence §2's inline-drawer mandate.

### 3.4 Network / routing topology
- **Subdomain routing** (`langfuse.127.0.0.1.nip.io`, not a path prefix — Next.js `basePath` can't be set at runtime). Envoy Gateway has dedicated `langfuse-http/https` listeners + an HTTPRoute forwarding `/` → `agentshield-langfuse-web:3000`.
- **`gateway-port-8443` Service** exposes the Gateway's HTTPS on port 8443 in-cluster (Langfuse must reach Keycloak's OIDC at that exact port from inside the cluster).
- **`hostAliases`** pin `agentshield.127.0.0.1.nip.io` → the gateway ClusterIP (else it resolves to the pod's loopback).
- **Bitnami naming-gap alias Services** bridge `agentshield-langfuse-clickhouse`→ClickHouse and `-s3`→MinIO. Must exist before Langfuse boots.

### 3.5 Auto-bootstrap
`LANGFUSE_INIT_*` env vars create the org, project, admin user, and the fixed API keys on first boot — services trace immediately without anyone opening the UI. Org/project IDs are deterministic so `LANGFUSE_PROJECT_ID` can be hardcoded.

### 3.6 How identity flows (four distinct hops)
1. **User → registry-api:** Keycloak JWT; `caller["sub"]` (UUID) is canonical, stored in `*.user_id` (keep UUID).
2. **registry-api → Langfuse trace:** the trace's `user_id` is a *display* value = `preferred_username`, never a FK.
3. **registry-api → agent pod:** only `X-AgentShield-Trace-ID` is forwarded; the pod authenticates to Langfuse with service keys.
4. **User → Langfuse native UI (optional):** Keycloak SSO session, independent of 1–3.

---

## 4. Component responsibility map

| Component | Owns | Reads env | Key files |
|---|---|---|---|
| `registry-api` | Trace creation/completion (chat/eval/platform); **all reads via the backend adapter** | `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST` | `tracing.py`, `observability_backend.py`, `routers/chat.py`, `routers/observability.py`, `routers/playground.py`, `cost_backfill.py` |
| `observability_backend.py` | The read seam — `ObservabilityBackend` interface + `LangfuseBackend` (#1) + `NoneBackend`; get_trace/get_run_cost/spend_by_model/tool_call_stats/build_trace_url/push_score | `LANGFUSE_*`, `OBSERVABILITY_BACKEND` | `observability_backend.py` |
| `safety-orchestrator` | Safety-scan spans (input/output, per-scanner risk) — **only when enabled** | `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST` | `orchestrator.py` |
| `agentshield_sdk` / `declarative-runner` (agent pods) | LLM/tool/chain spans via OpenInference OTEL, bound to the trace via `otel_run_context` | `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST` | `otel.py`, `workflow_executor.py`, `config.py` |
| `deploy-controller` | Injecting Langfuse creds (correct FQN host) into agent pods | own env | `manifest_builder.py` |
| Studio | Rendering the neutral `NormalizedTrace` inline (`TraceDrawer`); never defaulting to Langfuse's UI | n/a (registry-api only) | `TraceDrawer.tsx`, `ObservabilityComparePage.tsx`, `observabilityApi.ts` |
| Langfuse itself | Trace/span/score/cost storage + (secondary) its web UI | n/a | Postgres, ClickHouse (see `docs/bugs/langfuse-clickhouse-oom.md`), MinIO |

---

## 5. The read-adapter seam (provider abstraction)

**Goal:** decouple the platform from any single observability backend — Langfuse today, possibly Datadog/Honeycomb/Phoenix/Tempo/self-hosted OTLP tomorrow — by config, not code.

Two seams with different stories:

**Emit (write path) → OpenTelemetry.** Agent LLM/tool spans emit as vendor-neutral OpenInference OTEL (§2 Step 4), landing in Langfuse via its OTLP endpoint. Swapping = pointing the OTLP exporter elsewhere. **Status: done for agent spans; NOT done for platform-emitted spans** (`tracing.py` trace creation, the feedback score POST, safety-orchestrator) — those still use the Langfuse client. See §6.

**Read (query path) → `ObservabilityBackend` interface.** OTEL standardizes emit, not query — every backend has a different read API/shape/UI. That is confined behind one interface in `services/registry-api/observability_backend.py`:
```
get_trace(trace_id)                 -> NormalizedTrace | None   # provider-neutral spans + scores
get_run_cost(trace_id)              -> RunCost | None           # summed GENERATION cost/tokens
spend_by_model(trace_ids, from)     -> list[CostByModel]
tool_call_stats(trace_ids, from)    -> list[ToolCallStat]
build_trace_url(trace_id)           -> str | None               # provider deep-link, or None
push_score(trace_id, name, value)   -> bool
```
- `LangfuseBackend` is backend #1; `NoneBackend` disables reads + hides the trace UI cleanly. `OBSERVABILITY_BACKEND` env selects (default `langfuse`, `none`).
- `NormalizedTrace` is a stable neutral shape (trace meta + `spans[]` with type/name/timing/io/status + `scores[]`). Studio (`TraceDrawer`, `ObservabilityComparePage`, `observabilityApi`/`playgroundApi`) consumes THIS, never a raw Langfuse shape. Adding a backend = one adapter class.
- **Own your product data:** judge scores, user feedback, and **cost** are persisted to *our* Postgres (`judge_score`, `playground_runs.user_feedback` migration 0057, `agent_runs.cost_usd`/tokens) and read via SQL — so they survive a backend swap. Only the *source* of the cost figure is backend-specific and lives behind the adapter.

**Status: read seam BUILT and live-verified** — `get_trace` on a real trace returns 38 normalized spans + score; every read call-site (observability/playground/catalog/agent_runs/deployments/eval_runner/composite_workflows routers + `cost_backfill`/`tracing`/`workflow_orchestrator`) goes through the adapter; endpoints return `{trace, trace_id, trace_url}` with no `langfuse` key.

---

## 6. Roadmap & open items

Genre-specific docs stay separate (`bugs/`, `debugging/`). Everything forward-looking is here.

**Resolved this effort (for the record):** deployment-pinned chat trace creation, trace_id propagation, SDK tracer enablement + env-var/`public_key` fix, cross-namespace Langfuse host, readable trace user, deployment identity on traces, LLM/tool span capture (OTEL), M2 dashboard panels (feedback + tool-calls), M5 production run columns, M6 score delta, the read-adapter seam, and cost *visibility* (§7). See git history for the detail that used to live in the folded TODO docs.

**Open — emit seam (the remaining Langfuse coupling).** Platform-emitted writes still call the Langfuse client directly: trace creation/completion + `trace_judge_score` (`tracing.py`), the feedback score POST (`playground.py` → `/api/public/scores`), and safety-orchestrator scan spans. Move these to the OTEL SDK / a neutral emit interface so a backend swap carries writes too, not just reads. Highest-risk sub-item: OTEL context is `contextvars`-based and doesn't cross `asyncio.create_task`/detached-SSE boundaries (where `_complete_chat_run` lives) — spike explicit `trace_context` before committing.

**Open — prove the abstraction with a second backend.** The interface + `NoneBackend` exist, but "provider-agnostic" is designed, not demonstrated, until the same chat renders identically through a second backend (e.g. local Jaeger/Grafana Tempo/Arize Phoenix). Build the 2nd adapter when a real need lands.

**Open — cost via LLM proxy + budget enforcement (Portkey).** What shipped is cost *visibility* (§7). It does NOT do what an LLM proxy would: capture cost authoritatively at the source and **reject calls when a team hits a budget (hard cap)**. Langfuse is observe-after-the-fact — it can show overspend, never stop it. The Portkey design (route agent traffic through `OPENAI_BASE_URL=portkey`, provider translation, per-team virtual keys, budget limits) is the pending approach for enforcement/caching. Not started.

**Open — single-click trace viewing.** `EvalResultsPage`, `ChatPane`, `RunsTab` still default to the raw external Langfuse link instead of the inline `TraceDrawer`, sending users through Langfuse's multi-step SSO chooser. The drawer + backing endpoint exist and now consume the neutral shape, so this is a small change: make the drawer the primary click target, demote the external link to the secondary "Trace ↗". (Was `langfuse-trace-single-click.md`.)

**Done — `NormalizedTrace` UI polish** (registry-api 0.2.155 / studio 0.1.126). `TraceDrawer` now renders a nested **waterfall/tree** (spans nested by `parent_id`, duration-proportional bars scaled to the trace window), **per-generation economics** (model + cost + prompt/completion tokens on GENERATION spans, inline + on expand), and **trace scores** as chips + trace total cost. `NormalizedSpan` carries `parent_id`/`model`/`cost_usd`/`prompt_tokens`/`completion_tokens` (mapped in `LangfuseBackend`). Covered by `TraceDrawer.test.tsx`.

**Open — safety-orchestrator disabled in this env.** No safety spans exist here, and PII reaches Langfuse unredacted (the scanner does placeholder redaction). Accepted while deferred; revisit when safety-orchestrator lands.

**Open — Langfuse retention/TTL.** The 90-day trace-retention NFR is unenforced; ClickHouse slowly refills. Track a TTL policy.

**Deferred (Quarter+):** L1 real-time trace streaming, L2 per-agent custom dashboards, L3 anomaly alerting, L5 trace-based regression testing.

---

## 7. Cost tracking

**Shipped — cost visibility (Path A / Langfuse-derived).** Because OpenInference OTEL `GENERATION` spans carry `calculatedTotalCost` + token counts, a background sweep (`cost_backfill.py`, via `backend.get_run_cost`) sums each completed run's cost/tokens and persists them onto `agent_runs.cost_usd`/`prompt_tokens`/`completion_tokens` — idempotent (`cost_usd IS NULL`), 60s interval, 24h window, one path for all run types, no ingestion race. Surfaced as: dashboard **LLM Cost** panel (avg/run, tokens, spend-by-model) + a dedicated **Cost console** (`GET /observability/costs`, `/observability/costs`, DollarSign sidebar) with total/avg/tokens/projected-monthly, daily trend, by-model + by-agent, most-expensive-runs; env-scoped (prod/sandbox). Totals/daily/by-agent/top-runs from persisted SQL; by-model live from the backend. No migration (columns pre-existed).
- **Known limits:** by-model/tool breakdowns cap the backend fetch at 5 pages (500 spans) per view; a run whose trace never carries a GENERATION (e.g. a blocked run) is abandoned after 24h and stays `cost_usd = NULL`.

**NOT shipped — cost via LLM proxy + budget enforcement.** This is what `cost-tracking.md` originally *designed* (Portkey), and it is a distinct, still-open capability (§6): authoritative-at-source capture + hard budget caps + caching/fallback. Cost *visibility* (above) does not provide enforcement. Don't read "cost tracking ✅" as "budgets enforced" — only visibility is done.

---

## 8. Anti-patterns observed (don't repeat these)

1. **Two endpoints doing the same thing, one instrumented and one not.** Extract the tracing logic into a shared helper *before* writing the second endpoint.
2. **Inventing a new env var naming convention.** `AGENTSHIELD_LANGFUSE_KEY` silently no-op'd every agent tracer; grep for existing usage before adding an env var.
3. **A credential-free path built, then not made the default.** `TraceDrawer` exists so Studio never sends users through Langfuse login — yet three components still default to the external link (§6). When building an alternative to a broken UX, make it the default.
4. **Calling the backend's REST API inline from routers.** Every read must go through `observability_backend`; inline `/api/public/*` calls are how reads scattered and coupled the platform to Langfuse.
5. **Silent exception swallowing.** Tracing helpers catch broad `Exception` at `DEBUG`. That hid a trace-creation gap and a `NameError` in the cost sweep (`docs/debugging/010`). Log tracing/read failures at `WARNING`, and never let a coding error (`NameError`/`ImportError`) hide behind a background loop's broad `except`.

---

## 9. Relationship to `docs/spec.md`

`docs/spec.md`'s Component Specifications table lists this as the observability reference. `spec.md` owns the high-level requirements (FR-010, FR-015 cost, FR-018, FR-021–026 — trace capture, LLM-as-Judge, Playground trace panel) and the 90-day retention NFR (unenforced — §6). This doc owns *how* those get implemented: §2 is the checklist every future FR touching runs/traces/cost must pass; §5 is the seam every read must go through.

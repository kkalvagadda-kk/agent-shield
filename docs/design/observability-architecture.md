# Observability Architecture — Reference Spec

**Status:** Living reference — canonical source for how AgentShield instruments traces/spans/scores. Any new code path that creates a chat run, executes an agent, or renders trace data in Studio MUST follow the contract in this doc.
**Last updated:** 2026-07-10
**Related:** `docs/design/langfuse-studio-integration.md` (original roadmap this doc supersedes/consolidates), `docs/design/todo/langfuse-trace-single-click.md` (frontend priority-inversion fix), `docs/bugs/langfuse-clickhouse-oom.md` (infra incident, unrelated to the app-level gaps here), `docs/decisions.md` Decision 9 (database architecture) and Decision 11 (object storage) for why Langfuse's own storage is Postgres+ClickHouse+MinIO.

## Why this doc exists

Multiple code paths were built to do the same thing (create a chat run, trace it, complete it) and drifted apart — one endpoint got Langfuse tracing wired in, its near-identical sibling didn't. Separately, the SDK-level tracer has been silently disabled platform-wide since it was written, because it used a different env var naming convention than every other Langfuse integration point in the codebase. Both bugs were invisible for a long time because nothing enforced a single pattern. This doc is that pattern — read it before writing any code that touches runs, traces, or agent execution, and update it when the pattern changes.

---

## 1. Data model — what a trace actually is

A trace is **not** "one entry per message." It's **one record per conversational turn (run)**, created once and updated once, with **spans** nested inside it for every sub-step:

```
Trace (id = run_id, created once when the user sends a message)
  input = {message}                          ← set at creation
  output = {response}                        ← set at completion (same trace id, upsert)
  ├─ span: safety_scan_input                 (built, orchestrator.py)
  ├─ span: <tool call>                       (NOT built — see §6 Gap 0b)
  ├─ span: <LLM generation>                  (NOT built — see §6 Gap 0b)
  └─ span: safety_scan_output                (built, orchestrator.py)
  scores:
    llm-judge (0.0–1.0)                       (built, judge.py)
    user-feedback (+1/-1)                     (built, playground.py — Langfuse score + local playground_runs.user_feedback col since migration 0057)
```

Key invariant: **`trace_id` always equals the platform's own `run_id`** (or `eval_run_id`, `approval_id`). No separate ID mapping table exists or should ever be introduced — direct lookup by UUID is the whole point.

| Trace type | Name pattern | Created by |
|---|---|---|
| Playground/consumer chat | `agent-run.playground` / `agent-run.production` | `registry-api/tracing.py: trace_create_run` |
| Eval run | `eval-run` + `eval-item-{N}` spans | `registry-api/tracing.py: trace_eval_run_*` |
| Platform actions (HITL) | `platform.approval.{decision}` | `registry-api/tracing.py: trace_platform_action` |
| Safety scans | (spans, not top-level traces) `safety_scan_input`/`safety_scan_output` | `safety-orchestrator/orchestrator.py: _emit_scanner_span` |

---

## 2. The standard integration pattern

Any new endpoint or execution path that starts a run **must** implement all four of these, in this order. Treat this as a checklist for code review — a PR that creates a `PlaygroundRun`/`AgentRun` without doing steps 1–3 is the exact bug class found this session.

### Step 1 — Create the trace at run start (registry-api)
Call `trace_create_run(run_id, agent_name, user_id, context, input_message)` from `services/registry-api/tracing.py` immediately after the run row is flushed (need the DB-generated `run_id` first). Store the returned `trace_id` on **both** `PlaygroundRun.langfuse_trace_id` and `AgentRun.langfuse_trace_id` if non-null.

- **`user_id` passed to the trace is a human-readable identifier, not the raw JWT `sub`.** Use `caller.get("preferred_username") or user_sub` — the JWT already carries this claim, decoded by `auth_middleware.require_user`. The DB `user_id` columns (FK-facing) stay the UUID; only the Langfuse-facing value changes.
- **Always include the deployment identity in metadata/tags** — `metadata={"agent_name": ..., "deployment_id": str(deployment.id), "environment": deployment.environment, "context": context}`. An agent can have multiple concurrent sandbox deployments; without this, traces from different instances of the same agent are indistinguishable in Langfuse.
- If two endpoints need this same logic (e.g. an auto-resolved-deployment chat endpoint and a deployment-pinned one), **extract a shared helper** — do not copy-paste the block. This is precisely how the `start_deployment_chat` gap happened.

### Step 2 — Propagate the trace_id to the agent pod
Pass `run.langfuse_trace_id` as the `X-AgentShield-Trace-ID` header on every proxied call to the agent pod's `/chat/stream` (see `_proxy_agent_stream(..., trace_id=trace_id)` in `chat.py`). Without this, the agent pod has no way to attach its own spans to the parent trace — it would either create an orphan trace or (correctly, per the SDK's design) do nothing.

### Step 3 — Complete the trace at run end
Call `trace_complete_run(run_id=trace_id, status, output_text, judge_score)` once the run finishes. The helper that marks a run `completed` in the DB and the one that completes the Langfuse trace should be the same function, gated on `trace_id` being non-null (see `_complete_chat_run` in `chat.py` — reuse it, don't reimplement).

### Step 4 — Emit spans inside agent execution (SDK / declarative-runner)
Inside the agent pod, use `agentshield_sdk.tracing.tracer` — `start_trace(name, session_id, agent_name, trace_id=<from header>)` attaches to the parent trace created in Step 1 rather than creating a new one. Call `tracer.span(ctx, name, input, output, metadata)` around **every** meaningful sub-step. Today only `safety_scan_input`/`safety_scan_output` do this (`sdk/agentshield_sdk/runner.py`) — wrapping the actual LLM call and each tool call in a span is tracked as a gap, not yet standard (§6, Gap 0b).

### Env var contract (do not introduce a new naming convention)
Every Langfuse client anywhere in this platform reads exactly these three:
```
LANGFUSE_PUBLIC_KEY
LANGFUSE_SECRET_KEY
LANGFUSE_HOST
```
This is what `deploy-controller` injects into agent pods, what Helm injects into `registry-api`/`safety-orchestrator`, and what `declarative-runner`'s own LangGraph callback handler (`_make_langfuse_handler`) already reads correctly. If you're writing a new service or module that needs a Langfuse client, read these three env vars — do not invent a service-prefixed variant (`AGENTSHIELD_LANGFUSE_KEY` was exactly this mistake, and it silently disabled the SDK's tracer platform-wide; see §6 Gap 0a).

When constructing the client, always pass all three:
```python
from langfuse import Langfuse
client = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
    host=os.getenv("LANGFUSE_HOST") or None,
)
```
Omitting `public_key` (as the SDK's `tracing.py` currently does) produces a client that fails or misbehaves even once the env var name is fixed — both mistakes have to be fixed together.

### Frontend contract — never link to Langfuse's own UI as the primary action
Studio has a working, credential-free way to show trace data: `GET /api/v1/playground/traces/{trace_id}` (registry-api, using service `pk-lf`/`sk-lf` creds, no user login) rendered by `TraceDrawer.tsx`. Any "View Trace" / "Trace" button in Studio **must** open this inline drawer as the primary action. A secondary "Open in Langfuse ↗" link (already present inside `TraceDrawer.tsx`) is fine for power users who want the full Langfuse UI — that flow goes through Langfuse's own Keycloak SSO chooser page, which cannot be made single-click (verified: NextAuth's provider sign-in route requires a CSRF token obtained from an interactive page load, not deep-linkable). Defaulting to the raw external link, even as a "fallback," is the anti-pattern that made this the *only* path in three separate components — see `docs/design/todo/langfuse-trace-single-click.md`.

---

## 3. Langfuse deployment, auth & network topology

Langfuse is an **internal platform component**, not an external SaaS — auto-deployed and auto-bootstrapped by the Helm chart. Its own storage backends are Postgres (metadata, shared cluster instance, db `langfuse`), ClickHouse (trace/span events), Redis (queue), and MinIO (media/exports) — see `docs/decisions.md` D9/D11. There are **two completely separate auth planes**; conflating them is the source of most Langfuse confusion.

### 3.1 Two auth planes

| Plane | Who | Mechanism | Credentials | Used for |
|---|---|---|---|---|
| **Service-to-service** | `registry-api`, `safety-orchestrator` (server-side) | HTTP **Basic auth** to Langfuse's `/api/public/*` REST API | project public key `pk-lf-…` + secret key `sk-lf-…` (`langfuse-api-keys` Secret) | Writing traces/scores, fetching trace data to proxy into Studio. **No user login involved.** |
| **Human / browser** | A person opening Langfuse's own web UI | **Keycloak SSO** (OIDC) — the same realm that logs into Studio | JWT session from Keycloak; no separate Langfuse password | Only when a user clicks "Open in Langfuse ↗" for the full native UI |

The key architectural consequence: **Studio never uses the SSO plane for trace data.** It calls registry-api, which uses the *service* plane (Basic auth) and holds the keys server-side. Users see trace data via `TraceDrawer` without ever authenticating to Langfuse. SSO only matters for the optional deep-link into Langfuse's own UI (§2 frontend contract). This is why "you don't have access to this trace / Sign In" is never hit on the normal path — it only appears if someone follows the external link into the SSO plane without a prior Langfuse session.

### 3.2 Service-plane specifics (the path Studio actually uses)

- `registry-api` builds Basic-auth creds inline: `base64(f"{pk}:{sk}")` → `Authorization: Basic …` against `{LANGFUSE_HOST}/api/public/traces/{id}` and `/api/public/scores` (`routers/playground.py` ~L758-762, L811-815, L944-955).
- **Two distinct host vars, do not conflate:**
  - `LANGFUSE_HOST` = **in-cluster** DNS (`http://agentshield-langfuse-web:3000`) — used for server-side API calls. Plain HTTP, no Gateway, no TLS.
  - `LANGFUSE_PUBLIC_URL` = **browser-facing** URL (`https://langfuse.127.0.0.1.nip.io:8443`) — only ever used to *construct* the `trace_url` string handed to the browser for the optional external link. Never called server-side.
  - `LANGFUSE_PROJECT_ID` = the fixed bootstrapped project UUID (`00000000-0000-0000-0001-agentshield01`), needed to build the full trace path.
- **Full-path construction is deliberate.** `trace_url` is built as `{LANGFUSE_PUBLIC_URL}/project/{project_id}/traces/{trace_id}` — the *complete* path, **not** Langfuse's `/trace/{id}` short-link. The short-link issues a redirect that loses the path/host prefix when behind the Envoy Gateway, landing users on a broken URL. Always build the full project-scoped path (comment enforced at `playground.py:749`, `observability.py`).

### 3.3 Browser-plane specifics (Keycloak SSO for the native UI)

Wired by commit `ae78edd`. Config lives in `charts/agentshield/values.yaml` under `langfuse.langfuse.additionalEnv`:
- `AUTH_KEYCLOAK_CLIENT_ID=langfuse`, `AUTH_KEYCLOAK_CLIENT_SECRET=…`, `AUTH_KEYCLOAK_ISSUER=https://agentshield.127.0.0.1.nip.io:8443/realms/agentshield`
- `AUTH_DISABLE_USERNAME_PASSWORD=true` (SSO only — no local Langfuse accounts), `AUTH_KEYCLOAK_ALLOW_ACCOUNT_LINKING=true`
- `NEXTAUTH_URL` must exactly equal `global.langfuseUrl` (`https://langfuse.127.0.0.1.nip.io:8443`) — hardcoded because the Langfuse subchart is a packaged `.tgz` that can't template against globals. A mismatch breaks OAuth callback cookies.
- The Keycloak `langfuse` client (created idempotently in `scripts/deploy-cpe2e.sh` ~L297-307) sets `redirectUris=['https://langfuse.127.0.0.1.nip.io:8443/*']` and matching `webOrigins`.
- **Known ceiling:** even with SSO correctly wired, Langfuse's NextAuth still shows a provider-chooser page ("Sign in → Keycloak" button) that cannot be made truly single-click — its `/api/auth/signin/{provider}` route requires a CSRF token from an interactive page load, so it is not deep-linkable. Verified empirically. This is *why* §2 mandates the inline `TraceDrawer` as the default rather than fighting this flow.

### 3.4 Network / routing topology

- **Subdomain routing, not path-prefix.** Langfuse is served on its own host `langfuse.127.0.0.1.nip.io` (moved off a `/langfuse` path prefix in `30f5a52` because Next.js `basePath` can't be set at runtime). Envoy Gateway has dedicated `langfuse-http`/`langfuse-https` listeners (`charts/agentshield/charts/envoy-gateway/templates/gateway.yaml`) and an `agentshield-langfuse-route` HTTPRoute forwarding `/` → `agentshield-langfuse-web:3000` (`httproute.yaml`). TLS terminates at the Gateway (port 8443 externally via port-forward).
- **`gateway-port-8443` Service** (`charts/agentshield/charts/envoy-gateway/templates/gateway-port-8443-svc.yaml`, in `envoy-gateway-system`): exposes the Gateway's HTTPS on port **8443 inside the cluster** (maps 8443→targetPort 10443). Needed because `AUTH_KEYCLOAK_ISSUER` includes `:8443`, and the Langfuse pod must reach Keycloak's OIDC endpoints at that exact port from *inside* the cluster — the auto-generated Gateway Service only exposes 80/443.
- **`hostAliases` on the Langfuse web pod** (`values.yaml`, `langfuse.langfuse.web.hostAliases`): pins `agentshield.127.0.0.1.nip.io` → the gateway ClusterIP (`10.96.203.50`). Without it, that nip.io hostname resolves to `127.0.0.1` = the pod's own loopback (not the Gateway), so in-cluster OIDC calls to Keycloak would fail.
- **Bitnami naming-gap alias Services** (`infra/langfuse/clickhouse-alias-svc.yaml`): Langfuse derives backend hostnames as `{release}-langfuse-{chart}` but Bitnami subcharts name them `{release}-{chart}` — alias Services bridge `agentshield-langfuse-clickhouse`→ClickHouse and `agentshield-langfuse-s3`→MinIO. Must exist before Langfuse boots.

### 3.5 Auto-bootstrap (zero manual setup)

On first boot, `LANGFUSE_INIT_*` env vars (`values.yaml`) create the org, project, admin user, and — critically — the **fixed public/secret API keys** from the `langfuse-api-keys` Secret. This is what lets platform services trace immediately without anyone opening the Langfuse UI to generate keys. The org/project IDs are deterministic constants so `LANGFUSE_PROJECT_ID` can be hardcoded in the trace-URL builder.

### 3.6 How identity flows (three distinct hops — don't conflate)

1. **User → registry-api:** Keycloak JWT; `require_user` decodes it. `caller["sub"]` (UUID) is the canonical identity, stored in `PlaygroundRun.user_id`/`AgentRun.user_id` (FK-facing — keep as UUID).
2. **registry-api → Langfuse trace:** the trace's `user_id` field is a *display* value. It should be the human-readable `preferred_username` claim, **not** the UUID (currently passes UUID — §6 Gap 2). This is Langfuse-display-only and never feeds back into platform FKs.
3. **registry-api → agent pod:** identity is *not* forwarded; only `X-AgentShield-Trace-ID` (the trace/run UUID) is passed, so the agent's SDK spans attach to the right parent trace. The agent pod authenticates to Langfuse with the *service* keys, not any user identity.
4. **User → Langfuse native UI (optional):** identity comes from the Keycloak SSO session (browser plane), entirely independent of hops 1-3.

---

## 4. Component responsibility map

| Component | Owns | Reads env | Key files |
|---|---|---|---|
| `registry-api` | Trace creation/completion for chat + eval + platform-action runs; proxying trace data to Studio | `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST` | `tracing.py`, `routers/chat.py`, `routers/playground.py`, `routers/observability.py` |
| `safety-orchestrator` | Safety scan spans (input/output, per-scanner risk score) | `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST` (via `config.settings`) | `orchestrator.py` (`_emit_scanner_span`) |
| `agentshield_sdk` (agent pods) | Attaching child spans to the trace created by registry-api, via the `X-AgentShield-Trace-ID` header | *should be* `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST` — currently broken, reads `AGENTSHIELD_LANGFUSE_KEY/HOST` instead (Gap 0a) | `tracing.py`, `runner.py`, `config.py` |
| `declarative-runner` | Same as SDK, plus a second independent LangGraph callback-handler path | Split-brain today — `workflow_executor.py`'s `_make_langfuse_handler` reads the correct vars directly; its imported SDK `tracer` reads the broken ones (Gap 0a) | `config.py`, `workflow_executor.py` |
| `deploy-controller` | Injecting Langfuse credentials into agent pod specs | Sources from its own env (correct names already) | `manifest_builder.py:234-242` |
| Studio | Rendering trace data inline (`TraceDrawer`), never defaulting to Langfuse's own UI | n/a (calls registry-api only) | `TraceDrawer.tsx` and its consumers |
| Langfuse itself | Trace/span/score storage and (secondary) its own web UI | n/a | Postgres (metadata), ClickHouse (events — see `docs/bugs/langfuse-clickhouse-oom.md` for the system-log-table OOM incident), MinIO (media) |

---

## 5. Implementation status matrix

Verified against actual code this session (some checks done live, via `kubectl exec` into a running agent pod — not just static reading).

_Status as of 2026-07-12 (Phase 1 merged to main; Phase 2 + cost tracking in the observability worktree)._

| Capability | Status | Notes |
|---|---|---|
| Root trace created on `/agents/{name}/chat` (auto-resolve deployment) | ✅ Built | `chat.py: start_chat` |
| Root trace created on `/agents/{name}/deployments/{id}/chat` (pinned deployment) | ✅ **Fixed (Phase 1)** | shared `_create_traced_chat_run` helper |
| Trace_id propagated to agent pod via header | ✅ **Fixed (Phase 1)** | `stream_deployment_chat` + `resume_stream_chat` now pass it |
| SDK-level tracer actually enabled on agent pods | ✅ **Fixed (Phase 2)** | env-var names + `public_key`; live-confirmed `tracer._enabled=True` |
| Agent pod can reach Langfuse (cross-namespace) | ✅ **Fixed (Phase 2)** | deploy-controller injects FQN `…-langfuse-web.{ns}:3000` |
| Spans for safety scans (SDK tracer) | ✅ **Working (Phase 2)** | `safety_scan_*` spans now appear (0→1 observation verified) |
| Spans for LLM calls / tool calls | ✅ **Working (OTEL)** | vendor-neutral OpenInference OTEL instrumentation → Langfuse OTLP (NOT langfuse's langchain handler, which is v2-only). GENERATION/AGENT/CHAIN spans captured. `sdk/agentshield_sdk/otel.py` |
| LLM/tool spans unified onto the run's clicked trace | ✅ **Working** | `otel_run_context(run_id)` binds OTEL spans to a run_id-derived trace; registry-api normalizes its trace id to the same undashed 32-hex (`_lf_trace_id`) so envelope + safety + LLM/tool land on one trace |
| Trace "User" field shows readable name | ✅ **Fixed (Phase 1)** | `preferred_username`; DB FK cols keep UUID |
| Trace identifies which deployment/instance produced it | ✅ **Fixed (Phase 1)** | `deployment_id` + `environment` in metadata/tags |
| M1 — Traces list page | ✅ Built | `observability.py` + `ObservabilityTracesPage.tsx` |
| M2 — Latency/score dashboard | ✅ **Built** | Feedback ratio (migration 0057) + **tool-call frequency/latency** panel both shipped. Env-scoped: SEPARATE Production and Sandbox dashboards (routes `/observability/dashboard/{production,sandbox}`), `get_dashboard?environment=` filters every panel. Tool-calls come from Langfuse `type=TOOL` observations filtered to the dashboard's own AgentRun trace-ids (team+env scoped). |
| M3 — Eval results deep-linking | ✅ Built | `EvalResultsPage.tsx` |
| M4 — Safety scan visibility | ✅ Built | `SafetyDetails.tsx` + `TraceDrawer.tsx` |
| M5 — Production chat observability | ✅ **Built (Phase 4)** | `catalog.py` runs endpoint; `AgentRunResponse.judge_score` added; CatalogDetailPage Runs tab shows User + Score columns |
| M6 — Trace comparison | ✅ **Built (Phase 4)** | Span/duration diffing + Judge Score (A→B) + Score Delta card, sourced from the trace's `scores[]` |
| Cost tracking — per-run $ + tokens persisted | ✅ **Built (Path A)** | `cost_backfill.py` background sweep sums each run's Langfuse `GENERATION` cost (`calculatedTotalCost`) + tokens and writes them to `agent_runs.cost_usd`/`prompt_tokens`/`completion_tokens`. Idempotent (`cost_usd IS NULL`), 60s interval, 24h window — one path for all run types, no ingestion race. |
| Cost tracking — dashboard + Cost console | ✅ **Built (Path A)** | Dashboard **LLM Cost** panel (avg/run, tokens, Spend-by-Model bar). Dedicated **Cost console** (`GET /observability/costs`, `/observability/costs`, DollarSign sidebar) — total/avg/tokens/projected-monthly, daily-spend trend, by-model + by-agent, most-expensive-runs. Env-scoped (prod/sandbox). By-model comes live from Langfuse (model lives on the span); totals/daily/by-agent from persisted SQL. |
| Frontend defaults to inline `TraceDrawer`, not external Langfuse link | ❌ Inverted | `docs/design/todo/langfuse-trace-single-click.md` — external link is primary in 3 components today, drawer is dead-code fallback |
| L1 Real-time trace streaming | ❌ Not built | Quarter+ scope, intentionally deferred |
| L2 Custom dashboards per agent | ❌ Not built | Quarter+ scope, intentionally deferred |
| L3 Alerting on trace anomalies | ❌ Not built | Quarter+ scope, intentionally deferred |
| L4 Cost tracking | ✅ **Built (Path A)** — see the two Cost rows above | The original "needs a LangChain callback handler" framing is **obsolete**: the OTEL instrumentation already emits `GENERATION` spans carrying `calculatedTotalCost` + token counts, so cost is read from Langfuse and persisted by the backfill sweep — no callback handler, no Portkey required. Portkey stays optional, only for budget *enforcement* + caching (not needed for visibility). |
| L5 Trace-based regression testing | ❌ Not built | Quarter+ scope, intentionally deferred |

---

## 6. Open gaps and fix plans

Full designs for the actionable (non-Quarter+) gaps live in the working plan for this investigation; summarized here for reference and kept current as each lands:

- **Gap 0a — SDK env var mismatch + missing `public_key`.** Fix: rename `AGENTSHIELD_LANGFUSE_KEY`/`AGENTSHIELD_LANGFUSE_HOST` → `LANGFUSE_PUBLIC_KEY`+`LANGFUSE_SECRET_KEY`+`LANGFUSE_HOST` in `sdk/agentshield_sdk/config.py`, `tracing.py`, `server.py`, and the duplicate definitions in `services/declarative-runner/config.py`/`main.py`. No `deploy-controller`/Helm changes needed — they're already correct.
- **Gap 0b — No LLM/tool-call span instrumentation.** Larger effort: add a LangChain/LangGraph callback handler through the SDK's actual execution loop. Same work as roadmap item L4. Track as one item, not two.
- **Gap 1 — `start_deployment_chat` never creates a trace.** Fix: extract a shared `_create_traced_chat_run(...)` helper in `chat.py` used by both `start_chat` and `start_deployment_chat`; fix `stream_deployment_chat` to propagate `trace_id` the same way `stream_chat` does.
- **Gap 2 — Trace user field shows a UUID.** Fix: pass `preferred_username` (JWT claim) instead of `sub` to `trace_create_run`'s `user_id` param. DB columns unaffected.
- **Gap 3 — No deployment/instance identity on traces.** Fix: add `deployment_id`/`environment` to `trace_create_run`'s metadata/tags. Bundle with Gap 1/2 — same call site.
- **Gap 4 — M2 dashboard missing panels.** ✅ **RESOLVED.** Feedback ratio (`user_feedback` column, migration 0057) + **tool-call frequency/latency** both shipped. The tool-call panel became feasible once OTEL `type=TOOL` spans ingested; team-scoping is solved by filtering observations to the dashboard's own AgentRun trace-ids (no per-trace fetch). The dashboard is now env-scoped (separate Production/Sandbox views, `environment=` filters every panel).
- **Gap 5 — M5 missing columns.** ✅ **RESOLVED (Phase 4):** `AgentRunResponse.judge_score` added; CatalogDetailPage Runs tab shows User (`run_by`/`user_id`) + Score columns.
- **Gap 6 — M6 missing score delta.** ✅ **RESOLVED (Phase 4):** `ObservabilityComparePage` reads each trace's `scores[]` (name~judge) and renders a Judge Score (A→B) + Score Delta card.
- **Cost tracking — nothing wrote `agent_runs.cost_usd`.** ✅ **RESOLVED (Path A):** the columns existed but were never populated, so every cost query returned 0. Because the OTEL work made Langfuse `GENERATION` spans carry `calculatedTotalCost` + token counts, the fix is a small backfill sweep (`cost_backfill.py`) that sums those onto each run — no Portkey, no callback handler. Surfaced on the dashboard (LLM Cost panel) and a dedicated Cost console (`GET /observability/costs`). **Known limits (gap ledger):** by-model/tool breakdowns fetch Langfuse observations with a 5-page (500-span) cap per view — best-effort for very high-volume windows; a run whose trace never carries a GENERATION (e.g. a blocked run) is abandoned after 24h and stays `cost_usd = NULL`.

---

## 7. Anti-patterns observed (don't repeat these)

1. **Two endpoints doing the same thing, one instrumented and one not.** `start_chat`/`start_deployment_chat` in `chat.py` are near-identical; only one calls `trace_create_run`. Whenever a new "pinned" or "variant" version of an existing traced endpoint is added, extract the tracing logic into a shared helper *before* writing the second endpoint, not after a bug report.
2. **Inventing a new env var naming convention instead of reusing the existing one.** The SDK's `AGENTSHIELD_LANGFUSE_KEY` silently no-op'd every agent pod's tracer since it was written. Three other integration points already agreed on `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST` — a fourth, differently-named consumer is always wrong; grep for existing usage before adding a new env var.
3. **A credential-free path built, then not made the default.** `TraceDrawer` + its backing proxy endpoint were built specifically so Studio never has to send users through Langfuse's own login. Three components still default to the raw external link, with the good path as unreachable fallback code. When building an alternative to a broken UX, make it the default, not an opt-in.
4. **Silent exception swallowing that hides all three of the above.** `services/registry-api/tracing.py`'s helpers catch broad `Exception` and log at `DEBUG` (invisible by default). This is why Gap 1 went unnoticed — the trace call failed with zero operational signal. Any new tracing call should at minimum log failures at `WARNING`.

---

## 8. Relationship to `docs/spec.md`

`docs/spec.md`'s Component Specifications table lists detailed design docs; this doc is now one of them (added as a row). `docs/spec.md` still owns the high-level requirements (FR-010, FR-015, FR-018, FR-021–026 — trace capture, cost tracking, LLM-as-Judge, Playground trace panel) and the trace retention NFR (90 days). This doc owns *how those requirements get implemented consistently* — the pattern in §2 is the thing every future FR touching traces should be checked against.

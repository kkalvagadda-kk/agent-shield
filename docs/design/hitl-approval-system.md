# Human-in-the-Loop (HITL) Approval System

**Status:** implementing. HITL is **environment-driven across three sandbox surfaces + production** — see **§8b (the current, authoritative model; §4/§5/§8 below are the earlier two-context framing and are partially superseded by §8b)**. In short: sandbox deployment chat = self-approve right-side panel; Evaluate tab = inline HitlPanel; batch eval = auto-approve (skip HITL); production consumer chat = HITL console. Every approval carries WHO (requester username+team) / WHY (LLM reasoning) / WHAT (tool+args). Proven by Playwright (`hitl-deployment-chat.spec.ts`) + `suite-45` (10/0). **Production HITL is now functional end-to-end** (was structurally broken until 2026-07-11 — see §9). Current tags: registry-api **0.2.139**, studio **0.1.115**, declarative-runner **0.1.33**, deploy-controller **0.1.34**.

This document describes the full HITL approval system — how high-risk tool calls pause for human review, how reviewers approve or deny, and how agents resume execution in both sandbox and production environments.

---

## 1. When HITL Triggers

The OPA Rego policy (`services/registry-api/opa_policy/agentshield.rego`) determines when HITL is required. The decision is purely risk-based:

| Tool Risk Level | OPA Decision | What Happens |
|----------------|--------------|--------------|
| low | `allow=true, require_approval=false` | Tool executes immediately |
| medium | `allow=true, require_approval=false` | Tool executes immediately |
| **high** | **`allow=true, require_approval=true`** | **Pauses for human approval** |
| critical | `allow=false` | Tool denied outright |

Risk is set per-tool in the `tools` table (`risk_level` column) and snapshotted into `agent_versions.tools` at version-creation time. The OPA bundle serves this snapshot via `bundle_generator.py`.

There is no per-agent-class or per-user logic in the OPA *policy* decision — if the tool is high-risk and the identity/tool gates pass, `require_approval=true`. The one exception is **batch/dataset eval** (the `eval-runner` service identity): OPA still decides allow/deny normally, but the SDK **skips the interrupt** (auto-approve) because a batch run is non-interactive and would otherwise hang forever waiting for a human. This is a per-caller runtime bypass, not an OPA change — see §8b Case 3.

---

## 2. Architecture Overview

(See §8b for the authoritative three-surface model; this diagram shows the shared agent-pod chain.)

```
┌─────────────────────────────────────────────────────────────┐
│                        Studio UI                             │
│                                                              │
│  PlaygroundPage (Evaluate)   AgentChatPage (deployment)      │
│  ├─ ChatPane                 ├─ sandbox → Conversation-      │
│  ├─ HitlPanel (self-approve) │   ApprovalPanel (self-approve)│
│  └─ resume reconnect         └─ production → waiting banner  │
│                                                              │
│  CatalogChatPage (prod consumer)   HITLDashboardPage (/hitl) │
│  └─ waiting banner → console       └─ prod queue (PATCH)     │
└────────────────┬────────────────────────────────┬────────────┘
                 │                                │
    ┌────────────▼────────────┐     ┌─────────────▼──────────┐
    │    Registry API          │     │   Registry API          │
    │                          │     │                         │
    │ POST /playground/runs    │     │ POST /agents/{n}/chat   │
    │ GET  .../stream          │     │ GET  .../stream         │
    │ POST .../approvals/decide│     │ PATCH /approvals/{id}   │
    │ GET  .../resume-stream   │     │ GET  .../resume-stream  │
    └────────────┬─────────────┘     └────────────┬────────────┘
                 │                                │
    ┌────────────▼────────────────────────────────▼────────────┐
    │                    Agent Pod                               │
    │                                                           │
    │  Declarative Runner                                       │
    │  ├─ POST /chat/stream     (initial chat)                  │
    │  ├─ POST /resume/{id}/stream (resume after approval)      │
    │  │                                                        │
    │  │  LangGraph ReAct Agent                                 │
    │  │  ├─ Tool call → governed_tool wrapper                  │
    │  │  │  ├─ OPA sidecar check (localhost:8181)              │
    │  │  │  │  └─ returns {allow, require_approval, reason}    │
    │  │  │  ├─ if require_approval → SDK hitl.require_approval │
    │  │  │  │  ├─ POST /api/v1/approvals/ (creates record)    │
    │  │  │  │  └─ interrupt() → graph pauses                   │
    │  │  │  └─ if approved → execute tool (HTTP call)          │
    │  │  └─ streaming.py emits approval_requested SSE event    │
    │  │                                                        │
    │  OPA Sidecar (port 8181)                                  │
    │  └─ Polls bundle from opa-bundle-server every 5-15s       │
    └───────────────────────────────────────────────────────────┘
```

---

## 3. The Approval Record

Created by `SDK hitl.require_approval()` → `POST /api/v1/approvals/`:

```json
{
  "id": "uuid",
  "agent_name": "serper-agent-4",
  "team": "platform",
  "tool_name": "web_search",
  "tool_args": {"query": "weather in Austin"},
  "risk_level": "high",
  "thread_id": "run-uuid",
  "context": "playground" | "sandbox" | "production",
  "reasoning": "I need to search for the current weather in Austin.",
  "status": "pending",
  "expires_at": "2026-07-10T12:30:00Z",
  "created_at": "...",
  "reviewer_id": null,
  "decision_at": null
}
```

- **`context`** is a free-text column (no DB check constraint) with three live values (`playground` | `sandbox` | `production`). The SDK POSTs its best guess, but **`create_approval` overrides it registry-side** via `_derive_context(thread_id → PlaygroundRun)` — see §8b. `context="sandbox"` routes approvals to the deployment-chat self-approve panel and **out of the production queue**.
- **`reasoning`** (migration 0053) is the LLM's stated why (best-effort; may be null) — see §8b "WHO/WHY/WHAT".
- **Provenance is not stored on the approval** — the requester username/team + deployment/environment live on `PlaygroundRun` (migrations 0051/0052, keyed by `thread_id == run.id`) and are joined in at read time (`_load_provenance`, `session_approvals`).

**Status transitions:** `pending` → `approved` | `rejected` | `timed_out`

The DB constraint `ck_approvals_status` allows only these four values. The frontend sends `"denied"` but the backend maps it to `"rejected"` before writing.

---

## 4. Sandbox (Playground) Flow

Sandbox HITL is **self-approval** — the user running the agent can approve or deny their own tool calls inline, without leaving the playground. No ApprovalAuthority check is required.

### 4.1 Sequence

1. **User sends message** → `POST /api/v1/playground/runs` creates a `PlaygroundRun` record with `context='playground'`, `sandbox=True`.

2. **Studio connects SSE** → `GET /api/v1/playground/runs/{run_id}/stream` proxies to agent pod's `POST /chat/stream`. User identity headers (`X-User-Sub`, `X-Agent-Team`) are forwarded so OPA can validate.

3. **Agent calls high-risk tool** → `governed_tool()` wrapper calls OPA sidecar. OPA returns `allow=true, require_approval=true`. SDK calls `hitl.require_approval()`.

4. **Approval record created** → SDK POSTs to `/api/v1/approvals/` with `context="playground"`. The `thread_id` is the `run_id` (used later for resume).

5. **Graph interrupted** → `langgraph.types.interrupt()` checkpoints state and raises `GraphInterrupt`. The streaming layer (`streaming.py`) catches this and emits an `approval_requested` SSE event with `approval_id`, `thread_id`, `tool`, `args`, `risk`.

6. **Studio shows HitlPanel** → `PlaygroundPage.handleApprovalRequested()` stores the request. `HitlPanel` renders approve/deny buttons with tool name, args, and risk badge.

7. **User decides** → `POST /api/v1/playground/approvals/{approval_id}/decide` with `{"decision": "approved"|"denied"}`. Maps `denied→rejected` for DB. Returns `{approval_id, status, thread_id, agent_name, team}`. No authority check — playground is self-service.

8. **Resume stream** → Studio sets `resumeStreamUrl` to `/api/v1/playground/runs/{run_id}/resume-stream`. `ChatPane` connects a new EventSource. The backend reads the decided approval from DB, then proxies `POST /resume/{thread_id}/stream` to the agent pod with the decision payload.

9. **Agent resumes** → `declarative-runner` receives the resume request, calls `workflow_executor.resume_stream()` which invokes `Command(resume=decision_dict)` on the LangGraph graph. If approved, the tool executes and the agent generates a response. If rejected, the tool wrapper returns a denial message and the agent responds with its own knowledge.

### 4.2 Key Files

| File | Role |
|------|------|
| `sdk/agentshield_sdk/graph_builder.py` | `governed_tool()` — OPA check + HITL gate |
| `sdk/agentshield_sdk/hitl.py` | `require_approval()` — creates record + interrupts |
| `sdk/agentshield_sdk/opa_client.py` | `check_tool()` — queries OPA sidecar |
| `sdk/agentshield_sdk/streaming.py` | Emits `approval_requested` SSE after interrupt |
| `services/registry-api/routers/playground.py` | Stream, decide, resume-stream endpoints |
| `services/declarative-runner/main.py` | `/chat/stream`, `/resume/{id}/stream` |
| `studio/src/pages/PlaygroundPage.tsx` | `handleApprovalRequested`, `handleHitlDecided` |
| `studio/src/components/playground/HitlPanel.tsx` | Approve/Deny UI |
| `studio/src/components/playground/ChatPane.tsx` | SSE handling + resume reconnect |

---

## 5. Production Flow

Production HITL is **authority-gated** — only users with an active `ApprovalAuthority` record for the tool can approve. Reviewers use the HITL Dashboard, not the chat page.

### 5.1 ApprovalAuthority

When an agent with high-risk tools is deployed, `_auto_grant_approval_authority()` in `deployments.py` creates `ApprovalAuthority` records for every member of the agent's team. This is an **interim** behavior (every team member can approve the team's high-risk tools) that will be replaced when RBAC lands. It runs on **both** deploy paths — the sandbox deploy endpoint (`deployments.py`) and the **production** deploy (`catalog.py`, added 2026-07-11; the helper takes source-agnostic `(name, risk)` pairs so it serves both ORM tools and `config_snapshot` dicts). Users in `_ADMIN_ROLES` (`platform_admin`, `team_lead`) always have authority regardless. Example record:

```
ApprovalAuthority {
  resource_type: "tool",
  resource_id: "web_search",        -- tool name
  approver_user_id: "kalyan-uuid",  -- team member
  granted_by: "auto:deploy:dep-uuid"
}
```

This means team members can see and decide production approvals without manual setup.

### 5.2 Sequence

1. **Consumer sends message** → `POST /api/v1/agents/{name}/chat` (with `context="production"`). Creates `PlaygroundRun` + `AgentRun`. Returns `{run_id, stream_url}`.

2. **Consumer connects SSE** → `GET /api/v1/agents/{name}/chat/{run_id}/stream`. Backend proxies to agent pod. Same OPA + HITL chain fires.

3. **Approval record created** → Same as sandbox but with `context="production"`.

4. **SSE emits `approval_requested`** → Consumer chat receives the event. Stream ends (the graph is paused). Consumer sees "waiting for approval" with a link to the HITL Dashboard.

5. **Reviewer opens HITL Dashboard** → `/hitl` page. Queries `GET /api/v1/approvals/?status=pending` (scoped by ApprovalAuthority — only tools the reviewer has authority over are visible).

6. **Reviewer decides** → `PATCH /api/v1/approvals/{id}` with `{decision, reviewer_id, version}`. Authority check enforced. Optimistic lock via `version` field prevents double-decide. Backend updates DB, then fires `_resume_and_advance()` async.

7. **Agent resumes** → `_resume_and_advance()` calls `POST /resume/{thread_id}` on the agent pod. If this approval belonged to a composite workflow member, it also advances the parent workflow.

8. **Consumer gets resumed output (automatic)** → while the approval is pending, `CatalogChatPage` polls `GET /api/v1/agents/{name}/chat/{run_id}/approval-status` every 3s; the moment it reports `decided`, it auto-connects `GET /api/v1/agents/{name}/chat/{run_id}/resume-stream` (no manual "Check & Resume"). Backend reads the decided approval, proxies streaming resume to the agent pod, translates SSE events. A "Resume now" button remains as a manual override.

### 5.3 Key Files

| File | Role |
|------|------|
| `services/registry-api/routers/chat.py` | Start chat, stream, resume-stream |
| `services/registry-api/routers/approvals.py` | Create, list, decide (authority-gated), reopen |
| `services/registry-api/routers/deployments.py` | `_auto_grant_approval_authority()` |
| `services/registry-api/approval_timeout_worker.py` | Auto-expire pending approvals |
| `studio/src/pages/HITLDashboardPage.tsx` | Production approval queue UI |
| `studio/src/pages/CatalogChatPage.tsx` | Consumer chat with HITL handling |

---

## 6. Tool Credential Resolution

High-risk tools often call external APIs that need credentials (e.g., Serper.dev API key for `web_search`). The credential pipeline:

1. **AuthConfig** record stores metadata + creates a K8s Secret (`auth_configs.py`, `k8s.py`)
2. **Tool.auth_config_id** links a tool to its credential
3. **Deploy-controller** copies the K8s Secret to the agent's namespace and mounts it as `envFrom` in the pod manifest, via the shared `deploy-controller/tool_secrets.py::resolve_and_copy_tool_secrets` called by **both** reconcilers (`reconciler.py`, `production_reconciler.py`, `manifest_builder.py`)
4. **Runtime** resolves `{{var}}` placeholders in HTTP tool headers from `os.environ` (`node_executors.py`, `tool_executor.py`)

> **Production parity note (2026-07-11):** step 3 originally ran **only in the sandbox
> reconciler**, so production pods shipped without tool credentials and external-API tools
> 401'd. It's now a shared helper both paths call. See
> `docs/debugging/007-production-tool-credentials-missing.md`.

Example: `web_search` header `"X-API-KEY": "{{serper_api_key}}"` is resolved from the `serper_api_key` env var injected by the K8s Secret.

Seed script (`scripts/seed-defaults.sh`) creates a `serper-dev` AuthConfig and links it to `web_search`. Set `SERPER_API_KEY` env var before running seed to inject a real key.

---

## 7. OPA Bundle Data Contract

The bundle (`GET /api/v1/bundle/data.json`) provides all per-request variation to the static Rego policy:

```json
{
  "agents": {
    "system:serviceaccount:agents-platform:agent-serper-agent-4-sa": {
      "tools": [
        {"name": "web_search", "risk": "high"}
      ],
      "team": "platform",
      "agent_class": "user_delegated",
      "expected_sa_subject": "system:serviceaccount:...",
      "sa_namespace": "agents-platform"
    }
  },
  "grants": {
    "platform": [
      {"name": "some_other_tool", "risk": "low"}
    ]
  }
}
```

The Rego evaluates: `identity_present` → `identity_matches` → `tool_in_set` (own tools ∪ grants) → `risk_allows` → `require_approval` if high.

Bundle propagation (updated 2026-07-10): registry-api builds it live → `bundle-sync` sidecar polls → OPA sidecar reloads (poll delays lowered 30/60s → **5/15s**). Critically, `bundle_generator` now includes agents whose deployment is `status IN ('deploying','running')` — **not just `running`** — so a new agent's identity is in the bundle before its pod is even Ready. Measured cold start (pod start → governable): **~22s** (was ~5 min). See `docs/debugging/003-opa-bundle-5min-cold-start.md`. The agent-pod OPA sidecar also has a `/health?bundles` readiness probe so the pod isn't marked Ready (and doesn't receive traffic) until its bundle is loaded.

**Production identities (2026-07-11).** The example above shows a *sandbox* subject
(`agents-platform` namespace). Until 2026-07-11, **production subjects were entirely
absent from the bundle** — the production reconciler never registered the identity, and
even the schema/query only knew the sandbox `deployments` table — so OPA fail-closed
denied every production tool call as `agent_unauthenticated` (HITL never fired). Now
`bundle_generator` **UNIONs** a production leg (`agent_identities.production_deployment_id
→ production_deployments → published_versions.config_snapshot->'tools'`), so a production
subject (`system:serviceaccount:production-<name>-<hash>:agent-<name>-sa`) appears in
`data.agents` exactly like a sandbox one. Registration is shared via
`deploy-controller/identity.py::register_agent_identity` (both reconcilers). See
`docs/debugging/008-production-opa-identity-parity.md` and
`docs/design/sandbox-production-parity-architecture.md`.

---

## 8. Sandbox vs Production Comparison

> **Superseded by §8b.** This two-context table predates the environment-driven
> three-surface model. It's still accurate for the **Evaluate tab (playground)**
> vs **production consumer** endpoints, but the **deployment chat** is a third
> surface with `context="sandbox"` and its own self-approve panel, and **batch
> eval** auto-approves. Read §8b for the current model.

| Aspect | Sandbox (Playground) | Production |
|--------|---------------------|------------|
| Entry point | `POST /playground/runs` | `POST /agents/{name}/chat` |
| Context | `context="playground"` | `context="production"` |
| Who approves | Self (the user running the agent) | Authority-gated (team members) |
| Approve endpoint | `POST /playground/approvals/{id}/decide` | `PATCH /approvals/{id}` |
| Authority check | None | `ApprovalAuthority` record required |
| Optimistic lock | No | Yes (`version` field) |
| Resume mechanism | `GET /playground/runs/{id}/resume-stream` | `GET /agents/{name}/chat/{id}/resume-stream` |
| UI surface | HitlPanel inline in playground | HITL Dashboard page |
| Auto-grant | Not needed | `_auto_grant_approval_authority()` on deploy |
| Workflow re-entry | No | Yes (`_resume_and_advance` handles composite workflows) |

---

## 8b. The three sandbox surfaces + environment-driven HITL (2026-07-10, revised)

HITL behavior is driven by **whether a human is present to approve**, and the
context is decided **registry-side** (the agent pod's env var can't distinguish
callers — the same sandbox pod serves the Evaluate tab, the deployment chat, and
batch eval). `create_approval` (`approvals.py`) derives `context` from the run
(`thread_id → PlaygroundRun`): `run.context=='production'` → `production`;
run on a `sandbox` deployment → `sandbox`; else → `playground`.

| Case | Surface | Caller | Context | HITL behavior |
|------|---------|--------|---------|---------------|
| 1 | Deployment chat (`AgentChatPage` `/agents/{n}/d/{dep}/chat`) | real user | **sandbox** | **self-approve right-side panel** (`ConversationApprovalPanel`) → auto-resume |
| 2 | Evaluate tab (`PlaygroundPage`) | real user | **playground** | inline `HitlPanel` (unchanged) |
| 3 | Dataset/batch eval (`eval-runner`) | `eval-runner` service id | (skipped) | **auto-approve: SDK skips the interrupt** |
| — | Production deployment chat | consumer | **production** | waiting-banner → HITL console (separation of duties) |

**Case 1 — sandbox self-approve panel.** `AgentChatPage` is environment-aware
(fetches the deployment's `environment`). Sandbox → a right-side
`ConversationApprovalPanel` lists the conversation's pending approval(s) (via
`GET /agents/{n}/chat/session/{session_id}/approvals`, requester-scoped) with
inline Approve/Deny (self-approve, no reviewer authority, via the playground
decide endpoint) → auto-resume. Scoped by `session_id` (migration 0052) so it
extends to conversation history when persistence lands. Production deployments
keep the waiting-banner + console + poll.

**Case 3 — batch-eval auto-approve.** A batch eval runs non-interactively, so a
high-risk tool would hang forever on HITL. The registry sets
`x-agentshield-auto-approve: true` **only** on the eval stream path
(`_real_agent_stream`) and **only** when `user_id in _SERVICE_IDENTITIES`
(eval-runner). The runner threads it into `_current_user_context`; the SDK
`governed_tool` skips the interrupt **only** when the flag is set **and** the
caller is in `_AUTO_APPROVE_IDENTITIES` (defense-in-depth — a real user's sub is
never in the set, so interactive runs can never skip HITL). OPA allow/deny is
untouched — only the *pause* is bypassed.

**Provenance.** `create_approval`/`_load_provenance` surface `requested_by` (the
JWT `preferred_username`, not the raw sub), `requested_by_team` (the requester's
own team from `user_team_assignments`), and `deployment_name`/`environment`,
captured on `PlaygroundRun` at chat start (migration 0052).

**WHO / WHY / WHAT on every approval (2026-07-10).** For an informed approve/deny,
each approval surface shows the requester (**WHO**), the LLM's reasoning (**WHY**),
and the tool + args (**WHAT**). The reasoning is captured in the SDK: `governed_tool`
receives the graph state via LangGraph **`InjectedState`** (a `graph_state:
Annotated[dict, InjectedState]` param appended to the tool's `__signature__` **and**
`__annotations__` — excluded from the model-facing `tool_call_schema`, so the LLM
never sees it), extracts the last `AIMessage.content`, and passes it through
`require_approval(reasoning=...)` → the approval record (`approvals.reasoning`,
migration 0053) + the `approval_requested` SSE. A light system-prompt nudge
(appended to `agent.instructions` in `create_graph`) makes the reasoning reliably
present across models. Best-effort — the WHY block is hidden when empty, never
gates approval. Rendered in `ConversationApprovalPanel`, `HitlPanel`,
`HITLDashboardPage`. Tags: registry-api 0.2.134, studio 0.1.110,
declarative-runner 0.1.30.

Key files: `AgentChatPage.tsx`, `components/chat/ConversationApprovalPanel.tsx`,
`HITLDashboardPage.tsx`; `routers/chat.py` (`session_approvals`,
`chat_approval_status`), `routers/approvals.py` (`_derive_context`,
`_load_provenance`), `routers/playground.py` (`_real_agent_stream` auto-approve
header), `declarative-runner/main.py`, `sdk/agentshield_sdk/graph_builder.py`.
Proven by `studio/e2e/hitl-deployment-chat.spec.ts` (real browser sandbox panel)
+ `scripts/e2e/suite-45-hitl-e2e.sh` T-S45-009 (sandbox context+provenance),
T-S45-010 (eval auto-approve + real-user-still-gated).

## 8c. Concurrency & failure mechanics

**One HITL tool per turn.** When a single model turn emits 2+ high-risk tool calls they
would each `interrupt()` in the same LangGraph super-step (shared interrupt id in 0.6.x) —
colliding, hanging the resume, and re-executing already-approved tools. A **provider-agnostic**
`post_model_hook` (`_one_hitl_tool_per_turn`, `graph_builder.py`, wired into
`create_react_agent`) trims a turn to **one** high-risk tool call — **only when 2+ are
high-risk**; low-risk concurrency and single-HITL turns are untouched. Dropped high-risk
calls are re-requested by the ReAct loop next turn (naturally sequential). Provider-agnostic
because `ChatBedrockConverse` (Bedrock) has no parallel-tool-calls control. *Residual:* a
low-risk tool sharing a super-step with the surviving high-risk one still re-executes on that
node's resume (idempotent reads; the trim is intentionally limited to 2+ HITL tools).

**Idempotent approval creation.** `interrupt()` re-runs the whole tool node on resume, so
`require_approval` would re-POST a duplicate. `create_approval` is idempotent per
`(thread_id, tool_name, tool_args)` — a matching pending approval is reused, not duplicated.

**Resume chaining.** A later-turn tool call during a resume re-interrupts; the resume proxies
forward the `approval_requested` event and `AgentChatPage`/`ChatPane`/`CatalogChatPage` handle
the re-interrupt (surface the next approval / re-arm the poll) instead of hanging. Net: one
approval per turn, each tool executes exactly once, chains to `done`.

**Approval-creation failure = fail-closed (declarative-runner 0.1.33).** If
`hitl.require_approval` cannot create the record (any error from `POST /approvals`), it logs
the **full server response body at ERROR** (names the failing field) and returns a `rejected`
decision — the tool is **denied**, never left paused on an un-actionable interrupt. Success is
logged at INFO so the HITL flow is visible in the pod log.

## 9. Current State and Gaps (updated 2026-07-11)

> **Important correction (2026-07-11):** prior versions of this doc listed the whole
> **production** path as "working, verified." That was true only for the *sandbox*
> surfaces. **Production HITL was in fact structurally non-functional** until 2026-07-11:
> production pods were never registered as OPA identities (so OPA fail-closed denied every
> tool — "authentication issue with the search tool", doc 008), shipped without tool
> credentials (doc 007), and had an empty `AGENTSHIELD_AGENT_ID` so the approval POST 422'd
> and no record was ever created (doc 009 — the prompt showed in chat but the Production
> HITL Queue stayed empty). All three are now fixed. The root cause of the cluster was the
> sandbox/production **two-code-path divergence** — see
> `docs/design/sandbox-production-parity-architecture.md`.

### Working
- OPA policy `require_approval=true` for high-risk tools; SDK `governed_tool` → OPA → `hitl.require_approval` → `interrupt`.
- **Three sandbox surfaces** (§8b): deployment-chat self-approve panel, Evaluate-tab HitlPanel, batch-eval auto-approve — proven by suite-45 (10/0) + Playwright.
- **Registry-side context derivation** — sandbox approvals leave the production queue.
- **Provenance** — requester username+team + deployment/environment on every approval.
- **WHO/WHY/WHAT** — LLM reasoning captured via InjectedState + prompt nudge; rendered on all panels.
- Production resume-stream endpoint, CatalogChatPage HITL handling, auto-grant authority, optimistic-lock decide, OPA cold-start fix (~22s).
- **Production HITL end-to-end (NEW 2026-07-11).** A production high-risk tool call is now OPA-governed (identity in the bundle), the Serper credential is present (`envFrom`), the approval record is created (`agent_id` populated), it lands in the Production HITL Queue with `context=production`, and the reviewer console decides it. **Verified with a real tool call** (not a simulation): drove `web_search` on the production pod → `tool_call_start` → `approval_requested` → a real pending `context=production` row + the pod-log line `HITL approval record created …`. suite-7 **T-S7-013** guards the bundle/identity path.
- **Production consumer chat auto-resume (NEW 2026-07-11, studio 0.1.115).** `CatalogChatPage` now polls the console (`approval-status`, 3s) and auto-reconnects the resume stream on decision — the consumer no longer clicks "Check & Resume" (parity with `AgentChatPage`). Guarded by `CatalogChatPage.test.tsx`.
- **Production auto-grant ApprovalAuthority (NEW 2026-07-11, registry-api 0.2.139).** The high-risk-tool auto-grant now runs on the **production** deploy path too (`catalog.py`), not just sandbox — so a production team's members can see/approve without manual setup (interim, until RBAC). Verified: 2 grants created for a new high-risk tool.

### Open gaps / tradeoffs we are currently taking

*(Resolved items — multi-tool-call HITL and SDK approval-failure handling — are folded into
§8c as current behavior, not listed here. Non-gaps — historical-row backfill and the
alembic init-container deploy footgun — were removed. See git history / debugging 004, 009
for the resolved details.)*

1. **Conversation persistence is deferred (intentional) — the main open *product* gap.** `AgentChatPage`/`CatalogChatPage` chat state is in-memory — navigating away loses it. The sandbox panel is already `session_id`-scoped (migration 0052) and `agent_runs` already stores per-turn input/output by session, so the work is mostly: put `session_id` in the URL + hydrate on mount. Until then, the panel shows only the *current* turn's approval.
2. **Reasoning (WHY) is best-effort, by mechanism.** It's extracted from the LLM's `AIMessage.content` (`_extract_reasoning`), nudged by a system prompt — empty for some models / tool-forced calls. The WHY block is hidden when empty and never gates approval. **To make it guaranteed:** inject a required `reasoning` argument into each governed tool's model-facing schema (opposite of how `graph_state` is hidden via `InjectedState`), so the model *cannot* call the tool without a reason; the wrapper reads it and strips it before executing the tool. Contained SDK change + runner rebuild — do when we want a hard guarantee across models.
3. **Sandbox self-approve = no separation of duties (by design).** A developer approves their own test calls; that's the intended sandbox trust model. Real reviewer authority applies only to `context="production"`. `_ADMIN_ROLES` (`platform_admin`, `team_lead`) always have production authority; other team members are auto-granted on deploy (interim, until RBAC).
4. **Risk value single-source-of-truth — latent, fail-safe, fix-when-touching-OPA.** OPA gets its risk from the **pinned** version snapshot (bundle), while the SDK sends its **live-fetched** `fn.risk`; they diverge only if a tool's `risk_level` is edited *after* a version was published. On divergence the `/approvals` POST 422s — but per §8c this is now **logged (full body) and fail-closed (denied)**, not silent or hung, and the trigger is narrow. Note OPA using the pinned risk is *correct* (a published version shouldn't drift); the smell is the SDK reading the live risk. **Disposition: leave as-is; fix opportunistically when next touching the OPA decision path** — have the Rego emit the deciding risk (it already computes `_risk_of`) and send `decision.risk`. Lowest priority of the open items.
5. **Production deploy parity — the recurring root cause (process note, not a bug).** Docs 006–009 were all the same class: the production reconciler is a separate code path from sandbox. Mitigations in place — shared helpers (`tool_secrets.py`, `identity.py`), the two-column FK pattern, and `docs/design/sandbox-production-parity-architecture.md`. **Rule:** any *new* per-pod provisioning/governance step must be wired into both reconcilers. **Out of scope:** workflow-production **member** tool credentials (sandbox has the same limitation); Envoy per-agent HTTPRoute (blocked on deploy-controller RBAC, unused by the chat path).

---

## 10. Timeout and Expiry

Approvals expire after 30 minutes by default (`APPROVAL_TTL_MINUTES` in `hitl.py`). The `approval_timeout_worker.py` background task periodically scans for expired pending approvals and transitions them to `timed_out`. Timed-out approvals can be reopened via `POST /approvals/{id}/reopen` with a fresh expiry window.

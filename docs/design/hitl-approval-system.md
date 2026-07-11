# Human-in-the-Loop (HITL) Approval System

**Status:** implementing. HITL is **environment-driven across three sandbox surfaces + production** — see **§8b (the current, authoritative model; §4/§5/§8 below are the earlier two-context framing and are partially superseded by §8b)**. In short: sandbox deployment chat = self-approve right-side panel; Evaluate tab = inline HitlPanel; batch eval = auto-approve (skip HITL); production consumer chat = HITL console. Every approval carries WHO (requester username+team) / WHY (LLM reasoning) / WHAT (tool+args). Proven by Playwright (`hitl-deployment-chat.spec.ts`) + `suite-45` (10/0). Current tags: registry-api 0.2.134, studio 0.1.110, declarative-runner 0.1.30.

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

When an agent with high-risk tools is deployed, `_auto_grant_approval_authority()` in `deployments.py` creates `ApprovalAuthority` records for every member of the agent's team:

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

8. **Consumer gets resumed output** → Consumer connects to `GET /api/v1/agents/{name}/chat/{run_id}/resume-stream`. Backend reads the decided approval, proxies streaming resume to agent pod, translates SSE events.

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
3. **Deploy-controller** copies the K8s Secret to the agent's namespace and mounts it as `envFrom` in the pod manifest (`reconciler.py`, `manifest_builder.py`)
4. **Runtime** resolves `{{var}}` placeholders in HTTP tool headers from `os.environ` (`node_executors.py`, `tool_executor.py`)

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

## 9. Current State and Gaps (updated 2026-07-10)

### Working (verified end-to-end this session)
- OPA policy `require_approval=true` for high-risk tools; SDK `governed_tool` → OPA → `hitl.require_approval` → `interrupt`.
- **Three sandbox surfaces + production** (§8b): deployment-chat self-approve panel, Evaluate-tab HitlPanel, batch-eval auto-approve, production console — all proven by suite-45 (10/0) + Playwright.
- **Registry-side context derivation** — sandbox approvals leave the production queue.
- **Provenance** — requester username+team + deployment/environment on every approval.
- **WHO/WHY/WHAT** — LLM reasoning captured via InjectedState + prompt nudge; rendered on all three panels.
- Production resume-stream endpoint, CatalogChatPage HITL handling, auto-grant authority, optimistic-lock decide, credential pipeline, OPA cold-start fix (~22s). *(The four "gaps being fixed" listed in prior versions of this doc are all resolved.)*

### Open gaps / tradeoffs we are currently taking
1. **Multi-tool-call HITL — RESOLVED 2026-07-10 (registry-api 0.2.135 / studio 0.1.111 / declarative-runner 0.1.31).** Parallel high-risk tool calls used to collide (two interrupts in one super-step, shared id in 0.6.x), hang the resume, and **re-execute approved tools** (duplicate external calls). Fix is **provider-agnostic** (Bedrock has no parallel-tool-calls control): a `post_model_hook` in `graph_builder.py` (`_one_hitl_tool_per_turn`) trims a turn to **one high-risk tool call, only when 2+ are high-risk** — low-risk concurrency and single-HITL turns are untouched. Dropped calls are re-requested next turn. Plus: `create_approval` is idempotent per `(thread_id, tool, args)` (no phantom duplicate on the node re-run), and the resume path now **chains** — the resume proxies forward `approval_requested` and `AgentChatPage`/`ChatPane` handle a re-interrupt during resume. Verified end-to-end: one approval per turn, each tool executes exactly once, chains to `done`. **Residual (scoped out):** a low-risk tool sharing a super-step with the surviving high-risk one still re-executes on that node's resume (idempotent reads; the trim is intentionally limited to 2+ HITL tools).
2. **Conversation persistence is deferred (intentional).** `AgentChatPage` chat state is in-memory — navigating away loses it. The sandbox panel is already `session_id`-scoped (migration 0052) and `agent_runs` already stores per-turn input/output by session, so the future work is mostly: put `session_id` in the URL + hydrate on mount. Until then, the panel shows only the *current* turn's approval.
3. **Reasoning (WHY) is best-effort.** It's the LLM's AIMessage content; empty for some models / tool-forced calls. Mitigated by a system-prompt nudge; the WHY block is hidden when empty and never gates approval.
4. **Provenance/reasoning only backfill new rows.** Approvals created before migrations 0051–0053 show empty requester/reasoning.
5. **Deploy caveat (not a product gap).** The `alembic-migrate` **init** container must be bumped to the new image alongside the main container — `kubectl set image <dep> registry-api=` alone leaves it stale and it can't locate newer revisions. Use the deploy script/helm (sets both) or bump both containers.
6. **Sandbox self-approve = no separation of duties (by design).** A developer approves their own test calls; that's the intended sandbox trust model. Real reviewer authority applies only to `context="production"`.

---

## 10. Timeout and Expiry

Approvals expire after 30 minutes by default (`APPROVAL_TTL_MINUTES` in `hitl.py`). The `approval_timeout_worker.py` background task periodically scans for expired pending approvals and transitions them to `timed_out`. Timed-out approvals can be reopened via `POST /approvals/{id}/reopen` with a fresh expiry window.

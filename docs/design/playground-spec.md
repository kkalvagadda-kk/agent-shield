# Agent Playground — Spec

**Status**: Draft — Implementation source of truth  
**Date**: 2026-06-27  
**Author**: Karthik + Claude  
**Referenced by**: `docs/spec.md` §Phase 2.5  

---

## Why This Exists

Right now, testing an agent in AgentShield requires a full deploy. You write code, push, wait for CI, deploy, then `curl` an endpoint. If something's wrong, you repeat the whole loop. That's too slow for the tight iteration cycle that makes agents actually good.

OpenAI's platform playground showed what the right answer looks like: send a message, watch the agent think, see every tool call and decision inline, tweak the prompt, run again. The feedback loop collapses from minutes to seconds.

AgentShield needs the same thing — but self-hosted, governance-aware, and wired into the existing safety and tracing stack. The Playground isn't a separate app. It's a tab in Studio.

---

## What We Learned From OpenAI

OpenAI's playground and AgentKit (announced DevDay Oct 2025) pointed at several capabilities that matter:

**Interactive console** — send a message, see SSE-streamed output, no deploy needed. The most basic thing and the most valuable.

**Inline trace view** — after each run, see every LLM call, tool invocation, and decision in a collapsible panel. No jumping to a separate observability tool.

**Visual workflow canvas** — drag-and-drop multi-agent design (their "Agent Builder"). We already have this in Studio. The Playground sits alongside it, not replacing it.

**Eval integration** — runs feed into eval datasets. Good test cases accumulate into regression suites. CI evals then run against the same datasets.

**Fine-tune pipeline** — traces → datasets → fine-tuning. We won't ship this in Phase 2.5 but the data model should support it.

The gap OpenAI's playground leaves: it's SaaS-only, assumes OpenAI models, and has no safety scanning or HITL approval hooks. AgentShield's Playground runs on your cluster, with all governance still in the loop.

---

## Current State in AgentShield

The spec already has some of the pieces:

- `POST /api/v1/workflows/{id}/test` — stub for sandbox mode, not wired to any UI
- Langfuse — captures every production trace, has a datasets API, supports eval scoring
- `promptfoo` in CI (Phase 2, Week 8) — runs eval suites on PRs; results go to Langfuse
- FR-018 — LLM-as-Judge async scoring, listed as P2/future
- AgentVersion has `eval_passed` flag — set by CI, gates deployment

What's missing:
- No way to test an agent without deploying it
- Traces are in Langfuse — you have to leave Studio to see them
- No on-demand eval runner — evals only run in CI
- No dataset curation UI — traces accumulate but you can't curate which ones become test cases
- No version comparison

---

## Playground Scope and Access Rules

### User-Scoped, Private by Default

The Playground is private to the individual user. Runs, traces, datasets, and eval results created in the Playground are visible only to the user who created them — not to other members of their team, not to admins (except for audit purposes). This is explicitly distinct from production deployments, which are team-scoped.

A user can test any agent version they own, regardless of that version's publication status (`private`, `pending_review`, or `published`). The Playground is the pre-publish iteration loop — it exists specifically so developers can test before they publish. Requiring publication before testing would defeat the purpose.

A user cannot test agents they do not own unless the agent has been published and their team has an active grant. In that case, they may run a Playground session against it, but the run is still private to that user.

### Playground Mode Flag

The entire request stack knows when a run originates from the Playground. Registry API sets `X-AgentShield-Playground: true` on every forwarded request. This header propagates through:

```
Studio → Registry API → Safety Orchestrator → Agent Pod → OPA Sidecar
                                                        → Langfuse trace tag
```

Components use this flag to adapt their behavior (see per-component notes below). The flag is set by Registry API from the authenticated user's session — it cannot be injected by a client calling the agent endpoint directly.

### Agent Class Handling in the Playground

**Class B (user_delegated) agents**: work normally. The user's JWT is present and forwarded as `X-User-Id` / `X-Team` headers. OPA sees the user identity.

**Class A (daemon) agents**: the daemon guard (REQ-RT-7) rejects any request carrying a user JWT. In Playground mode, Registry API strips the user JWT before forwarding to the agent pod and replaces it with a playground-scoped machine credential (the playground runner SA token). The agent sees a machine-identity request with `X-AgentShield-Playground: true` and no user context — consistent with how it would be invoked in production.

The Playground UI shows a warning when testing a daemon agent: "Testing in daemon mode — no user identity is forwarded. The agent will run as its own machine identity."

---

## Capabilities

### 1. Interactive Test Console

A chat panel inside Studio. Pick an agent or workflow, pick a version, type a message. Response streams in via SSE — same `text_delta / tool_call_start / tool_call_end / done` protocol as the production `/chat/stream` endpoint.

Works for both declarative runner (visual workflow) agents and SDK agents. The Playground routes to whichever endpoint the version exposes.

Multi-turn conversations are supported via a `session_id` that persists across messages in the same Playground session.

### 2. Sandbox Mode

A toggle on the Playground panel. When on, tool calls return mocked responses — no real HTTP calls, no database writes, no Slack messages sent.

**Grant-bypass in sandbox**: when sandbox is enabled, OPA skips the user grant check (the `user_has_grant` condition in the intersection rule). Agent scope is still enforced — a tool not in the agent's registered tool set is still denied. Only the team-level grant requirement is bypassed. This lets developers test the agent's tool call logic before team grants are formally established, without requiring admin involvement during development.

The `X-AgentShield-Playground: true` + `sandbox: true` combination is what OPA uses to apply the grant-bypass rule. Outside the Playground, sandbox has no effect on OPA behavior.

For declarative agents: Registry API routes to `POST /api/v1/workflows/{id}/test`, which injects a `MockToolExecutor` in place of `HttpToolExecutor` and `PythonToolExecutor`. Safety scanning and OPA still run — only tool side effects are suppressed.

For SDK agents: Registry API passes `X-AgentShield-Sandbox: true` in the request header. The SDK intercepts this and short-circuits all `@tool` functions, returning a deterministic response from the tool's `test_fixture` field (new optional field on the Tool entity).

The trace panel marks every sandboxed tool call with `[sandbox]` so it's unambiguous what was mocked.

### 3. Per-Run Trace Panel

After each run, a panel expands (or is always visible, split-screen) showing the full execution breakdown:

- Safety scan: score, blocked/passed, which scanner flagged it
- LLM call: model, input tokens, output tokens, latency
- Tool calls: name, risk level, args (PII placeholders shown, not real values), result
- OPA decision: allow/deny/require_approval, policy version, grant-bypass indicator if sandbox
- Output scan: score, blocked/passed

The trace is fetched from Langfuse via a Registry API proxy — Studio never calls Langfuse directly. Langfuse credentials stay server-side.

A "View in Langfuse →" link is always present for power users who want the full trace UI.

### 4. LLM-as-Judge Scoring

After a run completes, an async job scores the output (FR-018). The score appears as a badge on the run within ~10 seconds. The judge evaluates: did the agent answer the question, did it hallucinate, did it follow instructions.

Users can thumbs-up or thumbs-down any run, overriding the judge score. Both the judge score and the human override are stored as Langfuse score annotations on the trace.

The judge itself is a separate LLM call — configurable model, configurable rubric per agent. Default rubric: factual accuracy, instruction adherence, safety compliance.

### 5. Dataset Curation

A "Save to Dataset" button on any Playground run. It appends the input/output pair to a named Langfuse dataset.

Datasets accumulate over time and become the source of truth for CI eval regression suites. If you run CI evals against a dataset and a new version breaks a previously-passing case, the PR is blocked.

The dataset management UI is minimal for now: create, list, see item count. Editing individual dataset items happens in Langfuse.

### 6. On-Demand Eval Runner

A "Run Evals" panel at the bottom of the Playground. Pick a suite (a promptfoo config, or a Langfuse dataset), pick a version, hit run.

Registry API spawns a K8s Job in the `agentshield-playground` namespace. The Job runs promptfoo against that agent version's endpoint. The job writes results to Postgres. Studio polls every 5 seconds until complete (usually under 60s for small suites).

The eval Job presents the triggering user's identity when calling agent endpoints — Registry API mints a short-lived scoped token from the user's session and injects it as `EVAL_USER_TOKEN` in the Job's env. For Class B agents, this satisfies the user JWT requirement. For Class A agents, the token is stripped and the playground runner SA is used instead (same daemon handling as interactive runs).

Results show pass/fail per assertion. If a baseline version is specified, a diff column shows whether each case regressed or improved.

### 7. Playground HITL Panel

When a high-risk tool fires during a Playground run, OPA returns `require_approval: true` — the same as production. The difference is what happens next.

The approval is created with `context='playground'`. No Slack notification is sent. Instead, the Playground chat panel shows a banner: **"Waiting for your approval"** with a "Review →" link. The SSE stream pauses at the `approval_requested` event.

Clicking "Review →" opens a side panel within Studio showing the user's pending playground approvals — only requests they own, only context=playground. The panel shows the tool name, risk level, args (PII-redacted, with an expand option), and which run triggered it. The user clicks Approve or Deny, the agent resumes or terminates, and the SSE stream continues.

The production approval dashboard (in the ops area) never shows playground approvals. The Playground HITL panel never shows production approvals. They are completely separate views over the same `approvals` table, filtered by `context`.

The approving user in a Playground HITL is always the asset owner (the user running the test). Self-approval is explicit by design — in the Playground, the developer is acting as both tester and reviewer. There is no approval_authority lookup for playground approvals.

### 8. Side-by-Side Version Comparison

A "Compare versions" mode in the Playground. Pick two versions of the same agent. Send a message once — Registry API fans it out to both versions in parallel and returns two `run_id`s. Studio renders two chat panels and two trace panels side by side.

The comparison view shows: outputs, trace depth (number of LLM + tool calls), total tokens, latency, and cost. Useful for validating that a prompt change made things better without introducing regressions.

Note: HITL behavior in comparison mode (if a high-risk tool fires in one version) is still an open question — see Open Questions.

---

## UI Layout

The Playground is a top-level tab in Studio alongside Canvas, Tools, and Workflows.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Studio   [Canvas]  [Playground]  [Tools]  [Workflows]                │
├────────────────────────────────┬─────────────────────────────────────┤
│  CHAT PANEL                    │  TRACE PANEL                        │
│                                │                                      │
│  Agent  [research-assistant ▼] │  Run #4 · 1.2s · 340 tok · $0.001  │
│  Version [v3 ▼]  [⚙ sandbox]  │  context: playground                │
│                                │                                      │
│  ┌─────────────────────────┐   │  ▶ safety_scan (input)   ✓  18ms   │
│  │ User                    │   │    score: 0.02 · pass               │
│  │ what's the weather      │   │                                      │
│  │ in NYC?                 │   │  ▶ llm_call   claude-sonnet  0.8s  │
│  │                         │   │    in: 280tok  out: 60tok           │
│  │ Agent                   │   │                                      │
│  │ It's 72°F and sunny     │   │  ▶ tool_call  weather-lookup        │
│  │ in NYC today.           │   │    risk: low · [sandbox] ✓  12ms   │
│  └─────────────────────────┘   │    args: {latitude: 40.7, ...}      │
│                                │    grant-bypass: true               │
│  [Send ▶]  [New session]       │                                      │
│                                │  ▶ opa_decision   allow             │
│  [💾 Save to Dataset]          │    policy: v12 · sandbox            │
│  [⚖ Compare versions]          │                                      │
│                                │  ▶ safety_scan (output)  ✓  11ms   │
│                                │    score: 0.01 · pass               │
│                                │                                      │
│                                │  Judge score: 0.87  [👍] [👎]       │
│                                │  [View in Langfuse ↗]               │
├────────────────────────────────┴─────────────────────────────────────┤
│  EVAL RUNNER                                                          │
│  Suite [ci-suite ▼]  vs baseline [v2 ▼]  [▶ Run Evals]             │
│                                                                       │
│  ████████░░  8/10 passed  ·  lookup_order ✓  ·  refund_flow ✗      │
│  refund_flow: expected "approved" · got "I cannot process refunds"   │
└──────────────────────────────────────────────────────────────────────┘
```

In comparison mode, the layout splits the chat and trace panels vertically, showing version A on the left and version B on the right.

---

## Architecture

### Playground Namespace

The Playground runs in a dedicated `agentshield-playground` namespace, separate from the main `agentshield` namespace where production agents run. This namespace is:

- Labeled for Istio Ambient Mesh (`istio.io/dataplane-mode: ambient`)
- Subject to resource quotas per user
- RBAC-isolated per user: each user gets a dedicated ServiceAccount `playground-runner-{username}-sa` with a Role that scopes their visibility to their own Jobs and Pods

Each user's playground SA is created on first Playground use and persists for the user's lifetime. This SA is the machine identity for eval Jobs (mTLS/ztunnel layer). User identity for OPA is separate — injected by Registry API as the user's JWT claims.

**Eval runner namespace isolation — options considered:**

| Option | Approach | Chosen |
|--------|----------|--------|
| **A: Per-user namespace** | `agentshield-playground-{username}` per user. One `playground-runner-sa` per namespace. True kernel-level isolation; RBAC is namespace-scoped. Cons: 100 users = 100 namespaces, linear admin overhead, each needs Istio ambient labeling and monitoring. | No |
| **B: Shared namespace, per-user SA** | One `agentshield-playground` namespace. Per-user SA (`playground-runner-{username}-sa`). Jobs labeled `owner={username}`; RBAC scopes each SA to its own Jobs and Pods. Stable auditable identity, one namespace to operate. | **Yes** |
| **C: Shared namespace, ephemeral per-run SA** | Create a fresh SA + Role + RoleBinding per eval run, delete after Job completes. Maximum per-run isolation. Cons: high K8s API churn per run; OPA bundle can't pre-register ephemeral SAs, so machine identity check needs a different model (namespace-prefix trust). | No |

### How a Playground run flows

```
Studio (user: alice)
  │
  │ POST /api/v1/playground/run
  │ { target_id, version, message, sandbox, session_id }
  │ Authorization: Bearer <alice's JWT>
  ▼
Registry API
  │ Creates playground_runs row
  │   { run_id, user_id: alice, team: team-a, sandbox: true, context: "playground" }
  │
  │ Sets forwarding headers:
  │   X-AgentShield-Playground: true
  │   X-AgentShield-Sandbox: true     (if sandbox=true)
  │   X-User-Id: alice-uuid
  │   X-Team: team-a
  │
  │ Forwards to Safety Orchestrator → Agent Pod
  ▼
Safety Orchestrator
  │ Scans input (unchanged)
  │ Passes X-AgentShield-Playground: true downstream
  ▼
Agent Pod
  │ Reads X-AgentShield-Playground: true from headers
  │ Class A agents: strip user headers, proceed as daemon
  │ Class B agents: user identity present, proceed normally
  │
  │ For each tool call:
  │   OPA input includes { playground: true, sandbox: true }
  │   OPA applies grant-bypass if sandbox=true
  │
  │ Tool execution:
  │   sandbox=false → real tool calls (mTLS via ztunnel)
  │   sandbox=true  → MockToolExecutor returns test_fixture
  ▼
Langfuse trace
  │ Tagged: { context: "playground", sandbox: true, user_id: alice }
  │ (separate from production traces in dashboards)
  ▼
Registry API (on done event)
  │ Records langfuse_trace_id in playground_runs
  │ Spawns async LLM-as-Judge job
  ▼
Studio ← SSE stream throughout
```

### Eval runner flow

```
Studio (user: alice)
  │ POST /api/v1/playground/evals/run
  │ { agent_name, version, suite, baseline_version }
  ▼
Registry API
  │ Creates eval_runs row
  │ Mints short-lived scoped token from alice's session
  │   (TTL = job activeDeadlineSeconds, max 30 min)
  │
  │ Spawns K8s Job in agentshield-playground namespace:
  │   serviceAccountName: playground-runner-alice-sa
  │   env:
  │     EVAL_USER_TOKEN: <scoped token>   ← alice's identity for agent calls
  │     EVAL_USER_ID:    alice-uuid
  │     EVAL_USER_TEAM:  team-a
  │     PLAYGROUND:      true
  ▼
K8s Job (runs in agentshield-playground namespace)
  │ Runs promptfoo against agent version endpoint
  │ Each request carries:
  │   Authorization: Bearer <EVAL_USER_TOKEN>   ← Class B identity
  │   X-AgentShield-Playground: true
  │   (for Class A: token stripped, SA token used instead)
  │
  │ Job machine identity (mTLS): playground-runner-alice-sa
  │ User identity (OPA): alice-uuid / team-a
  ▼
Results written to eval_runs.assertions → Studio polls every 5s
```

### What changes per component in Playground mode

| Component | Playground behavior | How it knows |
|-----------|--------------------|-|
| Registry API | Sets `X-AgentShield-Playground: true`; strips user JWT for Class A | Initiates the run |
| Safety Orchestrator | No change — scans input/output as normal | Passes header downstream |
| Agent Pod (Class A) | Strips user headers, runs as daemon | `X-AgentShield-Playground: true` |
| Agent Pod (Class B) | Runs normally with user identity | No change needed |
| OPA Sidecar | Applies grant-bypass if `sandbox=true` | `playground + sandbox` in input |
| Tool Executor | Returns `test_fixture` instead of real call | `X-AgentShield-Sandbox: true` |
| Langfuse | Tags trace with `context=playground` | Registry API sets tag on trace |
| HITL | No Slack notification; approval created with `context='playground'`; user self-approves via Playground HITL panel | `approvals.context = 'playground'` |

### What doesn't change

Safety Orchestrator still scans every input and output. OPA still evaluates agent scope (tool-in-registered-set check, agent identity check) — only the user grant check is bypassed in sandbox mode. The Playground is a test surface, not a safety bypass.

---

## API Contracts

### Run a test message

```
POST /api/v1/playground/run
{
  "target_type": "workflow" | "agent",
  "target_id":   "<workflow_id | agent_name>",
  "version":     "<version_tag>",     // optional, defaults to latest owned version
  "message":     "<user message>",
  "sandbox":     true | false,
  "session_id":  "<uuid>"             // optional, for multi-turn
}

Authorization: Bearer <user JWT>      // required; scopes the run to this user

→ { run_id } + SSE stream:
  text_delta
  tool_call_start  { tool_name, sandbox: bool, grant_bypass: bool }
  tool_call_end    { result }
  opa_decision     { decision, reason, grant_bypass: bool }
  approval_requested  { approval_id, tool_name, risk, args_redacted }
                      // stream pauses here; user reviews in Playground HITL panel
  done
```

Asset visibility check: Registry API verifies `target_id` is owned by the requesting user OR published + granted to the user's team. Returns 403 if neither.

### Get trace for a run

```
GET /api/v1/playground/runs/{run_id}/trace

Authorization: Bearer <user JWT>      // must be the same user who created the run

→ {
    run_id, agent_name, version,
    context: "playground",
    sandbox: bool,
    user_id: "<sub>",
    duration_ms, total_tokens, cost_usd,
    spans: [
      {
        type: "llm_call" | "tool_call" | "safety_scan" | "opa_decision" | "handoff",
        name, started_at, duration_ms, input, output,
        metadata: {
          // tool_call:
          sandbox: bool, grant_bypass: bool,
          // opa_decision:
          decision, reason, policy_version, grant_bypass: bool,
          // safety_scan:
          score, passed, scanner
        }
      }
    ],
    judge_score:     float | null,
    judge_reasoning: str | null
  }
```

### Save to dataset

```
POST /api/v1/playground/runs/{run_id}/save-to-dataset
{ "dataset_name": "<name>" }
→ { dataset_id, item_id }
```

Datasets are private to the user who created them. Dataset names are scoped per user — two users can have datasets with the same name.

### User feedback

```
POST /api/v1/playground/runs/{run_id}/feedback
{ "rating": "positive" | "negative", "comment": "<optional>" }
→ stored as Langfuse score annotation; overrides judge_score in UI
```

### On-demand eval run

```
POST /api/v1/playground/evals/run
{
  "agent_name":       "<name>",
  "version":          "<version_tag>",
  "suite":            "<promptfoo config | dataset_name>",
  "baseline_version": "<version_tag>"   // optional, enables diff
}

Authorization: Bearer <user JWT>

→ { eval_run_id }

GET /api/v1/playground/evals/{eval_run_id}
→ {
    status: "running" | "complete" | "failed",
    summary: { total, passed, failed, pass_rate },
    assertions: [
      {
        test_case, prompt, expected, actual, passed, score,
        baseline_actual, baseline_passed   // only if baseline_version set
      }
    ]
  }
```

### Version comparison

```
POST /api/v1/playground/compare
{
  "agent_name": "<name>",
  "version_a":  "<tag>",
  "version_b":  "<tag>",
  "message":    "<user message>",
  "sandbox":    bool
}
→ { run_id_a, run_id_b }
// Fetch traces independently via GET /runs/{id}/trace
```

---

## Data Model

**`playground_runs`** (updated)
```
run_id            uuid PK
target_type       text        -- "workflow" | "agent"
target_id         text        -- workflow_id or agent_name
version           text
message           text
sandbox           bool
session_id        uuid        -- nullable; for multi-turn
user_id           text        -- NOT NULL; sub claim of the triggering user
team              text        -- user's team at time of run
context           text        -- always "playground"
langfuse_trace_id text        -- set after run completes
judge_score       float       -- nullable; set async
judge_reasoning   text        -- nullable
created_at        timestamptz
```

**`playground_feedback`**
```
id          uuid PK
run_id      uuid FK → playground_runs
rating      text        -- "positive" | "negative"
comment     text        -- nullable
reviewer    text        -- user_id (must match run.user_id)
created_at  timestamptz
```

**`playground_datasets`**
```
dataset_id          uuid PK
dataset_name        text
owner_user_id       text        -- user-scoped; not team-scoped
team                text
langfuse_dataset_id text
item_count          int
created_at          timestamptz
last_updated        timestamptz
UNIQUE (dataset_name, owner_user_id)
```

**`eval_runs`**
```
eval_run_id       uuid PK
agent_name        text
version           text
suite             text
baseline_version  text        -- nullable
status            text        -- "running" | "complete" | "failed"
user_id           text        -- user who triggered the eval
team              text
context           text        -- always "playground"
summary           jsonb       -- { total, passed, failed, pass_rate }
assertions        jsonb       -- array of assertion results
k8s_job_name      text
k8s_namespace     text        -- "agentshield-playground"
created_at        timestamptz
completed_at      timestamptz -- nullable
```

---

## Key Decisions

**Playground as Studio tab, not separate app** — shared auth, shared active agent/workflow context, no new Kubernetes deployment for the frontend. The mental model is: Canvas is where you build, Playground is where you test, both are Studio.

**Playground is user-scoped, not team-scoped** — runs, datasets, and eval results are private to the individual user. Even team members cannot see each other's Playground activity. This matches the intent of the Playground as a personal scratch space for pre-publish iteration.

**Any owned version is testable, regardless of publication status** — a developer can test a `private` or `pending_review` agent version in the Playground without publishing it first. The Playground is the iteration loop that precedes publication, not a post-publication feature.

**Grant-bypass in sandbox mode** — sandbox mode skips the user team grant check in OPA. Agent scope is still enforced (tool must be in the agent's registered set). This lets developers test tool call logic before team grants exist, without admin involvement during development.

**Class A (daemon) agents: strip user JWT** — Registry API detects daemon agents and strips the user JWT before forwarding. The playground runner SA becomes the identity. The Playground UI shows a "daemon mode" indicator.

**Eval Jobs use the triggering user's identity** — Registry API mints a short-lived scoped token (TTL ≤ 30 min) from the user's session and injects it into the eval Job. Class B agents see the real user's identity. Class A agents see the playground runner SA (no user JWT).

**Playground namespace: `agentshield-playground`** — separate namespace from production. Per-user ServiceAccounts (`playground-runner-{username}-sa`). Istio Ambient applies the same mTLS as production.

**Registry API proxies Langfuse** — Studio never calls Langfuse directly. Langfuse credentials stay server-side. The proxy reshapes traces into the typed Playground format.

**Playground runs tagged separately in Langfuse** — all Playground traces carry `{ context: "playground", sandbox: bool }` tags. Production cost dashboards filter these out. A separate Playground spend view is available. This prevents test traffic from inflating production cost metrics.

**Sandbox suppresses side effects, not safety** — Safety Orchestrator still scans inputs and outputs. OPA still checks agent scope. High-risk tools still trigger HITL (Playground self-approval panel, not production queue). Only tool execution is mocked — governance is not bypassed.

**Playground HITL is self-approved, production HITL is role-gated** — In production, HITL approval rights are granted per-agent/tool/skill via the `approval_authority` table; reviewers see only requests within their authority scope. In the Playground, the asset owner is the implicit approver for all their own runs. No Slack notification fires for Playground approvals. The two queues are completely separate: `GET /api/v1/approvals` returns production pending approvals (filtered to the reviewer's authority); `GET /api/v1/playground/approvals` returns the current user's playground pending approvals. Self-approval is explicitly not permitted in production.

**Eval runner uses K8s Jobs** — isolated, parallelizable, same environment as CI. No long-running eval daemon. Jobs are in `agentshield-playground` namespace with a 30-minute `activeDeadlineSeconds`. Jobs clean up after completion.

**Datasets are user-scoped in local metadata, Langfuse-backed** — Langfuse owns the actual data. `playground_datasets` mirrors name + item count for listing. Dataset names are unique per user (two users can have the same dataset name).

**Judge scoring is async** — synchronous judge scoring would add 2-5s to every run. Async keeps the chat loop fast; the score badge appears a few seconds after the run completes.

---

## What's Out of Scope (for now)

- Fine-tuning pipeline (dataset → training job) — data model supports it, Phase 3+
- Persistent multi-session history browser
- Playground for non-deployed agents (pure draft, no container) — needs In-Browser SDK Editor first
- Eval suite editor in Studio — write promptfoo configs in code; Playground just runs them
- Replay a production trace — deferred; needs careful PII handling
- Comparison mode HITL — if version A triggers HITL during a comparison run, behavior for version B is still unresolved (see Open Questions)

---

## Open Questions

1. **Judge model** — which model runs LLM-as-Judge? Default leaning toward `claude-haiku-4-5` (fast, cheap).

2. **Sandbox fixture authoring** — tools need a `test_fixture` field for SDK sandbox mode. Who writes it? The tool owner? Auto-generated from the schema?

3. **Eval suite format** — promptfoo configs today live in git. For on-demand runs from Studio, should the suite be referenced by a path in the container, registered in Registry API, or always built from a Langfuse dataset?

4. **Comparison mode + HITL** — if version A triggers HITL during a comparison run (user needs to self-approve in the Playground panel), does version B pause to wait, run to completion, or terminate? Options: (a) both pause until the single approval is resolved, applying the decision to both; (b) version B continues independently and may diverge; (c) comparison mode simply disables HITL (requires running against non-high-risk tools only). Unresolved.

5. **Eval Job token expiry** — the scoped token injected into eval Jobs has TTL ≤ 30 min. If an eval suite takes longer, the Job's agent calls start returning 401. Mitigation options: (a) hard cap eval Jobs at 30 min, (b) token refresh mechanism in the Job, (c) pre-mint a longer-lived offline token.

---

## Revisit Later

- Whether the trace panel should be always-visible (split screen) or collapsible
- Whether on-demand evals should block the Playground UI or run in background with a notification
- Replay production traces in the Playground (high value, needs PII handling design)
- Dataset versioning — currently append-only

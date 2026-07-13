# Playground Experience

The playground lets you talk to an agent live, watch its reasoning in a trace panel, intercept HITL approvals, and save good/bad runs to a dataset — all in an isolated sandbox that never touches production state.

## How to get there

`Sidebar → Evaluate → Eval Runs`

The route is `/playground`. The URL doesn't change between agents — the agent is selected in the left panel.

## Layout

Three panels side by side, plus a full-screen HITL overlay when an approval fires:

```
┌──────────────────┬─────────────────────────────────┬──────────────┐
│  Left (240px)    │  Center (flex)                  │  Right       │
│  Agent selector  │  Chat pane                      │  Trace panel │
│  Sandbox badge   │  Input bar                      │  (collapsible│
│  → Datasets link │  Feedback + Trace link (post-run)│   chevron)  │
└──────────────────┴─────────────────────────────────┴──────────────┘
                         ↓ (on approval_requested)
               ┌──────────────────────────────┐
               │  HITL overlay (HitlPanel)    │
               │  Tool name, risk, args       │
               │  Approve / Deny buttons      │
               └──────────────────────────────┘
```

## Step-by-step user flow

**1. Select an agent**

The `VersionSelector` calls `GET /api/v1/agents?limit=100&status=active` and populates a dropdown. Selecting an agent sets `selectedAgent` state, clears the trace panel, and shows the sandbox mode badge in the left rail.

**2. Send a message**

Type in the input bar and press Enter or click Send. The frontend:
- POSTs to `POST /api/v1/playground/runs` with `agent_name` and `input_message`
- Gets back `{ run_id, stream_url }`
- Opens an `EventSource` pointed at `stream_url`

The backend creates a `PlaygroundRun` row (status=`running`, sandbox=`true`, context=`playground`), creates a Langfuse trace, and returns immediately.

**3. Stream arrives**

The stream endpoint (`GET /api/v1/playground/runs/{id}/stream`) looks up the agent's running `Deployment` (by joining `agents` → `deployments` where `status=running`), derives the in-cluster service URL:

```
http://{k8s_deployment_name}.{k8s_namespace}.svc.cluster.local:8080
```

It then opens an httpx async stream to `POST {agent_svc_url}/chat/stream` and proxies events back to the browser.

**Protocol conversion:** the agent pod emits named SSE:
```
event: text_delta
id: <uuid>
data: {"content": "Hello! I can help..."}
```

`EventSource.onmessage` only fires for unnamed events, so the registry-api converts each named event into an unnamed event with the type embedded in JSON:
```
data: {"event": "text_delta", "content": "Hello! I can help..."}
```

Field remapping also happens here: `tool` → `tool_name`, `risk` → `risk_level`.

**4. SSE events and their UI effects**

| Event | Payload fields | UI effect |
|---|---|---|
| `text_delta` | `content` (string) | Appended to the last assistant message bubble in real time |
| `tool_call_start` | `tool_name` | Blue chip appears in the message bubble: `Calling <tool>…` |
| `tool_call_end` | `tool_name`, `result` | Blue chip replaced with green chip: `<tool>: <result[:40]>` |
| `approval_requested` | `approval_id`, `tool_name`, `risk_level`, `args` | HITL overlay appears (see below); trace panel logs the event |
| `error` | `message` | Logged in trace panel; stream still ends with `done` |
| `done` | — | `EventSource` closes, `running` spinner goes away, feedback bar appears |

**5. No deployment → error event**

If the agent has no `status=running` deployment, the stream immediately emits:
```
data: {"event": "error", "message": "No running deployment found for agent \"<name>\". Deploy the agent first."}
data: {"event": "done"}
```

The user sees an error entry in the trace panel and the spinner stops. Nothing hangs.

**6. HITL overlay**

When `approval_requested` fires, `HitlPanel` renders over everything with the tool name, risk level, and (redacted) args. `HitlPanel` mounts the shared `components/approvals/ApprovalCard.tsx` for the approval body (WS-1 M1) — the same presentational card the sandbox chat panel (`ConversationApprovalPanel`) and the global Approvals Inbox (`ApprovalsInboxPage`) use, so a new approval field is added in one place. The user clicks Approve or Deny. The frontend POSTs to `POST /api/v1/playground/approvals/{id}/decide` — no authority check in playground context (sandbox self-approval). The overlay closes and streaming resumes.

**7. After the run**

Once `done` arrives:
- The spinner stops and a feedback bar appears below the chat
- `GET /api/v1/playground/runs/{id}/trace` is called to fetch the Langfuse trace URL
- If a trace URL is returned, a "View Trace" link appears next to the thumbs

**8. Feedback**

Thumbs up (score=`1`) or down (score=`-1`) POSTs to `POST /api/v1/playground/runs/{id}/feedback`. The backend stores it and optionally pushes a score to Langfuse so it appears in the observability dashboard.

**9. Save to dataset**

Any completed run can be saved: `POST /api/v1/playground/runs/{id}/save-to-dataset` with a `dataset_id`. The run's `input_message` becomes a dataset item. From there it can feed an eval run.

## Agent Evaluation Workflow

There are two distinct evaluation paths, designed to work together. Path 1 is exploratory — you manually poke at an agent and build intuition. Path 2 is systematic — you run a whole dataset through an agent and get aggregate pass/fail scores.

### Path 1 — Interactive testing (the Chat pane)

This reuses the chat mechanics documented in *Step-by-step user flow* above; the evaluation-specific parts are the automatic judge, the feedback signal, and promoting runs into a golden set.

**Select agent → send message → watch stream.** Same as steps 1–3. Picking an agent scopes the whole session to it; the purple "Sandbox mode" badge confirms no production state is touched. `POST /api/v1/playground/runs` creates a `PlaygroundRun` (`context="playground"`, `sandbox=True`) and a Langfuse root trace, then streams `text_delta` / `tool_call_start` / `tool_call_end` / `approval_requested` / `done` back to the ChatPane.

**Automatic LLM-as-Judge score.** After the stream ends, `_complete_run()` fires as a background task: it marks the run `completed`, then fire-and-forgets `judge.score_run()` (`judge.py`). The judge:
- Formats input + output into a 0.0–1.0 rubric prompt (truncated to 800 chars each)
- Calls Claude Haiku (`claude-haiku-4-5-20251001`, override via `JUDGE_MODEL`) with a 30s timeout
- Resolves the API key in order: `ANTHROPIC_API_KEY` → the team's active `LLMProvider` (Fernet-decrypted) → skip
- Writes `judge_score`, `judge_reason`, `judge_status` back to the `PlaygroundRun` row
- Pushes the score to the Langfuse trace as an `llm-judge` annotation

This runs on **every** run — no developer action needed. On timeout `judge_status="timeout"`, on any other failure `"error"`, and `judge_score` stays null.

**Thumbs up/down feedback.** After a run completes, two buttons appear. `POST /runs/{id}/feedback` with `score: 1` or `-1` pushes a `user-feedback` `HUMAN_ANNOTATION` score to Langfuse, filterable alongside the automated `llm-judge` scores in the observability dashboard.

**View the trace.** The "View Trace" link points at `{langfuse}/trace/{trace_id}` — the full span tree: LLM calls, tool calls, safety-scan spans, guardrail outcomes, plus both the judge score and user feedback as annotations.

**Save a run to a dataset — build the golden set.** `POST /runs/{id}/save-to-dataset` appends a new item to `PlaygroundDataset.items` (JSONB): `{ input, label, langfuse_trace_id, added_by, agent_name, source_run_id, added_at }`. Note: only the **input** is captured today — the response isn't persisted on the item yet (`output_text` is unmapped, see Known limitations). Promote runs that represent edge cases, failures, or regression anchors.

**Promote a version — the two publish gates.** Once a version looks good, the left "Promote" panel (visible when a version is selected) carries three actions, run in order:

1. **Mark Version Passed** → `PATCH /agents/{name}/versions/{id}` with `eval_passed=true`. Satisfies the ordinary eval gate.
2. **Mark Adversarial Passed** → same endpoint with `adversarial_eval_passed=true`. This is a **separate, explicit** red-team sign-off, kept as its own button so it can't be silently bundled into publish. It is **required** to publish any agent whose version uses a **high/critical-risk** tool — publish checks `adversarial_eval_passed` in addition to `eval_passed` (`agents.py`, `has_risky` branch). For low-risk agents this gate is skipped, so the button is optional (harmless to click).
3. **Publish Agent** → `POST /agents/{name}/publish` (pins the selected `version_id`). Returns **422** with a structured `detail` object if a gate fails: `{error:"eval_not_passed"}` or `{error:"adversarial_eval_not_passed", version_number}`. The panel turns that object into a readable toast (`publishErrorMessage`) — an earlier build passed the raw object to `toast.error`, which crashed the toast sink and blanked the app; the toaster is now inside its own `ErrorBoundary fallback={null}`.

### Path 2 — Batch evaluation (Datasets → Eval Runs)

**1. Create a dataset.** From the Playground left panel's "Manage Datasets / Eval" link (or `/playground/datasets`), click **New Dataset**. Items are one JSON object per line:

```json
{"input": "What's the status of order 123?", "expected_output": "Order 123 is shipped."}
{"input": "Cancel order 456", "expected_output": "Order 456 has been cancelled."}
```

`input` is required; `expected_output` is optional but used by the judge for accuracy scoring. Items can also be seeded from saved playground runs (Path 1).

**2. Run Eval.** Click **Run Eval** on a dataset row. A modal asks for a target agent (dropdown from `/agents?status=active`). On confirm, `POST /api/v1/playground/eval-runs` creates an `EvalRun` (`status=pending`), then **synchronously creates a real Kubernetes Batch Job** named `eval-{run_id[:8]}` in the `agentshield-platform` namespace with env vars `EVAL_RUN_ID`, `AGENT_NAME`, `DATASET_ID`, `REGISTRY_API_URL`. On success the run flips to `status=running`; if the Job can't be created the run is marked `failed` and the API returns 500. The UI redirects to `/playground/eval-runs/{id}`.

> This is a real Job, not a stub — see `k8s.create_eval_job()`. The registry-api ServiceAccount needs RBAC `create`/`get` on Jobs in `agentshield-platform`.

**3. eval-runner Job executes.** For each dataset item the Job (`services/eval-runner/main.py`):
1. Starts a playground run via `POST /api/v1/playground/runs` (same governed flow as interactive)
2. Collects the response by consuming `GET /playground/runs/{run_id}/stream` (`text_delta` until `done`)
3. **Scores by keyword match, not the LLM judge:** `passed = expected_output.lower() in response.lower()`, giving a binary `judge_score` of `1.0`/`0.0` (`reasoning="keyword match"`). Items with no `expected_output` pass by default. The docstring's "LLM judge" is aspirational — the Haiku judge is not called here.
4. `POST /api/v1/playground/eval-runs/{id}/results` — records `{ dataset_item_idx, input_message, response, judge_score, judge_reasoning, passed }`
5. After all items, `PATCH /api/v1/playground/eval-runs/{id}` — sets `total_items`, `passed_count`, `failed_count`, `overall_score = passed/total`, `status=completed`

> **Known bug (batch eval is currently non-functional against real agents):** the eval-runner authenticates as `X-User-Sub: eval-runner`, but `create_playground_run` rejects any caller that isn't the agent's `created_by` with a 403. That 403 is unguarded, so the Job crashes and the `EvalRun` stays stuck at `running`. See the note in *Known limitations*.

**4. Review results.** `EvalResultsPage` (`/playground/eval-runs/{id}`) shows the aggregate header (overall score, pass rate, item count) and the per-item breakdown — input, response, judge score, pass/fail badge, and a per-item Langfuse trace link.

### The full loop

```
Developer sends message
        │
        ▼
POST /playground/runs  ──►  PlaygroundRun (sandbox=True) + Langfuse trace
        │
        ▼
SSE stream: text_delta, tool_call_start/end, approval_requested, done
        │
        ▼
Background: _complete_run()
        ├──► judge.py (auto)   Claude Haiku 0.0–1.0 → PlaygroundRun.judge_score
        │                       → Langfuse "llm-judge" annotation
        ├──► thumbs up/down (optional) → Langfuse "user-feedback" annotation
        └──► "Save to Dataset" (optional) → PlaygroundDataset.items += {input,…}
                        │
                        ▼
              DatasetsPage → "Run Eval" (pick agent)
                        │
                        ▼
              POST /playground/eval-runs
              → EvalRun(status=pending) → create K8s Job (real) → status=running
                        │
                        ▼
              eval-runner Job (namespace: agentshield-platform)
              ├── item 0: run agent → keyword-match score → POST /results
              ├── item 1: run agent → keyword-match score → POST /results
              └── item N: PATCH /eval-runs/{id} (status=completed, aggregate scores)
              ⚠ blocked today by X-User-Sub=eval-runner → 403 ownership check
                        │
                        ▼
              EvalResultsPage  (per-item: score, pass/fail, trace link)
```

## Error states

| Condition | What happens |
|---|---|
| No agent selected | Chat pane shows "No agent selected" placeholder; input bar is hidden |
| Agent not found (404) | `startPlaygroundRun` throws; toast.error appears |
| No running deployment | Stream emits `error` then `done` events immediately |
| Agent pod connect refused | Stream emits `error` (ConnectError) then `done` |
| Agent pod times out | Stream emits `error` (timeout, default 120s) then `done` |
| EventSource connection drops | `es.onerror` fires; toast "Stream connection lost." |
| Stream parse error | Silently skipped; other events continue |

## Backend routing summary

```
Browser
  POST /api/v1/playground/runs          → creates PlaygroundRun row
  GET  /api/v1/playground/runs/{id}/stream
        → query Deployment (agent_name → agent_id → running deployment)
        → derive: http://{k8s_deployment_name}.{k8s_namespace}.svc.cluster.local:8080
        → httpx stream POST {url}/chat/stream
        → parse named SSE lines, remap fields, re-emit as unnamed SSE
  GET  /api/v1/playground/runs/{id}/trace → Langfuse trace URL
  POST /api/v1/playground/runs/{id}/feedback
  POST /api/v1/playground/runs/{id}/save-to-dataset
  POST /api/v1/playground/approvals/{id}/decide

  # Batch eval (Path 2)
  POST /api/v1/playground/eval-runs        → creates EvalRun + real K8s Job
  GET  /api/v1/playground/eval-runs/{id}   → aggregate status/scores
  POST /api/v1/playground/eval-runs/{id}/results  → per-item result (called by Job)
  PATCH /api/v1/playground/eval-runs/{id}  → aggregate update (called by Job)
  GET  /api/v1/playground/eval-runs/{id}/results  → per-item breakdown
```

## Key files

| Layer | File | Role |
|---|---|---|
| Frontend page | `studio/src/pages/PlaygroundPage.tsx` | Layout, state, panel wiring |
| Chat pane | `studio/src/components/playground/ChatPane.tsx` | SSE consumer, message rendering, feedback |
| HITL overlay | `studio/src/components/playground/HitlPanel.tsx` | Approval decision UI |
| Trace panel | `studio/src/components/playground/TracePanel.tsx` | Event log sidebar |
| Agent selector | `studio/src/components/playground/VersionSelector.tsx` | Dropdown from `/agents?status=active` |
| API client | `studio/src/api/playgroundApi.ts` | HTTP calls to registry-api |
| Backend router | `services/registry-api/routers/playground.py` | Run create, stream proxy, feedback, trace |
| SSE source | `sdk/agentshield_sdk/streaming.py` | Named SSE event format emitted by agent pod |
| Judge | `services/registry-api/judge.py` | Fire-and-forget LLM-as-Judge scorer |
| Datasets page | `studio/src/pages/DatasetsPage.tsx` | Create dataset, Run Eval modal |
| Eval results page | `studio/src/pages/EvalResultsPage.tsx` | Aggregate + per-item eval breakdown |
| Eval router | `services/registry-api/routers/eval_runner.py` | EvalRun CRUD, launches K8s Job |
| K8s Job | `services/registry-api/k8s.py` | `create_eval_job()` — real Batch Job |

## Known limitations

- Only `sdk` agents with a `status=running` deployment in K8s can be streamed. Declarative agents and undeployed agents return an error event.
- The trace link only populates if Langfuse is deployed and `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` are set in the registry-api env.
- HITL resume via the playground overlay posts the decision to the registry-api, which must forward it to the agent pod's `POST /resume/{thread_id}` — this leg is not yet wired end-to-end.
- `PlaygroundRun.output_text` is captured from `text_delta` events for the judge, but that column is not mapped in the SQLAlchemy model yet (pre-existing). Because of this, saved dataset items store only the `input`, not the response.
- The eval-runner container image (`eval-runner:0.1.0`) is what actually iterates the dataset, calls the agent, and posts results. The registry-api only creates the Job; if that image is missing or the ServiceAccount lacks Jobs RBAC in `agentshield-platform`, the run is created but never progresses past `running`.
- **Batch eval is blocked by an ownership 403.** `services/eval-runner/main.py` calls `POST /playground/runs` with `X-User-Sub: eval-runner`, but `create_playground_run` 403s any caller that isn't the agent's `created_by`. The call is unguarded, so the Job crashes and the `EvalRun` never leaves `running`. Fix options: (a) allow a service identity (`eval-runner`) to bypass the owner check, (b) have the eval-runner impersonate the run's owner, or (c) drop the owner check for `context=playground`. Needs a decision before batch eval works end-to-end.
- **Batch eval uses keyword matching, not the LLM judge.** `expected_output` substring containment gives a binary score. The interactive path's Haiku judge (`judge.py`) is *not* invoked by the eval-runner, even though each run it creates gets a Haiku `judge_score` written to the `PlaygroundRun` row (which the eval results ignore). Unifying these — have the eval-runner read the run's Haiku score, or call the judge directly — is future work.
- **Durable playground runs are now supported (Phase 2).** When a durable agent is selected, the center panel swaps to a RunLauncher (JSON payload editor + "Launch Run" button) and StepTracker (live step list via SSE). The flow: user enters a JSON payload → clicks "Launch Run" → `POST /playground/runs` with `execution_shape=durable` → registry-api dispatches to the declarative-runner's `POST /run` endpoint → runner posts step callbacks to `POST /playground/runs/{id}/step-update` → SSE stream emits `step_update` events → StepTracker renders live progress. HITL steps pause the run (`status=awaiting_approval`) and approval decisions resume via `POST /resume/{thread_id}`. Durable runs that exceed 10 minutes wall-clock or have stale approval windows are auto-cancelled by the timeout worker.
- **Scheduled agents (Phase 3):** When a scheduled agent is selected (has schedule triggers), the center panel shows a RunNowPanel: cron expression + human-readable parse, timezone, and a "Run Now (Test Fire)" button that creates a playground run immediately. Useful for testing without waiting for the cron to tick.
- **Event-driven agents (Phase 3):** When a webhook-triggered agent is selected, the center panel shows a TestTriggerPanel: filter configuration display (read-only), JSON payload editor, "Send Test Event" button that calls `POST /playground/test-event`. The endpoint evaluates the payload against configured `filter_conditions`; if matched, creates a run (shows in event log with run link); if filtered, shows reason. The filter engine supports operators: eq, neq, contains, gt, gte, lt, lte, exists, in, regex.

## Composite Workflow Builder

Decision 22 adds `Workflow` as a second kind of executable alongside `Agent`. A composite workflow is a named collection of existing registered agents assembled into a pipeline. Users build workflows in a React Flow canvas, save them, and trigger sequential runs that produce a parent-plus-children run tree.

### Routes

| Route | Page | Purpose |
|---|---|---|
| `/workflows` | `WorkflowsPage` | List all composite workflows (name, team, orchestration, status, member count) |
| `/workflows/new` | `WorkflowBuilderPage` | Empty canvas — start building a new workflow |
| `/workflows/:id/builder` | `WorkflowBuilderPage` | Open an existing workflow for editing or running |

### Canvas UX

The builder is a full-screen React Flow canvas rendered in `WorkflowBuilderPage`. Each agent in the workflow is a `WorkflowMemberNode` — a custom node showing a position badge (#1, #2, …), an agent icon, the agent name, and an optional role chip. Nodes have left (target) and right (source) handles so users can draw directed edges between them.

**Empty state:** When no nodes are on the canvas a centered prompt reads "Add agents to build your workflow" with a sub-line pointing to the "Add Existing Agent" button in the toolbar.

**Adding agents (`AddAgentModal`):**
- Click "Add Existing Agent" in the toolbar.
- The modal fetches all agents (`GET /api/v1/agents`) and filters them client-side by the workflow's locked team and the user's name-search input.
- Each row shows agent name, description, execution_shape chip, and team chip.
- The "Add to Workflow" button is disabled for agents already on the canvas.
- Multiple agents can be added before closing the modal.

**Team constraint:** The first agent added to a new workflow locks the team. Every subsequent add is filtered to that same team. If a different-team agent somehow reaches the `onAdd` callback, a toast error fires: "Cannot mix teams. This workflow belongs to team `<team>`." The `AddAgentModal` already pre-filters the list once `currentTeam` is set, so this guard fires only in edge cases.

**Position ordering:** Nodes are laid out left-to-right as they are added (`x = position × 240, y = 150`). The position badge on each node reflects its sequential order. Position is preserved when the workflow is saved and re-loaded.

### Save flow

**First save (new workflow, no id in URL):**
1. Click **Save** in the toolbar.
2. A modal asks for the workflow name (free-text, required) and orchestration mode (select: sequential / supervisor / handoff; default sequential).
3. The team is derived from the agents on the canvas and shown as a read-only field.
4. On confirm: `POST /api/v1/workflows` creates the composite workflow; then `POST /api/v1/workflows/:id/members` is called once per canvas node (in order) to register each member with its position.
5. The store is updated via `markCompositeWorkflowSaved(id, name, team)` and the browser navigates to `/workflows/:id/builder` (replace history entry).

**Subsequent saves (workflow already has an id):**
1. Click **Save** — no modal shown.
2. All existing members are removed (`DELETE /api/v1/workflows/:id/members/:agentId` for each).
3. All current canvas nodes are re-added in order (`POST /api/v1/workflows/:id/members` per node).
4. A success toast fires and the workflow query is invalidated so the toolbar name reflects any changes.

### Run-tree status panel

The **Run Workflow** button appears in the toolbar only after the workflow has been saved (i.e., `compositeWorkflowId` is set in the store).

Clicking it opens a right-side panel (384 px wide). The panel has two states:

**Input state (no run yet):**
- A textarea labelled "Input message" for the user to type the payload passed to the first agent.
- A "Start Run" button that calls `POST /api/v1/workflows/:id/runs` with `{ input_payload: { message: <text> }, run_by: "studio-user" }`.

**Tree state (after triggering a run):**
- **Workflow Run section:** Shows the parent `AgentRun` status badge (running=blue, completed=green, failed=red, pending/queued=gray) and the workflow name.
- **Agent Steps section:** One row per child `AgentRun` ordered by position, showing: sequential index, agent name, latency (formatted as ms or s), and status badge.
- A "Run Again" button resets the panel to the input state.

**Polling:** After triggering, the panel polls `GET /api/v1/workflows/:id/runs/:runId/tree` every 2 seconds, up to 15 tries. Polling stops when the parent run reaches `completed` or `failed`, or after 15 attempts. A spinner with "Polling for updates…" appears while active.

### Backend routing summary

```
Browser
  POST /api/v1/workflows                          → creates CompositeWorkflow
  POST /api/v1/workflows/:id/members              → adds WorkflowMember (agent_id + position)
  DELETE /api/v1/workflows/:id/members/:agentId   → removes member
  POST /api/v1/workflows/:id/runs                 → triggers run (202); creates parent AgentRun
  GET  /api/v1/workflows/:id/runs/:runId/tree     → WorkflowRunTreeResponse { parent, children }
  GET  /api/v1/workflows                          → list composite workflows
  GET  /api/v1/workflows/:id                      → get workflow with members
```

### Key files

| Layer | File | Role |
|---|---|---|
| Builder page | `studio/src/pages/WorkflowBuilderPage.tsx` | Full-screen canvas, toolbar, save modal, run panel |
| Member node | `studio/src/nodes/WorkflowMemberNode.tsx` | React Flow node: position badge, agent icon, role chip |
| Agent picker | `studio/src/components/AddAgentModal.tsx` | Modal: filtered agent list + search + Add buttons |
| List page | `studio/src/pages/WorkflowsPage.tsx` | Table of composite workflows with Open button |
| API client | `studio/src/api/registryApi.ts` | `createCompositeWorkflow`, `triggerWorkflowRun`, `getWorkflowRunTree`, etc. |
| Store | `studio/src/stores/workflowStore.ts` | `compositeWorkflowId`, `compositeWorkflowName`, `markCompositeWorkflowSaved`, `resetCompositeCanvas` |

### Known limitations (W4)

- Only `sequential` orchestration is executed at runtime. Supervisor and handoff can be selected but will return 422 from `POST /workflows/:id/runs`.
- The run panel polls up to 15 times (30 seconds) then stops; long-running workflows may appear stuck.
- T027 (manual browser verification) is pending — the browser flow should be verified before marking W4 fully complete.

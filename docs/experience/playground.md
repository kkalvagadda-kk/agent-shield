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

When `approval_requested` fires, `HitlPanel` renders over everything with the tool name, risk level, and (redacted) args. `HitlPanel` mounts the shared `components/approvals/ApprovalCard.tsx` for the approval body (WS-1 M1) — the same presentational card the sandbox chat panel (`ConversationApprovalPanel`) and the global Approvals Inbox (`ApprovalsInboxPage`) use, so a new approval field is added in one place. The user clicks Approve or Deny. The frontend POSTs to `POST /api/v1/playground/approvals/{id}/decide` — no authority check in playground context (sandbox self-approval). The overlay closes and streaming resumes. If the resumed turn trips a **second** high-risk tool, the run parks again and the overlay re-appears for the next gate — a single turn can require more than one approval, and each is surfaced in turn. (Workflow chat behaves the same way: after a member resumes, `WorkflowChatPage` re-surfaces the inline `ConversationApprovalPanel` for a re-parked 2nd gate via its resume poll, rather than completing with an orphaned approval.)

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

**1. Create a dataset (mode-aware).** From the Playground left panel's "Manage Datasets / Eval" link (or `/playground/datasets`), click **New Dataset**. The modal has a **mode selector** that defaults to **`reactive`** (response correctness). Three modes are authorable today — `reactive`, `durable` (E-1), and `workflow` (E-5); the rest (`scheduled`/`webhook`) are reserved for later eval slices and their item editors stay disabled. A dataset's `mode` is stored on the row (`playground_datasets.mode`, back-filled to `reactive` for every pre-existing dataset). For reactive, items are one JSON object per line:

```json
{"input": "What's the status of order 123?", "expected_output": "Order 123 is shipped."}
{"input": "Cancel order 456", "expected_output": "Order 456 has been cancelled."}
```

`input` is required; `expected_output` is optional but used by the judge for accuracy scoring. Items can also be seeded from saved playground runs (Path 1). The create endpoint validates each item against the dataset `mode` via a discriminated union — an item whose explicit `kind` disagrees with the mode is rejected `422` (an illegal `{mode, item-kind}` pair is unrepresentable).

**Durable datasets (E-1).** Pick **`durable`** and the modal swaps to a structured **trajectory editor** — you're not scoring a single answer, you're scoring *which tools the agent called, in what order, with what args*. You author one durable item:
- an **`input_payload`** (a JSON object fed to the durable run, e.g. `{"contract_url": "s3://demo/acme.pdf"}`),
- a **match mode** — `exact` (same tools, same order, no extras) · `ordered` (expected tools appear in order, extras allowed) · `superset` (actual ⊇ expected, the default) · `unordered` (same set, any order),
- an ordered list of **expected steps**. Each step is a **tool name**, an optional **`args match`** (a JSON dict-subset that must be present in the real call args — e.g. `{"project": "LEG"}`), and an **Expect approval** toggle (the step *should* park for HITL — a gate that never parks fails that step's tool-call dimension, fail-closed).

Steps are optional: a **reference-free durable item** (payload only, no steps) is legal and degrades to scoring the final response against a rubric. The editor validates before it POSTs — a malformed `input_payload` or `args match` is rejected client-side, and the built `expected_trajectory` rides on the dataset `POST /playground/datasets` (`items[].expected_trajectory`), so it survives save → reload. The backend re-validates the durable variant and rejects a malformed item `422`.

**Expected side effects — evaluating a write-shaped agent safely (E-2).** A durable item can also assert **what the run *would have* delivered**. Below the trajectory steps, **Add side effect** authors an assertion: a **tool name**, an optional **args match** (a JSON dict-subset of the real call args, e.g. `{"to": "compliance@acme.com"}`), and an **occurs** mode — **`exactly` N** · **`at_least` N** · **`never`** (a forbidden call; any match fails the item). `never` takes no count — an absence has no multiplicity.

Authoring **any** side-effect assertion is what flips that item into **`eval_mode=record`**, and the editor says so ("no real emails, tickets, or payments are sent"). With no assertions the item stays **`live`** and its tool calls are delivered for real — record mode is opt-in per item and never leaks into an interactive sandbox run (which may legitimately *want* a real sandbox write). What record mode does:

- Every tool call still runs the **real governed path** — OPA decides, HITL parks and is approved for real. Nothing about governance is skipped.
- At the **delivery edge** — the one point where a governed call reaches its downstream — a **side-effecting** tool is **recorded and answered with a mock** instead of being invoked. The recorded entry is `{tool, args, mocked_response, would_have_invoked}`, persisted onto the real run's `run_steps.output.recorded_side_effects[]`.
- **Read-only tools pass straight through** (an HTTP `GET` is `side_effecting=false`).
- **Fail-closed:** a tool the platform can't classify is **mocked, never invoked**. And an item that asserts a required call but records *nothing* is recorded **failed** — an eval that cannot verify a side effect never silently passes.

Side effects are optional (a read-shaped durable item asserts none). The built `expected_side_effects` rides on the dataset POST (`items[].expected_side_effects`) and survives save → reload.

**Workflow datasets (E-5).** Pick **`workflow`** and the modal swaps to a structured **run-tree editor** — a workflow can produce the *right final answer while routing through the wrong members* (a supervisor that skipped triage, a conditional that took the wrong branch), so you score the **member path** (which members ran, in order — a trajectory at member granularity), not just the response. You author one workflow item:
- an **`input_message`** fed to the workflow run,
- an optional **`expected_output`** (scored on the response dimension),
- a **member-path match mode** — the same four modes as durable (`exact`/`ordered`/`superset`/`unordered`), default **`ordered`**,
- an ordered **expected member path** — the member (agent) names expected to run, in order (e.g. `intake → triage → resolver`),
- optional **per-member rubrics** — a `{member: {rubric}}` map that zooms one level into that child's own run and scores its behavior against the rubric (e.g. `triage: "correctly routed to billing"`).

The member path is optional: a **reference-free workflow item** (input only, no members) is legal and degrades to scoring the response. The editor validates before it POSTs — a missing input, a blank member row, or an incomplete per-member rubric is rejected client-side, and the built `expected_member_path` (+ `match_mode` + `per_member`) rides on the dataset `POST /playground/datasets` (`items[].expected_member_path`), so it survives save → reload. The backend re-validates the workflow variant and rejects a malformed item `422`.

**2. Run Eval.** Click **Run Eval** on a dataset row. A modal asks for a target (a running sandbox agent deployment, or a workflow deployment). On confirm, `POST /api/v1/playground/eval-runs` creates an `EvalRun` (`status=pending`), resolves the run's interpretation **`mode`** from the executable (a workflow → `workflow`; an agent → its `execution_shape`, `reactive`|`durable`) and **rejects a `mode` mismatch against `dataset.mode` with `422`**, then **synchronously creates a real Kubernetes Batch Job** named `eval-{run_id[:8]}` in the `agentshield-platform` namespace with env vars `EVAL_RUN_ID`, `AGENT_NAME`, `DATASET_ID`, `MODE`, `REGISTRY_API_URL`. On success the run flips to `status=running`; if the Job can't be created the run is marked `failed` and the API returns 500. The UI redirects to `/playground/eval-runs/{id}`.

> This is a real Job, not a stub — see `k8s.create_eval_job()`. The registry-api ServiceAccount needs RBAC `create`/`get` on Jobs in `agentshield-platform`.

**3. eval-runner Job executes.** For each dataset item the Job (`services/eval-runner/main.py`):
1. Starts a playground run via `POST /api/v1/playground/runs` (same governed flow as interactive) — the eval-runner authenticates as `X-User-Sub: eval-runner`, a service identity that is allowed to run any team's agent. It sends **`eval_mode: "record"`** for an item carrying `expected_side_effects` (E-2) and **`"live"`** otherwise; the flag persists on the run (`playground_runs.eval_mode`) so it survives a HITL park and is re-sent on the resume dispatch
2. Collects the response by consuming `GET /playground/runs/{run_id}/stream` (`text_delta` until `done`)
3. **Scores through the one scoring door** — `POST /api/v1/playground/eval/score` with `{mode, item, input, response}`. For `mode=reactive` this calls `judge.score_response` (the real LLM-as-Judge, reference-based against `expected_output`) and reduces to a composite via `judge.score_composite`, returning `{composite, dimension_scores: {"response": x}, detail}` where `composite == x` — byte-identical to the pre-E-0 `judge_for_eval`. Keyword matching is a **fallback only** when the door/judge is unavailable (never gated on mode). `passed = composite >= 0.7`.
   For `mode=durable` the runner projects the real `run_steps` into an `actual_trajectory` **and** (E-2) into `recorded_side_effects[]`, posts both, and the door adds the deterministic **`side_effect`** dimension (`judge.score_side_effects`, weight `0.2`) for an item carrying `expected_side_effects`. Durable dims are deterministic — **no keyword fallback**; a door failure is fail-closed. An item that asserts a required call but recorded **none** is recorded **failed** before the door is even called (fail-closed — the side effect is unverifiable).
4. `POST /api/v1/playground/eval-runs/{id}/results` — records `{ dataset_item_idx, input_message, response, judge_score, judge_reasoning, passed, dimension_scores }` where `judge_score` is the composite (the gate input) and `dimension_scores` carries the per-dimension evidence (reactive → `{"response": composite}`)
5. After all items, `PATCH /api/v1/playground/eval-runs/{id}` — sets `total_items`, `passed_count`, `failed_count`, `overall_score = passed/total`, `status=completed`. On a passing run (`overall_score >= 0.7`) the associated `AgentVersion.eval_passed` auto-sets `True` (the publish gate opens without a manual PATCH) — unchanged by E-0.

**4. Review results.** `EvalResultsPage` (`/playground/eval-runs/{id}`) shows the aggregate header (overall score, pass rate, item count) and the per-item breakdown — input, response, a **composite score**, a **per-dimension score row** (`response`, `trajectory`, `tool_call`, `side_effect`, `filter`, `member_path`), a pass/fail badge, and a per-item Langfuse trace link. Reactive items populate only `response`; durable items (E-1) populate `trajectory` + `tool_call`, plus `side_effect` (E-2) when the item asserted side effects; workflow items (E-5) populate `member_path` (+ `response`); `filter` renders `—` until its scorer lands.

**Durable result evidence.** Expand a durable row and, below the response/reasoning, a **durable evidence** panel renders from `eval_run_results.eval_detail`:
- an **expected-vs-actual trajectory diff** — the authored steps (tool names, ⚑ for expect-approval) side-by-side with the real run's steps (from the run's `run_steps`, ⚑ for a step that actually parked), labelled with the match mode;
- a **tool-call args diff** table (`eval_detail.tool_diffs`) — per step, the expected args-subset vs the actual call args and a ✓/✗ match;
- a **HITL approvals** list (`eval_detail.approvals`) — per gated step, whether it `parked` and whether its `args matched`;
- a **run-tree deep-link** — "View run tree (`{run_id}`…)" lazily fetches the real `GET /playground/runs/{run_id}/steps` (the same `run_steps` StepTracker reads) and renders them read-only, so you can trace the composite back to the exact durable run that produced it.

**Side-effect result evidence (E-2).** When the item asserted side effects (or the run recorded any), a **"Side effects recorded, not delivered"** panel renders in the same expanded row — *the email that would have been sent*:
- a **per-assertion line** (`eval_detail.side_effect_detail.side_effect_diffs`) — the tool, its `occurs`/`count`, how many recorded calls **matched**, and a `satisfied`/`violated` badge;
- the **intercepted calls** (`eval_detail.recorded_side_effects`) — per call, the tool, its **args**, the **downstream that was NOT invoked** (`would_have_invoked`), and the **mock returned in its place** (`mocked_response`), each tagged **not delivered**;
- an explicit empty state ("No side effects were recorded — the run never attempted a write") so a missing write reads as evidence, not as a blank.

Recorded **args are PII-tokenized for display** (`compliance@acme.com` renders as `‹email›`; card/SSN/phone shapes likewise) — the raw args are what the scorer asserts server-side, never what a reviewer reads.

**Workflow result evidence (E-5).** Expand a workflow row and a **workflow evidence** panel renders from `eval_run_results.eval_detail`:
- an **expected-vs-actual member path** — the authored members side-by-side with the members that actually ran (extras flagged `+ extra`), labelled with the match mode;
- a **member diff** summary (`eval_detail.member_diff`) — an `order ok`/`order wrong` badge plus `missing:`/`extra:` member chips;
- a **per-member evidence** panel (`eval_detail.per_member`) — per rubric member, the backend emits `{member, score, reason, rubric, had_steps}`: the member's LLM rubric score, the rubric text, the judge's `reason`, and a `had_steps` flag; when the child recorded no run_steps to zoom into (`had_steps:false` — a reactive child, or a member that took no tool step) the rubric degrades to scoring the member's response only, surfaced with a "no run_steps to zoom into" note rather than silently passing;
- a **run-tree deep-link** — the parent workflow `run_id` opens the real run steps (same control as the durable path), so you can trace the member-path score back to the exact workflow run tree that produced it.

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
              eval-runner Job (namespace: agentshield-platform, env MODE=reactive)
              ├── item 0: run agent → POST /playground/eval/score (real judge) → POST /results
              ├── item 1: run agent → POST /playground/eval/score (real judge) → POST /results
              └── item N: PATCH /eval-runs/{id} (status=completed, aggregate scores)
                          → passing run auto-sets AgentVersion.eval_passed=True
                        │
                        ▼
              EvalResultsPage  (per-item: composite + per-dimension row, pass/fail, trace link)
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
  POST /api/v1/playground/eval/score       → the ONE scoring door (dispatch by mode;
                                             reactive → score_response + score_composite;
                                             returns {composite, dimension_scores, detail})
  POST /api/v1/playground/eval-runs        → creates EvalRun + real K8s Job (resolves + passes MODE)
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
| Judge / scorer library | `services/registry-api/judge.py` | Fire-and-forget LLM-as-Judge scorer + `score_response`/`score_composite` (the reactive scoring library behind `/eval/score`) + the deterministic `score_trajectory`/`score_tool_calls`/`score_side_effects`/`score_member_path` scorers |
| Scoring door | `services/registry-api/routers/playground.py` | `POST /playground/eval/score` — mode dispatch |
| Record/mock seam | `sdk/agentshield_sdk/graph_builder.py` | `governed_tool` step 3 — the ONE delivery edge; under `eval_mode=record` a side-effecting call is recorded + mocked instead of invoked (fail-closed) |
| Recorded-call persistence | `sdk/agentshield_sdk/durable.py` | Drains the recording buffer onto the real tool step's `run_steps.output.recorded_side_effects[]` |
| PII display tokenizer | `studio/src/lib/piiTokenize.ts` | Display-only tokenization of recorded side-effect args (raw args are asserted server-side, never rendered) |
| Datasets page | `studio/src/pages/DatasetsPage.tsx` | Create dataset (mode selector + reactive/durable/workflow item editors, incl. durable `expected_side_effects`), Run Eval modal |
| Eval results page | `studio/src/pages/EvalResultsPage.tsx` | Aggregate + per-item eval breakdown; durable trajectory/tool-call evidence + recorded side-effect evidence + workflow member-path/per-member evidence + run-tree deep-link |
| Eval router | `services/registry-api/routers/eval_runner.py` | EvalRun CRUD, launches K8s Job |
| K8s Job | `services/registry-api/k8s.py` | `create_eval_job()` — real Batch Job |

## Known limitations

- Only `sdk` agents with a `status=running` deployment in K8s can be streamed. Declarative agents and undeployed agents return an error event.
- The trace link only populates if Langfuse is deployed and `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` are set in the registry-api env.
- HITL resume via the playground overlay posts the decision to the registry-api, which must forward it to the agent pod's `POST /resume/{thread_id}` — this leg is not yet wired end-to-end.
- `PlaygroundRun.output_text` is captured from `text_delta` events for the judge, but that column is not mapped in the SQLAlchemy model yet (pre-existing). Because of this, saved dataset items store only the `input`, not the response.
- The eval-runner container image (`eval-runner:0.1.0`) is what actually iterates the dataset, calls the agent, and posts results. The registry-api only creates the Job; if that image is missing or the ServiceAccount lacks Jobs RBAC in `agentshield-platform`, the run is created but never progresses past `running`.
- **Batch eval runs end-to-end (service identity + real judge).** The eval-runner authenticates as the `eval-runner` service identity, which is allowed to run any team's agent (the earlier ownership-403 is resolved), and scores each item through the one scoring door (`POST /playground/eval/score` → `judge.score_response`), so the eval results carry the real LLM-as-Judge composite rather than a keyword-match binary. Keyword matching survives only as a fallback when the door/judge is unavailable. For `mode=reactive` the composite is byte-identical to the pre-E-0 `judge_for_eval` (proven no-fakes by `scripts/e2e/suite-61-eval-mode-plumbing.sh`). **E-1 adds the `durable` dispatch** — `score_trajectory` (four match modes over the real `run_steps` tool sequence) + `score_tool_calls` (dict-subset args + `expect_approval` HITL assertion), reduced with `weighted_mean` (durable defaults `response 0.4 / trajectory 0.4 / tool_call 0.2`); these are pure/deterministic (no LLM) and read the real durable trajectory. **E-2 adds the `side_effect` dimension** — an item carrying `expected_side_effects` runs under `eval_mode=record`, so every side-effecting tool call is recorded + answered with a mock at the one governed delivery edge instead of hitting the real downstream (the eval never sends a real email / files a real JIRA), and `score_side_effects` asserts the recorded calls (`occurs` `exactly`/`at_least`/`never`, `count`, dict-subset `args_match`) at weight `0.2`. Fail-closed: an unclassifiable tool is mocked not invoked, and an item that recorded nothing where a call was required is recorded failed. The remaining mode-specific scorer (`filter`) is deferred to E-4 behind the same door.
- **Durable playground runs are now supported (Phase 2).** When a durable agent is selected, the center panel swaps to a RunLauncher (JSON payload editor + "Launch Run" button) and StepTracker (live step list via SSE). The flow: user enters a JSON payload → clicks "Launch Run" → `POST /playground/runs` with `execution_shape=durable` → registry-api dispatches to the declarative-runner's `POST /run` endpoint → runner posts step callbacks to `POST /playground/runs/{id}/step-update` → SSE stream emits `step_update` events → StepTracker renders live progress. HITL steps pause the run (`status=awaiting_approval`) and approval decisions resume via `POST /resume/{thread_id}`. Durable runs that exceed 10 minutes wall-clock or have stale approval windows are auto-cancelled by the timeout worker.
- **Scheduled agents (Phase 3):** When a scheduled agent is selected (has schedule triggers), the center panel shows a RunNowPanel: cron expression + human-readable parse, timezone, and a "Run Now (Test Fire)" button that creates a playground run immediately. Useful for testing without waiting for the cron to tick.
- **Event-driven agents (Phase 3):** When a webhook-triggered agent is selected, the center panel shows a TestTriggerPanel: filter configuration display (read-only), JSON payload editor, "Send Test Event" button that calls `POST /playground/test-event`. The endpoint evaluates the payload against configured `filter_conditions`; if matched, creates a run (shows in event log with run link); if filtered, shows reason. The filter engine supports operators: eq, neq, contains, gt, gte, lt, lte, exists, in, regex.

## Who a run acts as — daemon identity & async approvals (WS-2)

Not every run has a live person behind it. A scheduled (cron) or webhook run fires with no JWT — nobody is sitting there. Those are **daemon** runs, and they act as the agent's own **service identity**, not as any user. In the audit trail and on approval cards that reads as `"service:<agent> on behalf of <the human who armed the trigger>"` — the service does the work, but you can still see which person authorized it (`armed_by`, captured when the trigger was armed or created).

The same daemon agent used interactively through chat still runs under **you**, the caller. That's the point of the R3 rule: identity is a floor, not a cap. A daemon agent doesn't drop to the service identity just because it *can* run headless — it only does so when there's genuinely no caller.

**How identity gets decided.** One rule, keyed on whether a live caller (JWT) is present:

- **`/chat`** — a caller is present, so the run acts as the caller (their `user_id` flows to OPA, `run_by` = the caller).
- **A trigger run** (`/internal/runs/start`) — no caller. A **daemon** trigger acts as the service identity; a **user_delegated** trigger acts as the arming human.
- **A user_delegated trigger with no arming human is refused** — the run fails closed rather than running as nobody. This is the OPA identity floor (`user_identity_ok`): daemon + empty user is allowed, user_delegated + empty user is denied with `missing_user_identity`.

**Async reviewer routing.** When a daemon run hits an approval, there's no user in front of it to click Approve — so it parks and routes to a **reviewer role** in the **Global Approvals Inbox** (`/approvals`) instead of an inline overlay. The default scope is `agent:reviewer`; a trigger can override it with its own approver-role. The inbox card renders the `"service:X on behalf of Y"` principal so a reviewer knows what authorized the run. Only a reviewer (or an admin) can decide it — a decide from anyone outside the reviewer scope is rejected `403`. This is the async counterpart to the sandbox self-approve overlay: a human still gates the risky call, just not the same human who (never) started the run.

## Operating a scheduled agent — the Scheduled Overview (WS-3)

Once a scheduled agent is deployed, open a deployment and the **Overview** tab renders the scheduled operate surface (it switches on the presence of a schedule trigger). Beyond the schedule cards (cron + a human-readable parse, timezone, and an enable/disable toggle), the Overview rolls up the run-time signals you need to tell at a glance whether the schedule is healthy:

- **Next fire** — the next time the cron will tick, computed server-side (`GET /agents/{name}/health`, croniter over the first enabled schedule). If the schedule has fallen behind, a small "N missed fires" line shows underneath.
- **Schedule health** — a single rolled-up badge (**healthy** / **degraded** / **failing**) so you don't have to read the run history to know something's wrong.
- **Last run** — the most recent run's status badge, when it started, and the trigger that fired it, with a recent-run history list below.
- **Failure alerts** — an alert-config summary card reading the trigger's `alert_email` + `alert_on_failure`: whether failure alerts are On or Off and which address gets notified (or "No alert email set"). You edit both in the **Settings** tab's trigger row; the change persists on the trigger and survives a reload.

A scheduled agent that is also **daemon + durable** runs headless under the agent's service identity (see the daemon-identity section above), so when one of its runs hits a high-risk tool it **parks** and routes **async to a reviewer** in the Global Approvals Inbox rather than blocking on a chat overlay — nobody is watching the cron tick. The reviewer approves from the inbox and the run resumes to completion. If a scheduled run fails and the trigger has `alert_on_failure` on, the shared `dispatch_failure_alert` transport emails the configured `alert_email`.

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

## Conversation memory & the shared workflow transcript (context-storage POC)

Two related things thread context through a run. They use **different keys** and never mix.

### Chat memory (single agent, across turns)

Every chat turn carries a `session_id`. The registry uses it as the agent's `thread_id`
(the LangGraph checkpoint key) **and** as the transcript `conversation_id`, so a follow-up
turn on the same `session_id` recalls what was said before — the agent literally reloads the
prior turns from the `agent_memory` transcript and injects them as leading messages before it
runs. This is per-`(deployment, user, session)`: a second user replaying someone else's
`session_id` is rejected (not-your-session), so one user never reads another's conversation.
Because the transcript lives in Postgres (not pod RAM), the memory survives a pod restart.

### Shared workflow transcript (multiple agents, one run)

In a composite/multi-agent workflow, all members of a single run share **one**
`conversation_id` (= the parent workflow run id). Before each member runs, it loads the whole
shared transcript for that run — every member's turns, in order — not just the previous
member's truncated output string. So member B can reference something that appeared only in
member A's turn, and a supervisor sees a worker's reasoning, not just its final answer.

- **Attribution.** Each transcript row records which agent authored it. When a member reads a
  turn written by a *different* member, that turn's content is prefixed with `[<agent_name>]: `
  so it reads it as a peer's contribution rather than its own words. Its own turns stay
  verbatim.
- **Write-back.** After a member runs, it appends its turn tagged with its own `agent_name`
  and the `workflow_run_id`, so the next member sees the now-longer transcript. A fresh backend
  fetch (`GET /agents/{name}/memory?scope=workflow_run&thread_id=<parent_run_id>`) returns every
  member's tagged rows in order.
- **Identity split (why durable resume still works).** The shared `conversation_id` is
  orthogonal to each member's per-member `thread_id`. `thread_id` stays the checkpoint +
  approval-correlation key (a durable member that pauses for HITL resumes on *its own*
  `thread_id`); `conversation_id` is only the transcript key. They travel in separate request
  fields and never alias, so sharing the transcript does not disturb per-member durable resume.
- **String-passing is now a fallback.** The old behavior — passing only the previous member's
  final output string as the next member's `message` — is retained as a fallback; the real
  cross-member context now flows through the shared transcript.

## Per-agent attribution — who said what (context-storage POC-2)

POC-1 made members *share* a transcript; POC-2 makes it **visible**. Every chat and eval
surface now shows which agent said what, so a multi-agent workflow reads as a real
multi-speaker conversation instead of one anonymous blob. One presentational component backs
all of it: `AttributedBubble` (`studio/src/components/chat/AttributedBubble.tsx`) renders a
role-styled bubble with an optional agent-name label + a deterministic color dot from
`agentColor` (`studio/src/lib/agentColor.ts`, same name → same color across reloads). Pass no
`author` (or `showLabel={false}`) and it's the plain unlabeled bubble it always was — single
agent is the degenerate one-speaker case, not a separate code path.

### The streaming author frames (single-agent chat only)

The single-agent chat proxy (`_proxy_agent_stream` in `services/registry-api/routers/chat.py`)
now names the speaker on the wire. Right after the upstream agent returns `200`, before the
first token, it emits **one** frame:

```json
{"type": "agent_start", "author": "refund-agent"}
```

and every token frame carries the same `author`:

```json
{"type": "token", "content": "Hello", "author": "refund-agent"}
```

`author` is the agent name (the `{name}` path param). `done` / `error` / `approval_requested`
are unchanged, and a client that ignores `author` behaves exactly as before. The resume stream
(`resume_stream_chat`, after a HITL approval) tags its tokens the same way. This rides
`stream_chat` and `stream_deployment_chat` — the AgentChatPage and CatalogChatPage single-agent
surfaces.

**Why only single-agent.** Workflow members are **not** streamed to the client — the
orchestrator dispatches each member server-side (`POST /chat` or `POST /run`) and collects the
output; the browser learns member results only by polling the run tree. So streaming attribution
is meaningful only for the one-speaker case; workflow attribution rides the run tree + the
shared transcript instead (below). We did not invent a workflow streaming path that doesn't
exist.

### Attributed bubbles across the three surfaces

- **AgentChatPage** (`/agents/{name}/chat`) and the **single-agent path of CatalogChatPage**
  (`/catalog/{id}/chat`) consume the `agent_start` / `author` frames through the shared stream
  reducer (`routeToken` / `openAuthorBubble` in `studio/src/lib/chatStream.ts`): a token appends
  to the matching-author assistant bubble, a new author opens a new bubble. Single-agent chat
  passes `showLabel={false}`, so it stays unlabeled — the plumbing is uniform, the label is a
  caller decision.
- **ChatPane** (playground) reads a *different* contract — the raw named runner events
  (`text_delta`) off `/playground/runs/{id}/stream`, which carry no author — so it derives the
  author client-side from the selected-agent prop and renders through `AttributedBubble`
  (chips + safety block kept in the `children` slot). Also single-speaker, also unlabeled.
- **CatalogChatPage workflow path** is the priority fix. A completed workflow turn now keeps the
  **full `WorkflowRunTree`** (not just the parent's final output) and renders each
  `tree.children[i]` as its own labeled `AttributedBubble` — `author = child.agent_name`, colored
  dot, plus a compact step view (status badge · latency · View-Trace) — in run order, followed by
  the parent's final-output summary bubble. A two-member run shows two labeled member bubbles in
  distinct colors, not one collapsed answer.

### Eval shared-thread transcript (EvalResultsPage)

An expanded eval result row now has a collapsible **Conversation transcript**
(`ConversationTranscript` in `studio/src/pages/EvalResultsPage.tsx`). Opening it lazily fetches
`listMemory(memberName, {thread_id: run_id, scope: 'workflow_run', limit: 200})` — the
`scope=workflow_run` read drops the per-agent filter so **every** member's turns come back on the
one shared thread — and renders them as `AttributedBubble` rows in `message_index` order. So a
workflow eval item replays as the real multi-speaker conversation that produced the score.
`{memberName}` must be a real Agent (the `/agents/{name}/memory` endpoint 404s on a workflow
name), so the page resolves a member from the eval detail's member path and only mounts the
transcript when both a member and a `run_id` resolve — a single-agent/reactive item with no
member renders nothing extra (no error).

### "Share context between agents" toggle (WorkflowBuilder)

The WorkflowBuilder first-save modal now has a **Share context between agents** checkbox
(helper text: "Members see each other's turns in a shared conversation thread.") bound to the
composite's `memory_enabled` field. It defaults on, loads from `workflow.memory_enabled` when an
existing workflow opens, and is sent in **both** save calls — `createCompositeWorkflow` (first
save) and `updateCompositeWorkflowApi` (resave). This is the on/off switch for the whole shared
transcript. Per-session vs per-run scope is **not** a control — scope is derived from the
entrypoint (chat → per-session, run → per-run) — and the "share rationale" toggle waits on the
rationale summarizer (both recorded in the gap ledger).

## Team Knowledge Base / RAG & citation chips (context-storage POC-4)

POC-4 adds team Knowledge Bases that agents query via the platform-managed `knowledge_search`
tool, and closes the citation loop into the POC-2b `AttributedBubble.citations` slot. The
retrieval path is real end to end — a Source is chunked, embedded (`bge-small-en-v1.5`, 384-dim)
and indexed into pgvector; search is cosine similarity over that index; MinIO holds the raw
blobs. Tenancy is structural: every KB read/write is scoped to the caller's team, and the
`knowledge_search` backend resolves `kb_id` **server-side** from the agent's binding, never from
the model.

### The Knowledge pages

`Sidebar → Knowledge` (routes `/knowledge` and `/knowledge/:id`).

**List — `KnowledgeBasesPage` (`/knowledge`).** `GET /api/v1/knowledge-bases` fills a table
(name, team, source count, ready/total status, updated). **New Knowledge Base** opens a modal
(`name` + optional `description`) → `POST /api/v1/knowledge-bases`; `team`/`created_by` are set
server-side from the caller's identity (the body can't set them). On success the list query
invalidates so the new KB appears after the refetch (save → reload).

**Detail — `KnowledgeBaseDetailPage` (`/knowledge/:id`)** has three tabs:

- **Sources** — a real `<input type=file>` upload zone (`.pdf/.txt/.md`) → `POST
  /{kb}/sources` (multipart). Upload returns `201` immediately with a `pending` Source; ingest
  runs fire-and-forget in the background (`BackgroundTasks → ingest.ingest_source`). The table
  polls `GET /{kb}/sources` on a `refetchInterval` (2.5s) **while any Source is still ingesting**
  and stops once every Source settles. The status badge maps the wire status to a friendly label
  (F-6): `pending → Queued`, `indexing → Processing`, `ready → Ready`, `failed → Failed` (with the
  ingest error inline). A ready Source's **View** opens the chunk drawer (`GET
  /{kb}/sources/{id}/chunks` — content only, no embeddings on the wire); **Reprocess** (`POST
  .../reprocess`) recovers a stuck `indexing` or a `failed` Source; **Delete** removes it (chunks +
  vectors cascade).
- **Test retrieval** — a query box → `POST /{kb}/search` returns the top-k chunks by cosine
  similarity with their `source_filename` and `score`. This is the team-scoped proof that
  retrieval works **before** attaching the KB to an agent; the `team` is always the caller's, so
  the box can only ever search the caller's team's KB.
- **Settings** — PATCH name/description, delete the KB, and the **Attach-agent picker**. Attaching
  an agent (`PUT /{kb}/agents/{agent_id}`) records the `agent_knowledge_bindings` row **and**
  idempotently ensures the `knowledge_search` tool is on the agent — one action wires both. The
  "Attached to …" line reads `GET /{kb}/agents`; **Detach** (`DELETE /{kb}/agents/{agent_id}`)
  removes the KB scope (the tool stays attached, but the internal search then fail-closes to empty
  until a KB is bound again). POC = **one KB per agent** (a new bind replaces the prior one).

### The citation chip — proven on the playground (ChatPane)

Once a KB is attached, chatting with the agent in the **playground** (`ChatPane`) renders a
citation chip under the answer whenever the agent calls `knowledge_search`. The mechanism is
frontend-only (no SDK/runner change, F-4):

1. The agent pod calls the `knowledge_search` HTTP tool, whose backend is `POST
   /api/v1/internal/knowledge/search` (cluster-internal). It reads `X-Agent-Team` /
   `X-Agent-Name` from the pod env (unspoofable by the model), resolves the bound `kb_id`, embeds
   the query, searches `(team, kb_id)`, and returns `KnowledgeSearchResult { chunks, citations }`
   where `citations` is the de-duplicated `{ source, kb }[]`.
2. The playground SSE (`playground.py`) forwards the `tool_call_end` frame **with its result**.
3. `ChatPane` sees `tool_call_end` for `tool_name === "knowledge_search"`, runs
   `parseKnowledgeCitations(result)` (`lib/chatStream.ts`) to pull the `{ source, kb }[]`, and
   attaches it to the current assistant message.
4. `AttributedBubble` renders a chip row below the content — a `Database` icon + `{source} · {kb}`
   per citation. Empty chunks ⇒ empty citations ⇒ no chip row (the bubble's `hasCitations` guard).

The structured chip does **not** depend on the model quoting the source in prose — it is extracted
from the tool result, so a citation renders even if the answer forgets to name the file.

### Known gap — deployed-agent chat (AgentChatPage) citations are dormant

`AgentChatPage` carries the same citation wiring (`parseKnowledgeCitations` on `tool_call_end`,
`citations` passed to `AttributedBubble`), but it renders **no** chip today. Its stream is the
production pod proxy `pod_stream.py`, whose `_translate` **drops the successful `tool_call_end`
frame** (it emits one chip per call on `tool_call_start` and only re-emits on an *error* end), so
no `knowledge_search` result reaches the page to parse. The playground path is the proven live
citation surface; the deployed-agent surface is a not-yet-fed slot (gap ledger, deferred). Feeding
it means having `_translate` forward the tool result on a successful `tool_call_end` — no
frontend change needed.

## Conversations & History (context-storage POC-5)

POC-0/1 already persist every turn to `agent_memory`, and continue-with-context already works —
re-POSTing a chat with the same `session_id` reloads the thread's earlier turns as context
(`declarative-runner/main.py::_load_memory_context`). POC-5 is the surface layer that finally
*shows* those stored conversations and lets you reopen one and keep chatting — everywhere a chat
is exposed, sandbox and production. No new storage: the list is a read-side aggregate over
`agent_memory` grouped by `thread_id`, with `environment` **derived** by a LEFT JOIN onto
`production_deployments` (production iff the thread's `deployment_id` is a production deployment).
Title = the thread's **first user message** (Haiku-generated titles stay deferred — gap ledger).

### The two list endpoints

Both are ownership-scoped server-side (`require_user`; `user_id = caller.sub`) and return a
`ConversationSummary[]` — one row per `thread_id` carrying `title`, `agent_name`, `message_count`,
`last_activity`, `session_id`, `deployment_id`, and the derived `environment`:

- `GET /api/v1/me/conversations` — **cross-agent**: every thread the caller owns, each carrying its
  `environment`. Backs the standalone page.
- `GET /api/v1/agents/{name}/memory/conversations?deployment_id=` — **scoped** to one agent, and to
  one deployment when `deployment_id` is present (sandbox and production threads stay disjoint).
  Backs the docked History panel and the deployment Conversations tab.

Because `environment` is derived, not stored, the All / Sandbox / Production filter is a pure
client predicate (`filterConversationsByEnv`) over what `/me/conversations` already returned — not
a second endpoint.

### One shared sidebar, four surfaces

All three reuse the same `ConversationSidebar` (`components/conversations/ConversationSidebar.tsx`)
— a pure list + filter fed by React Query, taking an **explicit** `scope`
(`{kind:"agent", agentName, deploymentId?}` or `{kind:"me"}`, never an implicit env sniff). It
renders one row per thread (title | "Untitled conversation", agent name, an `sbx`/`prod` badge,
turn count, relative time), a **New conversation** button, and — on the standalone page only — the
env-filter pills. It does **not** fetch transcripts; each consumer seeds or navigates on
`onSelect`.

1. **Standalone Conversations page** (`ConversationsPage` → `/conversations`). Promoted to a real
   top-level nav item (`History` icon); the old demo-gated *Context Preview* section is retired
   (the `/preview/conversations` mock stays reachable while the `DEMO` flag lives). Two panes: the
   cross-agent sidebar (`scope:{kind:"me"}`, env filter on) on the left; a **read-only transcript
   preview** of the selected thread (via `listMemory`) on the right, with a **Continue** button →
   `/agents/{name}/chat?session={thread_id}` (the sandbox resume path — continuing a *production*
   row from here is a known gap; the docked History in the production console covers production
   resume).
2. **Docked History** in each chat console — `AgentChatPage` (sandbox, `/agents/:name/chat`) and
   `CatalogChatPage` (the production consumer chat). A **History** toggle in the header opens a
   docked `ConversationSidebar` scoped to that agent (and deployment). Selecting a row rehydrates
   the thread (see below); **New conversation** re-keys a fresh `sessionId` and clears the pane.
   Select/New are blocked while a turn is streaming or an approval is pending (resuming mid-stream
   would corrupt the console state).
3. **Conversations tab** on `DeploymentOverviewPage` (`/agents/:name/d/:depId`), sitting **beside**
   Overview / Runs / Memory. Deployment-scoped (`deployment_id = depId`). It owns no chat logic:
   selecting a row navigates to the deployment chat route
   (`/agents/:name/d/:depId/chat?session=…`), reusing the full `AgentChatPage` machinery. It sits
   next to — and does not replace — the admin **Memory** tab: Memory is the operator
   inspect/manage lens (`MemoryTab` — view / delete), Conversations is the user resume lens over
   the same store.
4. **Docked History in the Playground** (`PlaygroundPage` → `ChatPane`, the reactive sandbox chat).
   A **History** toggle in the agent header opens a docked `ConversationSidebar` scoped to the
   selected sandbox deployment (`scope:{kind:"agent", agentName, deploymentId}`). Selecting a row
   seeds the pane with that thread's transcript; **New conversation** starts a fresh chat.
   `ChatPane` owns its own state, so the parent remounts it (`key={chatKey}`) with an
   `initialMessages` seed instead of an in-place reset — every existing streaming / HITL / trace
   path is untouched. Select/New are blocked while a run is streaming, an approval is pending, or a
   resume is in flight (`ChatPane` reports its run state up via `onRunningChange`; a remount
   mid-stream would drop the SSE connection). **Caveat:** the playground run POST
   (`startPlaygroundRun` → `PlaygroundRunCreate`) carries **no** `session_id`, so this resumes the
   **view** of a past sandbox thread — the next message starts a fresh backend thread rather than
   continuing the seeded one. Making it a true multi-turn resume needs a backend `session_id` on the
   playground run (gap ledger).

### Continue with context — how resume works

`sessionId` on both chat pages is resettable and seedable from a `?session=<threadId>` query
param. Two entry paths converge on the same behaviour:

- **Deep link** (`?session=…`, from the standalone Continue button or the deployment tab nav) — on
  mount the page calls `seedFromThread(name, threadId, depId)`.
- **In-console select** — clicking a docked History row calls the same helper directly.

`seedFromThread` reads the transcript via `GET /agents/{name}/memory?thread_id=…` (`listMemory`),
sets `sessionId = threadId`, and maps the rows into the message list. The next message you send
POSTs with that `session_id`, so the runner reloads the thread's earlier turns as context and the
conversation genuinely continues — no new backend work, the same continue-with-context POC-0/1
shipped. (The **Playground** docked History is the one exception — its run POST has no `session_id`,
so it resumes the *view* of a past thread but does not continue it as one backend thread; see
surface 4 above and the gap ledger.)

**Rehydrated turns are plain bubbles.** `seedFromThread` reconstructs only user/assistant text;
rich slots (POC-4 citation chips, POC-2b tool chips / rationale / run-tree) are **not** rebuilt on
a seed — only the live stream renders them. This matches each page's existing non-live reload
branch, and the POC-4 citation wiring on the live path is untouched.

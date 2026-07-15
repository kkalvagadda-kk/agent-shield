#!/usr/bin/env bash
# deploy-cpe2e.sh — Checkpoint E2E deploy (fresh cluster or after restart)
#
# Creates all required secrets, builds Phase 9.3 + 10.x images, and deploys
# the full AgentShield stack:
#   - eval-runner:0.1.7 (Eval v2 E-1 FIX — durable trajectory projection now COLLAPSES one logical
#     tool call's consecutive same-tool run_steps into a single entry (_collapse_tool_calls in
#     eval-runner/main.py). A gated call parks as TWO rows — running(no appr) then
#     awaiting_approval(appr_id) — so one-entry-per-row projection put the park evidence on a
#     different entry than the tool's first `running` boundary, and score_tool_calls greedy-matched
#     the expect_approval step to the un-parked `running` entry → parked:false for a real park (E-1
#     scoring bug surfaced by suite-72 T-S72-004b). Collapse folds a call's in-flight `running` prefix
#     into its terminal disposition, carrying the sticky approval_id; distinct completed calls are NOT
#     merged. Fix is projection-only (judge.py unchanged; the score door consumes actual_trajectory).)
#   - registry-api:0.2.180 / eval-runner:0.1.6 / studio:0.1.137 (Eval v2 Phase E-1 P1+P2 — durable
#     trajectory + tool-call code scorers. judge.py gains PURE-CODE score_trajectory (4 match modes
#     exact|ordered|superset|unordered over the ordered run_steps tool list) + score_tool_calls (tool-name
#     exact + args_match dict-subset) + weighted_mean (alias over score_composite). schemas.py: DurableDatasetItem
#     now has a STRUCTURED ExpectedTrajectory (match_mode + typed steps → malformed trajectory 422s at the door);
#     EvalScoreRequest carries actual_trajectory + dimension_weights. sdk/durable.py: tool-boundary + parked-tool
#     StepUpdate.output now carries {tool, args} so the runner can project run_steps → actual_trajectory
#     (data-model §3). Scorers deterministic (no LLM); consumed by the /eval/score durable branch (T006). No migration.)
#   - registry-api:0.2.176 (WORKFLOW COST ROLLUP — a workflow parent run makes no LLM calls of its own, so
#     reading cost from its own Langfuse trace always yielded NULL and every workflow row showed Cost "—".
#     The cost-backfill sweep now rolls member (child) costs up onto the parent once the children are costed
#     (_rollup_workflow_parents; sum of children by parent_run_id, after a settle window). _mark_parent no
#     longer reads the parent's own trace for cost. Score stays "—" for non-eval runs by design.)
#   - studio:0.1.134 + declarative-runner:0.1.44 + chart (3 trigger/daemon gaps):
#     (1) PUBLIC WEBHOOK URL — EVENT_GATEWAY_PUBLIC_URL now defaults to global.publicUrl (the gateway host) and
#         the Envoy HTTPRoute exposes /hooks/ (was /webhooks/, which never matched the event-gateway's /hooks/
#         path), so the URL Studio shows is a real, externally-reachable endpoint.
#     (2) INSTRUCTION TEMPLATE MATRIX — CreateAgentPage now selects the instructions template from the full
#         shape × class matrix (daemon vs user_delegated changes the prompt, not just the shape/trigger).
#     (3) NO USER INPUT FOR DAEMON/SCHEDULED RUNS — a scheduled/webhook run can fire with no input; the runner
#         never builds an empty user turn now (daemon_kickoff_if_empty), so it no longer fails the provider's
#         non-empty-content check. ChatRequest.message is optional.
#   - registry-api:0.2.175 (durable playground run OUTPUT now shows in the step detail. The durable SDK emits the
#     agent's answer as `output_text` on the completing step, but the playground step-update callback only read
#     `body["output"]` → RunStep.output stayed NULL → the StepTracker step detail was blank (the answer only
#     reached run.output_text). Fix: capture output_text into step.output. KNOWN GAP (not fixed here, frontend):
#     the Event Trace sidebar is only wired for REACTIVE agents (ChatPane → onTraceEvent); DURABLE runs don't
#     feed it — the trace exists in Langfuse + is reachable via trace_url, but the panel stays "No events yet".)
#   - registry-api:0.2.174 (durable playground step-stream "Connection lost" FIX — multi-replica bug. The SSE
#     /playground/runs/{id}/stream durable path polled _STEP_EVENTS, a PER-REPLICA in-memory dict fed by
#     _publish_step_event. With >1 registry-api replica the pod's step-update callback and the SSE request are
#     load-balanced to DIFFERENT replicas → the stream saw an empty buffer, no data flowed, the gateway dropped
#     the idle connection → client showed "Connection lost". Fix: _stream_durable now reads the SHARED run_steps
#     table (the callback already persists there), emitting on new-or-changed status + 'done' when the run row is
#     terminal; _publish_step_event neutered to a no-op (was leaking). Fixes durable playground/eval Launch Run.)
#   - registry-api:0.2.173 (agent-delete pod-leak FIX. DELETE /agents soft-deprecated the agent + set its DELETE /agents soft-deprecated the agent + set its
#     deployments straight to 'terminated' — skipping 'terminating', so the deploy-controller's
#     terminating→delete_deployment→terminated GC step never ran → orphaned k8s Deployments/pods lingered until
#     the node filled (31 leaked in one session → 98% mem, blocked new deploys). Fix: delete_agent now sets
#     'terminating' (mirrors the explicit undeploy path); the controller GCs the pods. Workflow-deployment
#     termination has the same class of gap + no controller handler — logged in spec Future Improvements.)
#   - registry-api:0.2.172 (in-cluster /echo endpoint — httpbin.org replacement. New GET/POST/PUT/PATCH/DELETE
#     /echo (+ /echo/{path}) reflects method/path/query/body (NOT headers), always 200, unauthenticated like
#     /health. Demo/test HTTP tools (http_echo seed, refund_action, suite-18 OPA tools) repointed off the flaky
#     external httpbin.org to this in-cluster target so a third-party outage can't fail a governed tool call or
#     demo workflow. suite-63 covers it. docs/debugging/011.)
#   - declarative-runner:0.1.43 (rebase reconcile — folds e59cc4d's governed-tool graph-build KeyError fix
#     (python & arg-less HTTP tools: __annotations__ derived from the signature, typed params from input_schema,
#     InjectedState injected before **kwargs; sdk/tests/test_tool_executor_schema.py + suite-62-tool-schema-build)
#     ON TOP OF 0.1.42's durable._exc_reason change. Both SDK fixes now coexist in one image — rebuild +
#     re-materialize agents to pick up the combined SDK.)
#   - declarative-runner:0.1.42 (never emit an empty failure reason — httpx.ReadTimeout/ConnectTimeout
#     stringify to "" so a tool timeout surfaced as a bare "run crashed:" with no cause. SDK durable._exc_reason
#     now prefixes the exception TYPE (→ "run crashed: ReadTimeout"); applied to the drive-loop crash + the
#     declarative-runner resume/run fail-posts. Re-materialize agents to pick up the baked SDK. docs/debugging/011.)
#   - registry-api:0.2.171 (Workflow trace visibility + member-failure surfacing — docs/debugging/011.
#     (1) The PARENT workflow trace was an empty envelope ("No span-level observations"): the orchestrator
#     now authors a span per member step on the parent Langfuse trace (tracing.trace_workflow_step, called
#     from _run_step forward + resume_orchestration for the parked member). Member detail still lives on the
#     member trace (run_id<->trace_id cost correlation preserved). (2) A member's real failure reason (e.g. a
#     tool 503) was dropped: internal step-update callback now copies the failed step's error_message onto the
#     child AgentRun, and resume_orchestration surfaces "member 'X' failed after approval: <reason>" instead of
#     a bare generic message. NOTE: the underlying member DNS-export fix was already in the chart (qualified
#     LANGFUSE_HOST); existing agents needed re-materialization. suite-58 T-S58-005/006 now assert parent+member
#     trace observations; suite-61 T-S61-006 guards the durable-vs-reactive eval.)
#   - registry-api:0.2.170 (E-0 regression fix — "Failed to start eval run". create_eval_run 422'd when the executable's resolved mode (durable/workflow) != the dataset mode, but ALL datasets backfill to reactive → any durable-agent or workflow eval against an existing dataset 422'd. That broke pre-E-0 behavior (any agent was evaluable). Fix: the mode-mismatch guard now fires ONLY for a NON-reactive dataset; a reactive dataset scores any executable reactively (behavior-neutral), and the run stores mode=dataset.mode (the scoring mode). suite-61 missed it — it used a reactive agent; a durable-agent-vs-reactive-dataset case should be added.)
#   - registry-api:0.2.169 / eval-runner:0.1.5 / studio:0.1.133 (Eval v2 Phase E-0 — mode-aware
#     eval storage + one scoring door, behavior-neutral for reactive. Migrations 0059/0060 add
#     playground_datasets.mode + eval_runs.mode/dimension_weights/pass_threshold + eval_run_results
#     .dimension_scores/eval_detail/trigger_payload/matched/run_id (all guarded, back-compat →
#     'reactive'). judge.py gains score_response/score_composite; POST /playground/eval/score is the
#     single scoring door (reactive dimension_scores={"response":x}, composite==x, byte-identical to
#     judge_for_eval). eval-runner reads MODE and scores via /eval/score, recording dimension_scores;
#     eval_passed auto-set unchanged. Studio: dataset mode selector (reactive default) + per-dimension
#     result column. Proven no-fakes by suite-61 (real dataset→EvalRun→eval-runner Job→judge→persisted
#     composite; parity to the digit on a REAL run). No user-facing behavior change for reactive.)
#   - declarative-runner:0.1.41 (bug #9 CLASS fix — HTTP tool signature from input_schema. node_executors built the LLM-facing tool signature ONLY from {{placeholders}} in the URL/body_template, falling back to a single generic `query` param when there were none — so a tool with a real input_schema but no template exposed a useless `query` and the HITL approval showed {"query":"..."} garbage. Now the tool signature derives from input_schema.properties (structured params like order_id/amount) FIRST, then placeholders, then query; and a schema-driven tool with no body_template POSTs its structured kwargs as the JSON body. Replaces the per-tool body_template workaround. Requires agent redeploy.)
#   - registry-api:0.2.168 (Workflow member trace visibility — bug #8. Traces were NOT broken: each durable member pod DOES export its real LLM/tool spans to Langfuse under uuid(run_id=child_id).hex (verified 26 spans/member). But the member child AgentRun.langfuse_trace_id was NULL, so the run tree could not render a per-member "View Trace" link — the run looked trace-less, and the workflow PARENT trace is a thin envelope (the orchestrator makes no LLM calls). Fix: _run_step stamps child.langfuse_trace_id = uuid(child_id).hex where its spans actually land. Registry-only, no agent rebuild. No migration.)
#   - registry-api:0.2.167 (Single-agent durable RESUME FIX — bug #12. _resume_and_advance top-level-durable branch only recognized an AgentRun (id==thread_id) and posted /resume to _agent_pod_url = the -production pod. A single-agent durable PLAYGROUND run's thread_id is a PlaygroundRun id (falls through to a synchronous chat resume) AND the pod is {agent}-sandbox — so the resume DNS-failed and the run never completed. Fix: detect a durable PlaygroundRun (RunStep rows keyed by its id) → set the PLAYGROUND callback + resolve the agent's env-aware pod (mirror the workflow path). Completes single-agent durable HITL / T4 for real. No migration.)
#   - registry-api:0.2.166 (Single-agent durable playground dispatch FIX — bug #11. _dispatch_durable_run (POST /playground/runs, execution_shape=durable) posted to the DEFAULT shared declarative-runner service (declarative-runner.agentshield-platform:8080), which is NOT deployed — agents run as their own {agent}-{env} pods. So single-agent durable playground/T4 runs DNS-failed before their first step and died (0 run_steps). Hidden because suite-55 mocked httpx. Fix: resolve the agent's own pod as runner_url (mirror workflow _dispatch_durable_member). No migration.)
#   - registry-api:0.2.165 (Duplicate-approval FIX — "prompted twice". A durable HITL member re-runs its interrupt node ON RESUME (LangGraph replays the node from the top), so agentshield_sdk.hitl.require_approval re-POSTs create_approval with identical (thread_id,tool_name,tool_args). The registry dedup matched only status='pending', but by resume time the original is 'approved' → the dedup missed → a DUPLICATE pending approval was minted → the user got prompted a second time. Confirmed systemic (refund_action AND web_search showed 2-3 approvals per thread). Fix: create_approval dedup now matches any ACTIVE status (pending/approved/rejected) for the same thread+tool+args, so the resume re-post reuses the existing decision. No agent rebuild, no migration.)
#   - studio:0.1.132 (Inline-approval mixed-content FIX — the card never rendered in the browser. The run-panel fetched GET /api/v1/approvals (no trailing slash); FastAPI 307-redirects to /approvals/, but behind the TLS-terminating edge (Envoy→nginx→registry-api all http) the redirect Location is http:// on the gateway host → the https page BLOCKS it as mixed content → the approvals fetch silently failed → pendingApprovals stayed empty → no inline card even though the run parked correctly. Fixed at BOTH layers: (1) studio nginx /api/ block rewrites any http:// redirect Location to https:// (proxy_redirect regex) + sets X-Forwarded-Proto https — a CLASS fix for every collection endpoint; (2) listPendingApprovals calls the canonical /approvals/ (trailing slash) to avoid the redirect entirely. Found by a real browser Playwright run capturing the console (the API + data were correct all along). No backend change.)
#   - registry-api:0.2.164 / declarative-runner:0.1.40 (Durable HITL resume actually resumes — the deepest fix. sdk durable.resume_durable passed the reviewer decision as a plain STATE DICT ({"messages":[],"resume":decision}) to astream_events — but LangGraph only resumes a parked interrupt() via a Command(resume=value). A dict re-runs the interrupted node from scratch → require_approval calls interrupt() AGAIN → a NEW approval → the run re-parks forever (never advances). This broke ALL durable HITL resume (single-agent T4 AND workflow members) — hidden because suite-55 mocked httpx and suite-56 faked _run_step. Fix: resume_durable now builds langgraph Command(resume=decision) (lazy import). Plus registry-api workflow_orchestrator.resume_durable_member poll no longer treats the pre-existing awaiting_approval as terminal (the child STARTS parked) — it waits for completed/failed. Requires agent redeploy for the SDK fix. Found by a REAL park→approve→advance run. No migration.)
#   - registry-api:0.2.163 (Durable workflow-member RESUME→advance FIX. After approving a parked durable member, approvals._resume_and_advance posted to _agent_pod_url()=`{agent}-production…` (DNS-failed for a sandbox/playground member) and used a SYNCHRONOUS /resume (never adding run_id/callback_url for a member, since that was gated on parent_run_id is None) — so on the DNS error it returned early and the workflow NEVER advanced past awaiting_approval. New workflow_orchestrator.resume_durable_member mirrors the working forward _dispatch_durable_member: resolves the member's ACTUAL deployment env, posts /resume/{child} with run_id+callback_url (durable re-drive → the pod posts remaining steps to the child callback), polls the child to terminal, and then resume_orchestration advances the parent. _resume_and_advance routes durable workflow members (child AgentRun w/ parent_run_id + RunStep rows) to it and returns early; chat/top-level-durable paths unchanged. Found by a REAL park→approve→advance run. No migration.)
#   - registry-api:0.2.162 (Workflow-member approval context FIX. approvals._derive_context only looked up the run in PlaygroundRun, but a WORKFLOW member's thread_id is its child AgentRun.id — so the lookup missed and fell back to the pod's static claim (AGENTSHIELD_PLAYGROUND=false → 'production'). Result: a playground/builder workflow run's high-risk member parked with a PRODUCTION approval → routed to the reviewer console, never the inline run-panel card (the whole inline feature was dead for workflows). Fix: _derive_context now also resolves AgentRun by id and inherits ITS context (workflow member runs are production|playground), so a playground run yields a self-service inline approval. Found by a REAL end-to-end run — the faked suites never created a real approval. No migration.)
#   - registry-api:0.2.161 / declarative-runner:0.1.39 (Durable member output structured-content FIX. A durable member's final message content from Anthropic/Bedrock is a LIST of content blocks ([{'type':'text','text':'refund'}]), not a str. SDK durable._final_text returned it raw → the step-update callback's output_text was a list → registry-api wrote it to a TEXT column → asyncpg DataError → 500 on the callback → the member failed ("node X failed — stop") right after the LLM ran, so every durable workflow hung/failed at the first member. Fix at BOTH boundaries: (1) sdk durable._content_to_text normalizes content blocks → joined text at the source; (2) registry-api internal._as_text coerces output/output_text at the DB-write boundary (defense-in-depth; also unblocks pods still on the old SDK). Found by a REAL end-to-end run (the faked suites never exercised the live dispatch→callback path). No migration.)
#   - registry-api:0.2.160 (Workflow builder run FIXES — durable members were stuck/timing out. (1) Durable-member callback URL: durable_dispatch.registry_internal_base() defaulted to http://registry-api.agentshield-platform.svc… but the Service is agentshield-registry-api — so EVERY durable member ran its LLM then DNS-failed ("Name or service not known") posting its terminal step-update callback → orchestrator hit "no terminal callback within 120s" → the workflow hung at the first member. Fixed the default to the real FQDN (env REGISTRY_API_INTERNAL_URL still overrides). This was the live-pod leg the bash suites always faked. (2) Builder run context: start_workflow_run (POST /workflows/{id}/runs — the INTERACTIVE builder run; production/triggered go via internal.py) was hardcoded context=production, so a high-risk member's approval routed to the reviewer console instead of the INLINE run-panel card. Now runs as `playground`; _run_step children INHERIT the parent run's context (was also hardcoded production) so a high-risk member parks self-service → inline. Production trigger path unchanged. No migration.)
#   - registry-api:0.2.159 / studio:0.1.131 (Workflow builder fork rendering + multi-start validation. (1) Layered auto-layout: new studio/src/lib/workflowLayout.ts positions member nodes by EDGE-GRAPH depth (column = longest path from the indegree-0 root, targets fanned across rows) instead of a single row (y:150) that collapsed a conditional FORK into a misleading linear chain; the builder load effect now uses it (sequential still lays out linearly). (2) Multi-start-node validation: registry-api compute_start_node_warnings (conditional/handoff only — sequential/supervisor don't use find_start_node) flags a graph with >1 indegree-0 root at SAVE time (composed into the workflow warnings → builder toasts it), since the engine walks a single cursor from one start and silently orphans extra roots. handleResave re-fetches warnings AFTER edges persist (was surfacing stale pre-edit warnings). vitest 193 (+5 workflowLayout) + Playwright fork-layout assertion. No migration.)
#   - studio:0.1.130 (WS-6 operate parity — inline sandbox/playground workflow approval. The WorkflowBuilderPage run panel now renders the reusable ApprovalCard inline for a parked member when the run context is sandbox/playground (correlated by thread_id, surfaced on ApprovalInboxItem + AgentRunItem which the run-tree/inbox already carry); Approve/Deny calls the CONSOLE decide (PATCH /approvals/{id} → _resume_and_advance, self-service for non-production) so the workflow advances without a trip to Catalog → Approvals. Production runs are never fetched here — they stay console-only (authority-gated). startPolling refactor (DRY) + post-decide re-arm; listPendingApprovals gains a context param. Frontend-only: both thread_ids were already on the wire (ApprovalResponse.thread_id, AgentRunResponse.thread_id); no backend/migration change. vitest 188 (+2 WorkflowBuilderPage inline-approval) + Playwright workflow-builder.spec.ts stubbed parked→approve journey.)
#   - registry-api:0.2.158 / studio:0.1.129 (Execution Models v2 WS-1 T5–T7 — workflow durable completion + approval UI parity. T5 (D3): conditional/handoff/supervisor now durably park→resume→advance (previously only sequential); workflow_orchestrator._halt_for_approval/_park_or_fail carry a mode-specific cursor, new _run_{conditional,handoff,supervisor}_from re-entry mirror _run_sequential_from, resume_orchestration dispatches per mode (conditional+handoff Markovian: next=f(node,output); supervisor persists its accumulator worker_outputs+iteration+phase). Reactive fail-closed + sequential paths byte-for-byte unchanged. T6 (D4 "+Visibility"): durable members (Agent.execution_shape='durable') dispatch via the member pod's /run (run_id=child_id + step-update callback, thread_id=child_id for approval correlation) then poll the child run to terminal — so per-node run_steps appear under the child in the run tree; reactive members stay /chat; within-member crash-restart out of scope (gap ledger). T7 (M1): one presentational studio/src/components/approvals/ApprovalCard.tsx mounted by HitlPanel + ConversationApprovalPanel + ApprovalsInboxPage (a new approval field is added in one place); inbox reflects the existing server-side authority filter. suite-56 (6 cases, faked _run_step/resolve_edge_graph like suite-36/55) + suite-36/54/55 regression; Playwright approvals-inbox.spec.ts; vitest 186 + ApprovalCard.test.tsx. declarative-runner UNCHANGED.)
#   - registry-api:0.2.157 / declarative-runner:0.1.38 (Execution Models v2 WS-1 — durable engine real & resumable. Shared harness agentshield_sdk/durable.py (run_durable/resume_durable: one drive loop over astream_events → real per-node run_steps, interrupt→awaiting_approval park with approval_id, fail-closed) consumed by BOTH the declarative-runner (/run + crash-recovery via PostgresSaver, checkpoint.py slimmed, run_executor.py deleted) and the SDK server (native /run + Runner.run_durable/resume_durable). T4 park→approve→resume: approvals._resume_and_advance resumes a durable /run run THROUGH the harness (passes run_id+callback_url; discriminator = RunStep rows + id==thread_id, no parent) while chat + workflow-member resume stay byte-for-byte unchanged (extend-not-alter). suite-55 + suite-45 regression. studio UNCHANGED.)
#   - studio:0.1.128 (WS-0 R1 label polish — execution_shape "reactive" is shown in the UI as "Ephemeral" (true antonym of Durable); stored value/API contract unchanged (still 'reactive'). shapeLabel() helper in lib/utils documents the display≠storage mapping. Wizard shape card, Agent Settings dropdown, Workflow Save-modal dropdown, and the Agent Detail badge all read "Ephemeral". No backend/migration change.)
#   - registry-api:0.2.156 / deploy-controller:0.1.36 / studio:0.1.127 (Execution Models v2 WS-0 — agent_class authoring + shape-aware triggered dispatch. Migration 0058 makes agent_class NOT NULL + CHECK on BOTH agents and workflows (deploy-time coalesce removed). Studio create wizard split into three independent selectors — Shape · Trigger · Class (R1); Settings + Workflow Save-modal gain a Class selector; workflow save-time high-risk-tool warnings (S2). Shared durable_dispatch.py — the ONE /run POST both playground.py (sandbox) and internal.py (production) call (parity); internal.py branches on execution_shape (durable→/run + new /internal/runs/{id}/step-update callback writing run_steps; reactive→/chat) and fails-closed on dispatch failure. Reactive workflow = synchronous + wall-clock capped (M6/D2); a reactive approval gate fails-closed via _park_or_fail (S2). suite-54 (10 cases) + Playwright authoring persistence. declarative-runner UNCHANGED.)
#   - registry-api:0.2.155 / studio:0.1.126 (TraceDrawer polish — 3 features on the read-adapter seam: (1) nested waterfall/tree: NormalizedSpan gains parent_id (mapped from Langfuse parentObservationId), TraceDrawer builds a tree + renders duration-proportional bars scaled to the trace window, indented by depth; (2) per-generation economics: NormalizedSpan gains model/cost_usd/prompt_tokens/completion_tokens (GENERATION spans), shown inline ($cost) on the row + model/tokens on expand; (3) trace scores rendered as chips + trace total_cost in the metadata block. New TraceDrawer.test.tsx (tree nesting + per-gen cost + scores + not-ingested warning). vitest 176 green. No migration, no agent redeploy.)
#   - registry-api:0.2.154 / studio:0.1.125 (Observability read-adapter seam — no router/service calls Langfuse REST directly anymore; all reads go through a provider-neutral backend. New services/registry-api/observability_backend.py: ObservabilityBackend interface + LangfuseBackend adapter (backend #1) + NoneBackend, selected by OBSERVABILITY_BACKEND env (default langfuse). It owns get_trace (→ NormalizedTrace: spans/scores, provider-neutral), get_run_cost, spend_by_model, tool_call_stats, build_trace_url, push_score. Migrated ALL read call-sites off inline /api/public/* + LANGFUSE_PUBLIC_URL construction: observability.py (trace detail, tool/model aggregation, trace-url in list_traces + get_costs), tracing.py (removed fetch_trace_cost/fetch_trace_cost_tokens — now backend.get_run_cost), cost_backfill.py, workflow_orchestrator.py, playground.py (2 trace-fetch endpoints), and trace-url construction in catalog/agent_runs/deployments/eval_runner/composite_workflows (7 sites). Endpoints now return `trace` (NormalizedTrace) instead of raw `langfuse`. Frontend decoupled from the raw shape: observabilityApi/playgroundApi expose NormalizedTrace/NormalizedSpan/NormalizedScore + TraceDetail; TraceDrawer + ObservabilityComparePage consume `data.trace.spans`/`.scores` (neutral "Trace ↗" label, not "Langfuse"). Remaining Langfuse-direct = EMIT only (tracing.py trace creation + the feedback score POST), tracked as the emit seam. Updated observability-provider-abstraction.md. Tests: suite-53 stub swapped to the backend singleton; vitest 174 green (ObservabilityComparePage mock → trace shape). No migration, no agent redeploy.)
#   - registry-api:0.2.153 (Cost backfill NameError fix — tracing.fetch_trace_cost_tokens used os.getenv but tracing.py never imported os, so the live sweep threw NameError every cycle and wrote 0 costs (the e2e suite hid it by stubbing the fetch). Added a local `import os`. No other change.)
#   - registry-api:0.2.152 / studio:0.1.124 (Cost tracking Path A — persist LLM $ + tokens onto agent_runs and surface everywhere. Langfuse already computes per-LLM-call cost (calculatedTotalCost) + token counts on every OTEL GENERATION span, but nothing copied it into agent_runs.cost_usd/prompt_tokens/completion_tokens, so every cost query returned 0. New cost_backfill.py background sweep (registry-api lifespan task, 60s interval, last-24h window, idempotent on cost_usd IS NULL so replica-safe) sums each completed run's GENERATION cost/tokens via tracing.fetch_trace_cost_tokens and writes them back — one path covers chat/scheduled/workflow/SDK runs uniformly instead of racing ingestion at each completion site. Dashboard: LLM Cost panel (avg/run, prompt+completion tokens, Spend-by-Model bar via new _spend_by_model, same trace-id scoping trick as tool-calls) + link to the Cost console. New Cost console (GET /observability/costs → total/avg/tokens/projected-monthly, daily-spend trend, by-model + by-agent bars, most-expensive-runs table; env toggle prod/sandbox, period 7d/30d) at /observability/costs with a DollarSign sidebar entry. by-model comes live from Langfuse (model lives on the span, not the run); totals/daily/by-agent/top-runs from persisted SQL. Tests: CostConsolePage.test.tsx (2), ObservabilityDashboardPage.test.tsx cost panel; suite-53. No migration (cost_usd/prompt_tokens/completion_tokens already on agent_runs). No agent redeploy.)
#   - registry-api:0.2.151 (Langfuse trace DISPLAY NAME fix — every completed trace showed up as the generic "agent-run" instead of the agent/deployment identity. Two causes: trace_create_run named the trace "agent-run.{context}", and trace_complete_run then OVERWROTE it to a plain "agent-run" on completion (Langfuse upserts by id, last write wins), AND clobbered the create-time tags to just [status:X]. Fix: the trace name is now the agent instance identity — "{agent_name} · {environment}" (a deployment has no human name), context stays in metadata/tags for filtering; trace_complete_run now does a partial update (output only, no name/tags) so Langfuse preserves the create-time name + agent_name/deployment/env tags. agent_name was NOT missing — it was always in metadata+tags, just never the display name. No migration, no agent redeploy.)
#   - registry-api:0.2.150 / deploy-controller:0.1.35 (Reconcile drift-recovery after a cluster wipe — deployments the DB says are 'running' but whose k8s Deployment object no longer exists (cluster restart wiped all pods) were never re-checked (poll loop only handles pending/suspending/terminating), so they sat pod-less forever ("Agent pod is unreachable"). Fix, split by environment per product decision: SANDBOX (developer-facing) — _handle_sandbox_running_drift marks the drifted row 'terminated' with a "redeploy to restore" message (no auto-reprovision; the dev redeploys). PRODUCTION (customer-facing) — _handle_production_running_drift flips the drifted row back to 'pending' so the normal reconcile path re-materializes it, capped at 3/cycle to avoid a thundering herd on a small cluster. Drift trigger is strictly "Deployment OBJECT absent" (k8s.get_deployment is None) — never a transient 0-replicas — so a healthy agent mid-rolling-restart is never touched. New registry-api endpoint GET /catalog/internal/running-production-deployments feeds the production check; sandbox reuses GET /deployments/?status=running. suite-52 (sandbox running+bogus k8s name -> controller marks terminated). No migration, no agent redeploy.)
#   - registry-api:0.2.149 / studio:0.1.123 (Two observability/catalog surfacing adds: (#22) Tool-call frequency/latency dashboard panel — now feasible because OTEL TOOL/GENERATION spans ARE ingesting (verified: type=TOOL web_search with latency in seconds, traceId matches platform runs). get_dashboard fetches Langfuse type=TOOL observations (best-effort, paginated, 5-page cap) and keeps only those whose traceId is in THIS dashboard's AgentRun population (team+env+window) — solves the no-team-filter blocker without a per-trace fetch; aggregates count + avg latency by tool name (ToolCallStat). ObservabilityDashboardPage renders a Tool Calls panel (freq bar + Nx + avg latency). Graceful []-on-error. (#27) Catalog source-version label — the published label is a per-artifact publish counter (v1,v2) decoupled from the source agent version (v16), which confused publishing. CatalogVersionResponse now carries source_version_number (get_catalog_detail joins AgentVersion via source_version_id); the Versions tab shows "v2 (from agent v16)". Read-only, no schema/migration. Tests: ObservabilityDashboardPage.test.tsx tool-call panel; full vitest 171 green. No agent redeploy.)
#   - registry-api:0.2.148 / studio:0.1.122 (Credential integrity — two related fixes reconciled from parallel agents: (TODO #17) a tool-test HTTP-error string ("Client error '403 Forbidden' for url…") could be saved verbatim as a credential VALUE (AuthConfigCreate.credentials was an unconstrained dict → encrypted → written to the K8s Secret → agent mounted garbage → perpetual 403). Fix at the schema boundary (schemas.validate_credential_values, model_validator on AuthConfigCreate/Update): reject empty/whitespace, HTTP-error fingerprints, oversized, and multi-line inline values (mTLS PEM stays allowed); CredentialsPage save-handler blocks client-side + parses pydantic 422s. (TODO #18) the credential Key Name was free-text with no tool linkage, so users typed names the tool never reads AND hyphenated names (invalid env vars) were silently dropped by K8s envFrom. Fix: the key is now driven by the selected tool's {{placeholder}} in its HTTP header template (placeholder = pod env var = credential key; surfaced Tool.http_headers to the form, no new backend field), auto-filled + locked, with env-var-name validation. Backend guard added to the SAME validator: reject any credential KEY that isn't a valid env-var name (API/seed bypass the UI). Tests: CredentialsPage.test.tsx 5 cases (tool-driven key auto-fill/lock, hyphen rejected, value-guard); suite-51 6 cases (valid persists real value; 403-string→422; empty/oversized/multiline→422; mTLS PEM ok; PUT preserves original; bad KEY→422). Full vitest 170 green. No migration, no agent redeploy.)
#   - registry-api:0.2.147 / studio:0.1.121 (Two fixes: (A) Env-scoped observability dashboards — sandbox agents are experimental and must NOT dilute production metrics, so the dashboard is now split into SEPARATE Production and Sandbox views (routes /observability/dashboard/{production,sandbox}, two sidebar entries, legacy /dashboard redirects to production). get_dashboard takes environment=production|sandbox and filters EVERY panel (AgentRun.production_deployment_id vs sandbox_deployment_id; feedback via PlaygroundRun.sandbox). Supersedes the in-panel feedback split from 0.2.146 (FeedbackBreakdown reverted to a single env-scoped FeedbackSummary). suite-48 asserts prod/sandbox never blend; ObservabilityDashboardPage.test.tsx (prod + sandbox views). (B) Version dedup on deploy — deploy_agent's auto-version path bumped version_number on EVERY deploy without comparing the snapshot, so no-op redeploys created byte-identical duplicate versions (serper-agent-4 had 16 versions, 14 no-change dups). Fix: reuse the latest version when the canonical config+tools snapshot (tools sorted for stable compare) is unchanged, all environments; only mint a new version on a real change. suite-50 (deploy x2 unchanged=1 version; change=2). Existing junk versions left as-is by decision. No migration, no agent redeploy.)
#   - registry-api:0.2.146 / studio:0.1.120 (Observability — production user-feedback + dashboard split: user feedback (thumbs) existed ONLY in the playground ChatPane, so the dashboard Satisfaction panel reflected sandbox/eval feedback, not production — backwards from where it matters. Fix: (1) dashboard feedback is now SPLIT by environment (DashboardData.feedback: FeedbackBreakdown{production, sandbox}); get_dashboard groups PlaygroundRun.user_feedback by PlaygroundRun.sandbox (False=production consumer chat, True=playground/sandbox); the ObservabilityDashboardPage leads with a "Prod Satisfaction" card + a User Feedback panel showing Production (prominent) and Sandbox rows. (2) production feedback capture: CatalogChatPage (Marketplace consumer chat, context=production) now renders a 👍/👎 control under each completed assistant turn, wired to that turn's run_id via the SSE done event + submitRunFeedback (backend already accepted it — production chats create a PlaygroundRun with sandbox=False). Tests: suite-48 asserts the split (prod 2up vs sandbox 2up/1down); ObservabilityDashboardPage.test.tsx (prod-leads + prod-empty); CatalogChatPage.test.tsx (thumbs appears on done + submits + locks). Deferred (optional): AgentChatPage is a sandbox surface (context=playground/startDeploymentChat) — wiring it would fill only the sandbox bucket; not required for the production ask. No migration, no agent redeploy.)
#   - registry-api:0.2.145 (Observability M5 follow-up — judge production chats so the catalog Score column is populated: the M5 Score column reads AgentRun.judge_score, but the judge's _write_score only ever patched PlaygroundRun, so production runs always showed "—". Fix: (1) _write_score now follows langfuse_trace_id and also patches every AgentRun sharing that trace (score follows the run across both tables; AgentRun has no judge_status/reason cols so only the score is set); (2) chat.py _complete_chat_run fires the same fire-and-forget LLM-as-judge that playground uses, sourcing input/agent_name from the PlaygroundRun and the agent-owning team from the AgentRun. Costs +1 LLM call per production chat turn — an explicit product decision (user chose "judge production chats"). Deterministic coverage: suite-49 (_write_score dual-table write); full score_run→LLM path exercised by a real chat. No migration, no agent redeploy.)
#   - registry-api:0.2.144 / studio:0.1.119 (Observability Phase 4 — M5 production run columns + M6 trace-compare score delta: (M5) GET /catalog/{artifact_id}/runs now carries judge_score on AgentRunResponse (schema field; the endpoint already selects AgentRun which has the column) and the CatalogDetailPage Runs tab shows User (run_by/user_id) + Score columns the M5 doc specified. (M6) ObservabilityComparePage reads the judge score off each fetched Langfuse trace's scores[] (name~judge) and renders a Judge Score (A->B) + Score Delta card in the summary bar. Tests: ObservabilityComparePage.test.tsx (delta + missing-score); CatalogRun type carries user_id/judge_score. Frontend + one schema field; no migration, no agent redeploy.)
#   - registry-api:0.2.143 / studio:0.1.118 (Observability Phase 3a — user-feedback ratio dashboard panel: thumbs feedback (POST /playground/runs/{id}/feedback) was pushed ONLY to Langfuse as a score, so the M2 dashboard could not show a satisfaction ratio without a live Langfuse call. Added playground_runs.user_feedback SMALLINT (migration 0057, model) written in submit_run_feedback alongside the Langfuse push; GET /observability/dashboard now aggregates up/down/ratio from PlaygroundRun joined to Agent(team) within the window (DashboardData.feedback: FeedbackSummary); ObservabilityDashboardPage renders a Satisfaction metric card + a User Feedback panel. Phase 3b (tool-call frequency/latency) DEFERRED to the gap ledger — live Langfuse observations API returns only unclosed safety_scan spans (no tool/GENERATION spans ingesting yet) and has no team filter, so a panel now would be empty or globally mis-scoped.)
#   - declarative-runner:0.1.35 (Observability Phase 2 follow-up: (a) cross-namespace LANGFUSE_HOST — deploy-controller injected a namespace-bare host (agentshield-langfuse-web:3000) into agent pods that run in agents-* namespaces where it does NOT resolve, so span ingestion (langchain handler POSTs there) AND the /ready langfuse check both failed; now injects the FQN {release}-langfuse-web.{namespace}:3000. (b) langfuse is observability not a serving dependency — it no longer gates /ready (a trace-backend blip must not make agents un-servable); enabling the langfuse env vars had flipped the readiness check from "disabled" to "unreachable"→503. Helm-template + main.py/server.py change.)
#   - declarative-runner:0.1.34 (Observability Phase 2 — LLM/tool span capture: agent pods ran langfuse 4.14 (SDK pinned langfuse>=3.0) but the tracing code is written for the v2 API — Langfuse.trace() is gone in v4 and langfuse.callback moved to langfuse.langchain, so _make_langfuse_handler hit its bare except on EVERY call and the langchain CallbackHandler that captures LLM/tool generation spans was NEVER created. Plus base `langchain` was never installed. Plus the SDK Tracer read AGENTSHIELD_LANGFUSE_KEY/HOST which nothing sets (deploy-controller injects LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST) so tracer._enabled was always False. Fix (align DOWN to v2, matching registry-api's langfuse==2.*): pin SDK langfuse>=2.60,<3, add base `langchain`, fix env-var names to LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST in SDK+declarative config/server/main, pass public_key to the SDK Langfuse client. _make_langfuse_handler is UNCHANGED. PARTIAL RESULT: the env-var/public_key fix enables the SDK's v2 tracer so safety_scan_* spans now appear (0→1 observations verified), BUT LLM/tool generation spans remain BLOCKED — the agent's langchain stack is 1.x (langchain 1.3.13/langgraph 1.2.9) and langfuse v2's callback hard-imports the removed langchain.callbacks.base, so the langchain handler can't load. Agent-side LLM/tool spans REQUIRE langfuse v4 (docs/design/todo/observability-provider-abstraction.md — reclassified from deferred to required). REQUIRES redeploying agents to pick up the new declarative-runner image.)
#   - registry-api:0.2.141 (Observability Phase 1 — Langfuse trace-creation gap on deployment-pinned chat: start_deployment_chat created PlaygroundRun/AgentRun rows but never called trace_create_run, so deployment-pinned chats (the primary "Chat" button on DeploymentOverviewPage) had an empty Trace column and a trace that was never opened. Fix: extract shared _create_traced_chat_run helper used by BOTH start_chat and start_deployment_chat (single source of truth so the two paths can't drift again); stream_deployment_chat + resume_stream_chat now propagate run.langfuse_trace_id into _proxy_agent_stream (X-AgentShield-Trace-ID header) AND _complete_chat_run (both previously hardcoded None). Gap 2: trace user_id now the readable preferred_username, not the sub UUID (DB FK cols keep the UUID). Gap 3: trace tagged with deployment_id + environment so instances of the same agent are distinguishable. Also fixes a latent bug: start_chat hardcoded context="production" on the trace even for playground chats. NOTE: span-level LLM/tool observations still absent until Phase 2 (langfuse v4 standardization) — this phase fixes trace creation/propagation only.)
#   - registry-api:0.2.140 / studio:0.1.116 (Chat deployment-pinning fix — wrong-deployment routing: consumer chat re-resolved "most recent running" deployment at STREAM time (stream_chat + resume_stream_chat) instead of the deployment the run was pinned to at POST time, so a redeploy or a 2nd running deployment routed an in-flight chat (and HITL resume, whose thread checkpoint lives on the original pod) to the WRONG pod. Fix: _deployment_for_run resolves the pod from the id stored on the run (production_deployment_id / deployment_id) — never re-resolves; stream_deployment_chat now rejects a path dep_id that doesn't match the run (cross-agent guard); start_chat honors an optional deployment_id so a chat launched from a specific fleet row pins to exactly that deployment (Studio DeploymentsPage passes ?dep=, CatalogChatPage forwards it). Also: the DeploymentOverviewPage "API Endpoint" card now renders the deployment-pinned path (/agents/{name}/deployments/{depId}/chat) for sandbox deployments instead of the agent-scoped path — production stays agent-scoped (stable public contract, one prod pod). Prod stays one k8s Service per agent by design (rolling, not parallel pods) — NOT changed. No agent redeploy.)
#   - registry-api:0.2.139 / declarative-runner:0.1.33 (Production auto-grant ApprovalAuthority parity + HITL observability: (a) high-risk-tool auto-grant to all team members ran ONLY on the sandbox deploy path (deployments.py); production deploys (publish→catalog) never granted it, so production team members had no authority to see/approve HITL requests (interim behavior until RBAC, agreed same as sandbox). Generalized _auto_grant_approval_authority to take (name,risk) pairs (source-agnostic — ORM tools sandbox, config_snapshot dicts production) + call from catalog production-deploy. (b) declarative-runner enables INFO logging so HITL success lines show in the pod log; hitl.require_approval logs the full failure body at ERROR + FAIL-CLOSED (deny, not hang) when the approval record can't be created — fixes the silent-swallow that made doc 009 hard to debug.)
#   - registry-api:0.2.138 / deploy-controller:0.1.34 (Production HITL approval-record fix: production pods shipped with AGENTSHIELD_AGENT_ID empty because production_reconciler._build_agent_dict omitted the source agent id — so the SDK's approval POST sent agent_id="" → 422 uuid_parsing → the approval row was never created (SDK swallows the error and interrupts anyway), so the tool-approval prompt showed in chat but NOTHING appeared in the Production HITL Queue and the chat hung. Fix: catalog internal endpoint passes source_agent_id (= artifact.source_id = agents.id); _build_agent_dict sets id so AGENTSHIELD_AGENT_ID is populated. Same parity class (production synthesized-agent-dict missing a field the sandbox live-agent record provides). Re-reconcile production to rebuild the pod env.)
#   - registry-api:0.2.137 / deploy-controller:0.1.33 (Production OPA-identity governance parity — the real class fix: production agent pods were never registered as machine identities AND agent_identities.deployment_id FKs the sandbox `deployments` table only + bundle_generator INNER-JOINs deployments only, so production SA subjects could never enter the OPA bundle → every production tool call fails closed as agent_unauthenticated ("authentication issue with the search tool") and HITL/governance is non-functional in production. Fix: (1) agent_identities.production_deployment_id column FK production_deployments (migration 0055); (2) /identities endpoint+schema accept it; (3) shared identity.register_agent_identity() called by BOTH reconcilers (sandbox writes deployment_id, production writes production_deployment_id) — mirrors the tool_secrets shared-helper anti-drift pattern; (4) bundle_generator UNIONs a production leg (published_versions.config_snapshot->'tools') so production identities enter data.agents. Also fixes the 0.1.32 NameError (missing tool_secrets import in production_reconciler). New design doc docs/design/sandbox-production-parity-architecture.md. Recovery: re-reconcile running production deployments.)
#   - deploy-controller:0.1.32 (Production tool-credential parity: the production reconciler never resolved/copied tool-credential secrets (Serper key, etc.) into the production namespace or set envFrom — only the sandbox reconciler did — so every external-API tool call in a production pod 401'd ("authentication issue with the search tool"). Extracted the resolve+copy logic into shared tool_secrets.resolve_and_copy_tool_secrets() called by BOTH reconcilers so they can't drift again; production_reconciler now passes tool_secret_refs to build_deployment. Workflow-production member-tool creds remain a separate gap (affects sandbox too). No SDK/runner change.)
#   - registry-api:0.2.136 (Production consumer chat fix: PlaygroundRun.deployment_id FKs the sandbox `deployments` table, but production chat targets a `production_deployments` row (different table) — start_chat stuffed that id into deployment_id → FK violation on INSERT → 500 → "no running production deployment" in CatalogChatPage. Mirror AgentRun: new `production_deployment_id` column (FK production_deployments, migration 0054); start_chat writes the column whose FK the id satisfies (sandbox→deployment_id, prod→production_deployment_id); _load_provenance LEFT JOINs production_deployments + coalesces env='production'/namespace so the HITL console shows prod provenance. No agent redeploy.)
#   - studio:0.1.115 (Production consumer chat auto-resume: CatalogChatPage required a manual "Check & Resume" click after a HITL approval — the deployment-chat page (AgentChatPage) already auto-polled the console and resumed. Added the same auto-poll (getChatApprovalStatus every 3s → auto connectResumeStream on decided) to CatalogChatPage so the production consumer chat resumes automatically once a reviewer approves; the button is now an optional "Resume now" override.)
#   - studio:0.1.114 (Publish adversarial-eval gate producer: agents with a high/critical-risk tool require adversarial_eval_passed to publish, but nothing ever set it (no UI producer) so publishing any risky agent 422'd 'adversarial_eval_not_passed' with no way forward. Adds an explicit "Mark Adversarial Passed" button in the Playground promote panel (PATCH adversarial_eval_passed=true) — kept separate from the ordinary eval mark so the red-team sign-off stays visible. registryApi.patchVersion + AgentVersion type carry adversarial_eval_passed. Studio-only, no agent redeploy.)
#   - studio:0.1.112 (Sandbox approval panel shows ONLY the current approval (match live event approval_id) — not the session-scoped pending list, which piled up benign pending "orphan" rows left by the tool-node re-run on resume; ChatPane dedupes tool chips by tool_call_id. Studio-only, no agent redeploy.)
#   - registry-api:0.2.135 / studio:0.1.111 / declarative-runner:0.1.31 (Multi-tool HITL: provider-agnostic post_model_hook trims a turn to ONE high-risk tool call only when 2+ are high-risk (no concurrent-interrupt collision / duplicate execution); idempotent create_approval (no phantom duplicate on node re-run); resume chaining — resume proxies forward approval_requested + AgentChatPage/ChatPane handle re-interrupt during resume (ref + nonce); Evaluate-tab HitlPanel shows WHO. Needs agent redeploy.)
#   - registry-api:0.2.134 / studio:0.1.110 / declarative-runner:0.1.29 (HITL approval context WHO/WHY/WHAT: SDK captures LLM reasoning via InjectedState on governed_tool + system-prompt nudge; migration 0053 approvals.reasoning; reasoning threaded hitl.py→approval record + approval_requested SSE; session_approvals adds reasoning + requester username/team; ConversationApprovalPanel/HitlPanel/HITLDashboard render who/why/what. Needs agent redeploy.)
#   - registry-api:0.2.133 / studio:0.1.109 / declarative-runner:0.1.28 (Sandbox HITL 3 cases: (1) registry-derived approval context — sandbox deployment approvals routed to 'sandbox' out of the prod queue, migration 0052 playground_runs session_id + requester username/team, deployment_id on start_chat, session-approvals endpoint; studio env-aware AgentChatPage + ConversationApprovalPanel self-approve + HITL console username/team; (3) batch-eval auto-approve — registry sets x-agentshield-auto-approve only for eval-runner identity, runner threads it, SDK governed_tool skips the HITL interrupt gated on a trusted identity (defense-in-depth). Case 3 needs an agent redeploy.)
#   - eval-runner:0.1.11   (E-3 scheduled eval SHIPS: 9f6603a added `_run_scheduled_item` to eval-runner
#                           but did NOT bump the tag off 0.1.10 (set by 6d93401/E-2). With
#                           imagePullPolicy=IfNotPresent the node kept the cached E-2 image, so every
#                           MODE=scheduled Job silently fell through to the generic reactive path:
#                           response-only dims, no trigger_payload, no fail-closed. E-3's code had never
#                           once executed. suite-75 caught it (6/6 FAIL). NEVER reuse a tag — a code
#                           change without a bump does not reach the cluster.)
#   - registry-api:0.2.132 (OPA bundle cold-start fix: bundle_generator includes 'deploying' deployments (not just 'running') so a new agent's identity is in the OPA bundle immediately; OPA sidecar poll delays lowered 30/60→5/15s)
#   - registry-api:0.2.131 (Deployment-chat HITL: migration 0051 playground_runs.deployment_id; GET /agents/{n}/chat/{run}/approval-status (requester-scoped poll); list_approvals provenance enrichment (requested_by/deployment_name/environment via thread_id→run join))
#   - studio:0.1.108       (Deployment-chat HITL: AgentChatPage waiting-banner + poll + auto-resume (no inline approve/deny); HITL console requested_by + deployment/env columns)
#   - registry-api:0.2.105 (Slice 4: RBAC foundation — rbac.py module, migration 0044 artifact_role_grants, creator auto-grant, /me enrichment, role normalization)
#   - studio:0.1.85        (Slice 4: RequireRole guard, isAtLeast() in AuthContext, admin sidebar gated, route-level platform-admin guards)
#   - registry-api:0.2.104 (Slices 2+3: delete-version cascade (409 on published), workflow artifact page endpoints, WorkflowMiniGraph topology data)
#   - studio:0.1.84        (Slices 2+3: agent versions table with deploy/delete, WorkflowDetailPage, WorkflowMiniGraph SVG, workflow deployment overview topology)
#   - registry-api:0.2.103 (Slice 1b: workflow_versions + workflow_deployments tables (migrations 0042/0043); workflow version snapshot + deploy + lifecycle + stats/runs endpoints; agent_runs.workflow_deployment_id FK)
#   - studio:0.1.83        (Slice 1b: WorkflowDeploymentOverviewPage + /workflows/:id/d/:depId route; workflow version/deployment API client functions)
#   - registry-api:0.2.102 (Slice 1a: sandbox deployment lifecycle — migration 0041 (status enum +suspending/suspended/terminating, +suspended_at, +ttl_hours); PATCH /agents/{name}/deployments/{id} suspend/resume/terminate/upgrade; AgentResponse.latest_version_number)
#   - studio:0.1.82        (Slice 1a: DeploymentActions (state-based lifecycle) on Overview + Deployments tab; DeployModal (replicas+TTL); agent list name-link + version col + deploy modal)
#   - deploy-controller:0.1.23 (Slice 1a: sandbox suspend→scale0→suspended / terminate→delete→terminated handling in poll loop)
#   - registry-api:0.2.101 (Slice 1 deployment overview: deployments.name + agent_runs.sandbox_deployment_id (0040); GET /deployments/{id}/stats+/runs; AgentRunCreate accepts *_deployment_id)
#   - studio:0.1.81        (Slice 1: DeploymentOverviewPage + /agents/:name/d/:depId; artifact page = deployments/versions/settings; Overview*/RunsTab deployment-scoped)
#   - registry-api:0.2.84  (Production artifact isolation: catalog API, production_deployments, run isolation via production_deployment_id)
#   - studio:0.1.66        (CatalogDetailPage: versions/deployments/runs tabs, deploy/upgrade/suspend/resume actions)
#   - deploy-controller:0.1.13 (Production reconciler: poll catalog internal API, reconcile production pods from config_snapshot)
#   - registry-api:0.2.82  (Workflow publish endpoint + run_by/langfuse_trace_id for internal runs + trace_url in agent-runs response)
#   - studio:0.1.64        (Workflow publish button, grant form published-only filter + workflow support, Catalog uses CompositeWorkflows, Runs trace external link)
#   - registry-api:0.2.80  (Fix publish flow: auto-resolve agent_version_id in eval-run creation + pass AGENT_VERSION_ID to eval Job)
#   - studio:0.1.62        (Publish flow guard + runs/stats display: expandable rows, input preview, honest cost card)
#   - registry-api:0.2.75  (Fix Langfuse trace URL: use full /project/{pid}/traces/{tid} path to avoid redirect losing /langfuse/ prefix behind Gateway)
#   - registry-api:0.2.73  (Eval results publish lifecycle: expected_output column, langfuse_trace_id in schema, trace-by-id endpoint, admin publish eval evidence)
#   - studio:0.1.58        (Eval results UX: expandable rows, failed filter, score colors, action CTAs, TraceDrawer, publish eval gate, admin eval column, datasets eval runs)
#   - eval-runner:0.1.4    (Eval-mode LLM judge: sync POST /judge + markdown-strip keyword fallback)
#   - eval-runner:0.1.2    (Include expected_output in result POST)
#   - registry-api:0.2.72  (Batch eval fixes: judge Bedrock support via boto3, fix decrypt_json import, evalRunnerImage in values.yaml, save_run_to_dataset expected_output)
#   - registry-api:0.2.64  (Pausable workflow-HITL: migration 0032 agent_runs.orchestrator_state JSONB checkpoint; workflow_orchestrator per-child thread_id + authoritative pending-Approval pause detection (halts all 4 modes at awaiting_approval); resume_orchestration re-entry (sequential auto-advance); decide_approval _resume_and_advance workflow hook)
#   - deploy-controller:0.1.8  (inject AGENTSHIELD_OPA_URL=http://localhost:8181 into agent pods so SDK exits DEV_MODE and consults real OPA — fixes global mock-allow governance bypass)
#   - studio:0.1.48        (awaiting_approval amber badge in workflow run tree + RunsTab status filter option)
#   - registry-api:0.2.63  (Decision 24 impl#3: composable agent filter (?composable=true); workflow-level trigger CRUD (/workflows/{id}/triggers); production HITL resume (PATCH /approvals fires agent pod /resume); migration 0031 agent_events.workflow_id)
#   - studio:0.1.47        (Decision 24 impl#3: AddAgentModal composable filter + reactive/durable inline toggle; WorkflowTriggersPanel + Triggers button; execution_shape in workflow Save modal)
#   - scheduler:0.1.1      (fires workflow schedule triggers via UNION over agent+workflow trigger rows)
#   - event-gateway:0.1.1  (POST /hooks/workflow/{name}/{token} fires workflow webhook triggers)
#   - registry-api:0.2.62  (Per-schedule input_payload on agent_triggers (migration 0030); internal.py resolves scheduled input from the trigger; type-aware instruction templates support)
#   - studio:0.1.46        (Type-aware create-wizard instruction templates (scheduled/event-driven) + JSON input-payload field in wizard & Settings new-schedule form)
#   - registry-api:0.2.59  (Decision 22: composite workflows — rename agent_graphs, workflow members, run-tree orchestration, trigger_type=workflow)
#   - studio:0.1.43        (Decision 22: agent-graphs rename + composite workflow builder — add existing agents)
#   - declarative-runner:0.1.7 (Decision 22: WorkflowOrchestrator module + /workflow-run — future-state)
#   - registry-api:0.2.55  (Bug fix: deny-by-default agent/playground-run visibility for anonymous callers)
#   - registry-api:0.2.54  (Phase 9: agent_events + /events + rotate-token; FIX internal.py Deployment.agent_id/deployed_at; FIX AgentEventResponse INET→str coercion)
#   - event-gateway:0.1.0  (Phase 9 NEW: public webhook ingress — token + rate-limit + replay + filter + dispatch)
#   - studio:0.1.42        (Phase 9: OverviewEventDriven + webhook token rotation in Settings)
#   - registry-api:0.2.52  (Phase 8: alert config on triggers + SMTP failure alerts + /health endpoint; create_trigger persists alert fields)
#   - studio:0.1.41        (Phase 8: trigger alert config in Settings + health dots on agent list)
#   - registry-api:0.2.42  (Phase 4: AgentRun production tracking + /stats endpoint)
#   - safety-orchestrator:0.1.3 (per-scanner Langfuse spans + trace_id propagation)
#   - deploy-controller:0.1.7 (Phase 9.1 ensure_service_account wired in)
#   - studio:0.1.37        (Phase 4: AgentDetail tabbed layout + RunsTab + OverviewReactive)
#   - eval-runner:0.1.1    (batch eval 403 fix (service-identity) + Haiku judge poll)
#   - declarative-runner:0.1.4 (Phase 4: production AgentRun creation + completion tracking)
#   - python-executor:0.1.0 (sandboxed Python code runner)
#   - Langfuse:3.x         (LLM observability — auto-bootstrapped, internal to platform)
#   - PostgreSQL, Redis (infra)
#
# Seeded by step 8: 6 tools, 2 skills, 3 workflows, 5 agents
#
# Usage: bash scripts/deploy-cpe2e.sh
set -euo pipefail

RELEASE="agentshield"
CHART="charts/agentshield"
NAMESPACE="agentshield-platform"
TIMEOUT="25m"

# ── Credentials (dev defaults — change in production) ─────────────────────────
PG_PASS="DevPass2024"
REDIS_PASS="RedisPass2024"
MINIO_USER="agentshield-admin"
MINIO_PASS="MinioPass2024"
KC_ADMIN_PASS="AdminPass2024"
KC_PLATFORM_ADMIN_PASS="PlatformAdmin2024"
KC_REVIEWER_PASS="Reviewer2024"
# Fernet key for LLM credential encryption (32-byte base64 URL-safe)
ENCRYPTION_KEY="dGVzdGtleS10ZXN0a2V5LXRlc3RrZXktdGVzdGtleTA="

# ── Image tags ────────────────────────────────────────────────────────────────
# E-4 (phases 1-4, registry-api only): webhook eval — the filter decision as a
# first-class dimension + injection robustness (ASR vs utility reported separately).
#   * D2 — ONE run door: `test-event` no longer hand-builds a SECOND PlaygroundRun.
#     `_create_and_dispatch_playground_run` is now the single builder (1 def, 2 call
#     sites), which closes three live defects at once: test-event now threads
#     `eval_mode` (it defaulted to 'live', so a matched webhook eval would have
#     DELIVERED REAL SIDE EFFECTS), now DISPATCHES durable runs (they hung at
#     'running' forever), and now carries the Langfuse trace + agent_version_id.
#   * launch guard opens for mode='webhook' (was a hard 422 "not implemented yet")
#   * /eval/score mode='webhook' branch (was 501): score_filter + score_injection
#     (both pure code), action dims reused verbatim from E-0/E-1/E-2, plus a SAFETY
#     VETO — an exact filter error or a really-fired forbidden tool cannot be
#     out-voted by a weighted mean (both otherwise composited ABOVE the publish gate).
#   * NO new filter code — the parity-gated filter_engine is the only decider.
#
# E-4 P5 (eval-runner 0.1.12): the webhook eval branch — `MODE=webhook` fires each
# item's synthetic `trigger_payload` at the REAL test-event door, scores the REAL
# filter decision it returns, and only on a MATCH drives + scores the action (durable
# -inner → poll + project run_steps; reactive-inner → drive the stream) under E-2's
# record seam. A correct MISS creates NO run (run_id IS NULL — the evidence nothing
# ran). Writes `eval_run_results.matched`, orphaned since E-0 (no writer, no reader).
#   * FAIL-CLOSED DISPATCH (the load-bearing fix): `run_eval` dispatched by PRIORITY
#     FALLTHROUGH, so any MODE without a branch dropped through to the reactive tail —
#     no `eval_mode` (⇒ 'live' ⇒ REAL SIDE EFFECTS DELIVERED), no filter, and a
#     plausible `{"response": x}` PASS for an eval that tested nothing. CP1a opening
#     the guard for `webhook` one phase before this branch existed made that live and
#     reachable by API. Dispatch is now an explicit mode→handler MAP: `reactive` is a
#     registered handler, not the default tail, and an unhandled MODE resolves to None
#     ⇒ every item recorded FAILED with no run created. A missing branch is now a loud
#     error by construction instead of a fake green (proven by T-S77-010).
#
# E-4 P5b (registry-api 0.2.189): test-event now feeds a matched event the IDENTICAL
# production shape — `input_payload=payload` + the driving turn derived with
# internal.py's own line (`payload.get("message") or json.dumps(payload)`). The durable
# dispatch body carries ONLY `input_payload`, so passing None (as the D2 rewire did)
# dispatched `{}` and the agent answered "I have not been provided with any event
# payload": a REAL matched run, really scored, that never saw the event. Invisible
# until D2 made this door dispatch durable at all, and caught by suite-77's POSITIVE
# CONTROL — the exact failure a filter-miss-only test cannot see.
#
# E-2 (phases 1-3): side-effect record/mock seam. Migration 0063 adds
# `tools.side_effecting` (fail-closed backfill: only HTTP GET/HEAD is read-only) +
# `playground_runs.eval_mode` (PERSISTED — a durable HITL resume re-drives the graph
# and must re-cross the delivery edge in the same mode). `eval_mode` threads
# run-create → dispatch JSON body → runner `_current_eval_mode` ContextVar → the ONE
# governed-tool delivery edge (graph_builder step 3): under `record`, a side-effecting
# tool is recorded + answered with a mock sentinel and NOT invoked; OPA + HITL run
# unchanged. **declarative-runner MUST be rebuilt** — the seam lives in
# sdk/agentshield_sdk/ which is pip-bundled into the runner image.
REGISTRY_API_TAG="0.2.191"
SAFETY_ORCHESTRATOR_TAG="0.1.3"
DEPLOY_CONTROLLER_TAG="0.1.36"
STUDIO_TAG="0.1.143"
EVAL_RUNNER_TAG="0.1.12"
DECLARATIVE_RUNNER_TAG="0.1.49"
PYTHON_EXECUTOR_TAG="0.1.0"
SCHEDULER_TAG="0.1.1"
EVENT_GATEWAY_TAG="0.1.3"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> AgentShield CPE2E Deploy — $(date)"
echo ""

# ── Step 0: Pre-deploy backup (best-effort) ──────────────────────────────────
# If Postgres is already running, snapshot it before we touch anything.
PG_POD=$(kubectl get pod -n agentshield-platform -l app.kubernetes.io/name=postgresql \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$PG_POD" ]; then
  echo "[0/8] Pre-deploy Postgres backup..."
  bash "${REPO_ROOT}/scripts/backup-postgres.sh" || echo "  ⚠ backup failed (non-fatal)"
  echo ""
fi

# ── Step 1: Build images ──────────────────────────────────────────────────────
# IMPORTANT: Always use `helm upgrade` to deploy registry-api changes.
# `kubectl set image` only updates the main container — the alembic-migrate
# init container stays pinned to the old Helm-rendered tag, skipping new migrations.
# If you must use kubectl, update BOTH containers:
#   kubectl set image deployment/agentshield-registry-api \
#     alembic-migrate=registry.internal/agentshield/registry-api:$REGISTRY_API_TAG \
#     registry-api=registry.internal/agentshield/registry-api:$REGISTRY_API_TAG \
#     -n agentshield-platform

# Pre-build gate: `filter_engine.py` is duplicated in event-gateway (the real webhook
# hop) and registry-api (`/playground/test-event`, the door an E-4 webhook eval scores
# the filter through). They MUST be byte-identical — the gateway's ReDoS hardening once
# went un-back-ported for months, leaving registry-api running an unbounded regex and any
# webhook eval grading a decision production never makes. Gating BEFORE the build makes
# divergent engines undeployable rather than merely detectable. Fails loudly with a diff.
bash "$(dirname "${BASH_SOURCE[0]}")/check-filter-engine-parity.sh"

echo "[1/8] Building images..."
echo "  → registry-api:${REGISTRY_API_TAG} (E-4 P1-P4: webhook eval — D2 ONE run door (test-event stops hand-building a 2nd PlaygroundRun: now threads eval_mode + dispatches durable + traces), launch guard opens for mode=webhook, /eval/score webhook branch with score_filter + score_injection and a safety veto on filter errors / fired forbidden tools)"
docker build -t "registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}" services/registry-api/

echo "  → safety-orchestrator:${SAFETY_ORCHESTRATOR_TAG} (per-scanner Langfuse spans, trace_id propagation)"
docker build -t "registry.internal/agentshield/safety-orchestrator:${SAFETY_ORCHESTRATOR_TAG}" services/safety-orchestrator/

echo "  → deploy-controller:${DEPLOY_CONTROLLER_TAG} (OPA readiness probe: /health?bundles gate)"
docker build -t "registry.internal/agentshield/deploy-controller:${DEPLOY_CONTROLLER_TAG}" services/deploy-controller/

echo "  → declarative-runner:${DECLARATIVE_RUNNER_TAG} (fix HITL resume: Command(resume=) + streaming resume endpoint)"
docker build -t "registry.internal/agentshield/declarative-runner:${DECLARATIVE_RUNNER_TAG}" -f services/declarative-runner/Dockerfile .

echo "  → studio:${STUDIO_TAG} (WS-3 scheduled operate surface: OverviewScheduled now renders next-fire + rolled-up schedule-health badge from getAgentHealth, plus an alert-config summary (alert_email/alert_on_failure) from the trigger)"
docker build -t "registry.internal/agentshield/studio:${STUDIO_TAG}" studio/

echo "  → eval-runner:${EVAL_RUNNER_TAG} (NEW — batch eval K8s Job image)"
docker build -t "registry.internal/agentshield/eval-runner:${EVAL_RUNNER_TAG}" services/eval-runner/

echo "  → python-executor:${PYTHON_EXECUTOR_TAG} (new — sandboxed Python tool runner)"
docker build -t "registry.internal/agentshield/python-executor:${PYTHON_EXECUTOR_TAG}" services/python-executor/

echo "  → scheduler:${SCHEDULER_TAG} (Phase 7 — fires scheduled agents on cron, HA)"
docker build -t "registry.internal/agentshield/scheduler:${SCHEDULER_TAG}" services/scheduler/

echo "  → event-gateway:${EVENT_GATEWAY_TAG} (WS-4: ONE shared verify_webhook_auth wrapping BOTH hooks — per-application client-id + allowlist + HMAC signing; uniform-401 enumeration oracle CLOSED (stale-ts had its own body); +cryptography +AGENTSHIELD_ENCRYPTION_KEY so it can decrypt the client secret)"
docker build -t "registry.internal/agentshield/event-gateway:${EVENT_GATEWAY_TAG}" services/event-gateway/

# ── Step 2: Namespaces ────────────────────────────────────────────────────────
echo ""
echo "[2/8] Applying namespaces..."
kubectl apply -f infra/namespaces/agentshield-platform.yaml
kubectl apply -f infra/namespaces/agents-platform.yaml
kubectl apply -f infra/namespaces/agentshield-playground.yaml
kubectl apply -f infra/rbac/playground-runner-clusterrole.yaml

# ── Step 3: Secrets (all required by chart templates) ─────────────────────────
echo ""
echo "[3/8] Creating secrets..."

# Core platform secrets consumed by registry-api init containers + deployment
kubectl create secret generic agentshield-secrets \
  -n "$NAMESPACE" \
  --from-literal=registry-api-url="http://agentshield-registry-api.${NAMESPACE}:8000" \
  --from-literal=database-url="postgresql://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --from-literal=direct-database-url="postgresql://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --dry-run=client -o yaml | kubectl apply -f -

# Encryption key for LLM provider credentials
# Template expects key named "key"
kubectl create secret generic agentshield-encryption \
  -n "$NAMESPACE" \
  --from-literal=key="${ENCRYPTION_KEY}" \
  --from-literal=AGENTSHIELD_ENCRYPTION_KEY="${ENCRYPTION_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

# PostgreSQL passwords (Bitnami existingSecret pattern)
kubectl create secret generic postgres-passwords \
  -n "$NAMESPACE" \
  --from-literal=keycloak="${PG_PASS}" \
  --from-literal=agentshield="${PG_PASS}" \
  --from-literal=langfuse="${PG_PASS}" \
  --from-literal=langgraph="${PG_PASS}" \
  --from-literal=appsmith="${PG_PASS}" \
  --from-literal=registry-api-url="postgresql+asyncpg://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --from-literal=registry-api-direct-url="postgresql+asyncpg://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --dry-run=client -o yaml | kubectl apply -f -

# Redis password (Bitnami existingSecret)
kubectl create secret generic redis-password \
  -n "$NAMESPACE" \
  --from-literal=redis-password="${REDIS_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# MinIO root credentials (used by keycloak-raw.yaml + minio-raw.yaml templates)
kubectl create secret generic minio-credentials \
  -n "$NAMESPACE" \
  --from-literal=root-user="${MINIO_USER}" \
  --from-literal=root-password="${MINIO_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Keycloak admin credentials (keycloak-raw.yaml)
kubectl create secret generic keycloak-admin-password \
  -n "$NAMESPACE" \
  --from-literal=admin-password="${KC_ADMIN_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Keycloak realm user passwords
kubectl create secret generic keycloak-user-passwords \
  -n "$NAMESPACE" \
  --from-literal=platform-admin="${KC_PLATFORM_ADMIN_PASS}" \
  --from-literal=agent-reviewer="${KC_REVIEWER_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Langfuse tracing keys + NextAuth/encryption secrets
# public-key/secret-key are used to auto-bootstrap the AgentShield project on first boot
# and are shared with registry-api/safety-orchestrator for SDK tracing.
LANGFUSE_SALT="$(openssl rand -base64 32 2>/dev/null || echo 'agentshield-dev-salt-placeholder-32')"
LANGFUSE_ENC_KEY="$(openssl rand -hex 32 2>/dev/null || echo 'a1b2c3d4e5f6789012345678901234560123456789012345678901234567890b')"
kubectl create secret generic langfuse-api-keys \
  -n "$NAMESPACE" \
  --from-literal=public-key="pk-lf-agentshield-dev-local-0001" \
  --from-literal=secret-key="sk-lf-agentshield-dev-local-0001" \
  --from-literal=nextauth-secret="agentshield-nextauth-dev-2024-sec" \
  --from-literal=salt="${LANGFUSE_SALT}" \
  --from-literal=encryption-key="${LANGFUSE_ENC_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Slack webhook (registry-api reads webhook-url key)
kubectl create secret generic slack-credentials \
  -n "$NAMESPACE" \
  --from-literal=bot-token="xoxb-placeholder-dev-token" \
  --from-literal=signing-secret="placeholder-signing-secret-dev" \
  --from-literal=webhook-url="https://hooks.slack.com/services/placeholder/dev" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "  All secrets applied."

# ── Step 4: Helm dependency update ───────────────────────────────────────────
echo ""
echo "[4/8] Updating Helm dependencies..."
helm dependency update "$CHART" 2>/dev/null || true

# Apply Langfuse-specific infra (ClickHouse + S3 alias Services).
# Bitnami sub-charts name services <release>-{chart} but Langfuse derives
# <release>-langfuse-{chart}. These alias Services bridge that naming gap.
kubectl apply -f infra/langfuse/clickhouse-alias-svc.yaml 2>/dev/null || true

# Apply OPA Bundle Server infra (nginx + bundle-sync sidecar).
# The bundle-sync sidecar polls registry-api /api/v1/bundle every 30s so
# OPA sidecars always have fresh policy + data without ConfigMap patches.
kubectl apply -f infra/opa-bundle-server/configmap-nginx-conf.yaml 2>/dev/null || true
kubectl apply -f infra/opa-bundle-server/service.yaml 2>/dev/null || true
kubectl apply -f infra/opa-bundle-server/deployment.yaml 2>/dev/null || true

# opa-sidecar-config ConfigMap must exist in every agents-* namespace — the
# deploy-controller mounts it into each agent pod's OPA sidecar (bundle polling).
# Without it, agent pods hang in ContainerCreating ("configmap opa-sidecar-config
# not found"). The manifest targets agents-platform; mirror it to other team ns.
kubectl apply -f infra/opa-bundle-server/configmap-opa-config.yaml 2>/dev/null || true
for team_ns in agents-operations; do
  kubectl get ns "$team_ns" >/dev/null 2>&1 && \
    kubectl get configmap opa-sidecar-config -n agents-platform -o yaml 2>/dev/null \
    | sed "s/namespace: agents-platform/namespace: ${team_ns}/" \
    | kubectl apply -f - 2>/dev/null || true
done

# ── Step 5: Helm upgrade ──────────────────────────────────────────────────────
echo ""
echo "[5/8] Helm upgrade/install '${RELEASE}'..."

# Clean up stale realm-init job if it exists (hook fails on re-deploy otherwise)
kubectl delete job "${RELEASE}-realm-init" -n "$NAMESPACE" --ignore-not-found=true

# Image tags, component enable/disable toggles, dev sizing, and global.postgresHost
# are now baked into charts/agentshield/values.yaml as the default composition, so
# a plain `helm upgrade --install` (no --set flags) deploys the full local platform.
# Keep image-tag bumps in sync between this script's build steps and values.yaml.
# To override per-environment, add a -f <values-override>.yaml or --set here.
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

# ── Step 6: Wait for rollouts ─────────────────────────────────────────────────
echo ""
echo "[6/8] Waiting for rollouts..."
kubectl rollout status statefulset/agentshield-postgresql -n "$NAMESPACE" --timeout=5m
kubectl rollout status statefulset/agentshield-redis-master -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout=5m
kubectl rollout status deployment/agentshield-deploy-controller -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-studio -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-python-executor -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-scheduler -n "$NAMESPACE" --timeout=3m || echo "  (Scheduler starting)"
kubectl rollout status deployment/agentshield-langfuse-web -n "$NAMESPACE" --timeout=5m || echo "  (Langfuse web may need DB migrations — check logs if still pending)"
kubectl rollout status deployment/agentshield-langfuse-worker -n "$NAMESPACE" --timeout=3m || echo "  (Langfuse worker starting)"

# Create Keycloak client for Langfuse SSO (idempotent — skips if exists)
echo "  Creating Keycloak client 'langfuse' for SSO..."
kubectl exec -n "$NAMESPACE" deploy/agentshield-registry-api -c registry-api -- python3 -c "
import urllib.request, urllib.parse, json
data = urllib.parse.urlencode({'grant_type':'password','client_id':'admin-cli','username':'admin','password':'AdminPass2024'}).encode()
req = urllib.request.Request('http://agentshield-keycloak/realms/master/protocol/openid-connect/token', data=data)
token = json.loads(urllib.request.urlopen(req).read())['access_token']
client = json.dumps({'clientId':'langfuse','name':'Langfuse','enabled':True,'protocol':'openid-connect','publicClient':False,'secret':'langfuse-client-secret-2024','redirectUris':['https://langfuse.127.0.0.1.nip.io:8443/*'],'webOrigins':['https://langfuse.127.0.0.1.nip.io:8443'],'standardFlowEnabled':True,'directAccessGrantsEnabled':True}).encode()
req2 = urllib.request.Request('http://agentshield-keycloak/admin/realms/agentshield/clients', data=client, headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'})
try:
    urllib.request.urlopen(req2); print('  Created')
except urllib.error.HTTPError as e:
    print('  Already exists (OK)' if e.code==409 else f'  Error: {e.code}')
" 2>/dev/null || echo "  Warning: could not create Langfuse SSO client"

# Create langfuse-media bucket in the Langfuse MinIO (s3) pod.
# MinIO starts with no buckets; Langfuse needs this bucket for event blob storage.
echo "  Creating langfuse-media bucket in MinIO..."
MINIO_POD=$(kubectl get pod -n "$NAMESPACE" --no-headers | grep "agentshield-s3-" | awk '{print $1}' | head -1)
if [ -n "$MINIO_POD" ]; then
  kubectl exec -n "$NAMESPACE" "$MINIO_POD" -- \
    mc alias set local http://localhost:9000 langfuse-admin LangfuseMinio2024 2>/dev/null || true
  kubectl exec -n "$NAMESPACE" "$MINIO_POD" -- \
    mc mb local/langfuse-media 2>/dev/null || true
  echo "  langfuse-media bucket ready."
else
  echo "  Warning: Langfuse MinIO pod not found — create bucket manually."
fi

# ── Step 7: Seed default teams ────────────────────────────────────────────────
echo ""
echo "[7/8] Seeding default teams..."
REGISTRY_URL="http://localhost:8000"
kubectl port-forward svc/agentshield-registry-api -n "$NAMESPACE" 8000:8000 &
PF_PID=$!
sleep 3

for TEAM_NAME in platform operations; do
  NAMESPACE_VAL="agents-${TEAM_NAME}"
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${REGISTRY_URL}/api/v1/teams/" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${TEAM_NAME}\",\"namespace\":\"${NAMESPACE_VAL}\"}")
  if [ "$STATUS" = "201" ]; then
    echo "  Created team: ${TEAM_NAME}"
  elif [ "$STATUS" = "409" ]; then
    echo "  Team already exists: ${TEAM_NAME} (skipped)"
  else
    echo "  Warning: team ${TEAM_NAME} returned HTTP ${STATUS}"
  fi
done

kill $PF_PID 2>/dev/null || true
wait $PF_PID 2>/dev/null || true

# ── Step 8: Seed default resources ───────────────────────────────────────────
echo ""
echo "[8/8] Seeding default resources (tools, skills, agents, workflows)..."
kubectl port-forward svc/agentshield-registry-api -n "$NAMESPACE" 8001:8000 &
PF2_PID=$!
sleep 3

REGISTRY_URL="http://localhost:8001" bash scripts/seed-defaults.sh || true

kill $PF2_PID 2>/dev/null || true
wait $PF2_PID 2>/dev/null || true

echo ""
echo "================================================================"
echo "  AgentShield CPE2E Deploy — COMPLETE"
echo "================================================================"
echo ""
kubectl get pods -n "$NAMESPACE" --no-headers | sort
echo ""

# --- Envoy Gateway status ---
GW_STATUS=$(kubectl get gateway agentshield-gateway -n "$NAMESPACE" -o jsonpath='{.status.conditions[?(@.type=="Programmed")].status}' 2>/dev/null || echo "")
if [ "$GW_STATUS" = "True" ]; then
  GW_ADDR=$(kubectl get gateway agentshield-gateway -n "$NAMESPACE" -o jsonpath='{.status.addresses[0].value}' 2>/dev/null || echo "pending")
  echo "Envoy Gateway:  READY (address: ${GW_ADDR})"
  echo ""
  echo "Access (run 'bash scripts/gateway-proxy.sh' for local HTTPS, then):"
  echo "  Studio:        https://agentshield.127.0.0.1.nip.io:8443"
  echo "  Registry API:  https://agentshield.127.0.0.1.nip.io:8443/api/v1/health"
  echo "  Keycloak:      https://agentshield.127.0.0.1.nip.io:8443/realms/agentshield/.well-known/openid-configuration"
  echo "  Langfuse:      https://langfuse.127.0.0.1.nip.io:8443  (SSO via Keycloak — single login)"
  echo "  MinIO Console: https://agentshield.127.0.0.1.nip.io:8443/minio/"
  echo "  Webhooks:      https://agentshield.127.0.0.1.nip.io:8443/webhooks/"
elif [ -n "$GW_STATUS" ]; then
  echo "Envoy Gateway:  NOT READY (status: ${GW_STATUS})"
  echo "  Check: kubectl get gateway -n $NAMESPACE"
  echo ""
  echo "Fallback port-forward commands:"
  echo "  Registry API:      kubectl port-forward svc/agentshield-registry-api    -n ${NAMESPACE} 8000:8000"
  echo "  Studio:            kubectl port-forward svc/agentshield-studio          -n ${NAMESPACE} 5173:80"
else
  echo "Envoy Gateway:  NOT INSTALLED (controller missing or gateway not created)"
  echo "  Install: bash scripts/setup-envoy-gateway.sh"
  echo ""
  echo "Port-forward commands (legacy access):"
  echo "  Registry API:      kubectl port-forward svc/agentshield-registry-api    -n ${NAMESPACE} 8000:8000"
  echo "  Studio:            kubectl port-forward svc/agentshield-studio          -n ${NAMESPACE} 5173:80"
  echo "  Python Executor:   kubectl port-forward svc/agentshield-python-executor  -n ${NAMESPACE} 8081:8080"
  echo "  Langfuse UI:       kubectl port-forward svc/agentshield-langfuse-web    -n ${NAMESPACE} 4000:3000"
fi
echo ""
echo "Langfuse default credentials:"
echo "  URL:      http://agentshield.local/langfuse/ (or http://localhost:4000 via port-forward)"
echo "  Email:    admin@agentshield.local"
echo "  Password: AdminPass2024"
echo "  Project:  AgentShield Platform"
echo "  API Keys: pk-lf-agentshield-dev-local-0001 / sk-lf-agentshield-dev-local-0001"
echo ""
echo "Default resources seeded: 6 tools (5 HTTP + 1 Python), 2 skills, 3 workflows, 5 agents"
echo "Next: bash scripts/smoke-test-cpe2e-studio.sh"

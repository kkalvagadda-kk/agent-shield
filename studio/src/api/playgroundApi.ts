import { http } from "./registryApi";
import type { TraceDetail } from "./observabilityApi";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export interface PlaygroundRun {
  id: string;
  user_id: string;
  agent_name: string;
  agent_version_id: string | null;
  context: string;
  sandbox: boolean;
  input_message: string | null;
  execution_shape: "reactive" | "durable";
  input_payload: Record<string, unknown> | null;
  trigger_type: string | null;
  trigger_payload: Record<string, unknown> | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  output_text: string | null;
}

export interface StepUpdateEvent {
  event: "step_update";
  step_number: number;
  step_name: string;
  status: string;
  output?: unknown;
  approval_id?: string;
}

export interface DurableRunResponse {
  run_id: string;
  stream_url: string;
  execution_shape: string;
}

export interface TestEventResponse {
  matched: boolean;
  reason: string;
  trigger_id?: string;
  run_id?: string;
  stream_url?: string;
}

// Eval v2 E-0 — the five eval families (== playground_datasets.mode / eval_runs.mode).
// Mirrors backend `DatasetMode` (schemas.py). E-0 only authors `reactive`.
export type DatasetMode =
  | "reactive"
  | "durable"
  | "scheduled"
  | "webhook"
  | "workflow";

export interface PlaygroundDataset {
  id: string;
  owner_user_id: string;
  name: string;
  mode: DatasetMode;
  schema_version: number;
  items: AnyDatasetItem[];
  created_at: string;
}

export interface DatasetItem {
  input: string;
  expected_output?: string;
}

// Eval v2 E-1 — durable dataset item (discriminated-union variant, `kind:"durable"`).
// Mirrors backend `DurableDatasetItem` (schemas.py) / e1/data-model.md §1. Scored
// against the real `run_steps` trajectory of a durable run.
export type TrajectoryMatchMode = "exact" | "ordered" | "superset" | "unordered";

export interface ExpectedTrajectoryStep {
  tool: string;
  // Partial dict-subset assertion on the tool-call args (must be present in the
  // actual call args). Absent-in-actual ⇒ that step's tool_call dimension fails.
  args_match?: Record<string, unknown>;
  // HITL-arg review: this step SHOULD park for approval (status awaiting_approval).
  expect_approval?: boolean;
}

export interface ExpectedTrajectory {
  match_mode: TrajectoryMatchMode;
  steps: ExpectedTrajectoryStep[];
}

// Eval v2 E-2 — one expected side effect. Mirrors backend `SideEffectAssertion`
// (schemas.py) / e2/data-model.md §4. Asserted against the calls the governed-tool
// delivery seam RECORDED instead of delivering (`eval_mode=record`) — so an item
// carrying these runs without ever sending the real email / filing the real JIRA.
export interface SideEffectAssertion {
  tool: string;
  // Partial dict-subset assertion on the recorded call args.
  args_match?: Record<string, unknown>;
  // exactly N | at_least N | never (any match ⇒ the assertion fails).
  occurs?: "exactly" | "at_least" | "never";
  count?: number;
}

export interface DurableDatasetItem {
  kind: "durable";
  input_payload: Record<string, unknown>;
  expected_output?: string;
  expected_trajectory?: ExpectedTrajectory;
  // Eval v2 E-2 — what the run SHOULD have delivered. Its presence is what makes
  // the eval-runner launch this item under `eval_mode=record`.
  expected_side_effects?: SideEffectAssertion[];
  rubric?: string;
  notes?: string;
}

// Eval v2 E-5 — workflow dataset item (discriminated-union variant, `kind:"workflow"`).
// Mirrors backend `WorkflowDatasetItem` (schemas.py) / eval-v2 data-model §2.5. Scored
// against the real workflow RUN TREE: the ordered member path (which members ran, in
// order) + an optional per-member rubric that zooms into a child's own run_steps.
export interface PerMemberExpectation {
  // Reference-free rubric scored over that member's own steps/response.
  rubric?: string;
}

export interface WorkflowDatasetItem {
  kind: "workflow";
  // Free-text input to the workflow (or `input_payload` for triggered workflows).
  input_message?: string;
  input_payload?: Record<string, unknown>;
  expected_output?: string;
  // Members expected to run, in order — a trajectory at member granularity.
  expected_member_path?: string[];
  // How the expected member path is compared to the real one (default "ordered").
  match_mode?: TrajectoryMatchMode;
  // Optional per-member expectation keyed by member (agent) name.
  per_member?: Record<string, PerMemberExpectation>;
}

// Eval v2 E-3 — scheduled dataset item (discriminated-union variant, `kind:"scheduled"`).
// Mirrors backend `ScheduledDatasetItem` (schemas.py) / e3/data-model.md §2.3.
//
// `job_spec` is the per-schedule job spec — the SAME shape as the shipped
// `AgentTrigger.input_payload`. The eval fires it through the shared run door as the
// run's `input_payload` (+ `trigger_type='schedule'` / `trigger_payload=job_spec`),
// i.e. the identical production shape, so what the eval runs is what the timer runs.
//
// `expected_side_effects` is the HEADLINE signal for scheduled — a scheduled agent's
// whole point is the effect it fires unattended. Its presence is what makes the
// eval-runner launch the item under `eval_mode=record` (recorded + mocked, never
// delivered). `expected_trajectory` is meaningful only for a durable-inner schedule.
export interface ScheduledDatasetItem {
  kind: "scheduled";
  job_spec: Record<string, unknown>;
  expected_output?: string;
  expected_trajectory?: ExpectedTrajectory;
  expected_side_effects?: SideEffectAssertion[];
}

// Eval v2 E-4 — webhook dataset item (discriminated-union variant, `kind:"webhook"`).
// Mirrors backend `WebhookDatasetItem` (schemas.py) / e4/data-model.md §2.
//
// `trigger_payload` is the SYNTHETIC EVENT. The eval fires it at the agent's REAL
// webhook trigger through the REAL `test-event` door, which runs the real
// `filter_engine` against the trigger's real `filter_conditions` — a copy the
// deploy-time parity gate keeps byte-identical to the event-gateway's. So the decision
// scored is the decision production makes, not an eval-only re-implementation.
//
// `expected_match` is the FIRST-CLASS signal: a webhook agent's first job is to NOT run
// on events it should filter. A correctly-filtered event scores `filter: 1.0` and runs
// NOTHING — a pass, not a skip. `expected_filter_reason` (only meaningful when
// `expected_match` is false) additionally requires that substring in the real reason: a
// miss for the WRONG reason means the rule under test was never exercised.
//
// The action expecteds (`expected_output`/`expected_trajectory`/`expected_side_effects`)
// are scored ONLY on a match — there is nothing to score when nothing ran.
export interface InjectionProbe {
  // Tools an injected instruction must NEVER be able to make the agent call. Checked
  // against the union of the recorded calls and the real trajectory.
  must_not_call?: string[];
  // Whether the agent must visibly refuse (a LIGHT keyword check — a calibrated
  // refusal classifier is an explicit gap-ledger deferral).
  must_refuse?: boolean;
}

export interface WebhookDatasetItem {
  kind: "webhook";
  trigger_payload: Record<string, unknown>;
  expected_match: boolean;
  expected_filter_reason?: string;
  expected_output?: string;
  expected_trajectory?: ExpectedTrajectory;
  expected_side_effects?: SideEffectAssertion[];
  // The payload is attacker-controlled (it arrives from the internet, not from an
  // authenticated user). A probe's presence makes the eval-runner run the item under
  // `eval_mode=record` regardless of assertions: the question is whether a forbidden
  // WRITE fired, and a live delivery would answer it by really wiring the money.
  injection_probe?: InjectionProbe;
}

export type AnyDatasetItem =
  | DatasetItem
  | DurableDatasetItem
  | ScheduledDatasetItem
  | WorkflowDatasetItem
  | WebhookDatasetItem;

export function isDurableItem(item: AnyDatasetItem): item is DurableDatasetItem {
  return (item as DurableDatasetItem).kind === "durable";
}

export interface EvalRun {
  id: string;
  user_id: string;
  /** THE publish threshold for this run — always resolved by the API (never null on
   *  the wire; `eval_run_response` fills pre-E-6 rows from the platform default).
   *  Render verdicts against THIS, never a local literal: the UI used to hardcode
   *  0.7, so a 0.85 run with pass_threshold=0.9 showed "passed" while the gate
   *  refused to publish. REQUIRED: the API always resolves it, and a `?` here would
   *  invite a local `?? 0.7` fallback — the re-declaration that caused the bug. If it
   *  is somehow absent at runtime, every `score >= threshold` yields false and the UI
   *  fails CLOSED (no verdict, no mark-passed button) rather than guessing. */
  pass_threshold: number;
  agent_name: string;
  agent_version_id: string | null;
  workflow_id: string | null;
  workflow_version_id: string | null;
  dataset_id: string;
  status: string;
  total_items: number | null;
  passed_count: number | null;
  failed_count: number | null;
  overall_score: number | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  sandbox_deployment_id: string | null;
  workflow_deployment_id: string | null;
  // Eval v2 E-0 — the interpretation mode this run was scored under (== the dataset's
  // authored mode). The backend has always returned it; declaring it here is what lets
  // the results page render evidence by the EXPLICIT discriminator instead of guessing
  // from which keys a row happens to carry (a scheduled `job_spec` and a webhook
  // `trigger_payload` share the same column).
  mode: DatasetMode;
}

export interface EvalRunResult {
  id: string;
  eval_run_id: string;
  dataset_item_idx: number;
  input_message: string | null;
  expected_output: string | null;
  response: string | null;
  judge_score: number | null;
  judge_reasoning: string | null;
  passed: boolean | null;
  langfuse_trace_id: string | null;
  trace_url: string | null;
  // Eval v2 E-0 — composite-score evidence. Reactive fills only
  // `dimension_scores = {response: x}` and `composite == judge_score`.
  dimension_scores: Record<string, number> | null;
  composite: number | null;
  // Eval v2 E-1 — durable per-dimension evidence + soft link to the run tree.
  eval_detail?: EvalDetail | null;
  run_id?: string | null;
  // Eval v2 E-3/E-4 — WHAT this result's run was actually fired with (== the run's
  // `input_payload`/`trigger_payload`): the JOB SPEC for a scheduled item, the
  // SYNTHETIC EVENT for a webhook item. Written by the eval-runner, and present on
  // fail-closed rows too, so the results UI can always show what was fired even when
  // the item could not be scored. Which one it is comes from the run's `mode`, never
  // from sniffing the shape.
  trigger_payload?: Record<string, unknown> | null;
  // Eval v2 E-4 — the REAL filter decision the `test-event` door returned for this
  // item's synthetic event (webhook mode only; null on every other family and on a
  // webhook row that fail-closed BEFORE firing, where no decision was ever made).
  // Written by the eval-runner's webhook branch, read by the filter-verdict block —
  // the column existed since E-0 with neither a writer nor a reader.
  matched?: boolean | null;
  created_at: string;
}

// Eval v2 E-1 — one projected `RunStep` row (e1/data-model.md §3), built by the
// eval-runner from the real durable run's `run_steps`.
export interface TrajectoryStep {
  step_number: number;
  name: string;
  status: string;
  tool?: string;
  args?: Record<string, unknown>;
  approval_id?: string | null;
}

// Per-expected-step tool-arg comparison (dict-subset). Mirrors backend
// `score_tool_calls` detail.tool_diffs[] (judge.py).
export interface ToolDiff {
  step: string;
  expected_args?: Record<string, unknown>;
  actual_args?: Record<string, unknown>;
  arg_match: boolean;
}

// HITL-arg (`expect_approval`) assertion outcome per gated step.
export interface ApprovalDetail {
  step: string;
  expected: boolean;
  parked: boolean;
  args_matched: boolean;
}

// Eval v2 E-5 — member-path diff over the workflow run tree (member granularity).
// Mirrors the `member_diff` detail from `score_member_path` (judge.py).
export interface MemberDiff {
  missing?: string[];
  extra?: string[];
  order_ok?: boolean;
  match_mode?: TrajectoryMatchMode;
}

// Eval v2 E-5 — per-member evidence: one zoom into a member (child) run. Mirrors
// EXACTLY what the backend `/eval/score mode=workflow` branch emits per member
// (playground.py `per_member_detail`): the rubric, the LLM rubric score, the
// judge's reasoning (`reason`), and whether the child had run_steps to zoom into
// (`had_steps` — false for a reactive child, which degrades to an empty-behavior
// score). The runner reads the child's steps and the judge scores over them; the
// steps themselves aren't echoed back in the detail — `had_steps` flags them.
export interface PerMemberEvidence {
  member: string;
  score?: number | null;
  rubric?: string | null;
  reason?: string | null;
  had_steps?: boolean | null;
}

// Eval v2 E-2 — one call the delivery seam RECORDED instead of delivering. Produced
// by the governed-tool seam (`graph_builder._record_side_effect`), drained onto
// `run_steps.output.recorded_side_effects[]` and projected back by the eval-runner.
// This is "the email that would have been sent".
export interface RecordedSideEffect {
  tool: string;
  args?: Record<string, unknown> | null;
  // What was returned to the agent in place of invoking the real downstream.
  mocked_response?: Record<string, unknown> | null;
  // The downstream that was NOT called.
  would_have_invoked?: string | null;
}

// Per-assertion outcome from `judge.score_side_effects` (detail.side_effect_diffs[]).
export interface SideEffectDiff {
  tool: string;
  args_match?: Record<string, unknown> | null;
  occurs?: string;
  count?: number;
  // How many recorded calls matched the assertion.
  matched: number;
  satisfied: boolean;
}

export interface SideEffectDetail {
  side_effect_diffs?: SideEffectDiff[] | null;
  recorded?: RecordedSideEffect[] | null;
}

// `eval_run_results.eval_detail` for a durable result. Mirrors the durable
// `EvalScoreResponse.detail` (e1/contracts/eval-score-api.md). Eval v2 E-5 adds the
// workflow run-tree fields (member path + per-member evidence); E-2 adds the
// recorded side effects + the `side_effect` dimension's per-assertion detail.
export interface EvalDetail {
  expected_trajectory?: ExpectedTrajectory | null;
  actual_trajectory?: TrajectoryStep[] | null;
  // Eval v2 E-3 — the job spec the scheduled score door echoes back (the item's
  // authored spec). The row's own `trigger_payload` is what the run was ACTUALLY
  // fed; this is the fallback for a row recorded before that column was written.
  job_spec?: Record<string, unknown> | null;
  tool_diffs?: ToolDiff[] | null;
  approvals?: ApprovalDetail[] | null;
  // Eval v2 E-2 — side-effect evidence. `recorded_side_effects` is always present
  // on a durable detail (empty for a `live` item); `side_effect_detail` only when
  // the item asserted side effects.
  recorded_side_effects?: RecordedSideEffect[] | null;
  side_effect_detail?: SideEffectDetail | null;
  // Eval v2 E-5 — workflow run-tree evidence.
  expected_member_path?: string[] | null;
  actual_member_path?: string[] | null;
  member_diff?: MemberDiff | null;
  per_member?: PerMemberEvidence[] | null;
  // Eval v2 E-4 — webhook filter + injection evidence.
  // The decision half: what the REAL filter decided and why, vs what the item expected.
  matched?: boolean | null;
  filter_reason?: string | null;
  filter_detail?: FilterDetail | null;
  // The injection half. `asr` (attack success rate: did a forbidden tool really fire?)
  // and `utility` (the response dim of the SAME run) are surfaced side by side ON
  // PURPOSE: an agent that refuses everything drives ASR to 0 and would read as a
  // perfect defense while being useless. Reporting only ASR would grade it perfect.
  asr?: number | null;
  utility?: number | null;
  forbidden_called?: string[] | null;
  injection_detail?: InjectionDetail | null;
  // True when the item carried a probe but the event was FILTERED — the probe was
  // never exercised, so there is no injection dimension. Surfaced rather than silently
  // omitted: an absent dimension must never read as a passing one.
  injection_not_exercised?: boolean | null;
  // The exact facts no weighted average may out-vote (a filter error; an injected
  // instruction that really fired a forbidden tool). When present, the composite was
  // forced to 0 regardless of the other dimensions.
  veto?: string[] | null;
}

export interface FilterDetail {
  matched?: boolean | null;
  expected_match?: boolean | null;
  filter_reason?: string | null;
  expected_filter_reason?: string | null;
  // null when no reason was asserted; false when the event was filtered for a
  // DIFFERENT reason than the item named (the rule under test never ran).
  reason_matched?: boolean | null;
  error?: string | null;
}

export interface InjectionDetail {
  asr?: number | null;
  utility?: number | null;
  forbidden_called?: string[] | null;
  refused?: boolean | null;
  must_not_call?: string[] | null;
  must_refuse?: boolean | null;
}

// True when this detail carries workflow run-tree evidence (member path / per-member)
// rather than the durable single-run trajectory.
export function isWorkflowDetail(detail: EvalDetail): boolean {
  return (
    detail.expected_member_path != null ||
    detail.actual_member_path != null ||
    detail.member_diff != null ||
    (detail.per_member != null && detail.per_member.length > 0)
  );
}

// Eval v2 E-0 — one scoring door. Mirrors backend EvalScoreRequest/Response
// (schemas.py). `mode` selects the scorer branch; reactive returns
// `dimension_scores = {response: x}` with `composite == x`.
export interface EvalScoreRequest {
  mode: DatasetMode;
  item?: Record<string, unknown>;
  run_id?: string;
  input?: string;
  response?: string;
  // Eval v2 E-1 — durable dispatch: the projected run-step trajectory + optional
  // per-dimension weight overrides (else durable defaults 0.4/0.4/0.2 + 0.2 side_effect).
  actual_trajectory?: TrajectoryStep[];
  dimension_weights?: Record<string, number>;
  // Eval v2 E-2 — the calls the delivery seam recorded instead of delivering,
  // projected off the real run_steps by the eval-runner (scores the side_effect dim).
  recorded_side_effects?: RecordedSideEffect[];
  // Eval v2 E-5 — workflow dispatch: the ordered member names the runner extracted
  // from the run tree + each member's projected child steps (per-member rubric).
  member_path?: string[];
  per_member_steps?: Record<string, TrajectoryStep[]>;
}

export interface EvalScoreResponse {
  composite: number;
  dimension_scores: Record<string, number>;
  detail?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Playground Runs
// ---------------------------------------------------------------------------
export async function startPlaygroundRun(body: {
  agent_name: string;
  input_message: string;
  agent_version_id?: string;
}): Promise<{ run_id: string; stream_url: string }> {
  const { data } = await http.post<{ run_id: string; stream_url: string }>(
    "/playground/runs",
    body
  );
  return data;
}

export async function listPlaygroundRuns(): Promise<PlaygroundRun[]> {
  const { data } = await http.get<PlaygroundRun[]>("/playground/runs");
  return data;
}

export function streamPlaygroundRun(runId: string): EventSource {
  return new EventSource(`/api/v1/playground/runs/${runId}/stream`);
}

// ---------------------------------------------------------------------------
// Run Trace & Feedback
// ---------------------------------------------------------------------------
export async function getRunTrace(runId: string): Promise<{
  run_id: string;
  trace_id: string | null;
  trace_url: string | null;
  status: string;
}> {
  const { data } = await http.get(`/playground/runs/${runId}/trace`);
  return data;
}

export async function submitRunFeedback(
  runId: string,
  score: 1 | -1,
  comment?: string
): Promise<{ langfuse_score_id: string | null }> {
  const { data } = await http.post(`/playground/runs/${runId}/feedback`, {
    score,
    comment,
  });
  return data;
}

export async function getTraceById(traceId: string): Promise<TraceDetail> {
  const { data } = await http.get<TraceDetail>(`/playground/traces/${traceId}`);
  return data;
}

// ---------------------------------------------------------------------------
// Playground Approvals
// ---------------------------------------------------------------------------
export async function listPlaygroundApprovals(statusFilter?: string): Promise<unknown[]> {
  const params: Record<string, string> = {};
  if (statusFilter) params.status = statusFilter;
  const { data } = await http.get<unknown[]>("/playground/approvals", { params });
  return data;
}

export interface PlaygroundApprovalDecideResponse {
  approval_id: string;
  status: string;
  thread_id: string;
  agent_name: string;
  team: string;
}

export async function decidePlaygroundApproval(
  approvalId: string,
  decision: "approved" | "denied"
): Promise<PlaygroundApprovalDecideResponse> {
  const { data } = await http.post<PlaygroundApprovalDecideResponse>(
    `/playground/approvals/${approvalId}/decide`,
    { decision },
  );
  return data;
}

// ---------------------------------------------------------------------------
// Datasets
// ---------------------------------------------------------------------------
export async function listDatasets(): Promise<PlaygroundDataset[]> {
  const { data } = await http.get<PlaygroundDataset[]>("/playground/datasets");
  return data;
}

export async function createDataset(body: {
  name: string;
  items: AnyDatasetItem[];
  mode?: DatasetMode;
  schema_version?: number;
}): Promise<PlaygroundDataset> {
  const { data } = await http.post<PlaygroundDataset>("/playground/datasets", body);
  return data;
}

export async function deleteDataset(id: string): Promise<void> {
  await http.delete(`/playground/datasets/${id}`);
}

// ---------------------------------------------------------------------------
// Eval Runs
// ---------------------------------------------------------------------------
export async function createEvalRun(body: {
  agent_name?: string;
  dataset_id: string;
  agent_version_id?: string;
  workflow_id?: string;
  sandbox_deployment_id?: string;
  workflow_deployment_id?: string;
}): Promise<EvalRun> {
  const { data } = await http.post<EvalRun>("/playground/eval-runs", body);
  return data;
}

export async function listEvalRuns(): Promise<EvalRun[]> {
  const { data } = await http.get<EvalRun[]>("/playground/eval-runs");
  return data;
}

export async function getEvalRun(id: string): Promise<EvalRun> {
  const { data } = await http.get<EvalRun>(`/playground/eval-runs/${id}`);
  return data;
}

export async function getEvalRunResults(id: string): Promise<EvalRunResult[]> {
  const { data } = await http.get<EvalRunResult[]>(`/playground/eval-runs/${id}/results`);
  return data;
}

// ---------------------------------------------------------------------------
// Durable runs
// ---------------------------------------------------------------------------
export async function launchDurableRun(
  agentName: string,
  inputPayload: Record<string, unknown>,
  versionId?: string
): Promise<DurableRunResponse> {
  const { data } = await http.post<DurableRunResponse>("/playground/runs", {
    agent_name: agentName,
    agent_version_id: versionId || undefined,
    execution_shape: "durable",
    input_payload: inputPayload,
  });
  return data;
}

export async function listRunSteps(
  runId: string
): Promise<StepUpdateEvent[]> {
  const { data } = await http.get<StepUpdateEvent[]>(
    `/playground/runs/${runId}/steps`
  );
  return data;
}

// ---------------------------------------------------------------------------
// General playground run creation (used by RunNowPanel)
// ---------------------------------------------------------------------------
export async function createPlaygroundRun(
  agentName: string,
  inputMessage?: string,
  versionId?: string
): Promise<DurableRunResponse> {
  const { data } = await http.post<DurableRunResponse>("/playground/runs", {
    agent_name: agentName,
    agent_version_id: versionId || undefined,
    input_message: inputMessage || "Manual test-fire",
  });
  return data;
}

// ---------------------------------------------------------------------------
// Test event (webhook trigger testing)
// ---------------------------------------------------------------------------
export async function testEvent(
  agentName: string,
  payload: Record<string, unknown>
): Promise<TestEventResponse> {
  const { data } = await http.post<TestEventResponse>("/playground/test-event", {
    agent_name: agentName,
    payload,
  });
  return data;
}

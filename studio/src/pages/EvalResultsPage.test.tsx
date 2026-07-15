import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import EvalResultsPage from "./EvalResultsPage";

vi.mock("../api/playgroundApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/playgroundApi")>();
  return {
    createEvalRun: vi.fn(),
    getEvalRun: vi.fn(),
    getEvalRunResults: vi.fn(),
    listRunSteps: vi.fn(),
    // Pure helper — use the real implementation so the workflow-vs-durable
    // evidence branch is exercised, not stubbed.
    isWorkflowDetail: actual.isWorkflowDetail,
  };
});

vi.mock("../api/registryApi", () => ({
  patchVersion: vi.fn(),
  patchWorkflowVersion: vi.fn(),
  publishAgent: vi.fn(),
  publishWorkflow: vi.fn(),
}));

vi.mock("../components/playground/TraceDrawer", () => ({
  default: () => <div data-testid="trace-drawer-stub" />,
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { getEvalRun, getEvalRunResults, listRunSteps } from "../api/playgroundApi";
import { fireEvent, waitFor } from "@testing-library/react";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;
const NOW = new Date().toISOString();

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/playground/eval-runs/:evalRunId" element={<EvalResultsPage />} />
    </Routes>,
    { routerEntries: ["/playground/eval-runs/run-1"] },
  );
}

describe("EvalResultsPage — dimension scores (Eval v2 E-0)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(getEvalRun).mockResolvedValue({
      id: "run-1",
      user_id: "u1",
      agent_name: "my-agent",
      agent_version_id: "v1",
      workflow_id: null,
      workflow_version_id: null,
      dataset_id: "ds-1",
      status: "completed",
      total_items: 1,
      passed_count: 1,
      failed_count: 0,
      overall_score: 0.9,
      started_at: NOW,
      completed_at: NOW,
      created_at: NOW,
      sandbox_deployment_id: "dep-1",
      workflow_deployment_id: null,
      // The backend has ALWAYS returned the run's interpretation mode; the
      // fixture omitted it. The results page now renders evidence by that
      // EXPLICIT discriminator (a scheduled job spec and a webhook synthetic
      // event share the `trigger_payload` column), so a fixture without it
      // models a response the API never sends.
      mode: "reactive" as const,
    });
    mock(getEvalRunResults).mockResolvedValue([
      {
        id: "res-1",
        eval_run_id: "run-1",
        dataset_item_idx: 0,
        input_message: "What is order 123?",
        expected_output: "Order 123 is shipped",
        response: "Order 123 is shipped",
        judge_score: 0.9,
        judge_reasoning: "Matches expected.",
        passed: true,
        langfuse_trace_id: null,
        trace_url: null,
        dimension_scores: { response: 0.9 },
        composite: 0.9,
        created_at: NOW,
      },
    ]);
  });

  it("renders the response dimension score; other dimensions render empty", async () => {
    renderPage();

    // Response dimension populated from dimension_scores = {response: 0.9}.
    const responseDim = await screen.findByTestId("dim-response");
    expect(responseDim).toHaveTextContent("0.90");

    // Non-reactive dimensions render an em-dash for a reactive result.
    expect(screen.getByTestId("dim-trajectory")).toHaveTextContent("—");
    expect(screen.getByTestId("dim-side_effect")).toHaveTextContent("—");
    expect(screen.getByTestId("dim-filter")).toHaveTextContent("—");
    expect(screen.getByTestId("dim-member_path")).toHaveTextContent("—");

    // Existing composite/overall display is preserved.
    expect(screen.getByText("Overall Score")).toBeInTheDocument();
  });
});

describe("EvalResultsPage — durable trajectory evidence (Eval v2 E-1)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(getEvalRun).mockResolvedValue({
      id: "run-1",
      user_id: "u1",
      agent_name: "contract-agent",
      agent_version_id: "v1",
      workflow_id: null,
      workflow_version_id: null,
      dataset_id: "ds-1",
      status: "completed",
      total_items: 1,
      passed_count: 0,
      failed_count: 1,
      overall_score: 0.6,
      started_at: NOW,
      completed_at: NOW,
      created_at: NOW,
      sandbox_deployment_id: "dep-1",
      workflow_deployment_id: null,
      // The backend has ALWAYS returned the run's interpretation mode; the
      // fixture omitted it. The results page now renders evidence by that
      // EXPLICIT discriminator (a scheduled job spec and a webhook synthetic
      // event share the `trigger_payload` column), so a fixture without it
      // models a response the API never sends.
      mode: "durable" as const,
    });
    mock(getEvalRunResults).mockResolvedValue([
      {
        id: "res-1",
        eval_run_id: "run-1",
        dataset_item_idx: 0,
        input_message: "Review the ACME contract",
        expected_output: null,
        response: "Parked jira_create for approval.",
        judge_score: 0.6,
        judge_reasoning: "Called an unexpected tool.",
        passed: false,
        langfuse_trace_id: null,
        trace_url: null,
        dimension_scores: { response: 0.9, trajectory: 0.5, tool_call: 0.5 },
        composite: 0.62,
        run_id: "pgrun-1234abcd",
        eval_detail: {
          expected_trajectory: {
            match_mode: "ordered",
            steps: [
              { tool: "parse_document" },
              { tool: "jira_create", args_match: { project: "LEG" }, expect_approval: true },
            ],
          },
          actual_trajectory: [
            { step_number: 1, name: "parse_document", status: "completed", tool: "parse_document", args: {} },
            {
              step_number: 2,
              name: "jira_create",
              status: "awaiting_approval",
              tool: "jira_create",
              args: { project: "LEG", summary: "x" },
              approval_id: "appr-1",
            },
          ],
          tool_diffs: [
            {
              step: "jira_create",
              expected_args: { project: "LEG" },
              actual_args: { project: "LEG", summary: "x" },
              arg_match: true,
            },
          ],
          approvals: [{ step: "jira_create", expected: true, parked: true, args_matched: true }],
        },
        created_at: NOW,
      },
    ]);
    mock(listRunSteps).mockResolvedValue([
      { event: "step_update", step_number: 1, step_name: "parse_document", status: "completed" },
      { event: "step_update", step_number: 2, step_name: "jira_create", status: "awaiting_approval", approval_id: "appr-1" },
    ]);
  });

  it("renders trajectory + tool_call dimension columns from the durable result", async () => {
    renderPage();
    const trajDim = await screen.findByTestId("dim-trajectory");
    expect(trajDim).toHaveTextContent("0.50");
    expect(screen.getByTestId("dim-tool_call")).toHaveTextContent("0.50");
  });

  it("renders the tool-diff panel + expected-vs-actual step diff on expand", async () => {
    renderPage();
    const inputCell = await screen.findByText("Review the ACME contract");
    fireEvent.click(inputCell);

    expect(await screen.findByTestId("durable-evidence")).toBeInTheDocument();
    expect(screen.getByTestId("tool-diff-panel")).toBeInTheDocument();
    expect(screen.getByTestId("actual-trajectory")).toBeInTheDocument();
    expect(screen.getByTestId("approvals-panel")).toBeInTheDocument();
    // Expected step shows the tool name; actual shows the parked marker.
    expect(screen.getByTestId("actual-trajectory")).toHaveTextContent("jira_create");
  });

  it("deep-links to the run tree via run_id, loading the real run steps", async () => {
    renderPage();
    const inputCell = await screen.findByText("Review the ACME contract");
    fireEvent.click(inputCell);

    const deepLink = await screen.findByTestId("run-steps-deeplink");
    // run_id is surfaced (truncated) on the deep-link control.
    expect(deepLink).toHaveTextContent("pgrun-12");
    fireEvent.click(deepLink);

    await waitFor(() => expect(listRunSteps).toHaveBeenCalledWith("pgrun-1234abcd"));
    // The control flips to "Hide run tree" once the read-only steps load.
    expect(await screen.findByText(/Hide run tree/i)).toBeInTheDocument();
  });
});

// Eval v2 E-2 — the side effects a record-mode eval INTERCEPTED. The fixtures below
// are the exact shape the real producers emit: the delivery seam records
// {tool,args,mocked_response,would_have_invoked} (graph_builder._record_side_effect),
// the eval-runner projects them off run_steps, and /eval/score returns them plus
// score_side_effects' per-assertion diffs in `eval_detail`.
describe("EvalResultsPage — recorded side effects (Eval v2 E-2)", () => {
  const evalRunBase = {
    id: "run-1",
    user_id: "u1",
    agent_name: "breach-agent",
    agent_version_id: "v1",
    workflow_id: null,
    workflow_version_id: null,
    dataset_id: "ds-1",
    status: "completed",
    total_items: 1,
    passed_count: 1,
    failed_count: 0,
    overall_score: 0.9,
    started_at: NOW,
    completed_at: NOW,
    created_at: NOW,
    sandbox_deployment_id: "dep-1",
    workflow_deployment_id: null,
    // The backend has ALWAYS returned the run's interpretation mode; the
    // fixture omitted it. The results page now renders evidence by that
    // EXPLICIT discriminator (a scheduled job spec and a webhook synthetic
    // event share the `trigger_payload` column), so a fixture without it
    // models a response the API never sends.
    mode: "durable" as const,
  };

  const resultBase = {
    id: "res-1",
    eval_run_id: "run-1",
    dataset_item_idx: 0,
    input_message: "Report the ACME breach",
    expected_output: null,
    response: "Emailed compliance.",
    judge_score: 0.9,
    judge_reasoning: "Reported correctly.",
    passed: true,
    langfuse_trace_id: null,
    trace_url: null,
    run_id: "pgrun-1234abcd",
    created_at: NOW,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mock(getEvalRun).mockResolvedValue(evalRunBase);
    mock(listRunSteps).mockResolvedValue([]);
  });

  async function expandRow() {
    renderPage();
    const inputCell = await screen.findByText("Report the ACME breach");
    fireEvent.click(inputCell);
  }

  it("renders the side_effect dimension score from the durable result", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        dimension_scores: { response: 0.9, side_effect: 1.0 },
        composite: 0.93,
        eval_detail: { recorded_side_effects: [], side_effect_detail: null },
      },
    ]);
    renderPage();
    // The key MUST be `side_effect` — the backend's dimension_scores key.
    expect(await screen.findByTestId("dim-side_effect")).toHaveTextContent("1.00");
  });

  it("renders the recorded call — the email that would have been sent — never delivered", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        dimension_scores: { response: 0.9, side_effect: 1.0 },
        composite: 0.93,
        eval_detail: {
          recorded_side_effects: [
            {
              tool: "send_email",
              args: { to: "compliance@acme.com", subject: "Q3 breach" },
              mocked_response: { status: "ok", id: "mock-2f1c" },
              would_have_invoked: "POST https://mail.internal/send",
            },
          ],
          side_effect_detail: {
            side_effect_diffs: [
              {
                tool: "send_email",
                args_match: { to: "compliance@acme.com" },
                occurs: "exactly",
                count: 1,
                matched: 1,
                satisfied: true,
              },
            ],
            recorded: [],
          },
        },
      },
    ]);
    await expandRow();

    expect(await screen.findByTestId("side-effect-evidence")).toBeInTheDocument();
    const call = screen.getByTestId("recorded-side-effect-0");
    expect(call).toHaveTextContent("send_email");
    // The downstream that was NOT called + the mock returned in its place.
    expect(call).toHaveTextContent("POST https://mail.internal/send");
    expect(call).toHaveTextContent("mock-2f1c");
    expect(call).toHaveTextContent(/not delivered/i);
    // PII policy: the recipient is tokenized for display, never rendered raw.
    expect(call).toHaveTextContent("‹email›");
    expect(call).not.toHaveTextContent("compliance@acme.com");
    // Non-PII args still readable, so the reviewer can judge the call.
    expect(call).toHaveTextContent("Q3 breach");

    // The assertion outcome (score_side_effects diff) renders as satisfied.
    expect(screen.getByTestId("side-effect-assertions")).toHaveTextContent(/satisfied/i);
  });

  it("renders a violated `never` assertion", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        passed: false,
        dimension_scores: { response: 0.9, side_effect: 0.0 },
        composite: 0.75,
        eval_detail: {
          recorded_side_effects: [
            {
              tool: "send_email",
              args: { to: "customer@acme.com" },
              mocked_response: { status: "ok", id: "mock-9a2b" },
              would_have_invoked: "POST https://mail.internal/send",
            },
          ],
          side_effect_detail: {
            side_effect_diffs: [
              { tool: "send_email", occurs: "never", matched: 1, satisfied: false },
            ],
            recorded: [],
          },
        },
      },
    ]);
    await expandRow();

    expect(await screen.findByTestId("dim-side_effect")).toHaveTextContent("0.00");
    const assertions = screen.getByTestId("side-effect-assertions");
    expect(assertions).toHaveTextContent(/violated/i);
    expect(assertions).toHaveTextContent("never");
    // The forbidden call the agent attempted is still shown (recorded, not sent).
    expect(screen.getByTestId("recorded-side-effect-0")).toHaveTextContent("send_email");
  });

  it("renders the empty state when an assertion recorded nothing", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        passed: false,
        dimension_scores: { response: 0.9, side_effect: 0.0 },
        composite: 0.75,
        eval_detail: {
          recorded_side_effects: [],
          side_effect_detail: {
            side_effect_diffs: [
              {
                tool: "send_email",
                occurs: "exactly",
                count: 1,
                matched: 0,
                satisfied: false,
              },
            ],
            recorded: [],
          },
        },
      },
    ]);
    await expandRow();

    expect(await screen.findByTestId("no-recorded-side-effects")).toHaveTextContent(
      /never attempted a write/i,
    );
    expect(screen.getByTestId("side-effect-assertions")).toHaveTextContent(/violated/i);
  });

  it("collapses away entirely for a live item that asserted no side effects", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        dimension_scores: { response: 0.9 },
        composite: 0.9,
        eval_detail: { recorded_side_effects: [], actual_trajectory: [] },
      },
    ]);
    await expandRow();

    expect(await screen.findByTestId("durable-evidence")).toBeInTheDocument();
    // No assertions and nothing recorded → the panel is not rendered at all.
    expect(screen.queryByTestId("side-effect-evidence")).not.toBeInTheDocument();
    expect(screen.getByTestId("dim-side_effect")).toHaveTextContent("—");
  });
});

describe("EvalResultsPage — workflow run-tree evidence (Eval v2 E-5)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(getEvalRun).mockResolvedValue({
      id: "run-1",
      user_id: "u1",
      agent_name: "refund-workflow",
      agent_version_id: null,
      workflow_id: "wf-1",
      workflow_version_id: "wfv-1",
      dataset_id: "ds-1",
      status: "completed",
      total_items: 1,
      passed_count: 0,
      failed_count: 1,
      overall_score: 0.6,
      started_at: NOW,
      completed_at: NOW,
      created_at: NOW,
      sandbox_deployment_id: null,
      workflow_deployment_id: "wdep-1",
      // The backend has ALWAYS returned the run's interpretation mode; the fixture
      // omitted it. The results page now renders evidence by that EXPLICIT
      // discriminator, so a fixture without it models a response the API never sends.
      mode: "workflow" as const,
    });
    mock(getEvalRunResults).mockResolvedValue([
      {
        id: "res-1",
        eval_run_id: "run-1",
        dataset_item_idx: 0,
        input_message: "Refund for order 123 never arrived",
        expected_output: "Refund issued",
        response: "Refund issued",
        judge_score: 0.6,
        judge_reasoning: "Correct answer but wrong route (skipped triage).",
        passed: false,
        langfuse_trace_id: null,
        trace_url: null,
        // member_path dimension penalized for the wrong route.
        dimension_scores: { member_path: 0.5, response: 0.9 },
        composite: 0.62,
        run_id: "wfrun-abcd1234",
        eval_detail: {
          expected_member_path: ["intake", "triage", "resolver"],
          actual_member_path: ["intake", "resolver"],
          member_diff: {
            missing: ["triage"],
            extra: [],
            order_ok: false,
            match_mode: "ordered",
          },
          // Exactly what the backend `/eval/score mode=workflow` branch emits per
          // member (playground.py per_member_detail): {member, score, reason,
          // rubric, had_steps}.
          per_member: [
            {
              member: "triage",
              score: 0.4,
              rubric: "correctly routed to billing",
              reason: "Never ran.",
              had_steps: false,
            },
          ],
        },
        created_at: NOW,
      },
    ]);
    mock(listRunSteps).mockResolvedValue([
      { event: "step_update", step_number: 1, step_name: "intake", status: "completed" },
      { event: "step_update", step_number: 2, step_name: "resolver", status: "completed" },
    ]);
  });

  it("renders the member_path dimension column from the workflow result", async () => {
    renderPage();
    const dim = await screen.findByTestId("dim-member_path");
    expect(dim).toHaveTextContent("0.50");
  });

  it("renders expected-vs-actual member path + member_diff + per-member panel on expand", async () => {
    renderPage();
    const inputCell = await screen.findByText("Refund for order 123 never arrived");
    fireEvent.click(inputCell);

    expect(await screen.findByTestId("workflow-evidence")).toBeInTheDocument();
    // Actual member path (the wrong route that skipped triage).
    const actual = screen.getByTestId("actual-member-path");
    expect(actual).toHaveTextContent("intake");
    expect(actual).toHaveTextContent("resolver");
    // member_diff surfaces the missing member + wrong order.
    const memberDiff = screen.getByTestId("member-diff");
    expect(memberDiff).toHaveTextContent("triage");
    expect(memberDiff).toHaveTextContent(/order wrong/i);
    // Per-member evidence panel with the rubric score.
    expect(screen.getByTestId("per-member-panel")).toBeInTheDocument();
    expect(screen.getByTestId("per-member-evidence-0")).toHaveTextContent("triage");
    expect(screen.getByTestId("per-member-evidence-0")).toHaveTextContent("0.40");
    // the judge's reasoning (backend `reason` field) + the had_steps=false degrade note.
    expect(screen.getByTestId("per-member-evidence-0")).toHaveTextContent("Never ran.");
    expect(screen.getByTestId("per-member-evidence-0")).toHaveTextContent(/no run_steps to zoom/i);
  });

  it("deep-links to the workflow run tree via the parent run_id", async () => {
    renderPage();
    const inputCell = await screen.findByText("Refund for order 123 never arrived");
    fireEvent.click(inputCell);

    const deepLink = await screen.findByTestId("run-steps-deeplink");
    expect(deepLink).toHaveTextContent("wfrun-ab");
    fireEvent.click(deepLink);

    await waitFor(() => expect(listRunSteps).toHaveBeenCalledWith("wfrun-abcd1234"));
    expect(await screen.findByText(/Hide run tree/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Eval v2 E-3 — scheduled results. The job spec is WHAT the eval fired (fed to the
// run as its input_payload / trigger_payload — the identical production schedule
// shape), and the reused E-2 panel shows what would have been sent because of it.
// ---------------------------------------------------------------------------
describe("EvalResultsPage — scheduled job-spec evidence (Eval v2 E-3)", () => {
  const JOB_SPEC = { report: "weekly-compliance", recipients: ["compliance@acme.com"] };

  const evalRunBase = {
    id: "run-1",
    user_id: "u1",
    agent_name: "nightly-compliance",
    agent_version_id: "v1",
    workflow_id: null,
    workflow_version_id: null,
    dataset_id: "ds-1",
    status: "completed",
    total_items: 1,
    passed_count: 1,
    failed_count: 0,
    overall_score: 0.9,
    started_at: NOW,
    completed_at: NOW,
    created_at: NOW,
    sandbox_deployment_id: "dep-1",
    workflow_deployment_id: null,
    // The backend has ALWAYS returned the run's interpretation mode; the
    // fixture omitted it. The results page now renders evidence by that
    // EXPLICIT discriminator (a scheduled job spec and a webhook synthetic
    // event share the `trigger_payload` column), so a fixture without it
    // models a response the API never sends.
    mode: "scheduled" as const,
  };

  const resultBase = {
    id: "res-1",
    eval_run_id: "run-1",
    dataset_item_idx: 0,
    input_message: JSON.stringify(JOB_SPEC),
    expected_output: null,
    response: "Weekly report sent.",
    judge_score: 0.9,
    judge_reasoning: "scheduled eval (mode=scheduled, inner=durable)",
    passed: true,
    langfuse_trace_id: null,
    trace_url: null,
    run_id: "pgrun-1234abcd",
    created_at: NOW,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mock(getEvalRun).mockResolvedValue(evalRunBase);
    mock(listRunSteps).mockResolvedValue([]);
  });

  async function expandRow() {
    renderPage();
    fireEvent.click(await screen.findByText(/weekly-compliance/));
  }

  it("renders the job spec fired by the scheduled eval (from trigger_payload)", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        dimension_scores: { response: 0.9, side_effect: 1.0 },
        composite: 0.94,
        trigger_payload: JOB_SPEC,
        eval_detail: { job_spec: JOB_SPEC, recorded_side_effects: [] },
      },
    ]);
    await expandRow();

    const panel = await screen.findByTestId("job-spec-evidence");
    expect(panel).toHaveTextContent("weekly-compliance");
    expect(panel).toHaveTextContent(/fed as input_payload/i);
  });

  it("falls back to detail.job_spec when the row has no trigger_payload", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        dimension_scores: { response: 0.9 },
        composite: 0.9,
        trigger_payload: null,
        eval_detail: { job_spec: JOB_SPEC, recorded_side_effects: [] },
      },
    ]);
    await expandRow();

    expect(await screen.findByTestId("job-spec-evidence")).toHaveTextContent(
      "weekly-compliance",
    );
  });

  it("renders the job spec on a FAIL-CLOSED row (what was fired, even unscored)", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        response: "",
        passed: false,
        judge_score: 0.0,
        judge_reasoning:
          "item asserts side effects but the eval_mode=record run recorded none",
        dimension_scores: null,
        composite: null,
        trigger_payload: JOB_SPEC,
        eval_detail: { reason: "side effect unverifiable — fail-closed" },
      },
    ]);
    await expandRow();

    expect(await screen.findByTestId("job-spec-evidence")).toHaveTextContent(
      "weekly-compliance",
    );
    // Fail-closed: no dimension was scored, so none renders a number.
    expect(screen.getByTestId("dim-side_effect")).toHaveTextContent("—");
  });

  it("renders the job spec alongside the REUSED recorded-side-effect panel", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        dimension_scores: { response: 0.9, side_effect: 1.0 },
        composite: 0.94,
        trigger_payload: JOB_SPEC,
        eval_detail: {
          job_spec: JOB_SPEC,
          recorded_side_effects: [
            {
              tool: "send_email",
              args: { to: "compliance@acme.com" },
              mocked_response: { status: "ok" },
              would_have_invoked: "POST https://mail.internal/send",
            },
          ],
          side_effect_detail: {
            side_effect_diffs: [
              {
                tool: "send_email",
                occurs: "exactly",
                count: 1,
                matched: 1,
                satisfied: true,
              },
            ],
            recorded: [],
          },
        },
      },
    ]);
    await expandRow();

    expect(await screen.findByTestId("job-spec-evidence")).toBeInTheDocument();
    // The E-2 panel is REUSED, not rebuilt: job spec in, recorded call out.
    expect(screen.getByTestId("side-effect-evidence")).toBeInTheDocument();
    expect(screen.getByTestId("recorded-side-effect-0")).toHaveTextContent(
      /not delivered/i,
    );
  });

  it("renders no job-spec panel for a non-scheduled result (no regression)", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        input_message: "Report the ACME breach",
        dimension_scores: { response: 0.9 },
        composite: 0.9,
        eval_detail: { recorded_side_effects: [] },
      },
    ]);
    renderPage();
    fireEvent.click(await screen.findByText("Report the ACME breach"));

    await waitFor(() =>
      expect(screen.queryByTestId("job-spec-evidence")).not.toBeInTheDocument(),
    );
  });
});

// ---------------------------------------------------------------------------
// Eval v2 E-4 — webhook results. Three things must be legible to a human scanning
// the page, or the eval is decorative:
//   1. the FILTER VERDICT (a correctly-filtered event is a PASS with no run at all —
//      an empty row must not read as a broken eval);
//   2. the SYNTHETIC EVENT that was fired (labelled as an event, not a "job spec" —
//      both ride the same `trigger_payload` column, so the label comes from the run's
//      explicit mode);
//   3. ASR *and* UTILITY side by side — an agent that refuses everything scores a
//      perfect ASR and is useless, and ASR alone would grade it flawless.
// ---------------------------------------------------------------------------
describe("EvalResultsPage — webhook filter + injection evidence (Eval v2 E-4)", () => {
  const MATCH_EVENT = { event_type: "payment.fail", order_id: "12345" };
  const MISS_EVENT = { event_type: "payment.ok", order_id: "67890" };

  const evalRunBase = {
    id: "run-1",
    user_id: "u1",
    agent_name: "payment-watcher",
    agent_version_id: "v1",
    workflow_id: null,
    workflow_version_id: null,
    dataset_id: "ds-1",
    status: "completed",
    total_items: 1,
    passed_count: 1,
    failed_count: 0,
    overall_score: 0.9,
    started_at: NOW,
    completed_at: NOW,
    created_at: NOW,
    sandbox_deployment_id: "dep-1",
    workflow_deployment_id: null,
    mode: "webhook" as const,
  };

  const resultBase = {
    id: "res-1",
    eval_run_id: "run-1",
    dataset_item_idx: 0,
    input_message: JSON.stringify(MISS_EVENT),
    expected_output: null,
    response: "",
    judge_score: 1.0,
    judge_reasoning: "webhook eval (mode=webhook, matched=False)",
    passed: true,
    langfuse_trace_id: null,
    trace_url: null,
    run_id: null,
    created_at: NOW,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mock(getEvalRun).mockResolvedValue(evalRunBase);
    mock(listRunSteps).mockResolvedValue([]);
  });

  it("a correctly FILTERED event renders the filter verdict, the synthetic event, and NO action dimensions", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        matched: false,
        dimension_scores: { filter: 1.0 },
        composite: 1.0,
        trigger_payload: MISS_EVENT,
        eval_detail: {
          matched: false,
          filter_reason: "event_type='payment.ok', expected 'payment.fail'",
          filter_detail: {
            matched: false,
            expected_match: false,
            expected_filter_reason: "payment.ok",
            reason_matched: true,
          },
          recorded_side_effects: [],
        },
      },
    ] as never);
    renderPage();
    fireEvent.click(await screen.findByText(/payment\.ok/));

    const verdict = await screen.findByTestId("filter-verdict");
    expect(verdict).toHaveTextContent(/filtered — nothing ran/i);
    expect(verdict).toHaveTextContent(/correct decision/i);
    expect(verdict).toHaveTextContent(/expected 'payment\.fail'/);
    // "nothing ran" must be legible as the PASS, not as a missing result.
    expect(verdict).toHaveTextContent(/whole job of a filter is to not run/i);

    // The event is labelled an EVENT, not a job spec (same column, explicit mode).
    expect(screen.getByTestId("synthetic-event-evidence")).toHaveTextContent(/payment\.ok/);
    expect(screen.queryByTestId("job-spec-evidence")).not.toBeInTheDocument();

    // filter scored; every action dimension absent (present-dims-only, not zeros).
    expect(screen.getByTestId("dim-filter")).toHaveTextContent("filter: 1.00");
    expect(screen.getByTestId("dim-response")).toHaveTextContent("—");
    expect(screen.getByTestId("dim-side_effect")).toHaveTextContent("—");
    expect(screen.getByTestId("dim-injection")).toHaveTextContent("—");
  });

  it("a FILTER ERROR renders the error verdict and the veto that forced the composite to 0", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        input_message: JSON.stringify(MISS_EVENT),
        matched: false,
        passed: false,
        judge_score: 0.0,
        dimension_scores: { filter: 0.0 },
        composite: 0.0,
        trigger_payload: MISS_EVENT,
        eval_detail: {
          matched: false,
          filter_reason: "event_type='payment.ok', expected 'payment.fail'",
          filter_detail: { matched: false, expected_match: true },
          veto: ["filter_error"],
          recorded_side_effects: [],
        },
      },
    ] as never);
    renderPage();
    fireEvent.click(await screen.findByText(/payment\.ok/));

    const verdict = await screen.findByTestId("filter-verdict");
    expect(verdict).toHaveTextContent(/FILTER ERROR/);
    expect(screen.getByTestId("filter-veto")).toHaveTextContent(/filter_error/);
    expect(screen.getByTestId("filter-veto")).toHaveTextContent(/forced to 0/i);
  });

  it("renders ASR and utility SIDE BY SIDE for a clean injection probe", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        input_message: JSON.stringify(MATCH_EVENT),
        matched: true,
        run_id: "pgrun-1234abcd",
        dimension_scores: { filter: 1.0, response: 0.9, injection: 1.0 },
        composite: 0.95,
        trigger_payload: MATCH_EVENT,
        eval_detail: {
          matched: true,
          filter_reason: "all rules matched",
          filter_detail: { matched: true, expected_match: true },
          asr: 0.0,
          utility: 0.9,
          forbidden_called: [],
          injection_detail: {
            asr: 0.0,
            utility: 0.9,
            forbidden_called: [],
            refused: false,
            must_not_call: ["wire_transfer"],
            must_refuse: false,
          },
          recorded_side_effects: [],
        },
      },
    ] as never);
    renderPage();
    fireEvent.click(await screen.findByText(/payment\.fail/));

    expect(await screen.findByTestId("filter-verdict")).toHaveTextContent(
      /matched — the agent ran/i,
    );
    // BOTH halves must render — ASR alone would grade a refuse-everything agent perfect.
    expect(screen.getByTestId("injection-asr")).toHaveTextContent("0.00");
    expect(screen.getByTestId("injection-asr")).toHaveTextContent(/no forbidden tool fired/i);
    expect(screen.getByTestId("injection-utility")).toHaveTextContent("0.90");
    expect(screen.getByTestId("dim-injection")).toHaveTextContent("injection: 1.00");
  });

  it("a FIRED forbidden tool renders ASR 1.0 and names the tool", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        input_message: JSON.stringify(MATCH_EVENT),
        matched: true,
        passed: false,
        run_id: "pgrun-1234abcd",
        dimension_scores: { filter: 1.0, response: 0.9, injection: 0.0 },
        composite: 0.0,
        trigger_payload: MATCH_EVENT,
        eval_detail: {
          matched: true,
          filter_detail: { matched: true, expected_match: true },
          asr: 1.0,
          utility: 0.9,
          forbidden_called: ["wire_transfer"],
          injection_detail: {
            asr: 1.0,
            utility: 0.9,
            forbidden_called: ["wire_transfer"],
            must_not_call: ["wire_transfer"],
          },
          veto: ["injection_succeeded"],
          recorded_side_effects: [],
        },
      },
    ] as never);
    renderPage();
    fireEvent.click(await screen.findByText(/payment\.fail/));

    const asr = await screen.findByTestId("injection-asr");
    expect(asr).toHaveTextContent("1.00");
    expect(asr).toHaveTextContent(/really fired a forbidden tool/i);
    expect(screen.getByTestId("injection-evidence")).toHaveTextContent(/wire_transfer/);
    expect(screen.getByTestId("filter-veto")).toHaveTextContent(/injection_succeeded/);
  });

  it("a probe that rode along on a FILTERED event reports not-exercised (never a silent 1.0)", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        matched: false,
        dimension_scores: { filter: 1.0 },
        composite: 1.0,
        trigger_payload: MISS_EVENT,
        eval_detail: {
          matched: false,
          filter_detail: { matched: false, expected_match: false },
          injection_not_exercised: true,
          recorded_side_effects: [],
        },
      },
    ] as never);
    renderPage();
    fireEvent.click(await screen.findByText(/payment\.ok/));

    expect(await screen.findByTestId("injection-evidence")).toHaveTextContent(
      /not exercised/i,
    );
    expect(screen.getByTestId("dim-injection")).toHaveTextContent("—");
  });

  it("a row that fail-closed BEFORE firing says NO DECISION — never renders as 'filtered'", async () => {
    mock(getEvalRunResults).mockResolvedValue([
      {
        ...resultBase,
        // The run never happened, but the row still records the event that WOULD have
        // been fired — as `input_message` and `trigger_payload` alike.
        input_message: JSON.stringify(MATCH_EVENT),
        matched: null,
        passed: false,
        judge_score: 0.0,
        dimension_scores: null,
        composite: null,
        trigger_payload: MATCH_EVENT,
        eval_detail: { reason: "item asserts side effects but the agent is reactive-inner" },
      },
    ] as never);
    renderPage();
    fireEvent.click(await screen.findByText(/payment\.fail/));

    const verdict = await screen.findByTestId("filter-verdict");
    expect(verdict).toHaveTextContent(/No filter decision was recorded/i);
    expect(verdict).toHaveTextContent(/not a "filtered" result/i);
    expect(verdict).not.toHaveTextContent(/correct decision/i);
    // The event that WOULD have been fired is still shown — a fail-closed row with no
    // evidence is unreadable.
    expect(screen.getByTestId("synthetic-event-evidence")).toHaveTextContent(/payment\.fail/);
  });
});

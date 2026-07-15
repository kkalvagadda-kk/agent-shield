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
    expect(screen.getByTestId("dim-side_effects")).toHaveTextContent("—");
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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import EvalResultsPage from "./EvalResultsPage";

vi.mock("../api/playgroundApi", () => ({
  createEvalRun: vi.fn(),
  getEvalRun: vi.fn(),
  getEvalRunResults: vi.fn(),
}));

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

import { getEvalRun, getEvalRunResults } from "../api/playgroundApi";

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
    expect(screen.getByTestId("dim-member")).toHaveTextContent("—");

    // Existing composite/overall display is preserved.
    expect(screen.getByText("Overall Score")).toBeInTheDocument();
  });
});

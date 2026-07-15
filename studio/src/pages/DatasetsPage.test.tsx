import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import DatasetsPage from "./DatasetsPage";

vi.mock("../api/playgroundApi", () => ({
  listDatasets: vi.fn(),
  listEvalRuns: vi.fn(),
  createDataset: vi.fn(),
  deleteDataset: vi.fn(),
  createEvalRun: vi.fn(),
}));

vi.mock("../api/registryApi", () => ({
  listAllDeployments: vi.fn(),
  listAllWorkflowDeployments: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  listDatasets,
  listEvalRuns,
  createDataset,
} from "../api/playgroundApi";
import { listAllDeployments, listAllWorkflowDeployments } from "../api/registryApi";
import { toast } from "sonner";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

describe("DatasetsPage — mode selector (Eval v2 E-0)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(listDatasets).mockResolvedValue([]);
    mock(listEvalRuns).mockResolvedValue([]);
    mock(createDataset).mockResolvedValue({
      id: "ds-1",
      owner_user_id: "u1",
      name: "new-ds",
      mode: "reactive",
      schema_version: 1,
      items: [],
      created_at: new Date().toISOString(),
    });
    mock(listAllDeployments).mockResolvedValue({ items: [], total: 0 });
    mock(listAllWorkflowDeployments).mockResolvedValue([]);
  });

  it("defaults the create-dataset mode selector to reactive", async () => {
    renderWithProviders(<DatasetsPage />);

    fireEvent.click(await screen.findByRole("button", { name: /New Dataset/i }));

    const select = (await screen.findByLabelText("Dataset mode")) as HTMLSelectElement;
    expect(select.value).toBe("reactive");
    // Reactive item editor is shown for the default mode.
    expect(screen.getByPlaceholderText(/expected_output/)).toBeInTheDocument();
  });

  it("submits the chosen mode when creating a dataset (reactive → items authored)", async () => {
    renderWithProviders(<DatasetsPage />);

    fireEvent.click(await screen.findByRole("button", { name: /New Dataset/i }));
    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "my-dataset" },
    });
    fireEvent.change(screen.getByPlaceholderText(/expected_output/), {
      target: { value: '{"input": "hi", "expected_output": "hello"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() =>
      expect(createDataset).toHaveBeenCalledWith({
        name: "my-dataset",
        mode: "reactive",
        items: [{ input: "hi", expected_output: "hello" }],
      }),
    );
  });

  it("disables the item editor for still-unbuilt modes and shows a hint", async () => {
    renderWithProviders(<DatasetsPage />);

    fireEvent.click(await screen.findByRole("button", { name: /New Dataset/i }));
    const select = (await screen.findByLabelText("Dataset mode")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "webhook" } });

    expect(select.value).toBe("webhook");
    // Reactive editor is gone; disabled placeholder editor + "coming later" hint.
    expect(screen.queryByPlaceholderText(/expected_output/)).not.toBeInTheDocument();
    const disabledEditor = screen.getByLabelText("Items editor (disabled)") as HTMLTextAreaElement;
    expect(disabledEditor).toBeDisabled();
    expect(screen.getByText(/coming later/i)).toBeInTheDocument();
  });
});

describe("DatasetsPage — durable item editor (Eval v2 E-1)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(listDatasets).mockResolvedValue([]);
    mock(listEvalRuns).mockResolvedValue([]);
    mock(createDataset).mockResolvedValue({
      id: "ds-2",
      owner_user_id: "u1",
      name: "durable-ds",
      mode: "durable",
      schema_version: 1,
      items: [],
      created_at: new Date().toISOString(),
    });
    mock(listAllDeployments).mockResolvedValue({ items: [], total: 0 });
    mock(listAllWorkflowDeployments).mockResolvedValue([]);
  });

  async function openDurableEditor() {
    renderWithProviders(<DatasetsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /New Dataset/i }));
    const select = (await screen.findByLabelText("Dataset mode")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "durable" } });
    return select;
  }

  it("shows the durable trajectory editor (not the reactive/disabled editors)", async () => {
    await openDurableEditor();
    expect(screen.getByLabelText("Durable input payload")).toBeInTheDocument();
    expect(screen.getByLabelText("Trajectory match mode")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/expected_output/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Items editor (disabled)")).not.toBeInTheDocument();
  });

  it("sends a valid expected_trajectory (with expect_approval) on save", async () => {
    await openDurableEditor();

    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "durable-ds" },
    });
    fireEvent.change(screen.getByLabelText("Durable input payload"), {
      target: { value: '{"contract_url": "s3://demo/acme.pdf"}' },
    });
    // superset is the default match mode; leave it.
    fireEvent.click(screen.getByRole("button", { name: /Add step/i }));
    fireEvent.change(screen.getByLabelText("Step 1 tool"), {
      target: { value: "jira_create" },
    });
    fireEvent.change(screen.getByLabelText("Step 1 args match"), {
      target: { value: '{"project": "LEG"}' },
    });
    fireEvent.click(screen.getByLabelText("Step 1 expect approval"));

    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() =>
      expect(createDataset).toHaveBeenCalledWith({
        name: "durable-ds",
        mode: "durable",
        items: [
          {
            kind: "durable",
            input_payload: { contract_url: "s3://demo/acme.pdf" },
            expected_trajectory: {
              match_mode: "superset",
              steps: [
                {
                  tool: "jira_create",
                  args_match: { project: "LEG" },
                  expect_approval: true,
                },
              ],
            },
          },
        ],
      }),
    );
  });

  it("rejects a malformed input_payload before POST", async () => {
    await openDurableEditor();
    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "durable-ds" },
    });
    fireEvent.change(screen.getByLabelText("Durable input payload"), {
      target: { value: "{not valid json" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(createDataset).not.toHaveBeenCalled();
  });

  it("rejects a malformed args_match JSON before POST", async () => {
    await openDurableEditor();
    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "durable-ds" },
    });
    fireEvent.change(screen.getByLabelText("Durable input payload"), {
      target: { value: '{"contract_url": "s3://demo/acme.pdf"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add step/i }));
    fireEvent.change(screen.getByLabelText("Step 1 tool"), {
      target: { value: "jira_create" },
    });
    fireEvent.change(screen.getByLabelText("Step 1 args match"), {
      target: { value: "{bad json" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(createDataset).not.toHaveBeenCalled();
  });

  it("allows a reference-free durable item (no steps) and omits expected_trajectory", async () => {
    await openDurableEditor();
    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "durable-ds" },
    });
    fireEvent.change(screen.getByLabelText("Durable input payload"), {
      target: { value: '{"contract_url": "s3://demo/acme.pdf"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() =>
      expect(createDataset).toHaveBeenCalledWith({
        name: "durable-ds",
        mode: "durable",
        items: [
          { kind: "durable", input_payload: { contract_url: "s3://demo/acme.pdf" } },
        ],
      }),
    );
  });
});

describe("DatasetsPage — workflow item editor (Eval v2 E-5)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(listDatasets).mockResolvedValue([]);
    mock(listEvalRuns).mockResolvedValue([]);
    mock(createDataset).mockResolvedValue({
      id: "ds-3",
      owner_user_id: "u1",
      name: "workflow-ds",
      mode: "workflow",
      schema_version: 1,
      items: [],
      created_at: new Date().toISOString(),
    });
    mock(listAllDeployments).mockResolvedValue({ items: [], total: 0 });
    mock(listAllWorkflowDeployments).mockResolvedValue([]);
  });

  async function openWorkflowEditor() {
    renderWithProviders(<DatasetsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /New Dataset/i }));
    const select = (await screen.findByLabelText("Dataset mode")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "workflow" } });
    return select;
  }

  it("shows the workflow run-tree editor (not the reactive/disabled editors)", async () => {
    await openWorkflowEditor();
    expect(screen.getByLabelText("Workflow input message")).toBeInTheDocument();
    expect(screen.getByLabelText("Member path match mode")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/expected_output/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Items editor (disabled)")).not.toBeInTheDocument();
  });

  it("sends a valid expected_member_path (+ per_member rubric) on save", async () => {
    await openWorkflowEditor();

    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "workflow-ds" },
    });
    fireEvent.change(screen.getByLabelText("Workflow input message"), {
      target: { value: "Refund for order 123 never arrived" },
    });
    // ordered is the default member-path match mode; leave it.
    fireEvent.click(screen.getByRole("button", { name: /Add member/i }));
    fireEvent.change(screen.getByLabelText("Member 1 name"), {
      target: { value: "intake" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add member/i }));
    fireEvent.change(screen.getByLabelText("Member 2 name"), {
      target: { value: "triage" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add member/i }));
    fireEvent.change(screen.getByLabelText("Member 3 name"), {
      target: { value: "resolver" },
    });
    // One per-member rubric zooming into the triage member.
    fireEvent.click(screen.getByRole("button", { name: /Add rubric/i }));
    fireEvent.change(screen.getByLabelText("Per-member 1 name"), {
      target: { value: "triage" },
    });
    fireEvent.change(screen.getByLabelText("Per-member 1 rubric"), {
      target: { value: "correctly routed to billing" },
    });

    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() =>
      expect(createDataset).toHaveBeenCalledWith({
        name: "workflow-ds",
        mode: "workflow",
        items: [
          {
            kind: "workflow",
            input_message: "Refund for order 123 never arrived",
            expected_member_path: ["intake", "triage", "resolver"],
            match_mode: "ordered",
            per_member: { triage: { rubric: "correctly routed to billing" } },
          },
        ],
      }),
    );
  });

  it("rejects a blank member row in the expected_member_path before POST", async () => {
    await openWorkflowEditor();
    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "workflow-ds" },
    });
    fireEvent.change(screen.getByLabelText("Workflow input message"), {
      target: { value: "Refund for order 123" },
    });
    // Add a member row but leave it blank → invalid expected_member_path.
    fireEvent.click(screen.getByRole("button", { name: /Add member/i }));
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(createDataset).not.toHaveBeenCalled();
  });

  it("rejects a missing input message before POST", async () => {
    await openWorkflowEditor();
    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "workflow-ds" },
    });
    // No input message.
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(createDataset).not.toHaveBeenCalled();
  });

  it("allows a reference-free workflow item (no members) and omits expected_member_path", async () => {
    await openWorkflowEditor();
    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "workflow-ds" },
    });
    fireEvent.change(screen.getByLabelText("Workflow input message"), {
      target: { value: "Say hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() =>
      expect(createDataset).toHaveBeenCalledWith({
        name: "workflow-ds",
        mode: "workflow",
        items: [{ kind: "workflow", input_message: "Say hello" }],
      }),
    );
  });
});

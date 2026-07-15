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

// Eval v2 E-2 — authoring `expected_side_effects` on a durable item. Its PRESENCE on
// the saved item is what makes the eval-runner launch that item under
// `eval_mode=record`, so the write tools are recorded + mocked instead of really
// firing. These tests assert the built POST body — the save path, not just the form.
describe("DatasetsPage — expected side effects (Eval v2 E-2)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(listDatasets).mockResolvedValue([]);
    mock(listEvalRuns).mockResolvedValue([]);
    mock(createDataset).mockResolvedValue({
      id: "ds-4",
      owner_user_id: "u1",
      name: "side-effect-ds",
      mode: "durable",
      schema_version: 1,
      items: [],
      created_at: new Date().toISOString(),
    });
    mock(listAllDeployments).mockResolvedValue({ items: [], total: 0 });
    mock(listAllWorkflowDeployments).mockResolvedValue([]);
  });

  async function openDurableEditorNamed() {
    renderWithProviders(<DatasetsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /New Dataset/i }));
    fireEvent.change(await screen.findByLabelText("Dataset mode"), {
      target: { value: "durable" },
    });
    fireEvent.change(screen.getByPlaceholderText(/order-lookup-tests/), {
      target: { value: "side-effect-ds" },
    });
    fireEvent.change(screen.getByLabelText("Durable input payload"), {
      target: { value: '{"breach_id": "B-9"}' },
    });
  }

  it("shows the side-effect editor with an empty (live) default state", async () => {
    await openDurableEditorNamed();
    expect(screen.getByText(/Expected Side Effects/i)).toBeInTheDocument();
    // No assertions authored → the item runs live and delivers for real. (The
    // `live`/`record` words sit in <code> tags, so match the surrounding prose —
    // getByText joins only an element's direct text nodes.)
    expect(
      screen.getByText(/and its tool calls are delivered for real/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("side-effect-0")).not.toBeInTheDocument();
  });

  it("warns that the item will run in record mode once an assertion is added", async () => {
    await openDurableEditorNamed();
    fireEvent.click(screen.getByRole("button", { name: /Add side effect/i }));
    expect(screen.getByTestId("side-effect-0")).toBeInTheDocument();
    expect(
      screen.getByText(/no real emails, tickets, or payments are sent/i),
    ).toBeInTheDocument();
  });

  it("sends expected_side_effects (exactly N + args_match) on save", async () => {
    await openDurableEditorNamed();
    fireEvent.click(screen.getByRole("button", { name: /Add side effect/i }));
    fireEvent.change(screen.getByLabelText("Side effect 1 tool"), {
      target: { value: "send_email" },
    });
    fireEvent.change(screen.getByLabelText("Side effect 1 args match"), {
      target: { value: '{"to": "compliance@acme.com"}' },
    });
    // `exactly` / count 1 are the defaults; leave them.
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() =>
      expect(createDataset).toHaveBeenCalledWith({
        name: "side-effect-ds",
        mode: "durable",
        items: [
          {
            kind: "durable",
            input_payload: { breach_id: "B-9" },
            expected_side_effects: [
              {
                tool: "send_email",
                occurs: "exactly",
                args_match: { to: "compliance@acme.com" },
                count: 1,
              },
            ],
          },
        ],
      }),
    );
  });

  it("omits count for a `never` assertion (a count on an absence is meaningless)", async () => {
    await openDurableEditorNamed();
    fireEvent.click(screen.getByRole("button", { name: /Add side effect/i }));
    fireEvent.change(screen.getByLabelText("Side effect 1 tool"), {
      target: { value: "issue_refund" },
    });
    fireEvent.change(screen.getByLabelText("Side effect 1 occurs"), {
      target: { value: "never" },
    });
    // The count field is hidden for `never`.
    expect(screen.queryByLabelText("Side effect 1 count")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() =>
      expect(createDataset).toHaveBeenCalledWith({
        name: "side-effect-ds",
        mode: "durable",
        items: [
          {
            kind: "durable",
            input_payload: { breach_id: "B-9" },
            expected_side_effects: [{ tool: "issue_refund", occurs: "never" }],
          },
        ],
      }),
    );
  });

  it("sends at_least with the authored count", async () => {
    await openDurableEditorNamed();
    fireEvent.click(screen.getByRole("button", { name: /Add side effect/i }));
    fireEvent.change(screen.getByLabelText("Side effect 1 tool"), {
      target: { value: "send_email" },
    });
    fireEvent.change(screen.getByLabelText("Side effect 1 occurs"), {
      target: { value: "at_least" },
    });
    fireEvent.change(screen.getByLabelText("Side effect 1 count"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() =>
      expect(createDataset).toHaveBeenCalledWith(
        expect.objectContaining({
          items: [
            expect.objectContaining({
              expected_side_effects: [
                { tool: "send_email", occurs: "at_least", count: 2 },
              ],
            }),
          ],
        }),
      ),
    );
  });

  it("rejects a blank side-effect tool name before POST", async () => {
    await openDurableEditorNamed();
    fireEvent.click(screen.getByRole("button", { name: /Add side effect/i }));
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(createDataset).not.toHaveBeenCalled();
  });

  it("rejects a malformed side-effect args_match before POST", async () => {
    await openDurableEditorNamed();
    fireEvent.click(screen.getByRole("button", { name: /Add side effect/i }));
    fireEvent.change(screen.getByLabelText("Side effect 1 tool"), {
      target: { value: "send_email" },
    });
    fireEvent.change(screen.getByLabelText("Side effect 1 args match"), {
      target: { value: "{bad json" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(createDataset).not.toHaveBeenCalled();
  });

  it("rejects a non-positive count before POST", async () => {
    await openDurableEditorNamed();
    fireEvent.click(screen.getByRole("button", { name: /Add side effect/i }));
    fireEvent.change(screen.getByLabelText("Side effect 1 tool"), {
      target: { value: "send_email" },
    });
    fireEvent.change(screen.getByLabelText("Side effect 1 count"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create Dataset/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(createDataset).not.toHaveBeenCalled();
  });

  it("removes an authored side effect", async () => {
    await openDurableEditorNamed();
    fireEvent.click(screen.getByRole("button", { name: /Add side effect/i }));
    expect(screen.getByTestId("side-effect-0")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Remove side effect 1"));
    expect(screen.queryByTestId("side-effect-0")).not.toBeInTheDocument();
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

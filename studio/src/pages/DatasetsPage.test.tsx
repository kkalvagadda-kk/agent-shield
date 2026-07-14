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

  it("disables the item editor for non-reactive modes and shows an E-1 hint", async () => {
    renderWithProviders(<DatasetsPage />);

    fireEvent.click(await screen.findByRole("button", { name: /New Dataset/i }));
    const select = (await screen.findByLabelText("Dataset mode")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "webhook" } });

    expect(select.value).toBe("webhook");
    // Reactive editor is gone; disabled placeholder editor + "coming in E-1" hint.
    expect(screen.queryByPlaceholderText(/expected_output/)).not.toBeInTheDocument();
    const disabledEditor = screen.getByLabelText("Items editor (disabled)") as HTMLTextAreaElement;
    expect(disabledEditor).toBeDisabled();
    expect(screen.getByText(/coming in E-1/i)).toBeInTheDocument();
  });
});

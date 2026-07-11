import { describe, it, expect, vi, beforeEach } from "vitest";
import { useEffect } from "react";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import PlaygroundPage from "./PlaygroundPage";
import * as api from "../api/registryApi";

// Auto-select an agent version on mount so the "Promote" panel renders.
vi.mock("../components/playground/VersionSelector", () => ({
  default: ({ onSelect }: { onSelect: (name: string, sel: unknown) => void }) => {
    // Run once on mount. PlaygroundPage passes a fresh inline onSelect each render,
    // so depending on it would loop (select → setState → re-render → new fn → refire).
    useEffect(() => {
      onSelect("risky-agent", { agentName: "risky-agent", versionId: "ver-1", deploymentId: null });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return <div data-testid="version-selector-stub" />;
  },
}));

// Inert stubs for the heavy playground children (streaming/canvas/etc.).
vi.mock("../components/playground/ChatPane", () => ({ default: () => <div /> }));
vi.mock("../components/playground/InteractionSurface", () => ({ default: () => <div /> }));
vi.mock("../components/playground/HitlPanel", () => ({ default: () => <div /> }));
vi.mock("../components/playground/TracePanel", () => ({ default: () => <div /> }));
vi.mock("../components/playground/WorkflowSelector", () => ({ default: () => <div /> }));

vi.mock("../api/registryApi", () => ({
  getAgent: vi.fn().mockResolvedValue({ name: "risky-agent" }),
  listTriggers: vi.fn().mockResolvedValue([]),
  patchVersion: vi.fn().mockResolvedValue({}),
  patchWorkflowVersion: vi.fn().mockResolvedValue({}),
  publishAgent: vi.fn().mockResolvedValue({ publish_request_id: "pr-1" }),
  publishWorkflow: vi.fn().mockResolvedValue({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("PlaygroundPage — adversarial-eval promotion", () => {
  beforeEach(() => vi.clearAllMocks());

  it("'Mark Adversarial Passed' PATCHes adversarial_eval_passed=true on the selected version", async () => {
    renderWithProviders(<PlaygroundPage />);

    const btn = await screen.findByRole("button", { name: /Mark Adversarial Passed/i });
    fireEvent.click(btn);

    await waitFor(() =>
      expect(api.patchVersion).toHaveBeenCalledWith("risky-agent", "ver-1", {
        adversarial_eval_passed: true,
      }),
    );
  });

  it("keeps the ordinary eval mark separate (does not set adversarial_eval_passed)", async () => {
    renderWithProviders(<PlaygroundPage />);

    const btn = await screen.findByRole("button", { name: /Mark Version Passed/i });
    fireEvent.click(btn);

    await waitFor(() =>
      expect(api.patchVersion).toHaveBeenCalledWith("risky-agent", "ver-1", {
        eval_passed: true,
      }),
    );
    // The eval mark must NOT smuggle the adversarial flag — it's a distinct sign-off.
    expect(api.patchVersion).not.toHaveBeenCalledWith(
      "risky-agent",
      "ver-1",
      expect.objectContaining({ adversarial_eval_passed: true }),
    );
  });
});

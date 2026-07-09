import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import DeploymentActions from "./DeploymentActions";
import type { Deployment } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  updateSandboxDeployment: vi.fn(),
  listVersions: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { updateSandboxDeployment, listVersions } from "../../api/registryApi";
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function dep(overrides: Partial<Deployment> = {}): Deployment {
  return {
    id: "dep-1",
    agent_id: "a1",
    agent_name: "my-agent",
    version_id: "v1",
    environment: "sandbox",
    status: "running",
    replicas: 1,
    canary_percent: null,
    k8s_namespace: "agents-default",
    k8s_deployment_name: "my-agent-sandbox",
    error_message: null,
    deployed_at: new Date().toISOString(),
    terminated_at: null,
    deployed_by: null,
    previous_version_id: null,
    name: "my-agent-ab12",
    suspended_at: null,
    ttl_hours: null,
    ...overrides,
  };
}

describe("DeploymentActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(updateSandboxDeployment).mockResolvedValue(dep());
    mock(listVersions).mockResolvedValue([
      { id: "v1", version_number: 1 },
      { id: "v2", version_number: 2 },
    ]);
  });

  it("running → shows Suspend / Upgrade / Terminate", () => {
    renderWithProviders(<DeploymentActions agentName="my-agent" deployment={dep()} />);
    expect(screen.getByRole("button", { name: /Suspend/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upgrade/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Terminate/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Resume/ })).not.toBeInTheDocument();
  });

  it("suspended → shows Resume + Terminate, not Suspend", () => {
    renderWithProviders(<DeploymentActions agentName="my-agent" deployment={dep({ status: "suspended" })} />);
    expect(screen.getByRole("button", { name: /Resume/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Terminate/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Suspend/ })).not.toBeInTheDocument();
  });

  it("transitional state → shows a spinner label, no action buttons", () => {
    renderWithProviders(<DeploymentActions agentName="my-agent" deployment={dep({ status: "suspending" })} />);
    expect(screen.getByText(/suspending…/i)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("clicking Suspend calls updateSandboxDeployment('suspend')", async () => {
    renderWithProviders(<DeploymentActions agentName="my-agent" deployment={dep()} />);
    await userEvent.click(screen.getByRole("button", { name: /Suspend/ }));
    await waitFor(() =>
      expect(updateSandboxDeployment).toHaveBeenCalledWith("my-agent", "dep-1", "suspend", undefined)
    );
  });

  it("Upgrade opens modal, picks a version and calls upgrade", async () => {
    renderWithProviders(<DeploymentActions agentName="my-agent" deployment={dep()} />);
    await userEvent.click(screen.getByRole("button", { name: /Upgrade/ }));
    // Modal appears with heading
    expect(await screen.findByText("Upgrade Deployment")).toBeInTheDocument();
    const select = await screen.findByRole("combobox");
    await userEvent.selectOptions(select, "v2");
    // The submit button in the modal also says "Upgrade"
    const buttons = screen.getAllByRole("button", { name: /Upgrade/ });
    const submitBtn = buttons.find((b) => b.classList.contains("btn-primary"));
    await userEvent.click(submitBtn!);
    await waitFor(() =>
      expect(updateSandboxDeployment).toHaveBeenCalledWith("my-agent", "dep-1", "upgrade", "v2")
    );
  });
});

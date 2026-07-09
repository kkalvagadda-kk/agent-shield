import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import VersionSelector from "./VersionSelector";
import type { Deployment } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  listAllDeployments: vi.fn(),
}));

import { listAllDeployments } from "../../api/registryApi";

const NOW = new Date().toISOString();

const DEPLOYMENTS: Deployment[] = [
  {
    id: "d1",
    agent_id: "a1",
    agent_name: "billing-bot",
    version_id: "v1",
    environment: "sandbox",
    status: "running",
    replicas: 1,
    canary_percent: null,
    k8s_namespace: "agents-default",
    k8s_deployment_name: "billing-bot-abc1",
    error_message: null,
    deployed_at: NOW,
    terminated_at: null,
    deployed_by: "user1",
    previous_version_id: null,
    name: "billing-bot-abc1",
    suspended_at: null,
    ttl_hours: null,
  },
  {
    id: "d2",
    agent_id: "a2",
    agent_name: "support-bot",
    version_id: "v2",
    environment: "sandbox",
    status: "running",
    replicas: 1,
    canary_percent: null,
    k8s_namespace: "agents-default",
    k8s_deployment_name: "support-bot-xyz2",
    error_message: null,
    deployed_at: NOW,
    terminated_at: null,
    deployed_by: "user2",
    previous_version_id: null,
    name: "support-bot-xyz2",
    suspended_at: null,
    ttl_hours: null,
  },
];

describe("VersionSelector", () => {
  beforeEach(() => {
    (listAllDeployments as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: DEPLOYMENTS,
      total: DEPLOYMENTS.length,
    });
  });

  it("renders a 'Select Deployment' label", () => {
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={vi.fn()} />
    );
    expect(screen.getByText(/select deployment/i)).toBeInTheDocument();
  });

  it("renders deployment options in the dropdown after loading", async () => {
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={vi.fn()} />
    );

    await waitFor(() =>
      expect(screen.queryByText("Loading deployments…")).not.toBeInTheDocument()
    );

    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();
    const options = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toContain("billing-bot");
    expect(options).toContain("support-bot");
  });

  it("shows loading placeholder while fetching", () => {
    (listAllDeployments as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={vi.fn()} />
    );
    expect(screen.getByText("Loading deployments…")).toBeInTheDocument();
  });

  it("calls onSelect with the agent name when an option is chosen", async () => {
    const onSelect = vi.fn();
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={onSelect} />
    );

    await waitFor(() =>
      expect(screen.queryByText("Loading deployments…")).not.toBeInTheDocument()
    );

    await userEvent.selectOptions(screen.getByRole("combobox"), "billing-bot");
    expect(onSelect).toHaveBeenCalledWith("billing-bot", {
      agentName: "billing-bot",
      versionId: "v1",
      deploymentId: "d1",
    });
  });

  it("shows deployment info when an agent is selected", async () => {
    renderWithProviders(
      <VersionSelector selectedAgent="billing-bot" onSelect={vi.fn()} />
    );

    expect(await screen.findByText("running")).toBeInTheDocument();
    expect(await screen.findByText(/Deployment: billing-bot-abc1/)).toBeInTheDocument();
  });

  it("shows empty state when no deployments exist", async () => {
    (listAllDeployments as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
    });
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={vi.fn()} />
    );

    await waitFor(() =>
      expect(screen.queryByText("Loading deployments…")).not.toBeInTheDocument()
    );

    expect(screen.getByText(/no running sandbox deployments/i)).toBeInTheDocument();
  });
});

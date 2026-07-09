import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import DeployModal from "./DeployModal";

const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useNavigate: () => navigateSpy,
}));
vi.mock("../api/registryApi", () => ({ deployAgent: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { deployAgent } from "../api/registryApi";
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

describe("DeployModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(deployAgent).mockResolvedValue({ id: "dep-9", name: "my-agent-ab12" });
  });

  it("deploys the given version to sandbox with replicas + ttl", async () => {
    renderWithProviders(
      <DeployModal agentName="my-agent" versionId="v1" versionLabel="v3" onClose={() => {}} />
    );

    // replicas field defaults to 1; set ttl
    const ttl = screen.getByPlaceholderText("Never");
    await userEvent.clear(ttl);
    await userEvent.type(ttl, "24");

    await userEvent.click(screen.getByRole("button", { name: /^Deploy$/ }));

    await waitFor(() =>
      expect(deployAgent).toHaveBeenCalledWith("my-agent", {
        version_id: "v1",
        environment: "sandbox",
        replicas: 1,
        ttl_hours: 24,
      })
    );
    // navigates to the new deployment overview
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith("/agents/my-agent/d/dep-9"));
  });

  it("omits ttl_hours when the field is blank", async () => {
    renderWithProviders(<DeployModal agentName="my-agent" versionId="v1" onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /^Deploy$/ }));
    await waitFor(() =>
      expect(deployAgent).toHaveBeenCalledWith("my-agent", {
        version_id: "v1",
        environment: "sandbox",
        replicas: 1,
        ttl_hours: undefined,
      })
    );
  });
});

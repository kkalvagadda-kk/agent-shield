import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import ApplicationsPage from "./ApplicationsPage";
import type { Application } from "../api/registryApi";

vi.mock("../api/registryApi", () => ({
  listApplications: vi.fn(),
  createApplication: vi.fn(),
  rotateApplicationSecret: vi.fn(),
  setApplicationEnabled: vi.fn(),
  deleteApplication: vi.fn(),
}));
vi.mock("../contexts/AuthContext", () => ({ useAuth: () => ({ team: "platform" }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  listApplications,
  createApplication,
  rotateApplicationSecret,
  setApplicationEnabled,
  deleteApplication,
} from "../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;
const NOW = new Date().toISOString();

const billing: Application = {
  id: "app1",
  team_name: "platform",
  name: "billing-service",
  enabled: true,
  created_by: "admin",
  created_at: NOW,
  rotated_at: null,
};

describe("ApplicationsPage", () => {
  beforeEach(() => {
    mock(listApplications).mockResolvedValue([]);
    mock(createApplication).mockResolvedValue({
      id: "app1",
      name: "billing-service",
      secret: "whsec_created_once_abc",
      created_at: NOW,
    });
    mock(rotateApplicationSecret).mockResolvedValue({
      id: "app1",
      secret: "whsec_rotated_xyz",
      rotated_at: NOW,
    });
    mock(setApplicationEnabled).mockResolvedValue({ ...billing, enabled: false });
    mock(deleteApplication).mockResolvedValue(undefined);
  });

  it("shows the empty state when the team has no applications", async () => {
    renderWithProviders(<ApplicationsPage />);
    expect(await screen.findByText(/no applications yet/i)).toBeInTheDocument();
  });

  it("creates an application and reveals the secret exactly once", async () => {
    renderWithProviders(<ApplicationsPage />);

    await userEvent.click((await screen.findAllByRole("button", { name: /new application/i }))[0]);
    await userEvent.type(screen.getByLabelText(/application name/i), "billing-service");
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() =>
      expect(createApplication).toHaveBeenCalledWith("platform", { name: "billing-service" })
    );
    const secretEl = await screen.findByTestId("application-secret");
    expect(secretEl).toHaveTextContent("whsec_created_once_abc");
    expect(screen.getByText(/won't be shown again/i)).toBeInTheDocument();

    // Dismissing the reveal hides the secret; it is never re-fetchable.
    await userEvent.click(screen.getByRole("button", { name: /dismiss secret/i }));
    await waitFor(() => expect(screen.queryByTestId("application-secret")).not.toBeInTheDocument());
  });

  it("lists applications without ever exposing a secret field", async () => {
    mock(listApplications).mockResolvedValue([billing]);
    renderWithProviders(<ApplicationsPage />);

    const row = await screen.findByTestId("application-row-billing-service");
    expect(within(row).getByText("billing-service")).toBeInTheDocument();
    expect(within(row).getByText(/enabled/i)).toBeInTheDocument();
    // No secret is present anywhere on the list view.
    expect(screen.queryByTestId("application-secret")).not.toBeInTheDocument();
    await waitFor(() => expect(listApplications).toHaveBeenCalledWith("platform"));
  });

  it("toggles enabled via setApplicationEnabled(false)", async () => {
    mock(listApplications).mockResolvedValue([billing]);
    renderWithProviders(<ApplicationsPage />);

    await userEvent.click(await screen.findByRole("button", { name: /disable billing-service/i }));
    await waitFor(() =>
      expect(setApplicationEnabled).toHaveBeenCalledWith("platform", "app1", false)
    );
  });

  it("rotates the secret and reveals the new one once", async () => {
    mock(listApplications).mockResolvedValue([billing]);
    renderWithProviders(<ApplicationsPage />);

    await userEvent.click(await screen.findByRole("button", { name: /rotate secret for billing-service/i }));
    await waitFor(() => expect(rotateApplicationSecret).toHaveBeenCalledWith("platform", "app1"));
    expect(await screen.findByTestId("application-secret")).toHaveTextContent("whsec_rotated_xyz");
  });

  it("deletes an application after confirmation", async () => {
    mock(listApplications).mockResolvedValue([billing]);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithProviders(<ApplicationsPage />);

    await userEvent.click(await screen.findByRole("button", { name: /delete billing-service/i }));
    await waitFor(() => expect(deleteApplication).toHaveBeenCalledWith("platform", "app1"));
    confirmSpy.mockRestore();
  });

  it("does NOT delete when confirmation is cancelled", async () => {
    mock(listApplications).mockResolvedValue([billing]);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders(<ApplicationsPage />);

    await userEvent.click(await screen.findByRole("button", { name: /delete billing-service/i }));
    expect(deleteApplication).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});

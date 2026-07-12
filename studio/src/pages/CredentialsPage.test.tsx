import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import CredentialsPage from "./CredentialsPage";

vi.mock("../api/registryApi", () => ({
  listAuthConfigs: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  createAuthConfig: vi.fn().mockResolvedValue({ id: "c1", name: "serper-dev" }),
  updateAuthConfig: vi.fn(),
  deleteAuthConfig: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { createAuthConfig } from "../api/registryApi";
import { toast } from "sonner";

async function openCreateForm() {
  renderWithProviders(<CredentialsPage />);
  // "New Credential" appears in header + empty-state; the header button is first.
  const buttons = await screen.findAllByRole("button", { name: /New Credential/i });
  await userEvent.click(buttons[0]);
  await screen.findByText("New Credential", { selector: "h2" });
}

async function fillForm(keyName: string, secretValue: string) {
  await userEvent.type(screen.getByPlaceholderText("e.g. serper-api-key"), "serper-dev");
  await userEvent.type(screen.getByPlaceholderText("e.g. serper_api_key"), keyName);
  await userEvent.type(screen.getByPlaceholderText("Paste secret value"), secretValue);
  await userEvent.click(screen.getByRole("button", { name: /^Create$/ }));
}

describe("CredentialsPage save guard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("saves a valid credential value with the real key in the payload", async () => {
    await openCreateForm();
    await fillForm("serper_api_key", "realKey123ABC");
    await waitFor(() => expect(createAuthConfig).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createAuthConfig).mock.calls[0][0]).toMatchObject({
      name: "serper-dev",
      type: "api_key",
      credentials: { serper_api_key: "realKey123ABC" },
    });
  });

  it("rejects an HTTP-error-shaped value and never POSTs it as a credential", async () => {
    await openCreateForm();
    await fillForm(
      "serper_api_key",
      "Client error '403 Forbidden' for url 'https://google.serper.dev/search'",
    );
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("looks like an HTTP error message"),
      ),
    );
    expect(createAuthConfig).not.toHaveBeenCalled();
  });
});

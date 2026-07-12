import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import CredentialsPage from "./CredentialsPage";
import type { RegistryTool } from "../api/registryApi";

vi.mock("../api/registryApi", async () => {
  const actual = await vi.importActual<typeof import("../api/registryApi")>(
    "../api/registryApi",
  );
  return {
    // Keep the real derivation helpers (expectedCredentialKeys / isValidEnvVarName)
    // so the test exercises the actual tool -> key linkage, not a stub.
    ...actual,
    listAuthConfigs: vi.fn(),
    createAuthConfig: vi.fn(),
    updateAuthConfig: vi.fn(),
    deleteAuthConfig: vi.fn(),
    listTools: vi.fn(),
  };
});

import {
  listAuthConfigs,
  createAuthConfig,
  listTools,
} from "../api/registryApi";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const SERPER_TOOL: RegistryTool = {
  id: "tool-serper",
  name: "web_search",
  display_name: "Web Search",
  description: "Serper.dev search",
  type: "http",
  http_method: "POST",
  http_url: "https://google.serper.dev/search",
  http_headers: { "Content-Type": "application/json", "X-API-KEY": "{{serper_api_key}}" },
  config: {},
};

describe("CredentialsPage — tool-driven key name", () => {
  beforeEach(() => {
    mk(listAuthConfigs).mockResolvedValue({ items: [], total: 0 });
    mk(listTools).mockResolvedValue({ items: [SERPER_TOOL], total: 1 });
    mk(createAuthConfig).mockResolvedValue({
      id: "ac1",
      name: "serper-dev",
      type: "api_key",
      owner_team: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  });

  it("auto-fills and locks Key Name from the selected tool", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CredentialsPage />);

    await user.click((await screen.findAllByRole("button", { name: /new credential/i }))[0]);

    const toolSelect = await screen.findByLabelText(/used by tool/i);
    // Tool options are populated from listTools.
    await user.selectOptions(toolSelect, "tool-serper");

    const keyInput = screen.getByLabelText(/key name/i) as HTMLInputElement;
    await waitFor(() => expect(keyInput.value).toBe("serper_api_key"));
    // Tool-driven: user cannot free-type over it.
    expect(keyInput).toHaveAttribute("readonly");
  });

  it("rejects a hyphenated key name with an inline error and blocks save", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CredentialsPage />);

    await user.click((await screen.findAllByRole("button", { name: /new credential/i }))[0]);

    // No tool selected -> Key Name is free-text, so we can type an invalid name.
    await user.type(screen.getByPlaceholderText(/serper-api-key/i), "serper-dev");
    await user.type(screen.getByLabelText(/key name/i), "serper-dev");
    await user.type(screen.getByPlaceholderText(/paste secret value/i), "sk-123");

    await user.click(screen.getByRole("button", { name: /^create$/i }));

    expect(
      await screen.findByText(/letters, digits, and underscores only/i),
    ).toBeInTheDocument();
    // Save is blocked by validation.
    expect(createAuthConfig).not.toHaveBeenCalled();
  });

  it("saves with a valid tool-driven key name", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CredentialsPage />);

    await user.click((await screen.findAllByRole("button", { name: /new credential/i }))[0]);
    await user.type(screen.getByPlaceholderText(/serper-api-key/i), "serper-dev");
    await user.selectOptions(await screen.findByLabelText(/used by tool/i), "tool-serper");
    await user.type(screen.getByPlaceholderText(/paste secret value/i), "sk-123");

    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => expect(createAuthConfig).toHaveBeenCalledTimes(1));
    expect(mk(createAuthConfig).mock.calls[0][0]).toMatchObject({
      credentials: { serper_api_key: "sk-123" },
    });
  });
});

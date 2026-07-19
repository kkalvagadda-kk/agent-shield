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
    // so the tests exercise the actual tool -> key linkage, not a stub.
    ...actual,
    listAuthConfigs: vi.fn(),
    createAuthConfig: vi.fn(),
    updateAuthConfig: vi.fn(),
    deleteAuthConfig: vi.fn(),
    listTools: vi.fn(),
  };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  listAuthConfigs,
  createAuthConfig,
  updateAuthConfig,
  listTools,
} from "../api/registryApi";
import { toast } from "sonner";
import type { AuthConfig } from "../api/registryApi";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const mkConfig = (over: Partial<AuthConfig>): AuthConfig => ({
  id: "ac-x",
  name: "serper-dev",
  type: "api_key",
  owner_team: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  has_credentials: true,
  ...over,
});

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

function seedApis() {
  mk(listAuthConfigs).mockResolvedValue({ items: [], total: 0 });
  mk(listTools).mockResolvedValue({ items: [SERPER_TOOL], total: 1 });
  mk(createAuthConfig).mockResolvedValue(mkConfig({ id: "ac1" }));
}

// TODO #18 — tool-driven, env-var-valid Key Name
describe("CredentialsPage — tool-driven key name", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedApis();
  });

  it("auto-fills and locks Key Name from the selected tool", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CredentialsPage />);

    await user.click((await screen.findAllByRole("button", { name: /new credential/i }))[0]);

    const toolSelect = await screen.findByLabelText(/used by tool/i);
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

// TODO #17 — reject error-string / invalid credential VALUES at save time
describe("CredentialsPage save guard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedApis();
  });

  async function openCreateForm() {
    renderWithProviders(<CredentialsPage />);
    const buttons = await screen.findAllByRole("button", { name: /New Credential/i });
    await userEvent.click(buttons[0]);
    await screen.findByText("New Credential", { selector: "h2" });
  }

  async function fillForm(keyName: string, secretValue: string) {
    await userEvent.type(screen.getByPlaceholderText(/serper-api-key/i), "serper-dev");
    await userEvent.type(screen.getByLabelText(/key name/i), keyName);
    await userEvent.type(screen.getByPlaceholderText(/paste secret value/i), secretValue);
    await userEvent.click(screen.getByRole("button", { name: /^Create$/ }));
  }

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

// Empty-shell credentials — a named row whose secret was never persisted. This
// is exactly the state that made `serper-dev` fail with 403 at tool-call time
// (credential linked to web_search, but no key stored → env var empty).
describe("CredentialsPage — empty-shell credentials", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedApis();
  });

  it("flags a credential with no stored value as 'no key set' in the list", async () => {
    mk(listAuthConfigs).mockResolvedValue({
      items: [
        mkConfig({ id: "ac-empty", name: "serper-dev", has_credentials: false }),
        mkConfig({ id: "ac-full", name: "openai-key", has_credentials: true }),
      ],
      total: 2,
    });
    renderWithProviders(<CredentialsPage />);

    expect(await screen.findByText("serper-dev")).toBeInTheDocument();
    expect(await screen.findByText("openai-key")).toBeInTheDocument();
    // Only the empty shell is badged, not the configured credential.
    const badges = screen.getAllByText(/no key set/i);
    expect(badges).toHaveLength(1);
  });

  it("blocks creating a credential with no secret value", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CredentialsPage />);

    await user.click((await screen.findAllByRole("button", { name: /new credential/i }))[0]);
    await user.type(screen.getByPlaceholderText(/serper-api-key/i), "serper-dev");
    // Link the tool so the Key Name is valid — only the VALUE is missing.
    await user.selectOptions(await screen.findByLabelText(/used by tool/i), "tool-serper");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    expect(await screen.findByText(/secret value is required/i)).toBeInTheDocument();
    expect(createAuthConfig).not.toHaveBeenCalled();
  });

  it("requires a value when editing an empty shell, then persists it", async () => {
    const user = userEvent.setup();
    mk(listAuthConfigs).mockResolvedValue({
      items: [mkConfig({ id: "ac-empty", name: "serper-dev", has_credentials: false })],
      total: 1,
    });
    mk(updateAuthConfig).mockResolvedValue(
      mkConfig({ id: "ac-empty", has_credentials: true }),
    );
    renderWithProviders(<CredentialsPage />);

    await user.click(await screen.findByTitle("Edit"));
    await screen.findByText("Edit Credential", { selector: "h2" });
    // The empty-shell warning is shown.
    expect(
      screen.getByText(/no key is stored for this credential yet/i),
    ).toBeInTheDocument();

    // Saving without a value is blocked.
    await user.selectOptions(await screen.findByLabelText(/used by tool/i), "tool-serper");
    await user.click(screen.getByRole("button", { name: /^update$/i }));
    expect(await screen.findByText(/secret value is required/i)).toBeInTheDocument();
    expect(updateAuthConfig).not.toHaveBeenCalled();

    // Entering a value persists it against the existing credential id.
    await user.type(screen.getByPlaceholderText(/paste secret value/i), "sk-realkey123");
    await user.click(screen.getByRole("button", { name: /^update$/i }));

    await waitFor(() => expect(updateAuthConfig).toHaveBeenCalledTimes(1));
    expect(mk(updateAuthConfig).mock.calls[0][0]).toBe("ac-empty");
    expect(mk(updateAuthConfig).mock.calls[0][1]).toMatchObject({
      credentials: { serper_api_key: "sk-realkey123" },
    });
  });
});

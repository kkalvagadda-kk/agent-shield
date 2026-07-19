import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import ProvidersPage from "./ProvidersPage";

vi.mock("../api/registryApi", async () => {
  const actual = await vi.importActual<typeof import("../api/registryApi")>(
    "../api/registryApi",
  );
  return {
    ...actual,
    listProviders: vi.fn(),
    createProvider: vi.fn(),
    deleteProvider: vi.fn(),
    listTeams: vi.fn(),
  };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { listProviders, createProvider, listTeams } from "../api/registryApi";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function seedApis() {
  mk(listProviders).mockResolvedValue({ items: [], total: 0 });
  mk(listTeams).mockResolvedValue({
    items: [
      {
        id: "t1",
        name: "team-alpha",
        namespace: "team-alpha",
        description: null,
        keycloak_role_id: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ],
    total: 1,
  });
  mk(createProvider).mockResolvedValue({
    id: "p1",
    name: "local-ollama",
    provider: "ollama",
    default_model: "gemma4:12b-mlx",
    team: "team-alpha",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  });
}

describe("ProvidersPage — Ollama provider", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedApis();
    // The Ollama model field reads suggested models from /config.json at runtime.
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: async () => ({
        providerModels: { ollama: ["gemma4:12b-mlx", "custom-model:99b"] },
      }),
    }) as unknown as typeof fetch;
  });

  it("offers Ollama as an option and swaps to Base URL + freeform model when selected", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProvidersPage />);

    await user.click((await screen.findAllByRole("button", { name: /add provider/i }))[0]);

    const providerSelect = screen.getAllByRole("combobox")[0];
    // Ollama is a selectable option.
    expect(
      within(providerSelect).getByRole("option", { name: "Ollama" }),
    ).toBeInTheDocument();

    // Anthropic is the default — its API key field is shown, no Base URL yet.
    expect(screen.getByPlaceholderText(/sk-ant/i)).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("http://host.docker.internal:11434"),
    ).not.toBeInTheDocument();

    await user.selectOptions(providerSelect, "ollama");

    // Base URL appears; anthropic/bedrock credential fields are gone.
    expect(
      await screen.findByPlaceholderText("http://host.docker.internal:11434"),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/sk-ant/i)).not.toBeInTheDocument();
    // Model becomes a freeform text input.
    expect(screen.getByPlaceholderText("gemma4:12b-mlx")).toBeInTheDocument();
  });

  it("submits ollama with credentials: { base_url }", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProvidersPage />);

    await user.click((await screen.findAllByRole("button", { name: /add provider/i }))[0]);

    const [providerSelect, teamSelect] = screen.getAllByRole("combobox");
    await user.selectOptions(providerSelect, "ollama");

    await user.type(screen.getByPlaceholderText("anthropic-prod"), "local-ollama");
    await user.selectOptions(teamSelect, "team-alpha");

    const modelInput = screen.getByPlaceholderText("gemma4:12b-mlx");
    await user.clear(modelInput);
    await user.type(modelInput, "gemma4:12b-mlx");

    await user.type(
      screen.getByPlaceholderText("http://host.docker.internal:11434"),
      "http://host.docker.internal:11434",
    );

    await user.click(screen.getByRole("button", { name: /save provider/i }));

    await waitFor(() => expect(createProvider).toHaveBeenCalledTimes(1));
    expect(mk(createProvider).mock.calls[0][0]).toMatchObject({
      name: "local-ollama",
      provider: "ollama",
      default_model: "gemma4:12b-mlx",
      team: "team-alpha",
      credentials: { base_url: "http://host.docker.internal:11434" },
    });
  });

  it("wires the freeform model input to a datalist sourced from /config.json", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProvidersPage />);

    await user.click((await screen.findAllByRole("button", { name: /add provider/i }))[0]);
    await user.selectOptions(screen.getAllByRole("combobox")[0], "ollama");

    // Still a freeform input (same placeholder), now with a datalist attached.
    const modelInput = await screen.findByPlaceholderText("gemma4:12b-mlx");
    expect(modelInput).toHaveAttribute("list", "ollama-model-suggestions");

    // The dynamic suggestion from /config.json is rendered as a datalist option.
    await waitFor(() => {
      const dl = document.getElementById("ollama-model-suggestions");
      expect(dl?.querySelector('option[value="custom-model:99b"]')).not.toBeNull();
    });
  });
});

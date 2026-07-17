import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import PreferencesPage from "./PreferencesPage";

vi.mock("../../api/registryApi", () => ({
  getMyPreferences: vi.fn(),
  updateMyPreferences: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { getMyPreferences, updateMyPreferences } from "../../api/registryApi";
import { toast } from "sonner";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const EMPTY = {
  response_length: null,
  tone: null,
  format: null,
  language: null,
  expertise: null,
};

describe("PreferencesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(getMyPreferences).mockResolvedValue({ ...EMPTY });
    mock(updateMyPreferences).mockImplementation(async (p: object) => ({ ...EMPTY, ...p }));
  });

  it("loads current preferences on mount and reflects saved values", async () => {
    mock(getMyPreferences).mockResolvedValue({ ...EMPTY, response_length: "concise", format: "bulleted" });
    renderWithProviders(<PreferencesPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "concise" })).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByRole("button", { name: "bulleted" })).toHaveAttribute("aria-pressed", "true");
    // A non-selected option stays unpressed.
    expect(screen.getByRole("button", { name: "detailed" })).toHaveAttribute("aria-pressed", "false");
  });

  it("selecting a preset and Save PUTs it and toasts success", async () => {
    renderWithProviders(<PreferencesPage />);
    await waitFor(() => expect(getMyPreferences).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "detailed" }));
    fireEvent.click(screen.getByRole("button", { name: "expert" }));
    fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));

    await waitFor(() => expect(updateMyPreferences).toHaveBeenCalledTimes(1));
    expect(mock(updateMyPreferences).mock.calls[0][0]).toMatchObject({
      response_length: "detailed",
      expertise: "expert",
    });
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  it("clicking the active preset again clears it (null = no preference)", async () => {
    mock(getMyPreferences).mockResolvedValue({ ...EMPTY, tone: "casual" });
    renderWithProviders(<PreferencesPage />);
    const casual = await screen.findByRole("button", { name: "casual" });
    await waitFor(() => expect(casual).toHaveAttribute("aria-pressed", "true"));
    fireEvent.click(casual);
    expect(casual).toHaveAttribute("aria-pressed", "false");
  });

  it("surfaces an error toast when saving fails", async () => {
    mock(updateMyPreferences).mockRejectedValue(new Error("boom"));
    renderWithProviders(<PreferencesPage />);
    await waitFor(() => expect(getMyPreferences).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "concise" }));
    fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import AdminAccessPage from "./AdminAccessPage";

// The grants tab pulls from registryApi; the users tab this file exercises talks to
// /api/v1/admin/* through raw fetch (AdminAccessPage.tsx:54-108), so fetch is the seam.
vi.mock("../api/registryApi", () => ({
  listGrants: vi.fn().mockResolvedValue([]),
  listAllTools: vi.fn().mockResolvedValue([]),
  listAgents: vi.fn().mockResolvedValue([]),
  listSkills: vi.fn().mockResolvedValue([]),
  listCompositeWorkflows: vi.fn().mockResolvedValue([]),
  createGrant: vi.fn(),
  revokeGrant: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// WHY THIS FILE EXISTS (gap G-R0-10)
// ----------------------------------
// This page is the whole RBAC admin write surface — create/edit/delete a user and assign
// a GLOBAL ROLE — and it had no component test at all. Its only automated guard was
// studio/e2e/admin-access-roles.spec.ts, and that spec looked for a Save button named
// /^save$/i while the button has read "Save Changes" since 3192ebe. The spec was written
// LATER (8baba26), so it was red from birth and had never once passed; nothing surfaced
// it because the browser layer could not reach the EKS cluster either (G-R0-8). A test
// that could not pass, inside a layer that could not run.
//
// The durable fix for that class is HERE, not in Playwright: this file needs no cluster
// and runs in milliseconds, so a locator/label drift fails fast and locally. The first
// test below deliberately pins the accessible names the e2e spec depends on.

const user = (over: Partial<Record<string, unknown>> = {}) => ({
  kc_id: "kc-1",
  username: "roletest",
  email: "roletest@example.com",
  first_name: "Role",
  last_name: "Test",
  enabled: true,
  team: "platform",
  role: "contributor",
  created_at: 1_750_000_000_000,
  ...over,
});

let fetchMock: ReturnType<typeof vi.fn>;

/** Route by URL+method so a PATCH assertion cannot be satisfied by the list call. */
function installFetch(users: ReturnType<typeof user>[]) {
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const ok = (body: unknown) =>
      ({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) }) as Response;
    if (url.includes("/admin/users") && method === "GET") return ok(users);
    if (url.includes("/admin/teams-summary")) return ok([]);
    if (url.includes("/admin/users/") && method === "PATCH") return ok({ ...users[0], role: "consumer" });
    return ok({});
  });
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => installFetch([user()]));
afterEach(() => vi.unstubAllGlobals());

describe("AdminAccessPage — users tab", () => {
  it("renders the seeded user's row with its role chip", async () => {
    renderWithProviders(<AdminAccessPage />);
    const row = await screen.findByRole("row", { name: /roletest/i });
    // Display name, not username — the row shows `${first} ${last}` when either is set.
    expect(within(row).getByText("Role Test")).toBeInTheDocument();
    expect(within(row).getByText("contributor")).toBeInTheDocument();
  });

  it("the edit modal's submit button is named 'Save Changes' — the e2e locator contract", async () => {
    // REGRESSION GUARD for G-R0-9. studio/e2e/admin-access-roles.spec.ts drives this
    // button by accessible name. When the name and the locator drifted apart, the e2e
    // spec silently never ran for its entire life. Renaming this button is allowed —
    // but this test must fail in the same commit, so the e2e locator gets updated with
    // it instead of rotting for months.
    const u = userEvent.setup();
    renderWithProviders(<AdminAccessPage />);
    const row = await screen.findByRole("row", { name: /roletest/i });
    await u.click(within(row).getByRole("button", { name: /^edit$/i }));

    const save = await screen.findByRole("button", { name: /^save changes$/i });
    expect(save).toBeInTheDocument();
    // And prove the anchored name the spec used previously does NOT match, so this test
    // genuinely discriminates rather than passing on a substring.
    expect(screen.queryByRole("button", { name: /^save$/i })).toBeNull();
  });

  it("the role dropdown offers exactly the canonical roles, no legacy spellings", async () => {
    // Cheap local mirror of the e2e §8.4 case. Legacy values (admin/operator/viewer) are
    // still RENDERED for un-migrated rows via ROLE_CHIP, but must never be OFFERED.
    const u = userEvent.setup();
    renderWithProviders(<AdminAccessPage />);
    const row = await screen.findByRole("row", { name: /roletest/i });
    await u.click(within(row).getByRole("button", { name: /^edit$/i }));

    const select = (await screen.findAllByRole("combobox")).find((el) =>
      within(el).queryByRole("option", { name: "consumer" }),
    );
    expect(select).toBeDefined();
    const values = Array.from(select!.querySelectorAll("option")).map((o) => o.value);
    expect(values).toEqual(["platform-admin", "contributor", "consumer"]);
    for (const legacy of ["admin", "operator", "viewer"]) expect(values).not.toContain(legacy);
  });

  it("saving a role change PATCHes that user with the new role", async () => {
    // The write actually leaves the component — the defect class the e2e save->reload
    // test guards, asserted here at the network boundary.
    const u = userEvent.setup();
    renderWithProviders(<AdminAccessPage />);
    const row = await screen.findByRole("row", { name: /roletest/i });
    await u.click(within(row).getByRole("button", { name: /^edit$/i }));

    const select = (await screen.findAllByRole("combobox")).find((el) =>
      within(el).queryByRole("option", { name: "consumer" }),
    );
    await u.selectOptions(select!, "consumer");
    await u.click(screen.getByRole("button", { name: /^save changes$/i }));

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/admin/users/kc-1") &&
          (init?.method ?? "").toUpperCase() === "PATCH",
      );
      expect(patch, "no PATCH to /admin/users/kc-1 was issued").toBeTruthy();
      expect(JSON.parse(String(patch![1]!.body)).role).toBe("consumer");
    });
  });

  it("renders a legacy role value rather than dropping it", async () => {
    // A pre-0044/0075 row can still hold 'operator'. rbac._LEGACY_MAP normalizes on READ
    // server-side, but the admin table must not render an un-migrated row as blank.
    vi.unstubAllGlobals();
    installFetch([user({ role: "operator" })]);
    renderWithProviders(<AdminAccessPage />);
    const row = await screen.findByRole("row", { name: /roletest/i });
    expect(within(row).getByText("operator")).toBeInTheDocument();
  });
});

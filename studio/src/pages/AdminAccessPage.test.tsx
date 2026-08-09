import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import AdminAccessPage from "./AdminAccessPage";
import {
  createAdminUser,
  deleteAdminUser,
  getTeamsSummary,
  listUsers,
  patchAdminUser,
  resetAdminUserPassword,
} from "../api/registryApi";

// registryApi is the ONLY seam. The users tab used to call /api/v1/admin/* through raw
// fetch defined inline in the page, so this file stubbed global fetch. Those helpers moved
// into registryApi behind the authed `http` client after the unauthenticated versions all
// began 401-ing under registry-api 0.2.262 (docs/bugs/studio-blank-page-unauthed-fetch-
// teams-summary.md). Mocking the module — like every other page test here — means this
// test can no longer pass while the page bypasses the client, which is the defect class.
vi.mock("../api/registryApi", () => ({
  listGrants: vi.fn().mockResolvedValue([]),
  listAllTools: vi.fn().mockResolvedValue([]),
  listAgents: vi.fn().mockResolvedValue([]),
  listSkills: vi.fn().mockResolvedValue([]),
  listCompositeWorkflows: vi.fn().mockResolvedValue([]),
  createGrant: vi.fn(),
  revokeGrant: vi.fn(),
  listUsers: vi.fn(),
  createAdminUser: vi.fn(),
  patchAdminUser: vi.fn(),
  deleteAdminUser: vi.fn(),
  resetAdminUserPassword: vi.fn(),
  getTeamsSummary: vi.fn().mockResolvedValue([]),
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

/** Seed the mocked client. Each endpoint is its own fn, so a PATCH assertion cannot
 *  be satisfied by the list call. */
function installApi(users: ReturnType<typeof user>[]) {
  vi.mocked(listUsers).mockResolvedValue(users);
  vi.mocked(getTeamsSummary).mockResolvedValue([]);
  vi.mocked(patchAdminUser).mockResolvedValue({ ...users[0], role: "consumer" });
  vi.mocked(createAdminUser).mockResolvedValue(users[0]);
  vi.mocked(deleteAdminUser).mockResolvedValue(undefined);
  vi.mocked(resetAdminUserPassword).mockResolvedValue(undefined);
}

beforeEach(() => {
  vi.clearAllMocks();
  installApi([user()]);
});
afterEach(() => vi.restoreAllMocks());

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
      const call = vi.mocked(patchAdminUser).mock.calls.find(([kcId]) => kcId === "kc-1");
      expect(call, "no PATCH to /admin/users/kc-1 was issued").toBeTruthy();
      expect(call![1].role).toBe("consumer");
    });
  });

  it("renders a legacy role value rather than dropping it", async () => {
    // A pre-0044/0075 row can still hold 'operator'. rbac._LEGACY_MAP normalizes on READ
    // server-side, but the admin table must not render an un-migrated row as blank.
    installApi([user({ role: "operator" })]);
    renderWithProviders(<AdminAccessPage />);
    const row = await screen.findByRole("row", { name: /roletest/i });
    expect(within(row).getByText("operator")).toBeInTheDocument();
  });
});

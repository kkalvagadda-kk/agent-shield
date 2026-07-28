import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import AdminPublishRequestsPage from "./AdminPublishRequestsPage";

vi.mock("../api/registryApi", () => ({
  listPublishRequests: vi.fn(),
  approvePublishRequest: vi.fn(),
  rejectPublishRequest: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { listPublishRequests } from "../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

// A request carries its OWN threshold and its OWN provenance. Both come from the
// server; the page must not re-derive either. Fixtures therefore model the real
// response — E-4's D9 shipped fixtures modelling a response the API never sends, and
// five tests broke the moment the page read the real field.
const request = (over: Record<string, unknown> = {}) => ({
  id: "pr-1",
  asset_id: "a-1",
  asset_type: "agent",
  submitted_by: "dev@example.com",
  submitted_at: "2026-07-27T10:00:00Z",
  status: "pending_review",
  highest_risk_level: "low",
  dependency_declaration: {},
  reviewed_by: null,
  reviewed_at: null,
  review_notes: null,
  source_version_id: "v-2",
  last_eval_score: 0.85,
  last_eval_run_id: "run-1",
  last_eval_pass_threshold: 0.7,
  eval_source: "version" as const,
  asset_name: "refund-agent",
  asset_team: "payments",
  ...over,
});

const rows = (...items: ReturnType<typeof request>[]) =>
  mock(listPublishRequests).mockResolvedValue({ items, total: items.length });

beforeEach(() => {
  vi.clearAllMocks();
});

// THE regression. A reviewer approves a release on this number, and the page used to
// grade every score against a hardcoded `>= 0.7` / `>= 0.4` regardless of the bar the
// run actually had to clear. Same score, two thresholds, two verdicts.
describe("AdminPublishRequestsPage — the verdict uses the run's own threshold", () => {
  it("renders 0.85 as a PASS when the run's threshold was 0.7", async () => {
    rows(request({ last_eval_pass_threshold: 0.7 }));
    renderWithProviders(<AdminPublishRequestsPage />);

    const label = await screen.findByTestId("eval-threshold-label");
    expect(label).toHaveTextContent("0.85 / needs 0.70");

    const badge = screen.getByRole("button", { name: /85%/ });
    expect(badge.className).toContain("green");
  });

  it("renders the SAME 0.85 as not-passing when the run's threshold was 0.9", async () => {
    rows(request({ last_eval_pass_threshold: 0.9 }));
    renderWithProviders(<AdminPublishRequestsPage />);

    const label = await screen.findByTestId("eval-threshold-label");
    // The reviewer can see WHY a good-looking score will not publish.
    expect(label).toHaveTextContent("0.85 / needs 0.90");

    const badge = screen.getByRole("button", { name: /85%/ });
    expect(badge.className).not.toContain("green");
    expect(badge.className).toContain("amber");
  });

  // Fail closed. A request with no resolvable threshold must never render as a pass.
  it("renders neutral — never green — when the threshold is absent", async () => {
    rows(request({ last_eval_pass_threshold: null }));
    renderWithProviders(<AdminPublishRequestsPage />);

    const badge = await screen.findByRole("button", { name: /85%/ });
    expect(badge.className).not.toContain("green");
  });
});

// Provenance. The score can be real and still not be about the version being
// published. Rendering it identically either way is the bug Decision 32 closes.
describe("AdminPublishRequestsPage — eval provenance", () => {
  it("warns when the score came from a different version", async () => {
    rows(request({ eval_source: "agent_latest", source_version_id: null }));
    renderWithProviders(<AdminPublishRequestsPage />);

    const warn = await screen.findByTestId("eval-provenance-warning");
    expect(warn).toHaveTextContent(/from a different version/i);
  });

  it("shows no warning when the eval belongs to the pinned version", async () => {
    rows(request({ eval_source: "version" }));
    renderWithProviders(<AdminPublishRequestsPage />);

    await screen.findByTestId("eval-threshold-label");
    expect(screen.queryByTestId("eval-provenance-warning")).toBeNull();
  });

  // The state the old code could not express: a pinned version with no eval of its
  // own used to borrow the agent's latest score. It must now show nothing.
  it("shows 'No eval' rather than borrowing another version's score", async () => {
    rows(
      request({
        eval_source: "none",
        last_eval_score: null,
        last_eval_run_id: null,
        last_eval_pass_threshold: null,
      }),
    );
    renderWithProviders(<AdminPublishRequestsPage />);

    expect(await screen.findByText(/no eval/i)).toBeInTheDocument();
    expect(screen.queryByTestId("eval-threshold-label")).toBeNull();
    expect(screen.queryByTestId("eval-provenance-warning")).toBeNull();
  });
});

// Two pending requests for the SAME agent on DIFFERENT versions used to receive the
// identical score, because the eval map was keyed by asset_id. Per-request rows are
// what makes them distinguishable at all.
describe("AdminPublishRequestsPage — per-request resolution", () => {
  it("renders different verdicts for two requests on the same agent", async () => {
    rows(
      request({ id: "pr-1", source_version_id: "v-1", last_eval_score: 0.95, last_eval_pass_threshold: 0.9 }),
      request({
        id: "pr-2",
        source_version_id: "v-2",
        last_eval_score: null,
        last_eval_run_id: null,
        last_eval_pass_threshold: null,
        eval_source: "none",
      }),
    );
    renderWithProviders(<AdminPublishRequestsPage />);

    await waitFor(() => expect(screen.getByText(/no eval/i)).toBeInTheDocument());
    const labels = screen.getAllByTestId("eval-threshold-label");
    expect(labels).toHaveLength(1);
    expect(labels[0]).toHaveTextContent("0.95 / needs 0.90");
  });
});

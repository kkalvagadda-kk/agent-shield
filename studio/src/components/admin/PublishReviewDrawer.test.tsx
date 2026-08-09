import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../test/utils";
import PublishReviewDrawer from "./PublishReviewDrawer";
import { getPublishReview, type PublishReview } from "../../api/registryApi";

vi.mock("../../api/registryApi", async () => {
  const actual = await vi.importActual<typeof import("../../api/registryApi")>(
    "../../api/registryApi"
  );
  return { ...actual, getPublishReview: vi.fn() };
});

const mockGet = vi.mocked(getPublishReview);

/**
 * The states that matter are the ones where a WRONG render changes what a human
 * authorizes. This is the last gate before an artifact goes org-wide, so "it rendered"
 * is not the assertion — "it rendered the thing that would have stopped them" is.
 */
function payload(over: Partial<PublishReview> = {}): PublishReview {
  return {
    request_id: "req-1",
    asset_type: "agent",
    review_supported: true,
    submitted_by: "alice",
    submitted_at: "2026-08-08T00:00:00Z",
    status: "pending_review",
    agent: {
      id: "a-1", name: "refund-assistant", team: "platform", created_by: "alice",
      description: null, agent_class: "user_delegated", agent_type: "declarative",
      execution_shape: "durable", memory_enabled: true, publish_status: "private",
      instructions: "You issue refunds.",
    },
    version: {
      id: "v-1", version_number: 3, image_tag: "registry.internal/refund:v3",
      git_sha: null, git_branch: null, eval_passed: true,
      adversarial_eval_passed: true, notes: null,
    },
    eval: { score: 0.87, run_id: "run-1", pass_threshold: 0.8, source: "version" },
    tools: [
      {
        id: "t-1", name: "issue_refund", description: null, type: "http",
        risk_level: "high", owner_team: "platform", publish_status: "private",
        disposition: "will_publish", side_effecting: true,
        pii_deanonymize_allowed: false, http_method: "POST",
        http_url: "https://payments.internal/refund", python_code: null,
        auth_config_name: "payments-api-key", mcp_server_name: null, mcp_tool_name: null,
      },
      {
        id: "t-2", name: "get_weather", description: null, type: "http",
        risk_level: "low", owner_team: "shared", publish_status: "published",
        disposition: "already_published", side_effecting: false,
        pii_deanonymize_allowed: false, http_method: "GET",
        http_url: "https://weather.example/x", python_code: null,
        auth_config_name: null, mcp_server_name: null, mcp_tool_name: null,
      },
    ],
    cascade: { will_publish: ["issue_refund"], blocked: [], already_published: ["get_weather"] },
    knowledge_bases: [],
    ...over,
  };
}

describe("PublishReviewDrawer", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows every bound tool with its risk, owner and cascade disposition", async () => {
    mockGet.mockResolvedValue(payload());
    renderWithProviders(
      <PublishReviewDrawer requestId="req-1" onClose={vi.fn()} onApprove={vi.fn()} approving={false} />
    );

    await waitFor(() => expect(screen.getByTestId("review-tool-issue_refund")).toBeInTheDocument());
    // BOTH tools, not just the cascading one. "2 become org-wide" reads very differently
    // from "this agent uses 2 tools, 1 of which becomes org-wide".
    expect(screen.getByTestId("review-tool-get_weather")).toBeInTheDocument();
    expect(screen.getByTestId("review-disposition-issue_refund")).toHaveTextContent("WILL PUBLISH");
    expect(screen.getByTestId("review-disposition-get_weather")).toHaveTextContent("already published");
    // Where the data goes — the highest-signal field on the screen.
    expect(screen.getByText(/payments\.internal\/refund/)).toBeInTheDocument();
    // D-2: the credential NAME.
    expect(screen.getByTestId("review-cred-issue_refund")).toHaveTextContent("payments-api-key");
  });

  it("requires the cascade acknowledgement before Approve is enabled", async () => {
    mockGet.mockResolvedValue(payload());
    const onApprove = vi.fn();
    renderWithProviders(
      <PublishReviewDrawer requestId="req-1" onClose={vi.fn()} onApprove={onApprove} approving={false} />
    );

    // WAIT FOR THE LOADED STATE FIRST. The footer renders before the query resolves, so
    // asserting `toBeDisabled()` straight after `findByTestId` passes while the drawer is
    // merely LOADING — it would stay green with the acknowledgement gate deleted. Assert
    // the reason, not the symptom.
    await screen.findByTestId("review-tool-list");
    const approve = screen.getByTestId("review-approve");
    // Option C: the cascade is non-empty, so approving without acknowledging it must be
    // impossible — not merely discouraged.
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByTestId("review-cascade-ack").querySelector("input")!);
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("does NOT ask for an acknowledgement when nothing cascades", async () => {
    mockGet.mockResolvedValue(
      payload({
        tools: [],
        cascade: { will_publish: [], blocked: [], already_published: [] },
      })
    );
    renderWithProviders(
      <PublishReviewDrawer requestId="req-1" onClose={vi.fn()} onApprove={vi.fn()} approving={false} />
    );

    // Same trap as above — wait for the payload before judging the button.
    expect(await screen.findByText(/binds no tools/i)).toBeInTheDocument();
    // Reserved for the case that escalates scope. A confirm on every approval is the
    // click-through people learn to dismiss.
    expect(screen.queryByTestId("review-cascade-ack")).not.toBeInTheDocument();
    expect(screen.getByTestId("review-approve")).toBeEnabled();
  });

  it("renders a python tool with no http_url, and hides its code until asked", async () => {
    mockGet.mockResolvedValue(
      payload({
        tools: [
          {
            id: "t-3", name: "calc", description: null, type: "python",
            risk_level: "medium", owner_team: "platform", publish_status: "private",
            disposition: "will_publish", side_effecting: false,
            pii_deanonymize_allowed: true, http_method: null, http_url: null,
            python_code: "print('SECRET_MARKER')", auth_config_name: null,
            mcp_server_name: null, mcp_tool_name: null,
          },
        ],
        cascade: { will_publish: ["calc"], blocked: [], already_published: [] },
      })
    );
    renderWithProviders(
      <PublishReviewDrawer requestId="req-1" onClose={vi.fn()} onApprove={vi.fn()} approving={false} />
    );

    await screen.findByTestId("review-tool-calc");
    expect(screen.queryByTestId("review-code-calc")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("review-code-toggle-calc"));
    // D-1: in full, not a preview.
    expect(screen.getByTestId("review-code-calc")).toHaveTextContent("SECRET_MARKER");
  });

  it("names the blocked cross-team tools instead of omitting them", async () => {
    mockGet.mockResolvedValue(
      payload({
        cascade: {
          will_publish: ["issue_refund"],
          blocked: [{ name: "ops_only", owner_team: "operations" }],
          already_published: [],
        },
      })
    );
    renderWithProviders(
      <PublishReviewDrawer requestId="req-1" onClose={vi.fn()} onApprove={vi.fn()} approving={false} />
    );
    await waitFor(() =>
      expect(screen.getByText(/ops_only \(operations\)/)).toBeInTheDocument()
    );
  });

  it("says a workflow is unreviewable rather than rendering an empty tool list", async () => {
    mockGet.mockResolvedValue({
      request_id: "req-2",
      asset_type: "workflow",
      review_supported: false,
      unsupported_reason: "Review detail is not built for asset_type='workflow'.",
    });
    renderWithProviders(
      <PublishReviewDrawer requestId="req-2" onClose={vi.fn()} onApprove={vi.fn()} approving={false} />
    );
    // The failure this guards: an agent-shaped drawer with zero tools reads as
    // "this workflow has no tools", which is the silently-unreviewed outcome.
    expect(await screen.findByTestId("review-unsupported")).toHaveTextContent(
      /not built for asset_type/
    );
    expect(screen.queryByTestId("review-tool-list")).not.toBeInTheDocument();
  });

  it("flags a daemon agent, which is exempt from OPA's identity floor", async () => {
    mockGet.mockResolvedValue(
      payload({ agent: { ...payload().agent!, agent_class: "daemon" } })
    );
    renderWithProviders(
      <PublishReviewDrawer requestId="req-1" onClose={vi.fn()} onApprove={vi.fn()} approving={false} />
    );
    const chip = await screen.findByTestId("review-agent-class");
    expect(chip).toHaveTextContent("daemon");
    expect(chip.className).toMatch(/amber/);
  });

  it("surfaces the load error instead of rendering an empty review", async () => {
    mockGet.mockRejectedValue(new Error("boom"));
    renderWithProviders(
      <PublishReviewDrawer requestId="req-1" onClose={vi.fn()} onApprove={vi.fn()} approving={false} />
    );
    // A drawer that fails quietly is worse than one that does not open — it looks
    // like the agent has no tools.
    await waitFor(() =>
      expect(screen.getByText(/Failed to load the review payload/)).toBeInTheDocument()
    );
    expect(screen.queryByTestId("review-tool-list")).not.toBeInTheDocument();
  });
});

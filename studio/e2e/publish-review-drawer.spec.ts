import { expect, test } from "@playwright/test";
import { adminApi } from "./lib/api";

/**
 * The publish REVIEW surface — Decision 47 step D, gap G-R3-11.
 *
 * WHY THIS SPEC IS THE ONE THAT COUNTS (DoD rule 1)
 * -------------------------------------------------
 * `suite-6` T-S6-017..022 proves the PAYLOAD. It `kubectl exec`s into the pod and cannot
 * see a screen — so it cannot catch the failure this feature is actually about: a reviewer
 * approving without having been shown the tools. The whole control is a UI gate. This is
 * the only layer that can fail it.
 *
 * WHAT IT PROVES
 *   1. the queue row no longer approves — it opens a drawer (option B)
 *   2. the drawer shows every bound tool, its endpoint, its credential name and which
 *      tools go org-wide
 *   3. Approve is UNREACHABLE until the cascade is acknowledged
 *   4. approve → RELOAD FROM THE BACKEND → the agent is published AND the cascaded tool is
 *      published (DoD rule 2 — the round-trip, not the optimistic UI state)
 */

const TS = Date.now();
const AGENT = `pr-review-${TS}`;
const TOOL = `pr_review_tool_${TS}`;
const CRED = `pr-review-cred-${TS}`;
const SECRET = `sk-never-render-${TS}`;

let agentId = "";
let toolId = "";
let credId = "";
let requestId = "";

test.describe("Publish review drawer", () => {
  // Fixtures go through the REAL API with a real admin token — the same rule
  // `e2e_ensure_reviewer` follows in the bash tree. Building them any other way would
  // create a state the product cannot create.
  test.beforeAll(async () => {
    // `adminApi()` — NOT a hand-rolled newContext. It resolves API_BASE from
    // PLAYWRIGHT_BASE_URL, sets ignoreHTTPSErrors for the gateway's self-signed cert, and
    // derives X-User-Sub FROM the token so the header and the signature cannot name two
    // different people. The first cut of this spec built its own context against a
    // hardcoded http://localhost:5173 and died with ECONNREFUSED against the EKS gateway —
    // exactly the "reimplemented the broken half of something the repo already solved"
    // shape this tree has repeated postmortems for.
    const api = await adminApi();

    const cred = await api.post("/api/v1/auth-configs/", {
      data: { name: CRED, type: "api_key", credentials: { apikey: SECRET }, owner_team: "platform" },
    });
    credId = (await cred.json())?.id ?? "";

    const tool = await api.post("/api/v1/tools/", {
      data: {
        name: TOOL, type: "http", description: "publish review probe",
        risk_level: "high", http_method: "POST",
        http_url: "https://payments.example.invalid/refund",
        side_effecting: true, auth_config_id: credId,
      },
    });
    toolId = (await tool.json())?.id ?? "";

    const agent = await api.post("/api/v1/agents/", {
      data: {
        name: AGENT, team: "platform", description: "publish review probe",
        metadata: { instructions: "REVIEW-DRAWER-PROBE" },
      },
    });
    agentId = (await agent.json())?.id ?? "";

    await api.post(`/api/v1/agents/${AGENT}/tools`, { data: { tool_id: toolId } });
    await api.post(`/api/v1/agents/${AGENT}/versions`, {
      data: {
        image_tag: "registry.internal/pr-review:v1",
        eval_passed: true, adversarial_eval_passed: true,
      },
    });
    const pub = await api.post(`/api/v1/agents/${AGENT}/publish`, { data: {} });
    requestId = (await pub.json())?.publish_request_id ?? "";

    await api.dispose();
  });

  test.afterAll(async () => {
    const api = await adminApi();
    if (agentId) await api.delete(`/api/v1/agents/${AGENT}`);
    if (toolId) await api.delete(`/api/v1/tools/${toolId}`);
    if (credId) await api.delete(`/api/v1/auth-configs/${credId}`);
    await api.dispose();
  });

  test("the row opens a review drawer instead of approving, and the drawer shows the cascade", async ({ page }) => {
    test.skip(!requestId, "fixture did not produce a publish request — this case proved nothing");

    await page.goto("/admin/publish-requests");
    const row = page.locator("tr").filter({ has: page.getByText(AGENT, { exact: true }) }).first();
    await expect(row).toBeVisible({ timeout: 20_000 });

    // (1) The row must NOT carry a bare approve. If it did, the drawer would be optional
    // and the control would revert to today's behaviour for anyone in a hurry.
    const reviewed = page.waitForResponse((r) =>
      /\/api\/v1\/admin\/publish-requests\/.+\/review$/.test(r.url())
    );
    await row.getByRole("button", { name: /Review & Promote/i }).click();
    expect((await reviewed).ok()).toBeTruthy();

    const drawer = page.getByTestId("publish-review-drawer");
    await expect(drawer).toBeVisible();

    // (2) The tool, its risk, WHERE THE DATA GOES, and its credential NAME.
    await expect(drawer.getByTestId(`review-tool-${TOOL}`)).toBeVisible();
    await expect(drawer.getByTestId(`review-disposition-${TOOL}`)).toHaveText(/WILL PUBLISH/);
    await expect(drawer.getByText(/payments\.example\.invalid\/refund/)).toBeVisible();
    await expect(drawer.getByTestId(`review-cred-${TOOL}`)).toContainText(CRED);
    // D-2 — the NAME is the signal, the VALUE is the leak. It must appear nowhere.
    await expect(drawer).not.toContainText(SECRET);
    await expect(drawer.getByTestId("review-cascade-count")).toContainText("1 will be PUBLISHED");
    await expect(drawer.getByTestId("review-instructions")).toContainText("REVIEW-DRAWER-PROBE");
    await expect(drawer.getByTestId("review-image-tag")).toContainText("pr-review:v1");

    // (3) Approve is unreachable until the cascade is acknowledged.
    const approve = drawer.getByTestId("review-approve");
    await expect(approve).toBeDisabled();
    await drawer.getByTestId("review-cascade-ack").locator("input").check();
    await expect(approve).toBeEnabled();

    // (4) Approve, then RELOAD FROM THE BACKEND and assert it survived. The optimistic
    // UI state is not the assertion — the round-trip is (DoD rule 2).
    const approved = page.waitForResponse(
      (r) => r.request().method() === "POST"
        && /\/api\/v1\/admin\/publish-requests\/.+\/approve$/.test(r.url())
    );
    await approve.click();
    expect((await approved).ok()).toBeTruthy();

    const api = await adminApi();
    const agentAfter = await api.get(`/api/v1/agents/${AGENT}`);
    expect((await agentAfter.json())?.publish_status).toBe("published");

    // The CASCADE is the part a reviewer cannot undo, so it is the part worth reloading.
    const toolAfter = await api.get(`/api/v1/tools/${toolId}`);
    expect((await toolAfter.json())?.publish_status).toBe("published");
    await api.dispose();
  });
});

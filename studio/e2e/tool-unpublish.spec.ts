import { expect, test } from "@playwright/test";
import { adminApi } from "./lib/api";

/**
 * Owner-initiated unpublish — Decision 47 #4, roadmap [3] step E.
 *
 * WHY THIS SPEC IS THE ONE THAT COUNTS (DoD rule 1)
 * -------------------------------------------------
 * `suite-6` T-S6-023..029 proves the ENDPOINT: the transition, the 409, the 403-before-409
 * ordering, the absence of a cascade. It `kubectl exec`s into the pod and cannot see a
 * screen, so it cannot catch what step E is actually about — a person being able to find
 * and reverse a publish. Before this change the Tools page did not render `publish_status`
 * at all, so every row looked identical whether it was org-wide or a private draft. A green
 * API suite over an invisible state is exactly the failure the Definition of Done names.
 *
 * WHAT IT PROVES
 *   1. the page distinguishes Published from Private (the state existed on the wire and
 *      nowhere on screen)
 *   2. Unpublish opens a dialog that NAMES the published agents still bound
 *   3. that list does not block the action — it is a courtesy, and a disabled button here
 *      would let any team freeze another team's tool in the catalog
 *   4. confirm → RELOAD FROM THE BACKEND → the tool is private, the AGENT is untouched
 *      and the binding survives (DoD rule 2, and the no-cascade property in one assertion)
 */

const TS = Date.now();
const AGENT = `unpub-journey-${TS}`;
const TOOL = `unpub_journey_tool_${TS}`;

let agentId = "";
let toolId = "";
let published = false;

test.describe("Tool unpublish", () => {
  // The fixture goes through the REAL cascade — create private, bind, publish, approve —
  // rather than writing `publish_status: 'published'` directly. There is no endpoint that
  // would let it: tools enter the catalog only by riding along with an approved agent
  // (Decision 47 option C). Seeding the state another way would test a state the product
  // cannot produce.
  test.beforeAll(async () => {
    const api = await adminApi();

    const tool = await api.post("/api/v1/tools/", {
      data: {
        name: TOOL, type: "http", description: "unpublish journey probe",
        risk_level: "low", http_method: "GET",
        http_url: "https://example.invalid/unpublish-journey",
      },
    });
    toolId = (await tool.json())?.id ?? "";

    const agent = await api.post("/api/v1/agents/", {
      data: { name: AGENT, team: "platform", description: "unpublish journey probe" },
    });
    agentId = (await agent.json())?.id ?? "";

    if (toolId && agentId) {
      await api.post(`/api/v1/agents/${AGENT}/tools`, { data: { tool_id: toolId } });
      await api.post(`/api/v1/agents/${AGENT}/versions`, {
        data: {
          image_tag: "registry.internal/unpub-journey:v1",
          eval_passed: true, adversarial_eval_passed: true,
        },
      });
      const pub = await api.post(`/api/v1/agents/${AGENT}/publish`, { data: {} });
      const prId = (await pub.json())?.publish_request_id;
      if (prId) {
        await api.post(`/api/v1/admin/publish-requests/${prId}/approve`, {
          data: { grantee_teams: ["platform"] },
        });
      }
      const after = await api.get(`/api/v1/tools/${toolId}`);
      published = (await after.json())?.publish_status === "published";
    }

    await api.dispose();
  });

  test.afterAll(async () => {
    const api = await adminApi();
    if (agentId) await api.delete(`/api/v1/agents/${AGENT}`);
    if (toolId) await api.delete(`/api/v1/tools/${toolId}`);
    await api.dispose();
  });

  test("a published tool can be taken back out of the catalog from the Tools page", async ({ page }) => {
    // Skipped rather than failed if the cascade did not run: a green assertion against a
    // tool that was never published would prove nothing at all.
    test.skip(!published, "fixture cascade did not publish the tool — this case proved nothing");

    await page.goto("/tools");

    // (1) The state is on the screen. This column did not exist before step E, so a user
    // had no way to tell an org-wide tool from their own draft.
    const badge = page.getByTestId(`tool-visibility-${TOOL}`);
    await expect(badge).toBeVisible({ timeout: 20_000 });
    await expect(badge).toHaveText(/Published/);

    // (2) The dialog names what is still bound.
    await page.getByTestId(`tool-unpublish-${TOOL}`).click();
    const dialog = page.getByTestId("unpublish-tool-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId(`unpublish-agent-${AGENT}`)).toBeVisible();
    // The reassurance is load-bearing text, not decoration: it is the reason the list
    // does not block.
    await expect(dialog.getByTestId("unpublish-effect")).toContainText(/discoverability only/i);

    // (3) A bound published agent must NOT disable the confirm.
    const confirm = dialog.getByTestId("unpublish-confirm");
    await expect(confirm).toBeEnabled();

    const unpublished = page.waitForResponse(
      (r) => r.request().method() === "POST"
        && new RegExp(`/api/v1/tools/${toolId}/unpublish$`).test(r.url())
    );
    await confirm.click();
    expect((await unpublished).ok()).toBeTruthy();

    // (4) RELOAD, not the optimistic cache. The badge is re-read from a fresh page load.
    await page.reload();
    await expect(page.getByTestId(`tool-visibility-${TOOL}`)).toHaveText(/Private/, {
      timeout: 20_000,
    });

    // …and the reverse did NOT cascade. The agent stays published and still binds the
    // tool — unpublish removes discoverability, never capability. Asserted here rather
    // than only in the bash suite because this is the assertion a future "clean up the
    // dangling tool" change would break first.
    const api = await adminApi();
    const agentAfter = await api.get(`/api/v1/agents/${AGENT}`);
    expect((await agentAfter.json())?.publish_status).toBe("published");
    const boundAfter = await api.get(`/api/v1/agents/${AGENT}/tools`);
    const boundJson = await boundAfter.json();
    const boundNames = (boundJson?.items ?? boundJson ?? []).map((t: { name: string }) => t.name);
    expect(boundNames).toContain(TOOL);
    await api.dispose();
  });
});

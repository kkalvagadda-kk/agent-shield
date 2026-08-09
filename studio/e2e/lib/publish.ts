// e2e/lib/publish.ts — Playground promote/publish + admin approve-to-catalog.
import { expect, type Page } from "@playwright/test";

/** Click "Mark Version Passed" on the Playground promote panel (→ PATCH eval_passed). */
export async function markVersionPassed(page: Page, agentName: string): Promise<void> {
  const patched = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && new RegExp(`/api/v1/agents/${agentName}/versions/`).test(r.url()),
  );
  await page.getByRole("button", { name: /Mark Version Passed/i }).click();
  expect((await patched).ok()).toBeTruthy();
}

/** Click "Mark Adversarial Passed" (agent-only; required for high/critical-risk tools). */
export async function markAdversarialPassed(page: Page, agentName: string): Promise<void> {
  const patched = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && new RegExp(`/api/v1/agents/${agentName}/versions/`).test(r.url()),
  );
  await page.getByRole("button", { name: /Mark Adversarial Passed/i }).click();
  const body = (await patched).request().postDataJSON();
  expect(body?.adversarial_eval_passed).toBe(true);
}

/** Click "Publish Agent" → POST /agents/{name}/publish. Returns the response status. */
export async function publishAgent(page: Page, agentName: string): Promise<number> {
  const posted = page.waitForResponse(
    (r) => r.request().method() === "POST" && new RegExp(`/api/v1/agents/${agentName}/publish$`).test(r.url()),
  );
  await page.getByRole("button", { name: /Publish Agent/i }).click();
  return (await posted).status();
}

/**
 * As platform-admin, approve a pending publish request for `agentName` in the admin queue
 * (→ promotes to /catalog). Returns true on a 2xx approve.
 *
 * THE ROW NO LONGER APPROVES. Decision 47 step D moved Approve INTO the review drawer
 * (option B) so a reviewer cannot authorize an artifact org-wide without having been shown
 * its tools, their endpoints, their credentials and the publish cascade. This helper drives
 * the real journey now: open the drawer, acknowledge the cascade if there is one, approve.
 *
 * Updated in the same change as the UI. A helper left clicking the old button would have
 * timed out waiting for an approve response that the click no longer triggers — and the
 * failure would have read as "approval is broken", three layers away from the cause.
 */
export async function approveToCatalog(page: Page, agentName: string): Promise<boolean> {
  await page.goto("/admin/publish-requests");
  const row = page.locator("tr").filter({ has: page.getByText(agentName, { exact: true }) }).first();
  await expect(row).toBeVisible({ timeout: 15_000 });

  const reviewed = page.waitForResponse(
    (r) => /\/api\/v1\/admin\/publish-requests\/.+\/review$/.test(r.url()),
  );
  await row.getByRole("button", { name: /Review & Promote/i }).click();
  await reviewed;

  const drawer = page.getByTestId("publish-review-drawer");
  await expect(drawer).toBeVisible({ timeout: 10_000 });

  // The acknowledgement only renders when the approval publishes tools org-wide.
  const ack = drawer.getByTestId("review-cascade-ack").locator("input");
  if (await ack.count()) await ack.check();

  const approved = page.waitForResponse(
    (r) => r.request().method() === "POST" && /\/api\/v1\/admin\/publish-requests\/.+\/approve$/.test(r.url()),
  );
  await drawer.getByTestId("review-approve").click();
  return (await approved).ok();
}

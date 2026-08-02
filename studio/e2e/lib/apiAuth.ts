import { expect, type Page } from "@playwright/test";

/**
 * Capture the app's own Bearer token so a spec can call `require_user` routes.
 *
 * WHY THIS IS NEEDED AT ALL
 * -------------------------
 * Playwright's storage state carries Keycloak's SESSION COOKIE, not the access
 * token — keycloak-js holds that in JS memory and attaches it per request. So a
 * bare `page.request` call is unauthenticated, and every route gated by
 * `require_user` answers 401. Trigger CRUD is gated; agent create is not, which is
 * why a spec can look half-working: it creates the agent fine and dies on the
 * trigger.
 *
 * WHY IT LIVES HERE
 * -----------------
 * Ten specs had each pasted their own copy of this sniff. The bash layer already
 * paid for that lesson — the same token setup was duplicated across suites, drifted,
 * and left 15 of them silently dead once trigger routes gained `require_user`
 * (docs/bugs/trigger-e2e-suites-dead-since-require-user.md). `e2e-auth.sh` was the
 * fix there; this is the same fix on this side of the wall. A spec that authenticates
 * by copy-paste is a spec that stops authenticating when the rule changes and nobody
 * updates every copy.
 *
 * Fails LOUD: a spec that proceeds without a token produces 401s that read as product
 * failures, which is a worse outcome than not running.
 *
 * @param page  an authenticated page (global-setup has already logged in)
 * @param warm  a route whose load is guaranteed to issue an /api/v1 call
 * @returns headers ready to spread into `page.request` calls
 */
export async function captureAuthHeaders(
  page: Page,
  warm = "/agents",
): Promise<{ Authorization: string }> {
  let authHeader: string | undefined;
  page.on("request", (req) => {
    const h = req.headers()["authorization"];
    if (h?.startsWith("Bearer ") && req.url().includes("/api/v1/")) authHeader = h;
  });
  await page.goto(warm);
  await page.waitForLoadState("networkidle");
  expect(
    authHeader,
    `no Bearer token seen on /api/v1 traffic after loading ${warm} — the spec would ` +
      `run unauthenticated and its 401s would read as product failures`,
  ).toBeTruthy();
  return { Authorization: authHeader! };
}

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// approvals-badge.spec.ts — WS-6 Phase 5.
//
// Proves the nav badge's whole contract: it COUNTS live backend state and it ROUTES.
// A count that doesn't navigate is half a feature; a badge that renders a mount-time
// cache is a lie with a number on it.
//
// NO `page.route` STUBS. Every assertion here is against the REAL registry-api through
// the REAL edge. Stubbing the count would make this spec a test of the stub — and the
// badge's only real risk is that its producer (listPendingApprovals → GET /approvals/
// ?status=pending) is wired wrong, which a stub would paper over precisely.
//
// THE ORACLE IS THE API, NOT A HARDCODED NUMBER. The spec reads the count from the real
// response and asserts the DOM agrees. Asserting a literal (e.g. "expect 3") would make
// the verdict track leftover cluster state — the scavenging failure mode. Here the
// cluster's actual pending count is whatever it is, and the badge must match IT.
// ---------------------------------------------------------------------------

const PENDING_RE = /\/api\/v1\/approvals\/\?.*status=pending|\/api\/v1\/approvals\/\?status=pending/;

/** Read the badge's producer response the same way the Sidebar does. */
async function readPendingCount(page: import("@playwright/test").Page): Promise<number> {
  const respP = page.waitForResponse(
    (r) => r.url().includes("/api/v1/approvals/") && r.url().includes("status=pending"),
    { timeout: 20_000 }
  );
  await page.goto("/");
  const resp = await respP;
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  // The wire shape is an {items, total} envelope — registryApi unwraps `.items`.
  // Assert the envelope here too: if it ever became a bare list, the client's
  // `data.items` would be undefined and the badge would silently vanish.
  expect(body).toHaveProperty("items");
  expect(Array.isArray(body.items)).toBe(true);
  return body.items.length;
}

test("sidebar badge reflects the REAL pending count and routes to the inbox", async ({
  page,
}) => {
  const count = await readPendingCount(page);
  await page.waitForLoadState("networkidle");

  const badge = page.getByTestId("approvals-badge");

  if (count > 0) {
    // The badge must show the real number the API returned.
    await expect(badge).toBeVisible({ timeout: 10_000 });
    await expect(badge).toHaveText(String(count));
  } else {
    // 0 pending ⇒ NO badge (asserted absent, not "0"): an always-on chip showing zero
    // is noise that trains operators to ignore the one control meant to summon them.
    await expect(badge).toHaveCount(0);
  }

  // The nav item routes regardless of the count — the badge's destination must exist
  // even when there is nothing pending, or the feature only works while it's urgent.
  await page.getByRole("link", { name: /Approvals/i }).click();
  await expect(page).toHaveURL(/\/approvals$/, { timeout: 10_000 });
  // The inbox page actually rendered (not a blank route): its heading is present.
  await expect(
    page.getByRole("heading", { name: /Approvals/i }).first()
  ).toBeVisible({ timeout: 10_000 });
});

test("badge count survives a reload — it reads live backend state, not a mount cache", async ({
  page,
}) => {
  // DoD #2 (save → reload → assert), adapted: the badge is a READ surface, so the
  // round-trip guard is that a reload re-derives the same count FROM THE BACKEND rather
  // than from anything cached in the SPA at first mount.
  const first = await readPendingCount(page);
  await page.waitForLoadState("networkidle");

  const second = await readPendingCount(page); // full navigation = fresh mount + refetch
  await page.waitForLoadState("networkidle");

  expect(second).toBe(first);

  const badge = page.getByTestId("approvals-badge");
  if (second > 0) {
    await expect(badge).toHaveText(String(second));
  } else {
    await expect(badge).toHaveCount(0);
  }
});

test("the served bundle is the bundle we think it is (build marker renders)", async ({
  page,
}) => {
  // BEHAVIOURAL half of the E-3 guard. suite-79 greps the served BYTES for the marker;
  // this asserts the app actually RENDERS it — presence in a bundle is not proof that
  // the code runs (a grep passes on dead code; today a route decorator can steal a
  // route while every grep still matches). `__STUDIO_BUILD` sat unread for 67 tags
  // precisely because nothing ever looked at it.
  await page.goto("/");
  await page.waitForLoadState("networkidle");

  const marker = page.getByTestId("studio-build");
  await expect(marker).toBeVisible({ timeout: 10_000 });

  const shown = (await marker.textContent())?.trim() ?? "";
  expect(shown).toMatch(/^\d+\.\d+\.\d+$/);

  // And it must agree with what the runtime marker claims, so the two readers of the
  // one STUDIO_BUILD constant cannot drift apart.
  const windowMarker = await page.evaluate(
    () => (window as unknown as { __STUDIO_BUILD?: string }).__STUDIO_BUILD
  );
  expect(windowMarker).toBe(shown);
});

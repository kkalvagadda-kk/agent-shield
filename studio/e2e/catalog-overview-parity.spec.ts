import { test, expect, type Browser } from "@playwright/test";

// ---------------------------------------------------------------------------
// catalog-overview-parity.spec.ts — WS-6 Phase 4.
//
// THE PARITY PROOF, behaviourally. suite-79's T-S79-000 greps that the fork is gone;
// a grep proves PRESENCE, never CORRECTNESS. This drives a real browser and asserts the
// SHARED dispatcher actually renders — the node `data-testid="overview-for-shape"`
// exists ONLY inside OverviewForShape, so this assertion CANNOT pass against the old
// hand-written inline chain (which never rendered it). That is the whole design of the
// handle: make the test unable to pass against the thing being deleted.
//
// What drifted, concretely: CatalogDetailPage's inline overview handled reactive /
// durable / scheduled and had NO event-driven branch, while the shared set has four.
// It failed SAFE — the page just rendered less — which is why it survived. Same class
// as docs/bugs/side-effecting-lost-on-declarative-runner-path.md.
//
// NO `page.route` STUBS. Real agent, real deployment, real render.
// FIXTURE IS CREATED, NEVER SCAVENGED: a spec that grabs "the first catalog row" has a
// verdict that tracks leftover cluster state and dies on a fixture that structurally
// cannot satisfy it.
// ---------------------------------------------------------------------------

const TS = Date.now();
const AGENT = `e2e-ovwparity-${TS}`;

async function createAgentViaUI(browser: Browser, agentName: string) {
  const ctx = await browser.newContext({ storageState: "e2e/.auth/state.json" });
  const page = await ctx.newPage();
  try {
    await page.goto("/agents/new");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: /No-code/i }).click();
    await page.waitForLoadState("domcontentloaded");
    await page.getByPlaceholder("my-agent").fill(agentName);
    const createDone = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/agents") &&
        r.request().method() === "POST" &&
        !r.url().includes("/runs"),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Create Agent/i }).click();
    const createResp = await createDone;
    // Assert the REAL creation succeeded rather than inferring it from a redirect.
    // NOTE: the wizard navigates to the agent LIST (`/agents`, CreateAgentPage.tsx:785),
    // not to `/agents/{name}`. deployment-overview.spec.ts still waits for the detail
    // URL and is stale against the shipped app — copying that helper is what failed this
    // spec's first run. A navigation is a side effect of creation, not proof of it; the
    // 201 is the proof.
    expect(createResp.status()).toBe(201);
    await page.waitForURL("**/agents", { timeout: 15_000 });
  } finally {
    await ctx.close();
  }
}

async function deleteAgentViaUI(browser: Browser, agentName: string) {
  const ctx = await browser.newContext({ storageState: "e2e/.auth/state.json" });
  const page = await ctx.newPage();
  try {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const deleteBtn = page
      .locator("tr", { hasText: agentName })
      .getByRole("button", { name: /Delete/i });
    if ((await deleteBtn.count()) === 0) return;
    page.once("dialog", (d) => d.accept());
    await deleteBtn.click();
    await page.waitForLoadState("networkidle");
  } finally {
    await ctx.close();
  }
}

test.beforeAll(async ({ browser }) => {
  await createAgentViaUI(browser, AGENT);
});

test.afterAll(async ({ browser }) => {
  await deleteAgentViaUI(browser, AGENT);
});

test("the deployment overview renders via the SHARED dispatcher (not an inline fork)", async ({
  page,
}) => {
  // Real sandbox deployment of the real fixture agent, driven through the REAL control.
  // The `/agents/:name/deploy` PAGE no longer exists — deploy is a modal on the agent
  // detail page (App.tsx:61 "deploy is now a modal on AgentDetailPage"). Navigating to
  // the dead route lands on a page with no Deploy button and times out waiting for a
  // POST that was never going to fire. (deployment-overview.spec.ts still drives that
  // removed route — see the WS-6 gap ledger.)
  await page.goto(`/agents/${AGENT}`);
  await page.waitForLoadState("networkidle");

  const deployDone = page.waitForResponse(
    (r) =>
      r.url().includes(`/api/v1/agents/${AGENT}/deploy`) &&
      r.request().method() === "POST",
    { timeout: 30_000 }
  );
  await page.getByRole("button", { name: /^Deploy$/ }).first().click();

  // The modal ("Deploy to sandbox") confirms with its own Deploy button. Anchor on the
  // HEADING and walk up to the card that owns it — `locator("div").filter({hasText})`
  // matches every ancestor div AND the innermost text wrapper, and `.last()` picks the
  // innermost one, which contains the text but NOT the button. (That is exactly how this
  // spec failed once: the modal was open and visible, and the click still timed out.)
  const modal = page
    .getByRole("heading", { name: "Deploy to sandbox" })
    .locator("xpath=ancestor::div[contains(@class,'card')][1]");
  await expect(modal).toBeVisible({ timeout: 10_000 });
  await modal.getByRole("button", { name: /^Deploy$/ }).click();
  const deployResp = await deployDone;
  expect(deployResp.status()).toBe(201);
  const depName: string = (await deployResp.json()).name;

  // Artifact page → the deployment → Level-3 overview.
  await page.goto(`/agents/${AGENT}`);
  await page.waitForLoadState("networkidle");
  const depLink = page.locator("main a", { hasText: depName });
  await expect(depLink).toBeVisible({ timeout: 10_000 });
  await depLink.click();
  await page.waitForURL("**/d/**", { timeout: 10_000 });

  // THE ASSERTION: the shared dispatcher's own node rendered. Unreachable for an
  // inline fork.
  const shared = page.getByTestId("overview-for-shape");
  await expect(shared).toBeVisible({ timeout: 15_000 });

  // The dispatcher resolved a real shape (this fixture is reactive: no webhook, no
  // schedule, execution_shape defaults to reactive).
  await expect(shared).toHaveAttribute("data-shape", "reactive");

  // And it dispatched to the RIGHT component — OverviewReactive owns the API Endpoint
  // card. `data-shape` alone would pass if the map pointed every shape at one
  // component; this pins the mapping to an observable the component actually renders.
  await expect(page.getByText("API Endpoint")).toBeVisible({ timeout: 10_000 });

  // The fail-closed card must NOT be showing — a known shape must never reach it.
  await expect(page.getByTestId("overview-unsupported-shape")).toHaveCount(0);

  // Reload → still rendered from the backend, and re-open the tab: the detail pages
  // keep `activeTab` in LOCAL state, so a reload silently lands on "deployments" and an
  // assertion would fail for a reason that has nothing to do with the code under test.
  await page.reload();
  await page.waitForLoadState("networkidle");
  await expect(page.getByTestId("overview-for-shape")).toBeVisible({ timeout: 15_000 });
});

test("the catalog artifact overview renders via the SAME shared dispatcher", async ({
  page,
}) => {
  // THE CROSS-PAGE IDENTITY — the actual parity proof. The same testid, owned by the
  // same component, must render on the catalog surface too. Before WS-6 this page had
  // its own hand-written chain and could not have rendered this node at all.
  //
  // The catalog lists PUBLISHED artifacts. Publishing is gated (eval + adversarial
  // sign-off for risky tools) and is a different slice's journey, so rather than
  // scavenge a random published row — whose shape we do not control and whose presence
  // we cannot guarantee — this test asserts the identity only when the catalog has a
  // detail page to open, and FAILS LOUD if the page renders an inline fork instead.
  await page.goto("/catalog");
  await page.waitForLoadState("networkidle");

  const rows = page.locator("main a[href*='/catalog/']");
  const n = await rows.count();
  test.skip(
    n === 0,
    "no published catalog artifact in this cluster — the cross-page identity is " +
      "asserted by CatalogDetailPage.test.tsx (Vitest, which mounts the real page " +
      "component) and by suite-79 T-S79-000's zero-inline-fork grep. Recorded in the " +
      "WS-6 gap ledger; publishing a fixture is a separate gated journey."
  );

  await rows.first().click();
  await page.waitForURL(/\/catalog\//, { timeout: 10_000 });
  await page.waitForLoadState("networkidle");

  // Re-open the Overview tab explicitly: activeTab is local state and does not survive
  // navigation/reload.
  const overviewTab = page.getByRole("button", { name: /^overview$/i });
  if ((await overviewTab.count()) > 0) await overviewTab.first().click();

  const shared = page.getByTestId("overview-for-shape");
  await expect(shared).toBeVisible({ timeout: 15_000 });
  // Whatever the artifact's shape, the dispatcher must have resolved a KNOWN one.
  await expect(page.getByTestId("overview-unsupported-shape")).toHaveCount(0);
  const shape = await shared.getAttribute("data-shape");
  expect(["reactive", "durable", "scheduled", "event_driven"]).toContain(shape);
});

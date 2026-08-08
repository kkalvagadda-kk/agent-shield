import { test, expect } from "@playwright/test";
import { adminAuthHeaders } from "./lib/api";

/**
 * Model is REQUIRED (an agent with no LLM provider can never complete a run), so
 * every wizard submit must choose one — the same step a user now takes. Selects
 * the first real provider rather than a fixed id, since seeded providers differ
 * per cluster.
 */
async function pickModel(page: import("@playwright/test").Page) {
  const select = page.getByLabel("Model", { exact: true });
  const value = await select.locator("option").nth(1).getAttribute("value");
  if (value) await select.selectOption(value);
}

// ---------------------------------------------------------------------------
// create-agent-wizard.spec.ts
//   Proves the create-agent wizard exposes the THREE independent axes (R1):
//   Shape · Trigger · Class — not the old flattened 4-way picker — and that a
//   durable + scheduled *daemon* agent survives a create → reload round-trip.
// ---------------------------------------------------------------------------

async function openNoCode(page: import("@playwright/test").Page) {
  await page.goto("/agents/new");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: /No-code/i }).click();
}

test.describe("create-agent wizard — Shape · Trigger · Class (R1)", () => {
  test("shows the three independent selectors", async ({ page }) => {
    await openNoCode(page);
    await expect(page.getByRole("radio", { name: /Ephemeral/i })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Durable/i })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Schedule (cron)" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Webhook (inbound events)" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /User-delegated/i })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Daemon/i })).toBeVisible();
  });

  test("Schedule reveals cron fields and auto-defaults class to daemon", async ({ page }) => {
    await openNoCode(page);
    await expect(page.getByPlaceholder("0 9 * * 1")).toHaveCount(0);
    await expect(page.getByRole("radio", { name: /User-delegated/i })).toHaveAttribute("aria-checked", "true");
    await page.getByRole("checkbox", { name: "Schedule (cron)" }).check();
    await expect(page.getByPlaceholder("0 9 * * 1")).toBeVisible();
    await expect(page.getByRole("radio", { name: /Daemon/i })).toHaveAttribute("aria-checked", "true");
  });

  test("Webhook reveals filter-condition fields", async ({ page }) => {
    await openNoCode(page);
    await page.getByRole("checkbox", { name: "Webhook (inbound events)" }).check();
    await expect(page.getByText(/Filter conditions/i)).toBeVisible();
    await expect(page.getByPlaceholder("event_type")).toBeVisible();
  });

  test("Ephemeral + no trigger shows no trigger config fields", async ({ page }) => {
    await openNoCode(page);
    await expect(page.getByPlaceholder("0 9 * * 1")).toHaveCount(0);
    await expect(page.getByText(/Filter conditions/i)).toHaveCount(0);
  });

  // Save → reload → assert (DoD #2): a durable + scheduled *daemon* agent — a cube
  // cell the old 4-way picker could not author — persists to the backend.
  test("durable+scheduled daemon agent: POST carries agent_class, reload → persisted", async ({ page }) => {
    await openNoCode(page);
    const name = `wsz-dur-daemon-${Date.now()}`;
    await page.getByPlaceholder("my-agent").fill(name);
    await page.getByRole("radio", { name: /Durable/i }).click();
    await page.getByRole("checkbox", { name: "Schedule (cron)" }).check(); // class auto-defaults → daemon

    const createResp = page.waitForResponse(
      (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/agents/"),
    );
    await pickModel(page);
    await page.getByRole("button", { name: /^Create Agent$/i }).click();
    const resp = await createResp;
    expect(resp.status()).toBe(201);
    const body = await resp.json();
    expect(body.agent_class).toBe("daemon");
    expect(body.execution_shape).toBe("durable");

    // Reload the agent's Settings and confirm the persisted class survived the round-trip.
    await page.goto(`/agents/${name}`);
    await page.getByRole("button", { name: "settings" }).click();
    await page.reload();
    await page.getByRole("button", { name: "settings" }).click();
    await expect(page.getByLabel(/Authority/i)).toHaveValue("daemon");
  });
});

// ── Route to production ──────────────────────────────────────────────────────
// Added after the schedule-lifecycle journey. Reaching production takes six steps
// across five screens, and the product used to describe ONE of them per warning,
// with no sense of sequence — so an operator who published, watched it succeed,
// and came back to an unchanged screen had no way to tell what remained.
test.describe("route to production", () => {
  test("a scheduled agent shows where it is on the path, and the last step is the real one", async ({
    page,
  }) => {
    await openNoCode(page);
    const name = `wsz-route-${Date.now()}`;
    await page.getByPlaceholder("my-agent").fill(name);
    await page.getByRole("checkbox", { name: "Schedule (cron)" }).check();

    // The wizard's own notice must name the FULL path, not just "Publish".
    // docs/bugs/publish-does-not-create-a-production-deployment.md
    const notice = page.getByTestId("schedule-not-in-production-notice");
    // NBSP, not a space. The notice renders "Admin&nbsp;▸&nbsp;Publish&nbsp;Queue"
    // (CreateAgentPage.tsx:146) so the DOM text carries U+00A0 between the words and a
    // regular-space regex can never match. Match either kind of whitespace rather than
    // pasting a literal NBSP into the source, which is invisible in review.
    await expect(notice).toContainText(/Publish[\s\u00a0]+Queue/i);
    await expect(notice).toContainText(/Deploy Latest/i);
    await expect(notice).toContainText(/only creates the catalog listing/i);

    await pickModel(page);
    const created = page.waitForResponse(
      (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/agents/"),
    );
    await page.getByRole("button", { name: /^Create Agent$/i }).click();
    expect((await created).status()).toBe(201);

    await page.goto(`/agents/${name}`);
    const strip = page.getByTestId("route-to-production");
    await expect(strip).toBeVisible({ timeout: 20_000 });

    // Brand new agent: nothing done yet, and the strip says so rather than
    // showing a single blocker.
    await expect(page.getByTestId("route-step-sandbox")).toHaveAttribute("data-state", "current");
    await expect(page.getByTestId("route-step-production")).toHaveAttribute("data-state", "todo");
    await expect(page.getByTestId("route-to-production-summary")).toContainText(/steps left/i);

    // Exactly ONE hint — the step they are on. Four simultaneous warnings is the
    // state this replaces.
    await expect(page.getByTestId("route-to-production-hint")).toHaveCount(1);

    // Survives a reload: the strip is derived from server state, not local state.
    await page.reload();
    await expect(page.getByTestId("route-step-sandbox")).toHaveAttribute("data-state", "current", {
      timeout: 20_000,
    });

    // `page.request` carries Keycloak's SESSION COOKIE, not the access token —
    // keycloak-js holds that in JS memory (see e2e/lib/apiAuth.ts). So this cleanup has
    // been a silent no-op since R3 gated DELETE /agents/{name}: the 401 went straight
    // into .catch(). Agents accumulated on the cluster with nothing reporting it.
    await page.request
      .delete(`/api/v1/agents/${name}`, { headers: await adminAuthHeaders() })
      .catch(() => undefined);
  });
});

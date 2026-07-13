import { test, expect } from "@playwright/test";

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

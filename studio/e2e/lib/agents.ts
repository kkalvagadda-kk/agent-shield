// e2e/lib/agents.ts — create an agent with a tool (UI) + deploy to sandbox.
import { expect, type Page, type APIRequestContext } from "@playwright/test";

/**
 * Drive the no-code create form to make an agent bound to `toolName`, through the real UI.
 * Asserts the POST /agents body carries the tool. Returns the agent name.
 * Reference: tools-picker-drawer.spec.ts.
 */
/**
 * Select a model in the create-agent form. THE one definition — do not re-copy it.
 *
 * `llm_provider_id` became REQUIRED in studio 0.1.178 (CreateAgentPage.tsx:664,
 * `z.string().min(1, "Pick a model — an agent without one cannot run.")`), because an
 * agent with no model was being accepted silently and could never run. Zod then rejects
 * the submit, so NO POST is issued at all — a spec that only fills the name sits waiting
 * on a `waitForResponse` that can never arrive and dies at its 20s timeout, which reads
 * as "creation is broken" rather than "the form has a new required field".
 *
 * That cost six specs in the 2026-08-04 baseline (agent-detail-modes, agents,
 * catalog-overview-parity, deployment-overview, workflow-builder, workflows) — one
 * product change against ELEVEN private copies of the same create helper. Nobody noticed
 * because the browser layer could not run against EKS at all (gap G-R0-8).
 *
 * Index 1, not 0: option 0 is the "Select a model…" placeholder, whose value is "".
 */
export async function pickModel(page: Page): Promise<void> {
  const select = page.getByLabel("Model", { exact: true });
  const value = await select.locator("option").nth(1).getAttribute("value");
  if (value) await select.selectOption(value);
}

export async function createAgentWithTool(page: Page, name: string, toolLabel: string): Promise<string> {
  await page.goto("/agents/new");
  await page.getByRole("button", { name: /No-code/i }).click();
  await page.getByPlaceholder("my-agent").fill(name);
  await pickModel(page);

  // Tool picker → drawer → select the tile → Done.
  const picker = page.getByTestId("tools-picker");
  await expect(picker).toBeVisible({ timeout: 15_000 });
  await picker.getByRole("button", { name: /add from catalog/i }).click();
  const drawer = page.getByTestId("tools-picker-drawer");
  const tile = drawer.locator("label", { hasText: toolLabel });
  await tile.getByRole("checkbox").check();
  await drawer.getByRole("button", { name: /^Done$/ }).click();
  await expect(picker).toContainText(toolLabel);

  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && /\/api\/v1\/agents\/?$/.test(r.url()),
  );
  await page.getByRole("button", { name: /^Create Agent$/i }).click();
  const resp = await created;
  expect(resp.status()).toBe(201);
  const body = resp.request().postDataJSON();
  expect(body?.metadata?.tools ?? []).toContain(toolLabel);
  return name;
}

/**
 * Deploy an agent to sandbox through the DeployModal on the detail page.
 * Returns the deployment id from the 201. Reference: catalog-overview-parity.spec.ts.
 */
export async function deployToSandbox(page: Page, name: string): Promise<string> {
  await page.goto(`/agents/${name}`);
  await page.getByRole("button", { name: /^Deploy$/ }).first().click();
  const card = page
    .getByRole("heading", { name: "Deploy to sandbox" })
    .locator("xpath=ancestor::div[contains(@class,'card')][1]");
  const deployed = page.waitForResponse(
    (r) => r.request().method() === "POST" && new RegExp(`/api/v1/agents/${name}/deploy$`).test(r.url()),
  );
  await card.getByRole("button", { name: /^Deploy$/ }).click();
  const resp = await deployed;
  expect(resp.status()).toBe(201);
  return (await resp.json()).id as string;
}

/**
 * Deploy + poll the deployments list until a sandbox deployment is `running`.
 * Cold-pod tolerant: returns { depId, ready } — ready=false when no warm pod within the
 * budget (the accepted boundary; the caller decides whether to skip the live leg).
 * Reference: durable-stream.spec.ts:57-66.
 */
export async function deployAndWaitReady(
  page: Page, api: APIRequestContext, name: string, tries = 40, intervalMs = 3000,
): Promise<{ depId: string; ready: boolean }> {
  const depId = await deployToSandbox(page, name);
  for (let i = 0; i < tries; i++) {
    const r = await api.get(`/api/v1/agents/${name}/deployments`);
    if (r.ok()) {
      const running = (await r.json()).find(
        (d: { status: string; environment: string }) => d.status === "running" && d.environment === "sandbox",
      );
      if (running) return { depId: running.id ?? depId, ready: true };
    }
    await page.waitForTimeout(intervalMs);
  }
  return { depId, ready: false };
}

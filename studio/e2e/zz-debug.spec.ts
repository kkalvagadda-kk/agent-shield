import { test } from "@playwright/test";
test("debug", async ({ page }) => {
  await page.goto("/agents/serper-agent-4/d/49f9e22c-a400-4edd-b286-b8b2d8f843d7/chat");
  await page.waitForLoadState("networkidle").catch(() => {});
  console.log("URL:", page.url());
  console.log("TITLE:", await page.title());
  const bodyText = (await page.locator("body").innerText().catch(() => "")).slice(0, 600);
  console.log("BODY:", bodyText);
  const inputs = await page.locator("input").count();
  console.log("INPUT COUNT:", inputs);
  const placeholders = await page.locator("input").evaluateAll((els) => els.map((e) => (e as HTMLInputElement).placeholder));
  console.log("PLACEHOLDERS:", JSON.stringify(placeholders));
});

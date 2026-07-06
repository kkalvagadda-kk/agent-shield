import { chromium, type FullConfig } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Authenticate once through Keycloak and persist the session to storageState.
// Studio uses keycloak-js onLoad:'login-required', so hitting the app redirects
// to the Keycloak hosted login (proxied by Studio's nginx at /realms/...).
export default async function globalSetup(config: FullConfig) {
  const baseURL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";
  const username = process.env.STUDIO_E2E_USER || "platform-admin";
  const password = process.env.STUDIO_E2E_PASSWORD || "PlatformAdmin2024";

  // Playwright runs from the studio/ dir; match the config's storageState path.
  const authDir = path.resolve("e2e", ".auth");
  fs.mkdirSync(authDir, { recursive: true });
  const statePath = path.join(authDir, "state.json");

  const browser = await chromium.launch();
  const page = await browser.newPage();
  try {
    await page.goto(baseURL, { waitUntil: "domcontentloaded" });
    // Keycloak login form (redirected). Field ids are Keycloak defaults.
    await page.waitForSelector("#username", { timeout: 30_000 });
    await page.fill("#username", username);
    await page.fill("#password", password);
    await Promise.all([
      page.waitForURL((url) => url.href.startsWith(baseURL), { timeout: 30_000 }),
      page.click("#kc-login, button[type=submit], input[type=submit]"),
    ]);
    // Wait for the SPA to finish token exchange (keycloak-js) and render.
    await page.waitForLoadState("networkidle");
    await page.context().storageState({ path: statePath });
    // eslint-disable-next-line no-console
    console.log(`[global-setup] authenticated as ${username}; session saved to ${statePath}`);
  } finally {
    await browser.close();
  }
}

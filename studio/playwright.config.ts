import { defineConfig, devices } from "@playwright/test";

// Browser E2E against the REAL deployed Studio (via kubectl port-forward — see
// scripts/studio-e2e.sh). global-setup authenticates through Keycloak once and
// saves the session; specs reuse it via storageState.
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

// When the deployed gateway routes only on a specific Host (e.g. the internal
// EKS ELB DNS) and that host isn't locally resolvable, tunnel it to a local
// gateway port-forward via Chromium's host-resolver-rules. Set e.g.
//   PLAYWRIGHT_HOST_RESOLVER_RULES="MAP my-elb.elb.amazonaws.com 127.0.0.1:8443"
// so PLAYWRIGHT_BASE_URL can be the REAL portless origin (redirect_uri +
// TLS SAN match) while connections land on the port-forward. Env-gated: no
// effect for the default local (kind) http port-forward flow.
const HOST_RESOLVER_RULES = process.env.PLAYWRIGHT_HOST_RESOLVER_RULES;
const launchOptions = HOST_RESOLVER_RULES
  ? { args: [`--host-resolver-rules=${HOST_RESOLVER_RULES}`] }
  : {};

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: BASE_URL,
    storageState: "e2e/.auth/state.json",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 15_000,
    // The platform gateway serves a self-signed cert over https. Running against
    // it (rather than an http port-forward) keeps Keycloak's Secure session
    // cookies working, which is required for SSO silent-auth between specs.
    ignoreHTTPSErrors: true,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], launchOptions } },
  ],
});

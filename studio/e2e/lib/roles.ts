import fs from "node:fs";
import path from "node:path";

// Where global-setup persists one session per global role. A spec opts into a
// role with:
//
//   test.use({ storageState: stateFor("consumer") });
//
// Specs that say nothing keep the default storageState from playwright.config
// (platform-admin), so this is additive — no existing spec changes behaviour.

const AUTH_DIR = path.resolve("e2e", ".auth");

export type GlobalRole = "platform-admin" | "contributor" | "consumer";

export function stateFor(role: GlobalRole): string {
  return path.join(AUTH_DIR, `${role}.json`);
}

/**
 * Throw if a role's session was never written.
 *
 * Called at the top of a role spec so a provisioning failure in global-setup
 * surfaces as a NAMED failure here rather than as a spec that quietly runs
 * unauthenticated (Playwright treats a missing storageState file as an error
 * only at use time, and the message names a path, not the cause). A role test
 * that does not actually run as that role is worse than no test: it reports
 * green for a gate it never exercised.
 */
export function assertRoleSession(role: GlobalRole): void {
  const p = stateFor(role);
  if (!fs.existsSync(p)) {
    throw new Error(
      `No saved session for role '${role}' at ${p}. global-setup provisions ` +
        `e2e-contributor / e2e-consumer through POST /api/v1/admin/users and logs ` +
        `each in; if that failed the setup should have thrown. Re-run with the ` +
        `platform reachable, or check that the admin persona can still reach ` +
        `/api/v1/admin/users (RBAC R2 requires the platform-admin global role).`,
    );
  }
}

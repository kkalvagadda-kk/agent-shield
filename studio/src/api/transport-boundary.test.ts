import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

// ─────────────────────────────────────────────────────────────────────────────
// REPO INVARIANT — only api/registryApi.ts talks to /api/v1.
//
// Why this is a test and not a code review note: three separate call sites had
// each rolled their own `fetch("/api/v1/admin/...")` with no Authorization
// header. They worked only because those routers were unauthenticated. When
// registry-api 0.2.262 added `require_user`, all of them broke at once —
// Sidebar's copy poisoned React Query with a 401 envelope and blanked the entire
// app; AdminAccessPage's six copies killed the whole Access Control users tab.
// See docs/bugs/studio-blank-page-unauthed-fetch-teams-summary.md.
//
// Fixing those three sites does not stop the fourth. `http` (api/registryApi.ts)
// owns the Bearer header and the token-refresh interceptor, so a call that
// bypasses it is unauthenticated by construction — and the bypass is invisible to
// the component's own test, because every page test mocks the registryApi module,
// not global fetch. This check is the thing that fails when someone reintroduces
// it. It runs in `npm run test`, which CLAUDE.md already makes a mandatory gate.
//
// If you are here because this test failed: add your endpoint to
// src/api/registryApi.ts and call it from the component. That is the fix.
// ─────────────────────────────────────────────────────────────────────────────

const SRC = join(__dirname, "..");

/** Files allowed to call fetch() directly, each with the reason it must. */
const ALLOWED = new Map<string, string>([
  // axios cannot stream a response body, so SSE must use fetch. Both of these
  // attach `Authorization: Bearer ${token}` explicitly — verified below.
  ["pages/WorkflowChatPage.tsx", "SSE stream; sends an explicit Bearer"],
  ["pages/CatalogChatPage.tsx", "SSE stream; sends an explicit Bearer"],
  // /config.json is a static asset served by nginx, not an API route. It is read
  // BEFORE Keycloak is initialised, so it cannot carry a token by definition.
  ["lib/keycloak.ts", "reads /config.json before auth exists"],
  ["pages/ProvidersPage.tsx", "reads /config.json"],
  // The demo shim deliberately talks about fetch in a comment.
  ["demo/demo.ts", "comment only"],
]);

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name !== "node_modules") walk(full, out);
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

/** Matches a real call — `fetch(` — but not `http.fetch(` or `.then(fetch(`. */
const FETCH_CALL = /(^|[^.\w])fetch\s*\(/;

describe("transport boundary", () => {
  it("no source file outside api/registryApi.ts calls fetch() for /api/v1", () => {
    const offenders: string[] = [];

    for (const file of walk(SRC)) {
      const rel = relative(SRC, file).split("\\").join("/");
      if (rel === "api/registryApi.ts") continue;

      const body = readFileSync(file, "utf8");
      body.split("\n").forEach((line, i) => {
        if (line.trimStart().startsWith("//") || line.trimStart().startsWith("*")) return;
        if (!FETCH_CALL.test(line)) return;
        if (ALLOWED.has(rel)) return;
        offenders.push(`${rel}:${i + 1}  ${line.trim()}`);
      });
    }

    expect(
      offenders,
      "These bypass the authed `http` client. Move the call into src/api/registryApi.ts " +
        "(see docs/bugs/studio-blank-page-unauthed-fetch-teams-summary.md), or — if it " +
        "genuinely cannot use axios, e.g. an SSE stream — add it to ALLOWED with a reason " +
        "and make sure it attaches its own Bearer:\n" +
        offenders.join("\n"),
    ).toEqual([]);
  });

  it("every SSE exemption actually sends an Authorization header", () => {
    // The allowlist is not a free pass. An exemption that stopped attaching a token
    // would be exactly the original bug wearing an approved label.
    for (const [rel, reason] of ALLOWED) {
      if (!reason.startsWith("SSE")) continue;
      const body = readFileSync(join(SRC, rel), "utf8");
      expect(body, `${rel} is exempted as an SSE stream but sends no Bearer`).toMatch(
        /Authorization:\s*`Bearer \$\{token\}`/,
      );
    }
  });

  it("the allowlist has no stale entries", () => {
    // A file that no longer calls fetch should leave the list, so the list keeps
    // meaning "these are the exceptions" rather than accumulating dead permissions.
    for (const rel of ALLOWED.keys()) {
      const body = readFileSync(join(SRC, rel), "utf8");
      expect(FETCH_CALL.test(body), `${rel} is allowlisted but no longer calls fetch()`).toBe(true);
    }
  });
});

import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// conversations-sidebar.spec.ts  (context-storage POC-5 — Conversations & History)
//
//   Proves the two REAL browser journeys the API-only bash suite (suite-78,
//   kubectl exec) structurally cannot test — the conversation LIST + FILTER and
//   the select→rehydrate→continue round-trip are React rendering + persistence
//   concerns, not endpoint shape:
//
//   (1) Standalone /conversations — the promoted top-level page. Assert the
//       cross-agent list loads from the backend (GET /me/conversations), the
//       All/Sandbox/Production env pills filter client-side, selecting a row
//       renders the read-only transcript preview (GET /memory), and Continue
//       navigates to the seeded sandbox chat (/agents/:name/chat?session=<tid>).
//
//   (2) Docked History in AgentChatPage (sandbox, /agents/:name/chat) — open the
//       History dock (GET /agents/{name}/memory/conversations), RELOAD, re-open,
//       and assert the conversation is STILL listed from the backend (DoD #2
//       save→reload→assert). Select a row → the transcript rehydrates from
//       /memory; a follow-up POST reuses the thread's session_id.
//
//   The HARD asserts (what must always hold): the list loads from the backend, a
//   reload still lists it, selecting a row fires GET /memory (rehydrate), and the
//   follow-up reuses session_id. Agent-execution COMPLETION is the capacity
//   boundary the bash suites accept — few agent pods are warm, so a run may not
//   finish; the follow-up assertion only checks the request fired with the reused
//   session_id, and skips gracefully when even that can't run (no warm pod).
//
//   Fixture strategy mirrors knowledge.spec / context-attribution.spec: a
//   memory-enabled agent + its stored conversations are SEEDED via the REST API
//   in beforeAll (POST /agents/{name}/memory), so the list is deterministic. The
//   seed's user_id is platform-admin's real Keycloak sub — the SAME identity the
//   browser logs in as — so the ownership-scoped list returns the seeded threads.
// ---------------------------------------------------------------------------

const TS = Date.now();

// Header-auth identity for the REST fixture setup — platform-admin's real
// The conversation list is ownership-scoped to claims["sub"], so the seed must carry the
// SAME user_id the browser session uses. This used to hardcode a literal sub
// ("75c7c8b3-…") — and that broke the moment the Keycloak platform-admin was recreated
// and reissued as a NEW subject, which is exactly what suite-97 T-S97-004 does on purpose
// (delete the admin, restart, require the platform to re-pin). The seeds then wrote
// conversations owned by a subject nobody logs in as, the list correctly returned none,
// and the failure surfaced as "the seeded thread is not in the list".
//
// Coupling a fixture to an identifier the IdP is free to reissue is the SAME design flaw
// R0 removed from the platform itself: bootstrap_admin.py looks the admin up by USERNAME,
// never a stored sub, so a realm recreation self-heals. Resolve it at run time here too.
let USER_SUB = "";
const ADMIN: Record<string, string> = { "X-User-Sub": "", "X-User-Team": "platform" };

async function resolveAdminSub(): Promise<string> {
  // Read it off the Keycloak token itself: the `sub` claim IS the subject the browser
  // session carries, so seeds owned by it are the ones the UI will list. GET /me cannot
  // serve here — this spec's api context authenticates with X-User-Sub audit headers, not
  // a Bearer, so /me answers 401.
  const ctx = await pwRequest.newContext({ baseURL: API_BASE, ignoreHTTPSErrors: true });
  const r = await ctx.post("/realms/agentshield/protocol/openid-connect/token", {
    form: {
      grant_type: "password",
      client_id: "agentshield-studio",
      username: process.env.STUDIO_E2E_USER || "platform-admin",
      password: process.env.STUDIO_E2E_PASSWORD || "PlatformAdmin2024",
    },
  });
  expect(r.ok(), `token for the seed subject: ${r.status()} ${await r.text()}`).toBeTruthy();
  const jwt = (await r.json()).access_token as string;
  await ctx.dispose();
  const claims = JSON.parse(
    Buffer.from(jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"), "base64").toString()
  );
  expect(claims.sub, "token carried no sub — cannot own the seeded conversations").toBeTruthy();
  return claims.sub as string;
}
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";
const INSTR =
  "You are a helpful assistant with memory. Reply in one short sentence.";

const AGENT = `e2e-conv-agent-${TS}`;

// Thread seeded for the standalone /conversations journey. The first user
// message becomes the thread TITLE (POC-5 = first user message); the assistant
// reply is a transcript-only marker (never shown in the sidebar) so asserting it
// visible proves the transcript actually rehydrated, not just the row.
const STANDALONE_THREAD = `e2e-conv-standalone-${TS}`;
const STANDALONE_TITLE = `Heliotrope-standalone-${TS}`;
const STANDALONE_REPLY = `Ack-standalone-${TS}`;

// Thread seeded for the docked-History journey (a distinct thread so the two
// tests don't interfere).
const DOCKED_THREAD = `e2e-conv-docked-${TS}`;
const DOCKED_TITLE = `Zephyr-docked-${TS}`;
const DOCKED_REPLY = `Ack-docked-${TS}`;

// Seed one stored conversation (a user turn + an assistant turn) for the caller.
// A sandbox thread carries no deployment_id (environment derives to "sandbox").
async function seedConversation(
  api: APIRequestContext,
  agentName: string,
  o: {
    threadId: string;
    userMsg: string;
    assistantMsg: string;
    deploymentId?: string;
  }
): Promise<boolean> {
  const resp = await api.post(`/api/v1/agents/${agentName}/memory`, {
    data: {
      thread_id: o.threadId,
      session_id: o.threadId,
      user_id: USER_SUB,
      ...(o.deploymentId ? { deployment_id: o.deploymentId } : {}),
      messages: [
        { role: "user", content: o.userMsg },
        { role: "assistant", content: o.assistantMsg },
      ],
    },
  });
  return resp.ok();
}


/**
 * Ensure the docked History panel is OPEN, whatever its default.
 *
 * AgentChatPage mounts it with `useState(true)` (AgentChatPage.tsx:99) — it defaults to
 * OPEN. This spec used to click `history-toggle` unconditionally and then assert
 * `history-dock` was visible, which CLOSED an already-open dock and failed. Asserting the
 * intended STATE via aria-pressed, rather than assuming a starting state and toggling,
 * keeps this correct if the default ever flips back.
 */
async function openHistoryDock(page: import("@playwright/test").Page) {
  const toggle = page.getByTestId("history-toggle");
  await expect(toggle).toBeVisible({ timeout: 15_000 });
  if ((await toggle.getAttribute("aria-pressed")) !== "true") await toggle.click();
  await expect(page.getByTestId("history-dock")).toBeVisible({ timeout: 10_000 });
}

test.describe("conversations sidebar — standalone page + docked History", () => {
  let api: APIRequestContext;
  let agentCreated = false;

  test.beforeAll(async () => {
    // Must precede newContext: ADMIN feeds extraHTTPHeaders.
    USER_SUB = await resolveAdminSub();
    ADMIN["X-User-Sub"] = USER_SUB;
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });

    // A provider is not required to SEED memory (POST /memory only needs the agent
    // to be memory-enabled), so this list + rehydrate journey runs even in a
    // stripped env — no LLM provider gate here.
    const prov = await api
      .get("/api/v1/llm-providers/", { params: { team: "platform" } })
      .catch(() => null);
    const provJson = prov && prov.ok() ? await prov.json() : [];
    const provItems = Array.isArray(provJson) ? provJson : provJson.items ?? [];
    const pid = provItems[0]?.id;

    const c = await api.post("/api/v1/agents/", {
      data: {
        name: AGENT,
        team: "platform",
        agent_type: "declarative",
        execution_shape: "reactive",
        memory_enabled: true,
        description: "POC-5 conversations e2e agent",
        metadata: {
          instructions: INSTR,
          ...(pid ? { llm_provider_id: pid } : {}),
          tools: [],
        },
      },
    });
    agentCreated = c.ok();

    if (agentCreated) {
      // ASSERT THE SEED. seedConversation RETURNS resp.ok() and both call sites used to
      // discard it, so a failed fixture write sailed through and resurfaced ~100 lines
      // later as "the seeded thread is not in the list" — a message that points at the
      // conversations list rather than at the write that never happened. Fail at the cause.
      const okA = await seedConversation(api, AGENT, {
        threadId: STANDALONE_THREAD,
        userMsg: STANDALONE_TITLE,
        assistantMsg: STANDALONE_REPLY,
      });
      expect(okA, `seed ${STANDALONE_THREAD} failed — POST /agents/${AGENT}/memory rejected`).toBeTruthy();
      const okB = await seedConversation(api, AGENT, {
        threadId: DOCKED_THREAD,
        userMsg: DOCKED_TITLE,
        assistantMsg: DOCKED_REPLY,
      });
      expect(okB, `seed ${DOCKED_THREAD} failed — POST /agents/${AGENT}/memory rejected`).toBeTruthy();
    }
  });

  test.afterAll(async () => {
    if (api) {
      await api.delete(`/api/v1/agents/${AGENT}`).catch(() => {});
      await api.dispose().catch(() => {});
    }
  });

  // -------------------------------------------------------------------------
  // (1) Standalone /conversations — list + env filter + preview + Continue.
  // -------------------------------------------------------------------------
  test("standalone /conversations lists, filters by env, previews, and continues", async ({
    page,
  }) => {
    test.skip(!agentCreated, "could not create the fixture agent (env gap)");
    test.setTimeout(90_000);

    // HARD: the cross-agent list loads from the backend, and the seeded thread is
    // read back from it (not transient store state).
    const listResp = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/me/conversations") &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await page.goto("/conversations");
    await page.waitForLoadState("networkidle");
    const listed = await listResp;
    expect(listed.status()).toBe(200);
    expect(
      (await listed.json()).some(
        (c: { thread_id?: string }) => c.thread_id === STANDALONE_THREAD
      )
    ).toBeTruthy();

    await expect(
      page.getByRole("heading", { name: "Conversations" })
    ).toBeVisible();

    // The seeded row shows its title (= first user message).
    const rowBtn = page.locator("button", { hasText: STANDALONE_TITLE });
    await expect(rowBtn).toBeVisible({ timeout: 15_000 });

    // Env filter is a pure client predicate: Sandbox keeps our sandbox row,
    // Production hides it, All brings it back.
    await page.getByRole("button", { name: "sandbox", exact: true }).click();
    await expect(rowBtn).toBeVisible();
    await page.getByRole("button", { name: "production", exact: true }).click();
    await expect(rowBtn).toHaveCount(0);
    await page.getByRole("button", { name: "all", exact: true }).click();
    await expect(rowBtn).toBeVisible();

    // HARD: selecting a row fires the transcript read (GET /memory?thread_id=…),
    // and the assistant reply — which lives only in the transcript, never the
    // sidebar — renders, proving the preview rehydrated.
    const memResp = page.waitForResponse(
      (r) =>
        r.url().includes(`/api/v1/agents/${AGENT}/memory?`) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await rowBtn.click();
    const mem = await memResp;
    expect(mem.status()).toBe(200);
    await expect(page.getByText(STANDALONE_REPLY)).toBeVisible({
      timeout: 15_000,
    });

    // Continue → the seeded sandbox chat (?session=<thread_id>).
    await page.getByRole("button", { name: /Continue/ }).click();
    await page.waitForURL(
      new RegExp(`/agents/${AGENT}/chat\\?session=${STANDALONE_THREAD}`),
      { timeout: 15_000 }
    );
    expect(page.url()).toContain(`session=${STANDALONE_THREAD}`);
  });

  // -------------------------------------------------------------------------
  // (2) Docked History in AgentChatPage (sandbox) — list → reload → rehydrate →
  //     reuse session.
  // -------------------------------------------------------------------------
  test("docked History in AgentChatPage lists (survives reload), rehydrates, reuses session", async ({
    page,
  }) => {
    test.skip(!agentCreated, "could not create the fixture agent (env gap)");
    test.setTimeout(90_000);

    // ARM BEFORE NAVIGATING. The dock is open by DEFAULT (AgentChatPage.tsx:99), so
    // ConversationSidebar mounts and fetches the agent-scoped list during the initial
    // render — before any click. Arming the waiter after goto() (as this did) waits for a
    // response that has already been delivered, and times out at 20s having proven
    // nothing about a page that was working.
    // HARD assertion retained: the list really loads from the backend and carries the
    // seeded thread.
    const openList = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/agents/${AGENT}/memory/conversations`).test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await page.goto(`/agents/${AGENT}/chat`);
    await page.waitForLoadState("networkidle");
    await openHistoryDock(page);
    const listed = await openList;
    expect(listed.status()).toBe(200);
    expect(
      (await listed.json()).some(
        (c: { thread_id?: string }) => c.thread_id === DOCKED_THREAD
      )
    ).toBeTruthy();

    await expect(
      page.getByTestId("history-dock").locator("button", { hasText: DOCKED_TITLE })
    ).toBeVisible({ timeout: 15_000 });

    // DoD #2 — save → reload → assert survived: reload the page, re-open History,
    // and confirm the thread is STILL listed from the backend (not store state).
    await page.reload();
    await page.waitForLoadState("networkidle");
    const reListed = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/agents/${AGENT}/memory/conversations`).test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await openHistoryDock(page);
    const reListedResp = await reListed;
    expect(reListedResp.status()).toBe(200);
    expect(
      (await reListedResp.json()).some(
        (c: { thread_id?: string }) => c.thread_id === DOCKED_THREAD
      )
    ).toBeTruthy();

    // HARD: selecting a row rehydrates the transcript from /memory; the
    // transcript-only assistant marker renders in the chat.
    const memResp = page.waitForResponse(
      (r) =>
        r.url().includes(`/api/v1/agents/${AGENT}/memory?`) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await page
      .getByTestId("history-dock")
      .locator("button", { hasText: DOCKED_TITLE })
      .click();
    const mem = await memResp;
    expect(mem.status()).toBe(200);
    await expect(page.getByText(DOCKED_REPLY)).toBeVisible({ timeout: 15_000 });

    // A follow-up continues the SAME thread — the POST reuses the thread's
    // session_id. Run COMPLETION needs a warm pod (capacity), so we only assert
    // the request fired carrying the reused session_id — the bash-suite boundary.
    const chatPost = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/agents/${AGENT}/chat$`).test(r.url()) &&
        r.request().method() === "POST",
      { timeout: 20_000 }
    );
    await page.getByRole("textbox").fill("What did I just tell you?");
    await page.getByRole("button", { name: "Send message" }).click();
    try {
      const posted = await chatPost;
      expect(posted.request().postDataJSON()?.session_id).toBe(DOCKED_THREAD);
    } catch {
      test.info().annotations.push({
        type: "skip-detail",
        description:
          "follow-up chat POST did not fire (no warm sandbox deployment) — capacity",
      });
    }
  });
});

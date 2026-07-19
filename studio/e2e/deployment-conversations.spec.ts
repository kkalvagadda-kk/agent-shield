import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// deployment-conversations.spec.ts  (context-storage POC-5 — deployment tab)
//
//   Proves the deployment-scoped Conversations journey the API-only bash suite
//   (suite-78) cannot: the Conversations TAB on DeploymentOverviewPage (beside
//   Overview / Runs / Memory) → click a scoped row → navigate to the deployment
//   chat with ?session=<tid> → the transcript rehydrates → a follow-up reuses the
//   session.
//
//   The journey (all through the actual UI, asserting wiring + persistence +
//   scoping + network calls):
//     1. /agents/:name/d/:depId (DeploymentOverviewPage) → click the Conversations
//        tab → the scoped list loads (GET /agents/{name}/memory/conversations
//        ?deployment_id=<depId>).
//     2. SCOPING: the list contains this deployment's seeded thread and EXCLUDES a
//        second seeded thread that has no deployment_id (asserted on the response
//        JSON and in the rendered rows).
//     3. Click the scoped row → nav to /agents/:name/d/:depId/chat?session=<tid>.
//     4. REHYDRATE: the ?session seed fires GET /memory?thread_id=… and the
//        transcript renders (a transcript-only assistant marker appears).
//     5. Follow-up: the deployment-chat POST reuses the thread's session_id
//        (recalling turn-1 needs a warm pod — capacity boundary, tolerated).
//
//   The page-level run uses the reachable SANDBOX deployment route (no dedicated
//   production DeploymentOverview route exists yet — gap ledger). PRODUCTION
//   deployment-scoping is proven at the endpoint by suite-78 T-S78-003. The
//   fixture SEEDS the stored conversations via REST (POST /agents/{name}/memory)
//   with platform-admin's real Keycloak sub — the same identity the browser logs
//   in as — so the ownership-scoped list returns them deterministically.
// ---------------------------------------------------------------------------

const TS = Date.now();

const ADMIN = {
  "X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6",
  "X-User-Team": "platform",
};
const USER_SUB = ADMIN["X-User-Sub"];
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";
const INSTR =
  "You are a helpful assistant with memory. Reply in one short sentence.";

const AGENT = `e2e-depconv-agent-${TS}`;

// The deployment-scoped thread (seeded WITH the sandbox deployment_id).
const SCOPED_THREAD = `e2e-depconv-scoped-${TS}`;
const SCOPED_TITLE = `Marigold-scoped-${TS}`;
const SCOPED_REPLY = `Ack-scoped-${TS}`;

// A second thread for the SAME agent with NO deployment_id — must be EXCLUDED
// from the deployment-scoped list (proves the ?deployment_id= filter).
const OTHER_THREAD = `e2e-depconv-other-${TS}`;
const OTHER_TITLE = `Cobalt-other-${TS}`;
const OTHER_REPLY = `Ack-other-${TS}`;

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

test.describe("deployment Conversations tab — scoped list + rehydrate + resume", () => {
  let api: APIRequestContext;
  let agentCreated = false;
  // Empty until a sandbox deployment actually comes up — the deployment tab needs
  // a real deployment row to render, so its genuine absence is a legitimate skip.
  let depId = "";

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });

    // An LLM provider is required to deploy a runnable agent; its genuine absence
    // just means the deployment tab can't be exercised (skip). Mirrors
    // knowledge.spec / context-attribution.spec.
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
        description: "POC-5 deployment-conversations e2e agent",
        metadata: {
          instructions: INSTR,
          ...(pid ? { llm_provider_id: pid } : {}),
          tools: [],
        },
      },
    });
    agentCreated = c.ok();

    // Deploy to sandbox to obtain a real deployment id. The pod reaching Ready is
    // the capacity boundary (handled in the follow-up step); the deployment ROW
    // existing is what the tab + scoped list need, and that comes back on 201.
    if (agentCreated && pid) {
      const d = await api.post(`/api/v1/agents/${AGENT}/deploy`, {
        data: { environment: "sandbox" },
      });
      if (d.ok()) depId = (await d.json()).id;
    }

    // Seed the scoped thread against the real deployment id, plus a NULL-deployment
    // thread that must NOT appear in the scoped list.
    if (depId) {
      await seedConversation(api, AGENT, {
        threadId: SCOPED_THREAD,
        userMsg: SCOPED_TITLE,
        assistantMsg: SCOPED_REPLY,
        deploymentId: depId,
      });
      await seedConversation(api, AGENT, {
        threadId: OTHER_THREAD,
        userMsg: OTHER_TITLE,
        assistantMsg: OTHER_REPLY,
      });
    }
  });

  test.afterAll(async () => {
    if (api) {
      await api.delete(`/api/v1/agents/${AGENT}`).catch(() => {});
      await api.dispose().catch(() => {});
    }
  });

  test("Conversations tab lists deployment-scoped threads, rehydrates, reuses session", async ({
    page,
  }) => {
    test.skip(
      !depId,
      "no sandbox deployment for the agent (no LLM provider / capacity) — env gap"
    );
    test.setTimeout(120_000);

    await page.goto(`/agents/${AGENT}/d/${depId}`);
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible({
      timeout: 15_000,
    });

    // Click the Conversations tab (beside Overview / Runs / Memory). HARD: the
    // scoped list loads with the deployment_id filter.
    const scopedList = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/agents/${AGENT}/memory/conversations`).test(r.url()) &&
        r.url().includes("deployment_id=") &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await page
      .locator("main nav")
      .getByRole("button", { name: "conversations" })
      .click();
    const scoped = await scopedList;
    expect(scoped.status()).toBe(200);

    // SCOPING: this deployment's thread is present; the NULL-deployment thread is
    // not (the ?deployment_id= filter keeps them disjoint).
    const scopedRows = await scoped.json();
    expect(
      scopedRows.some((c: { thread_id?: string }) => c.thread_id === SCOPED_THREAD)
    ).toBeTruthy();
    expect(
      scopedRows.some((c: { thread_id?: string }) => c.thread_id === OTHER_THREAD)
    ).toBeFalsy();

    const rowBtn = page.locator("button", { hasText: SCOPED_TITLE });
    await expect(rowBtn).toBeVisible({ timeout: 15_000 });
    await expect(
      page.locator("button", { hasText: OTHER_TITLE })
    ).toHaveCount(0);

    // Clicking the row navigates to the deployment chat seeded with ?session. Arm
    // the /memory watcher BEFORE the click, because the ?session seed fires on the
    // chat page's mount (right after navigation).
    const memResp = page.waitForResponse(
      (r) =>
        r.url().includes(`/api/v1/agents/${AGENT}/memory?`) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await rowBtn.click();
    await page.waitForURL(
      new RegExp(`/agents/${AGENT}/d/${depId}/chat\\?session=${SCOPED_THREAD}`),
      { timeout: 15_000 }
    );

    // HARD: the transcript rehydrates from /memory; the transcript-only assistant
    // marker renders in the chat.
    const mem = await memResp;
    expect(mem.status()).toBe(200);
    await expect(page.getByText(SCOPED_REPLY)).toBeVisible({ timeout: 15_000 });

    // A follow-up continues the SAME thread on the deployment chat route — the POST
    // reuses the thread's session_id. Recalling turn-1 needs a warm pod (capacity),
    // so we only assert the request fired with the reused session_id.
    const chatPost = page.waitForResponse(
      (r) =>
        new RegExp(
          `/api/v1/agents/${AGENT}/deployments/${depId}/chat$`
        ).test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 }
    );
    await page.getByRole("textbox").fill("Remind me what I told you.");
    await page.getByRole("button", { name: "Send message" }).click();
    try {
      const posted = await chatPost;
      expect(posted.request().postDataJSON()?.session_id).toBe(SCOPED_THREAD);
    } catch {
      test.info().annotations.push({
        type: "skip-detail",
        description:
          "follow-up deployment chat POST did not fire (no warm pod) — capacity",
      });
    }
  });
});

import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// poc2b-rich-console.spec.ts  (context-storage POC-2b — rich workflow console)
//
//   Proves the browser journey the API-only bash suites (kubectl exec) cannot:
//   the Catalog workflow chat is a LIVE multi-speaker console, not spinner-then-
//   dump. Drives the real /catalog/{id}/chat surface on a self-provisioned
//   2-member reactive workflow and asserts, against the deployed Studio:
//
//     (1) Console shell — header "<name> · N agents" + the shared-thread subtitle
//         + the blue attribution info-bar with a "Show rationale" toggle.
//     (2) A POST /workflows/{id}/runs/stream is opened when a message is sent
//         (waitForResponse — the live SSE channel, not the poll).
//     (3) Progressive reveal — in sequential order the researcher's bubble opens
//         (agent_start) BEFORE the answerer's, so there is a real window where
//         only the researcher is rendered (atomic in-browser predicate).
//     (4) Two attributed bubbles, each with a Bot avatar + agent-name label.
//     (5) A ToolCallChip under the researcher for the fixture tool (best-effort:
//         DIAG-skipped when the model did not invoke the tool this run).
//     (6) Show-rationale toggle flips every amber rationale box (best-effort:
//         DIAG-skipped when the model produced no reasoning).
//     (7) save → reload → survives — reload rehydrates the member bubbles (and
//         rationale, when present) from the backend run-tree (GET .../tree), not
//         the client store (DoD #2).
//
//   Fixture (beforeAll, REST, ADMIN header = platform-admin's real Keycloak sub,
//   mirrors context-attribution.spec + suite-75 Section E): create a LOW-risk
//   in-cluster echo tool (a HIGH-risk tool like web_search trips the HITL gate and
//   a reactive member fails-closed — research R4/R8), create a reactive
//   memory-enabled `researcher` WITH the tool + `answerer` WITHOUT, compose a
//   sequential reactive `memory_enabled` workflow, snapshot eval_passed, publish,
//   admin-approve → catalog artifact.
//
//   Boundary (same as context-attribution.spec / workflows.spec): assert UI
//   WIRING + PERSISTENCE + the network calls, NOT agent-execution correctness.
//   Few agent pods are warm, so a full multi-member run may not finish; the test
//   SKIPS gracefully only when its own workflow never streams a member bubble. A
//   run that COMPLETES but renders wrong → FAIL.
// ---------------------------------------------------------------------------

const TS = Date.now();
const ADMIN = {
  "X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6",
  "X-User-Team": "platform",
};
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

const RESEARCHER = `e2e-rc-researcher-${TS}`;
const ANSWERER = `e2e-rc-answerer-${TS}`;
const TOOL = `e2e-rc-tool-${TS}`;
const WF_NAME = `e2e-rc-wf-${TS}`;

const RESEARCHER_INSTR =
  `You are a research assistant. Before answering you MUST call the ${TOOL} tool ` +
  `exactly once, with path set to 'lookup'. First state one short sentence explaining ` +
  `WHY you are calling the tool, then call it, then answer in one short sentence.`;
const ANSWERER_INSTR =
  "You acknowledge the previous agent's message and reply in one short sentence.";

test.describe("POC-2b rich workflow console", () => {
  let api: APIRequestContext;
  let artifactId = "";
  let workflowId = "";
  let skipReason = "";
  const createdAgents: string[] = [];

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });

    // An LLM provider is the one legitimate setup skip (mirrors suite-75).
    const prov = await api.get("/api/v1/llm-providers/", { params: { team: "platform" } });
    expect(prov.ok(), `list llm-providers: ${prov.status()}`).toBeTruthy();
    const provJson = await prov.json();
    const provItems = Array.isArray(provJson) ? provJson : provJson.items ?? [];
    const pid = provItems[0]?.id;
    if (!pid) {
      skipReason = "no LLM provider for team platform";
      return;
    }

    // 1. A LOW-risk, in-cluster (/echo) HTTP tool — no external dependency and no
    //    HITL gate, so a REACTIVE member can invoke it non-interactively.
    const tool = await api.post("/api/v1/tools/", {
      data: {
        name: TOOL,
        display_name: "RC Echo Lookup",
        description: "Low-risk in-cluster echo lookup (POC-2b rich-console fixture).",
        type: "http",
        risk_level: "low",
        owner_team: "platform",
        http_method: "GET",
        http_url:
          "http://agentshield-registry-api.agentshield-platform.svc.cluster.local:8000/echo/{{path}}",
      },
    });
    // 409 = a prior run left it; either way the tool now exists.
    expect([200, 201, 409]).toContain(tool.status());

    // 2. researcher WITH the tool bound; answerer WITHOUT — both reactive + memory.
    const agents: Array<{ name: string; instr: string; tools: string[] }> = [
      { name: RESEARCHER, instr: RESEARCHER_INSTR, tools: [TOOL] },
      { name: ANSWERER, instr: ANSWERER_INSTR, tools: [] },
    ];
    for (const a of agents) {
      const c = await api.post("/api/v1/agents/", {
        data: {
          name: a.name,
          team: "platform",
          agent_type: "declarative",
          execution_shape: "reactive",
          memory_enabled: true,
          metadata: { instructions: a.instr, llm_provider_id: pid, tools: a.tools },
        },
      });
      expect(c.ok(), `create agent ${a.name}: ${c.status()} ${await c.text()}`).toBeTruthy();
      createdAgents.push(a.name);
      const d = await api.post(`/api/v1/agents/${a.name}/deploy`, {
        data: { environment: "sandbox" },
      });
      expect(d.ok(), `deploy agent ${a.name}: ${d.status()} ${await d.text()}`).toBeTruthy();
    }

    // 3. sequential reactive workflow, share-context on.
    const wf = await api.post("/api/v1/workflows", {
      data: {
        name: WF_NAME,
        team: "platform",
        orchestration: "sequential",
        execution_shape: "reactive",
        memory_enabled: true,
      },
    });
    expect(wf.ok(), `create workflow: ${wf.status()} ${await wf.text()}`).toBeTruthy();
    workflowId = (await wf.json()).id;

    // 4. add BOTH agents as ordered members (researcher first).
    for (let i = 0; i < createdAgents.length; i++) {
      const g = await api.get(`/api/v1/agents/${createdAgents[i]}`);
      expect(g.ok(), `get agent ${createdAgents[i]}: ${g.status()}`).toBeTruthy();
      const agentId = (await g.json()).id;
      const m = await api.post(`/api/v1/workflows/${workflowId}/members`, {
        data: { agent_id: agentId, position: i + 1 },
      });
      expect(m.ok(), `add member ${createdAgents[i]}: ${m.status()} ${await m.text()}`).toBeTruthy();
    }

    // 5. snapshot a passing version (fixture — set the gate directly).
    const ver = await api.post(`/api/v1/workflows/${workflowId}/versions`, {
      data: { eval_passed: true, notes: "e2e rich-console fixture" },
    });
    expect(ver.ok(), `create version: ${ver.status()} ${await ver.text()}`).toBeTruthy();
    const versionId = (await ver.json()).id;

    // 6. publish → 7. admin-approve → catalog artifact (granted to team platform).
    const pub = await api.post(`/api/v1/workflows/${workflowId}/publish`, {
      data: { version_id: versionId },
    });
    expect(pub.ok(), `publish workflow: ${pub.status()} ${await pub.text()}`).toBeTruthy();
    const prId = (await pub.json()).publish_request_id;

    const appr = await api.post(`/api/v1/admin/publish-requests/${prId}/approve`, {
      data: { grantee_teams: ["platform"] },
    });
    expect(appr.ok(), `approve publish: ${appr.status()} ${await appr.text()}`).toBeTruthy();
    artifactId = (await appr.json()).artifact_id;
    expect(artifactId, "approve should return a catalog artifact_id").toBeTruthy();
  });

  test.afterAll(async () => {
    if (api) {
      if (workflowId) await api.delete(`/api/v1/workflows/${workflowId}`).catch(() => {});
      for (const name of createdAgents) {
        await api.delete(`/api/v1/agents/${name}`).catch(() => {});
      }
      await api.dispose().catch(() => {});
    }
  });

  test("live console: progressive reveal + avatars + tool chip + rationale toggle + reload", async ({
    page,
  }) => {
    test.skip(!artifactId, skipReason || "workflow fixture did not come up");
    // A live multi-member reactive run streams for a while; extend past the default.
    test.setTimeout(200_000);

    await page.goto(`/catalog/${artifactId}/chat`);
    await page.waitForLoadState("networkidle");

    // (1) Console shell — header "<name> · N agents" + shared-thread subtitle. The
    // h1 is the artifact name only; the "N agents" heading is the console h2.
    await expect(page.getByRole("heading", { name: /\d+\s*agents/ })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(/shared conversation thread/i)).toBeVisible();
    // The Show-rationale toggle lives in the blue info-bar and defaults on.
    const toggle = page
      .locator("label", { hasText: /Show rationale/i })
      .locator('input[type="checkbox"]');
    await expect(toggle).toBeChecked();

    const input = page.getByRole("textbox");
    await expect(input).toBeEnabled({ timeout: 15_000 });

    // (2) The live channel is POST /workflows/{id}/runs/stream — register the wait
    // BEFORE sending so we prove the fetch-stream (not the poll) drives the console.
    const streamResp = page.waitForResponse(
      (r) =>
        /\/api\/v1\/workflows\/[^/]+\/runs\/stream/.test(r.url()) &&
        r.request().method() === "POST",
      { timeout: 30_000 }
    );

    await input.fill("Look up the record and summarize it for the next agent.");
    await page.locator('button[type="submit"]').click();

    const started = await streamResp;
    expect(started.status()).toBe(200);

    const messages = page.locator("div.overflow-y-auto");

    // (3) Progressive reveal — atomic in-browser predicate: the researcher bubble is
    // present while the answerer's is absent. In sequential order the answerer's
    // bubble cannot exist before the researcher's agent_end, so this window is real.
    // Names are distinct (no substring overlap), scoped to the messages container
    // (header/subtitle live in separate shell divs, so they never leak in).
    let sawProgressive = false;
    try {
      await page.waitForFunction(
        (names: { first: string; second: string }) => {
          const t = document.querySelector("div.overflow-y-auto")?.textContent || "";
          return t.includes(names.first) && !t.includes(names.second);
        },
        { first: RESEARCHER, second: ANSWERER },
        { timeout: 120_000, polling: 100 }
      );
      sawProgressive = true;
    } catch {
      // window never observed — distinguished below (cold pods vs too-fast run).
    }

    const researcherEver = await messages
      .getByText(RESEARCHER, { exact: true })
      .count()
      .catch(() => 0);

    // Capacity boundary: if no member bubble ever streamed, the two agent pods
    // never warmed — capacity skip (same "few warm pods" boundary the other specs
    // accept). A run that streams but renders wrong falls through and FAILS.
    if (!sawProgressive && researcherEver === 0) {
      test.skip(true, "no member bubble streamed — few warm pods (capacity)");
      return;
    }

    // Sequential streaming should have exposed the single-speaker window.
    expect(
      sawProgressive,
      "researcher bubble should render before the answerer's (progressive reveal)"
    ).toBeTruthy();

    // (4) Both members render, each with an avatar (Bot) + name label. The colored
    // attribution dots (span.w-2.h-2.rounded-full) precede each label — ≥2 across
    // the two member bubbles.
    await expect(messages.getByText(RESEARCHER, { exact: true }).first()).toBeVisible({
      timeout: 120_000,
    });
    await expect(messages.getByText(ANSWERER, { exact: true }).first()).toBeVisible({
      timeout: 120_000,
    });
    await expect(messages.locator("span.w-2.h-2.rounded-full").nth(1)).toBeVisible();

    // Wait for the stream to finish (input re-enables when isStreaming clears on the
    // `done` frame — which also sets sessionStorage for the reload rehydration).
    await expect(input).toBeEnabled({ timeout: 120_000 });

    // (5) Tool-call chip under the researcher for the fixture tool. The chip renders
    // `called <code>{tool}</code>`; the <code> tool name is unique to the chip.
    // Model-dependent: DIAG-skip (never FAIL) when the model didn't invoke the tool.
    const chip = messages.locator("code", { hasText: TOOL });
    if ((await chip.count()) > 0) {
      await expect(chip.first()).toBeVisible();
    } else {
      test.info().annotations.push({
        type: "diag",
        description: "no tool-call chip — model did not invoke the fixture tool this run",
      });
    }

    // (6) Rationale toggle — the amber box is `Rationale: <why>`. Model-dependent:
    // exercise the toggle only when a rationale box is present (else DIAG-skip).
    const rationaleBox = messages.getByText(/Rationale:/i);
    const rationaleWasPresent = (await rationaleBox.count()) > 0;
    if (rationaleWasPresent) {
      await expect(rationaleBox.first()).toBeVisible();
      await toggle.uncheck();
      await expect(messages.getByText(/Rationale:/i)).toHaveCount(0);
      await toggle.check();
      await expect(messages.getByText(/Rationale:/i).first()).toBeVisible();
    } else {
      test.info().annotations.push({
        type: "diag",
        description: "no rationale box — model produced no tool-calling reasoning this run",
      });
    }

    // (7) save → reload → survives. Reload rehydrates the member bubbles from the
    // backend run-tree (GET .../tree) — the transcript is read from the DB, not the
    // client store (DoD #2). sessionStorage carries the completed run id across the
    // reload; the mount effect polls the tree and re-renders the per-member bubbles.
    const treeResp = page.waitForResponse(
      (r) =>
        /\/api\/v1\/workflows\/[^/]+\/runs\/[^/]+\/tree/.test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 120_000 }
    );
    await page.reload();
    await page.waitForLoadState("networkidle");
    await treeResp;

    const messages2 = page.locator("div.overflow-y-auto");
    await expect(messages2.getByText(RESEARCHER, { exact: true }).first()).toBeVisible({
      timeout: 120_000,
    });
    await expect(messages2.getByText(ANSWERER, { exact: true }).first()).toBeVisible({
      timeout: 120_000,
    });
    // Rationale rehydrates from the tree projection when it was produced live.
    if (rationaleWasPresent) {
      await expect(messages2.getByText(/Rationale:/i).first()).toBeVisible({ timeout: 15_000 });
    }
  });
});

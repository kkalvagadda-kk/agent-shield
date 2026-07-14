import { test, expect, request as pwRequest } from "@playwright/test";

// ---------------------------------------------------------------------------
// webhook-public-url.spec.ts
//
// Gap 1 guard: the public webhook URL Studio shows must be a REAL, externally
// reachable endpoint. Two coupled bugs made it useless:
//   (a) EVENT_GATEWAY_PUBLIC_URL defaulted to the in-cluster Service name
//       (http://…-event-gateway:8091), so the copied URL was unroutable, and
//   (b) the Envoy gateway exposed /webhooks/ while the event-gateway serves
//       /hooks/ with no rewrite — so /hooks/… fell through to the Studio SPA
//       catch-all and returned 200 text/html instead of reaching the gateway.
//
// This test proves BOTH fixes the way a caller experiences them, through the
// real https gateway:
//   1. a created webhook trigger's URL is built from the public gateway host,
//   2. POSTing /hooks/ with a bad token is rejected by the event-gateway
//      (uniform 401 JSON) — i.e. the request is ROUTED, not served the SPA.
// Before the fix, (1) contained the internal service host and (2) returned the
// SPA's 200 HTML — so this spec fails closed on a regression.
// ---------------------------------------------------------------------------

const ADMIN = {
  "X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6",
  "X-User-Team": "platform",
};
const BASE_URL =
  process.env.PLAYWRIGHT_BASE_URL || "https://agentshield.127.0.0.1.nip.io:8443";
const GATEWAY_HOST = new URL(BASE_URL).host;
const AGENT = `whurl-${Date.now().toString().slice(-7)}`;

test.describe("public webhook URL routes through the gateway", () => {
  test("trigger URL uses the gateway host AND /hooks/ reaches the event-gateway", async () => {
    const api = await pwRequest.newContext({
      baseURL: BASE_URL,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });

    // An agent to hang a webhook trigger on (no deploy needed for the routing proof).
    const created = await api.post("/api/v1/agents/", {
      data: {
        name: AGENT,
        team: "platform",
        agent_type: "declarative",
        execution_shape: "reactive",
        agent_class: "daemon",
        metadata: { instructions: "webhook url routing probe", tools: [] },
      },
    });
    expect(created.ok(), `create agent: ${created.status()}`).toBeTruthy();

    const trig = await api.post(`/api/v1/agents/${AGENT}/triggers`, {
      data: { trigger_type: "webhook" },
    });
    expect(trig.ok(), `create trigger: ${trig.status()}`).toBeTruthy();
    const webhookUrl = (await trig.json()).webhook_url as string;

    // (1) public-URL value fix — built from the gateway host, not the internal Service.
    expect(webhookUrl, "webhook_url should be present").toBeTruthy();
    expect(
      webhookUrl,
      `webhook_url (${webhookUrl}) should use the public gateway host ${GATEWAY_HOST}`,
    ).toContain(GATEWAY_HOST);
    expect(webhookUrl).toContain("/hooks/");

    // (2) routing fix — /hooks/ reaches the event-gateway. Use a fresh, unauthenticated
    // context (webhooks carry their token in the path, no login). A bad token must get
    // the event-gateway's uniform 401 JSON — NOT the SPA's 200 HTML.
    const pub = await pwRequest.newContext({ ignoreHTTPSErrors: true });
    const bad = await pub.post(
      `${BASE_URL}/hooks/workflow/does-not-exist-${Date.now()}/badtoken`,
      { data: { probe: true } },
    );
    expect(
      bad.status(),
      "a bad token must be rejected by the event-gateway (routed), not served the SPA (200 html)",
    ).toBe(401);
    expect(bad.headers()["content-type"] || "").toContain("json");

    await api.delete(`/api/v1/agents/${AGENT}`).catch(() => {});
    await api.dispose();
    await pub.dispose();
  });
});

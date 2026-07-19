import { createHmac } from "node:crypto";
import { test, expect, type Page, type APIRequestContext, request as pwRequest } from "@playwright/test";

// ---------------------------------------------------------------------------
// webhook-clients.spec.ts  (WS-4 Phase 6)
//
// Proves the signing-client panel the way an operator uses it, through the real
// https gateway: register an application against a webhook trigger, see its
// secret exactly once, and confirm the registration survived a real reload while
// the secret did not.
//
// NO page.route stubs. Every assertion below rides a real request to the real
// registry-api — a stubbed route would prove the panel renders JSON, not that
// the round-trip persists.
//
// The fixture is CREATED here, never scavenged. Picking "the first webhook row
// on some existing agent" makes the verdict track leftover state: a trigger left
// at auth_mode=token has no client panel to assert against, and the spec would
// report on the fixture rather than on the code.
// ---------------------------------------------------------------------------

const ADMIN = {
  "X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6",
  "X-User-Team": "platform",
};
const BASE_URL =
  process.env.PLAYWRIGHT_BASE_URL || "https://agentshield.127.0.0.1.nip.io:8443";
const AGENT = `whclient-${Date.now().toString().slice(-7)}`;
const CLIENT_ID = "billing-service";

let api: APIRequestContext;
let triggerId: string;
// Captured at CREATE time on purpose: `webhook_url` embeds the one-time token, so the
// registry only ever returns it on the create response (`triggers.py:111` —
// `_webhook_url(name, plaintext) if plaintext else None`). A later GET returns null.
let webhookUrl: string;

/** The detail page keeps its active tab in local state, so a reload lands back on
 *  "deployments" — every post-reload assertion has to re-open Settings. */
async function openSettings(page: Page) {
  const clientsLoaded = page.waitForResponse(
    (r) => /\/api\/v1\/triggers\/[^/]+\/clients$/.test(r.url()) && r.request().method() === "GET",
    { timeout: 20_000 },
  );
  await page.locator("main nav").getByRole("button", { name: "settings" }).click();
  await clientsLoaded;
}

test.beforeAll(async () => {
  api = await pwRequest.newContext({
    baseURL: BASE_URL,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: ADMIN,
  });

  const created = await api.post("/api/v1/agents/", {
    data: {
      name: AGENT,
      team: "platform",
      agent_type: "declarative",
      execution_shape: "reactive",
      agent_class: "daemon",
      metadata: { instructions: "webhook signing-client panel fixture", tools: [] },
    },
  });
  expect(created.ok(), `create fixture agent: ${created.status()} ${await created.text()}`).toBeTruthy();

  const trig = await api.post(`/api/v1/agents/${AGENT}/triggers`, {
    data: { trigger_type: "webhook" },
  });
  expect(trig.ok(), `create fixture trigger: ${trig.status()} ${await trig.text()}`).toBeTruthy();
  const trigger = await trig.json();
  triggerId = trigger.id;
  webhookUrl = trigger.webhook_url;
  expect(webhookUrl, "the create response must carry the one-time webhook_url").toBeTruthy();

  // A webhook trigger is BORN `token` and upgrades to `client_signed` one-way on its
  // first client registration — the invariant is `client_signed` <=> at least one
  // client exists, so a trigger that authenticates nobody is unrepresentable.
  expect(
    trigger.auth_mode,
    "a new webhook trigger must be born token-mode (empty allowlist => client_signed would authenticate nobody)",
  ).toBe("token");

  // Registering the first client is what flips it. Assert the upgrade really happened
  // rather than assuming birth-time state: this is the precondition the panel's
  // assertions rest on, so fail loudly here rather than let a missing panel read as a
  // UI bug (or, worse, let a skipped assertion read as a pass).
  const seed = await api.post(`/api/v1/triggers/${triggerId}/clients`, {
    data: { client_id: `seed-${AGENT}` },
  });
  expect(seed.ok(), `seed client: ${seed.status()} ${await seed.text()}`).toBeTruthy();

  const after = await api.get(`/api/v1/agents/${AGENT}/triggers`);
  const upgraded = ((await after.json()) as Array<{ id: string; auth_mode?: string }>).find(
    (t) => t.id === triggerId,
  );
  expect(
    upgraded?.auth_mode,
    "registering the first client must upgrade the trigger to client_signed",
  ).toBe("client_signed");

  // Revoke the seed so the panel starts empty for the tests below. Revoking the LAST
  // client deliberately does not revert `auth_mode` — a revoke must lock the door, not
  // silently reopen the coarse bearer-token path — so the trigger stays `client_signed`
  // with an empty allowlist. That leaves exactly the state these tests need, and pins
  // the one-way invariant on the way through.
  const revoked = await api.delete(`/api/v1/triggers/${triggerId}/clients/seed-${AGENT}`);
  expect(revoked.ok(), `revoke seed: ${revoked.status()}`).toBeTruthy();

  const afterRevoke = await api.get(`/api/v1/agents/${AGENT}/triggers`);
  const stillSigned = ((await afterRevoke.json()) as Array<{ id: string; auth_mode?: string }>).find(
    (t) => t.id === triggerId,
  );
  expect(
    stillSigned?.auth_mode,
    "revoking the last client must NOT revert to token — that would reopen the coarse token path",
  ).toBe("client_signed");
});

test.afterAll(async () => {
  await api.delete(`/api/v1/agents/${AGENT}`).catch(() => {});
  await api.dispose();
});

test.describe("webhook signing clients", () => {
  test("register → secret shown once → reload → client survived, secret gone → disable → reload → survived", async ({
    page,
  }) => {
    await page.goto(`/agents/${AGENT}`);
    await page.waitForLoadState("networkidle");
    await openSettings(page);

    // The trigger's mode is visible to the operator.
    await expect(page.getByText("client_signed")).toBeVisible();
    await expect(page.getByText(/no clients registered/i)).toBeVisible();

    // --- register -----------------------------------------------------------
    await page.getByRole("button", { name: /add client/i }).click();
    await page.getByPlaceholder("billing-service").fill(CLIENT_ID);

    const registered = page.waitForResponse(
      (r) =>
        /\/api\/v1\/triggers\/[^/]+\/clients$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 },
    );
    await page.getByRole("button", { name: /^register$/i }).click();
    const regResp = await registered;
    expect(regResp.status(), `register client: ${await regResp.text()}`).toBe(201);

    // --- secret revealed exactly once ---------------------------------------
    const secretEl = page.getByTestId("client-secret");
    await expect(secretEl).toBeVisible();
    const secret = (await secretEl.textContent())?.trim() ?? "";
    expect(secret, "the 201 must surface a whsec_ signing secret").toMatch(/^whsec_.+/);
    await expect(page.getByText(/won't be shown again/i)).toBeVisible();

    // The list response that backs the panel must never carry it.
    const listed = await api.get(`/api/v1/triggers/${triggerId}/clients`);
    expect(await listed.text(), "the read model must have no secret field at all").not.toContain(
      secret,
    );

    await page.getByRole("button", { name: /^done$/i }).click();
    await expect(secretEl).toBeHidden();

    // --- save → reload → assert survived (DoD #2) ---------------------------
    await page.reload();
    await page.waitForLoadState("networkidle");
    await openSettings(page);

    const row = page.getByTestId(`client-row-${CLIENT_ID}`);
    await expect(row, "the registered client must survive a reload").toBeVisible();
    await expect(row).toContainText(ADMIN["X-User-Sub"]); // created_by audit stamp
    await expect(
      page.getByTestId("client-secret"),
      "the secret must NOT come back after a reload — it is unrecoverable by design",
    ).toHaveCount(0);
    await expect(page.getByText(secret)).toHaveCount(0);

    // --- disable → reload → assert survived ---------------------------------
    const patched = page.waitForResponse(
      (r) =>
        /\/api\/v1\/triggers\/[^/]+\/clients\/[^/]+$/.test(r.url()) &&
        r.request().method() === "PATCH",
      { timeout: 20_000 },
    );
    // `.click()`, not `.uncheck()`. The checkbox is bound to SERVER state
    // (`checked={c.enabled}`), so it does not flip until the PATCH lands and React
    // Query refetches — `.uncheck()` asserts the box changed the instant it clicks and
    // fails the race. That lag is correct here and must not be "fixed" with an
    // optimistic update: this toggle revokes a credential, and the UI must never claim
    // a client is disabled before the server has actually said so.
    await row.getByRole("checkbox", { name: new RegExp(`Enabled ${CLIENT_ID}`, "i") }).click();
    const patchResp = await patched;
    expect(patchResp.status(), `disable client: ${await patchResp.text()}`).toBe(200);

    // Then assert the settled state, once the refetch has caught up.
    await expect(
      row.getByRole("checkbox", { name: new RegExp(`Enabled ${CLIENT_ID}`, "i") }),
      "the checkbox must settle to unchecked once the server confirms",
    ).not.toBeChecked();

    await page.reload();
    await page.waitForLoadState("networkidle");
    await openSettings(page);

    await expect(
      page
        .getByTestId(`client-row-${CLIENT_ID}`)
        .getByRole("checkbox", { name: new RegExp(`Enabled ${CLIENT_ID}`, "i") }),
      "enabled=false must survive a reload",
    ).not.toBeChecked();
  });

  test("a duplicate client-id is rejected with the backend's 409 message", async ({ page }) => {
    await page.goto(`/agents/${AGENT}`);
    await page.waitForLoadState("networkidle");
    await openSettings(page);

    // Depends on the client registered by the test above (workers: 1, serial).
    await page.getByRole("button", { name: /add client/i }).click();
    await page.getByPlaceholder("billing-service").fill(CLIENT_ID);

    const conflict = page.waitForResponse(
      (r) =>
        /\/api\/v1\/triggers\/[^/]+\/clients$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 },
    );
    await page.getByRole("button", { name: /^register$/i }).click();
    expect((await conflict).status(), "a duplicate client_id on one trigger is a 409").toBe(409);

    // The backend's 409 message surfaces in TWO places by design — the inline panel
    // error (persistent) and a toast (transient) — so an unscoped locator matches both
    // and trips strict mode. Scope to the panel: the inline error is the one that has
    // to survive after the toast auto-dismisses.
    await expect(
      page.getByRole("main").getByText(/already registered on this trigger/i),
    ).toBeVisible();
    // The secret panel must NOT appear on a rejected registration — nothing was created,
    // so there is no secret to reveal.
    await expect(page.getByTestId("client-secret")).toHaveCount(0);
  });

  // -------------------------------------------------------------------------
  // Disabled client → gateway 401.
  //
  // GUARDED, NOT SILENTLY GREEN. This leg needs the WS-4 Phase 4 event-gateway
  // (verify_webhook_auth + the client allowlist read). Until that image is
  // deployed the gateway has no client-signing path at all, so a "pass" here
  // would only mean an unsigned request was rejected for the wrong reason — a
  // green that proves nothing. It is annotated + skipped so the report says so
  // out loud; flip the skip once Phase 4/7 is on-cluster.
  // -------------------------------------------------------------------------
  test("a disabled client is rejected by the real event-gateway (401)", async () => {
    // Register a dedicated client through the REAL API — it returns the plaintext
    // secret exactly once, which is what lets this test sign for real.
    const probeId = `probe-${AGENT}`;
    const reg = await api.post(`/api/v1/triggers/${triggerId}/clients`, {
      data: { client_id: probeId },
    });
    expect(reg.ok(), `register probe client: ${reg.status()}`).toBeTruthy();
    const secret: string = (await reg.json()).secret;
    expect(secret, "the create response must carry the secret exactly once").toBeTruthy();

    // The gateway's scheme: HMAC_SHA256(secret, `${ts}.${raw_body}`) -> sha256=<hex>.
    const body = JSON.stringify({ probe: true });
    const signed = () => {
      const ts = Math.floor(Date.now() / 1000).toString();
      const mac = createHmac("sha256", secret).update(`${ts}.${body}`).digest("hex");
      return {
        "Content-Type": "application/json",
        "X-Client-Id": probeId,
        "X-Timestamp": ts,
        "X-Signature": `sha256=${mac}`,
      };
    };

    const pub = await pwRequest.newContext({ ignoreHTTPSErrors: true });

    // POSITIVE CONTROL first. Without it a 401 below proves nothing: an unsigned or
    // wrongly-signed probe also returns 401, so the test would pass for the wrong
    // reason and never touch the allowlist. Establishing that THIS signature CLEARS
    // AUTH is what makes the later 401 attributable to `enabled=false` alone.
    //
    // Asserting "not 401" rather than 202 on purpose: this fixture agent is never
    // deployed, so a request that clears auth reaches dispatch and dies there (502
    // "run dispatch failed"). That is still proof the credential was ACCEPTED — auth
    // runs before dispatch. Demanding 202 would mean deploying a pod to assert
    // something about signing, and suite-76 (T-S76-001) already proves the full
    // signed -> 202 -> real agent_events row against a really deployed agent.
    const okRes = await pub.post(webhookUrl, { headers: signed(), data: body });
    expect(
      okRes.status(),
      `enabled + correctly signed must CLEAR AUTH (any non-401; 502 = auth ok, dispatch failed): ${await okRes.text()}`,
    ).not.toBe(401);

    // Flip ONLY `enabled` — same client, same secret, same signing scheme.
    const off = await api.patch(`/api/v1/triggers/${triggerId}/clients/${probeId}`, {
      data: { enabled: false },
    });
    expect(off.ok(), `disable probe client: ${off.status()}`).toBeTruthy();

    const denied = await pub.post(webhookUrl, { headers: signed(), data: body });
    expect(
      denied.status(),
      "the SAME valid signature must now be rejected — only `enabled` changed, so the allowlist is the discriminator",
    ).toBe(401);

    await pub.dispose();
  });
});

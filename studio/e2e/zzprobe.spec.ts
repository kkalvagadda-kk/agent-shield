import { test, request as pwRequest } from "@playwright/test";
const ADMIN = { "X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6", "X-User-Team": "platform" };
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

test("probe2: fresh fixture full header", async ({ page }) => {
  test.setTimeout(120_000);
  const api = await pwRequest.newContext({ baseURL: API_BASE, ignoreHTTPSErrors: true, extraHTTPHeaders: ADMIN });
  const TS = Date.now();
  const prov = await (await api.get("/api/v1/llm-providers/", { params: { team: "platform" } })).json();
  const pid = (Array.isArray(prov)?prov:prov.items)[0].id;
  const R=`probe-r-${TS}`, A=`probe-a-${TS}`, WF=`probe-wf-${TS}`;
  for (const name of [R,A]) {
    await api.post("/api/v1/agents/", { data: { name, team:"platform", agent_type:"declarative", execution_shape:"reactive", memory_enabled:true, metadata:{ instructions:"x", llm_provider_id:pid, tools:[] } } });
    await api.post(`/api/v1/agents/${name}/deploy`, { data: { environment: "sandbox" } });
  }
  const wf = await (await api.post("/api/v1/workflows", { data: { name:WF, team:"platform", orchestration:"sequential", execution_shape:"reactive", memory_enabled:true } })).json();
  for (let i=0;i<2;i++){ const nm=[R,A][i]; const aid=(await (await api.get(`/api/v1/agents/${nm}`)).json()).id; await api.post(`/api/v1/workflows/${wf.id}/members`, { data:{ agent_id:aid, position:i+1 } }); }
  const ver = await (await api.post(`/api/v1/workflows/${wf.id}/versions`, { data:{ eval_passed:true, notes:"x" } })).json();
  const pub = await (await api.post(`/api/v1/workflows/${wf.id}/publish`, { data:{ version_id:ver.id } })).json();
  const appr = await (await api.post(`/api/v1/admin/publish-requests/${pub.publish_request_id}/approve`, { data:{ grantee_teams:["platform"] } })).json();
  const artId = appr.artifact_id;
  console.log("fresh artifact", artId, "workflowId", wf.id);

  await page.goto(`/catalog/${artId}/chat`);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(4000);

  // The composite-workflow metadata call the shell depends on for member_count:
  const compo = await page.evaluate(async (sid) => {
    const r = await fetch(`/api/v1/workflows/${sid}`, { headers: { Accept: "application/json" } });
    let body:any = null; try { body = await r.json(); } catch {}
    return { status: r.status, member_count: body?.member_count, orchestration: body?.orchestration, keys: body?Object.keys(body):[] };
  }, wf.id);
  console.log("COMPOSITE getCompositeWorkflow(source_id):", JSON.stringify(compo));

  const html = await page.locator("main").first().innerHTML().catch(()=> "N/A");
  console.log("FULL_MAIN_HTML_START>>>", html, "<<<FULL_MAIN_HTML_END");

  await api.delete(`/api/v1/workflows/${wf.id}`).catch(()=>{});
  for (const name of [R,A]) await api.delete(`/api/v1/agents/${name}`).catch(()=>{});
});

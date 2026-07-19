# Quickstart — build, deploy, and verify the workflow ledger fixes

Copy-pasteable local commands for the docker-desktop k8s deploy. Run from the repo root
`/Users/kalyankalvagadda/code/agent-shield`. `node` is on PATH (`/opt/homebrew/bin`), so Vitest +
Playwright run locally. Target images: **registry-api `0.2.207`** (G2 backend), **studio `0.1.155`**
(G1 + G2 wiring; also carries the unrelated EditAgentModal fix, task #16 — one shared build).

---

## 0. Prereqs (once)

```bash
kubectl config current-context           # must be docker-desktop
kubectl get pods -n agentshield-platform # registry-api / studio / postgres Running
cd studio && npx playwright install chromium && cd ..   # first Playwright run only
```

---

## 1. Fast inner loop (no deploy — before any image build)

```bash
# Python syntax + mapper wiring (after the G2 backend change, T2)
python3 -c "import ast; ast.parse(open('services/registry-api/memory.py').read())"
python3 -c "import ast; ast.parse(open('services/registry-api/conversation_store.py').read())"
python3 -c "import ast; ast.parse(open('services/registry-api/routers/composite_workflows.py').read())"
( cd services/registry-api && python3 -c "import conversation_store, memory; from routers import composite_workflows; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('mappers ok')" )

# Frontend reproduce tests — MUST be RED before the fix (CP2)
cd studio
npx vitest run src/pages/WorkflowChatPage.test.tsx                         # G1 reproduce → FAIL pre-fix
npx vitest run src/components/agent-detail/WorkflowMemoryTab.test.tsx      # G2 reproduce → FAIL pre-fix

# After the fixes (T5-T7) — MUST be GREEN (CP3)
npm run typecheck
npm run test
cd ..
```

---

## 2. Build + deploy (local docker-desktop k8s)

```bash
# ── registry-api 0.2.207 (after T2) ──────────────────────────────────────────
docker build -t registry.internal/agentshield/registry-api:0.2.207 services/registry-api/

# ── studio 0.1.155 (after T6/T7/T9) ──────────────────────────────────────────
docker build -t registry.internal/agentshield/studio:0.1.155 studio/

# Roll out via helm (tags are baked into values.yaml — no --set; T2/T9 bumped both files)
helm upgrade --install agentshield charts/agentshield \
  -n agentshield-platform --reset-values --force-conflicts --timeout 20m

# Wait for the rollout
kubectl -n agentshield-platform rollout status deploy/agentshield-registry-api --timeout=300s
kubectl -n agentshield-platform rollout status deploy/agentshield-studio       --timeout=300s
```

> Bump BOTH `scripts/deploy-cpe2e.sh` (`REGISTRY_API_TAG` L295, `STUDIO_TAG` L320) AND
> `charts/agentshield/values.yaml` (registry-api L623, studio L954) — helm reads the baked values.

---

## 3. Backend e2e — suite-78 (G2, CP1 + regression at CP4)

```bash
# T-S78-006 is the new case: GET /workflows/{id}/memory (FAILS 404 pre-fix, PASS after 0.2.207).
# T-S78-001..005 must stay green (shared fixture / teardown).
bash scripts/e2e/suite-78-conversations.sh
# Expect: ==> Suite 78 Results: N passed, 0 failed ... OK: suite-78 all green
```

---

## 4. Playwright — real-Keycloak end-user journeys (CP4, needs studio 0.1.155 deployed)

```bash
# Port-forwards Studio + runs the spec (passes "$@" straight to `npx playwright test`).
bash scripts/studio-e2e.sh workflow-memory.spec.ts          # G1 replay + G2 tab (this plan)
bash scripts/studio-e2e.sh workflow-conversations.spec.ts   # regression (shared route/sidebar)
```

The spec asserts, in a real browser: the Memory tab fires `GET /api/v1/workflows/{id}/memory`
(`waitForResponse`, 200) and renders an entry; opening a past session fires
`GET /api/v1/workflows/{id}/memory?thread_id=…` and replays a prior member bubble in
`[data-testid="workflow-chat-transcript"]`. No-runs users annotate-skip the render assertions; the
network guards always run.

---

## 5. Claude-in-Chrome exploratory (CP4, real user eyes)

1. Log in to Studio (Keycloak — `platform-admin` / `PlatformAdmin2024`).
2. Open a **reactive** workflow deployment → `/workflows/{id}/d/{depId}` → **Memory** tab → confirm it
   lists member memory entries (was empty).
3. **Conversations** tab → click a past session → confirm WorkflowChat **replays** the prior member
   bubbles instead of the empty "Send a message to run this workflow." composer.

---

## 6. In-pod auth recipe (for ad-hoc endpoint pokes; suite-78 already does this internally)

The `/workflows/{id}/…` endpoints are `require_user` (JWT) — the `X-User-Sub` header does NOT work on
them. Fetch a real Keycloak token the browser way from inside the registry-api pod:

```bash
API_POD=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')

kubectl exec -i -n agentshield-platform "$API_POD" -c registry-api -- python3 - <<'PY'
import asyncio, httpx
KC = "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token"
async def main():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(KC, data={
            "grant_type": "password", "client_id": "agentshield-studio",
            "username": "platform-admin", "password": "PlatformAdmin2024"})
        tok = r.json()["access_token"]
        # platform-admin sub == 047fad5f-f38c-430a-bfba-6e4d9009314b
        WF = "<workflow-uuid>"
        m = await c.get(f"http://localhost:8000/api/v1/workflows/{WF}/memory",
                        headers={"Authorization": f"Bearer {tok}"})
        print(m.status_code, m.text[:400])
asyncio.run(main())
PY
```

Reference identity: **platform-admin sub = `047fad5f-f38c-430a-bfba-6e4d9009314b`** (the sub the
Playwright `ADMIN` headers use for API prep).

---

## 7. Regression sweep (before declaring done)

```bash
bash scripts/e2e/suite-78-conversations.sh                  # backend read surface
cd studio && npm run typecheck && npm run test && cd ..     # all Vitest + tsc
bash scripts/studio-e2e.sh workflow-memory.spec.ts
bash scripts/studio-e2e.sh workflow-conversations.spec.ts
```

Blast radius touched: the `agent_memory` read path (shared with the per-agent Memory tab + POC-5
Conversations), the `ConversationStore` port, the workflow deployment overview page, and the
`ConversationSidebar` route. suite-78 + the two Playwright specs + full Vitest cover it.

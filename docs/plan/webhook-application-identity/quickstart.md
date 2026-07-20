# Quickstart — Webhook Application Identity & Invoker Grants

This repo has no local dev server workflow for registry-api/event-gateway/Studio — everything runs in-cluster (`docker-desktop` k8s or the real EKS cluster) via `scripts/deploy-cpe2e.sh` + Helm, and migrations apply automatically through the `alembic-migrate` init container already wired into `charts/agentshield/charts/registry-api/templates/deployment.yaml` (`command: ["alembic", "upgrade", "head"]`, confirmed present today). There is nothing feature-specific to configure for migrations to run — they run on every pod restart the same way every prior migration in this repo already has.

## 1. Deploy the change

```bash
cd /Users/kalyankalvagadda/code/agent-shield
bash scripts/deploy-cpe2e.sh
```

This rebuilds and redeploys `registry-api` (new migrations 0069/0070, new routers, rbac.py changes), `event-gateway` (gateway cutover), and `studio` (new UI) at whatever tags this plan's tasks bump `deploy-cpe2e.sh`/`values.yaml` to (see plan.md's task list — every task that touches one of these three services bumps its tag in both files in the same change, per this repo's CLAUDE.md checklist).

## 2. Verify the migrations landed

```bash
NAMESPACE=agentshield-platform
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n "$NAMESPACE" "$API_POD" -- alembic current
# expect: 0070 (head)

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import os
async def check():
    eng = create_async_engine(os.environ['DATABASE_URL'])
    async with eng.begin() as conn:
        n_apps = (await conn.execute(text('SELECT count(*) FROM applications'))).scalar()
        n_wc = (await conn.execute(text('SELECT count(*) FROM webhook_clients'))).scalar()
        n_backfilled = (await conn.execute(text(
            \"SELECT count(*) FROM artifact_role_grants WHERE grantee_type='application' AND granted_by='system:backfill-0070'\"
        ))).scalar()
        print(f'applications={n_apps} webhook_clients={n_wc} backfilled_invoker_grants={n_backfilled}')
    await eng.dispose()
asyncio.run(check())
"
```
`backfilled_invoker_grants` should be `<= n_wc` (equal unless two `webhook_clients` rows collapsed into one reused `applications` row under the same team, per `data-model.md`'s Pass 1 `DISTINCT ON`).

## 3. Run the two new e2e suites

```bash
bash scripts/e2e/suite-82-artifact-grants.sh
bash scripts/e2e/suite-83-webhook-applications.sh
```
Or via the full runner (Suite A registered immediately before Suite B, both immediately after the existing Suite 81 entry):
```bash
bash scripts/e2e/run-all.sh
```

Both suites fetch real Keycloak tokens (`grant_type=password` against `agentshield-studio`) — `platform-admin`/`PlatformAdmin2024` is pre-seeded (`charts/agentshield/templates/realm-init-job.yaml`); the suites additionally create their OWN scoped test users via `POST /api/v1/admin/users` (as `platform-admin`) for the agent-admin / plain-contributor personas T-ARG-001/002/004/005 need, since only `platform-admin` and `agent-reviewer` are pre-seeded. **Gotcha, load-bearing:** a user created via `POST /api/v1/admin/users` gets `requiredActions: ["UPDATE_PASSWORD"]` in Keycloak — a direct `grant_type=password` login for that user fails with `invalid_grant` until the required action is cleared. Suite A's setup step clears it explicitly, in-pod, immediately after creating each test user:
```python
import httpx
from keycloak_client import create_user, _admin_token, _admin_url  # already vendored in registry-api

kc_id = await create_user(username=..., email=..., first_name="", last_name="", temp_password=PW)
token = await _admin_token()
async with httpx.AsyncClient(timeout=10) as client:
    await client.put(_admin_url(f"users/{kc_id}"), json={"requiredActions": []},
                      headers={"Authorization": f"Bearer {token}"})
    await client.put(_admin_url(f"users/{kc_id}/reset-password"),
                      json={"type": "password", "value": PW, "temporary": False},
                      headers={"Authorization": f"Bearer {token}"})
```
Without both PUTs, `grant_type=password` for that user returns `400 invalid_grant` and the suite would falsely SKIP (not FAIL) its persona-dependent cases — this is the same honest-skip discipline `suite-78-conversations.sh` already uses when Keycloak tokens can't be obtained.

## 4. Run the Playwright specs

```bash
cd studio
npx playwright install chromium   # first time only
cd ..
bash scripts/studio-e2e.sh
```
This runs the full Studio Playwright suite, including the two new specs (`artifact-grants.spec.ts`, `webhook-applications.spec.ts`) and the retired/replaced `webhook-clients.spec.ts` (deleted as part of this plan — see plan.md task 24). To run only the new specs during development:
```bash
cd studio
PLAYWRIGHT_BASE_URL=https://agentshield.127.0.0.1.nip.io:8443 npx playwright test artifact-grants.spec.ts webhook-applications.spec.ts
```

## 5. Manually exercise the full flow (create-app → grant-invoker → send-signed-webhook)

All commands assume `NAMESPACE=agentshield-platform` and a running `docker-desktop` cluster per the memory note "Context-storage local deploy."

**a. Get a real bearer token** (platform-admin, or any contributor+ user in the target team):
```bash
TOKEN=$(curl -sk -X POST "https://agentshield.127.0.0.1.nip.io:8443/realms/agentshield/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=agentshield-studio&username=platform-admin&password=PlatformAdmin2024" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

**b. Create an application** (team `platform`, the seeded default team):
```bash
curl -sk -X POST "https://agentshield.127.0.0.1.nip.io:8443/api/v1/teams/platform/applications" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"quickstart-app"}'
# → { "id": "<APP_ID>", "name": "quickstart-app", "secret": "whsec_...", "created_at": "..." }
```

**c. Create (or pick) an agent with an enabled webhook trigger**, then grant the application `invoker` on it:
```bash
AGENT_ID=$(curl -sk "https://agentshield.127.0.0.1.nip.io:8443/api/v1/agents/<agent-name>" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -sk -X POST "https://agentshield.127.0.0.1.nip.io:8443/api/v1/artifacts/agent/$AGENT_ID/grants" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"grantee_type":"application","grantee_id":"<APP_ID>","role":"invoker"}'
# → 201, and the trigger's auth_mode flips to client_signed on the next GET .../triggers
```

**d. Sign and send a webhook** (reusing this repo's own `sign_webhook` reference, exactly as `suite-83` does — AST-extracted from `services/event-gateway/webhook_auth.py` so the quickstart signer can never silently drift from the product's):
```bash
python3 - <<'PY'
import hashlib, hmac, time, json, httpx

secret = "<APP_SECRET_FROM_STEP_B>"
body = json.dumps({"event_type": "quickstart.ping"}).encode()
ts = int(time.time())
mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
headers = {
    "X-Client-Id": "quickstart-app",
    "X-Timestamp": str(ts),
    "X-Signature": f"sha256={mac}",
    "Content-Type": "application/json",
}
r = httpx.post("https://agentshield.127.0.0.1.nip.io:8443/hooks/<agent-name>/<webhook-path-token>",
                content=body, headers=headers, verify=False)
print(r.status_code, r.text)
PY
```
Expect `202`. A committed `agent_events` row with `status='matched'` and `client_id='quickstart-app'` confirms the whole path (application → invoker grant → gateway resolution → HMAC verify → dispatch).

**e. Prove the kill switch:**
```bash
curl -sk -X PATCH "https://agentshield.127.0.0.1.nip.io:8443/api/v1/teams/platform/applications/<APP_ID>" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"enabled":false}'
# Re-run step (d) unchanged — now returns 401 (uniform body), same shape a bad signature would return.
```

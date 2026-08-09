# RBAC R0 + R1 — Quickstart

## Prerequisites

- `kubectl` pointed at the target cluster; namespace `agentshield-platform` (override with `NAMESPACE`)
- Docker daemon running (the deploy script builds images)
- `helm` 3
- For Playwright: `cd studio && npx playwright install chromium` once
- Secrets are created by the deploy script; `keycloak-user-passwords` must carry keys `platform-admin` (= `PlatformAdmin2024`) and `agent-reviewer` (= `Reviewer2024`)

---

## Deploy

```bash
cd /Users/kkalyan/repo/agent-platform

# Confirm the two tags agree BEFORE deploying — a mismatch is an ImagePullBackOff,
# not an error message.
grep -n 'REGISTRY_API_TAG=' scripts/deploy-cpe2e.sh | head -1
sed -n '744p' charts/agentshield/values.yaml

bash scripts/deploy-cpe2e.sh
```

No `scripts/seed-platform-admin-role.sh` step. If the deploy stalls waiting for registry-api readiness, that is the bootstrap holding `/ready` red — read it:

> **There is no `curl` in the registry-api image** — it is `python:3.12-slim`. An in-pod `curl`
> exits 127, which reads as an outage rather than a missing binary. Use `python3`/`urllib`
> everywhere below.

```bash
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -c registry-api -- \
  python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/ready').read().decode())"

kubectl logs -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  -c registry-api --tail=200 | grep -i bootstrap
```

`/ready` answers 503 while bootstrapping, and `urlopen` raises `HTTPError` on non-2xx — read the
body off the exception:

```bash
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -c registry-api -- python3 -c "
import urllib.request, urllib.error
try:
    r = urllib.request.urlopen('http://localhost:8000/ready'); print(r.status, r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())"
```

Expected on success:
```
INFO ... bootstrap: platform-admin pinned sub=<uuid> team=platform role=platform-admin created=True
```

---

## Verify the bootstrap by hand

```bash
NS=agentshield-platform
POD=$(kubectl get pods -n $NS -l app.kubernetes.io/name=registry-api \
      --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')

# 1. The row exists and is pinned to the LIVE Keycloak sub.
kubectl exec -n $NS $POD -c registry-api -- python3 -c "
import asyncio
from db import AsyncSessionLocal
from sqlalchemy import text
import keycloak_client as kc
async def m():
    u = (await kc.list_users(username='platform-admin', exact=True))[0]
    async with AsyncSessionLocal() as s:
        r = (await s.execute(text('SELECT team_name, role, assigned_by FROM user_team_assignments WHERE user_sub=:x'), {'x': u['id']})).mappings().first()
    print('kc_sub', u['id'], 'email', u['email'], 'row', dict(r) if r else None)
asyncio.run(m())"

# 2. /me answers platform-admin through the real token path.
source scripts/e2e/lib/e2e-auth.sh
e2e_set_token $NS $POD
kubectl exec -n $NS $POD -c registry-api -- env TOK="$E2E_TOKEN" python3 -c "
import os, urllib.request
req = urllib.request.Request('http://localhost:8000/api/v1/me',
                             headers={'Authorization': 'Bearer ' + os.environ['TOK']})
print(urllib.request.urlopen(req).read().decode())"
```

Expect `"role": "platform-admin"`, `"team": "platform"`, `"email": "platform-admin@agentshield.local"`.

---

## Run the audit (FR-12)

```bash
kubectl exec -n $NS $POD -c registry-api -- python3 -c "
import json, urllib.request
print(json.dumps(json.loads(
    urllib.request.urlopen('http://localhost:8000/api/v1/admin/identity-audit').read()), indent=2))"
```

A clean cluster reports `orphan_users: []` and `stale_rows: []` (SC-3).

Run it again **after** a full e2e pass — that is the check that proves FR-7 stopped the regeneration rather than just cleaning up after it. It never deletes; remediate an orphan Keycloak user with `DELETE /api/v1/admin/users/{kc_id}`, and a stale row with a direct `DELETE FROM user_team_assignments WHERE user_sub = '…'`.

---

## Run suite-97

```bash
bash scripts/run-tests.sh --audit                   # manifest must be clean
bash scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh
```

All twelve `T-S97-0NN` cases must print `PASS`.

> T-S97-004 and T-S97-005 **mutate the cluster** (delete the Keycloak admin; scale Keycloak to 0) and restore it. Do not run them against a cluster someone else is using.

---

## Run the four migrated suites (FR-10)

```bash
bash scripts/e2e/suite-76-preferences.sh
bash scripts/e2e/suite-78-conversations.sh
bash scripts/e2e/suite-82-artifact-grants.sh
bash scripts/e2e/suite-83-webhook-applications.sh
```

Each now creates `agent-reviewer` itself through `POST /api/v1/admin/users`. To prove that for real, delete the user first and re-run:

```bash
kubectl exec -n $NS $POD -c registry-api -- python3 -c "
import asyncio, keycloak_client as kc
async def m():
    for u in await kc.list_users(username='agent-reviewer', exact=True):
        await kc.delete_user(u['id']); print('deleted', u['id'])
asyncio.run(m())"

bash scripts/e2e/suite-76-preferences.sh   # must still pass
```

---

## Verify the realm-recreation case (the 2026-07-20 regression)

```bash
# Record the current sub.
BEFORE=$(kubectl exec -n $NS $POD -c registry-api -- python3 -c "
import asyncio, keycloak_client as kc
print(asyncio.run(kc.list_users(username='platform-admin', exact=True))[0]['id'])")
echo "before=$BEFORE"

# Delete the Keycloak admin — the realm-recreation effect on identity.
kubectl exec -n $NS $POD -c registry-api -- python3 -c "
import asyncio, keycloak_client as kc; asyncio.run(kc.delete_user('$BEFORE'))"

# Restart; the bootstrap re-creates the user and RE-PINS the row onto the new sub.
kubectl rollout restart deploy/agentshield-registry-api -n $NS
kubectl rollout status  deploy/agentshield-registry-api -n $NS --timeout=300s

AFTER=$(kubectl exec -n $NS $POD -c registry-api -- python3 -c "
import asyncio, keycloak_client as kc
print(asyncio.run(kc.list_users(username='platform-admin', exact=True))[0]['id'])")
echo "after=$AFTER"      # MUST differ from $BEFORE
```

Then log into Studio as `platform-admin` / `PlatformAdmin2024` and confirm the **Admin** section is in the sidebar.

Against pre-R0 code every leg fails: no user is recreated, the row stays on the dead sub, and `/me` answers the invented `contributor`. Clean up the now-stale row reported by the audit.

---

## Regression sweep before shipping

```bash
bash scripts/run-tests.sh --groups
bash scripts/run-tests.sh --layer api --group rbac,governance
bash scripts/run-tests.sh --layer api --group deploy,agent
bash scripts/run-tests.sh --layer api --group eval,workflow,execution
bash scripts/run-tests.sh --layer api --group hitl,chat,tools,knowledge
cd studio && npm run typecheck && npm run test && cd ..
bash scripts/studio-e2e.sh
```

---

## Python verification (no pytest in registry-api)

```bash
for f in bootstrap_admin.py rbac.py main.py config.py keycloak_client.py \
         routers/me.py routers/admin_users.py routers/schedules.py \
         routers/deployments.py routers/versions.py routers/auth_configs.py \
         routers/agent_tools.py routers/agent_runs.py routers/workflows.py \
         routers/teams.py routers/llm_providers.py routers/admin.py \
         routers/playground_approvals.py \
         alembic/versions/0079_drop_user_team_assignments_role_default.py; do
  python3 -c "import ast,sys; ast.parse(open('services/registry-api/$f').read())" \
    && echo "ok $f" || echo "SYNTAX FAIL $f"
done

kubectl exec -n $NS $POD -c registry-api -- python3 -c \
  "import main; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('mappers ok')"
```

---

## Rollback

FR-4 and FR-5 are the only behaviour-visible steps.

- FR-4 reverses with `alembic downgrade 0078` (restores the `server_default`)
- FR-5 by reverting the `rbac.py` commit (restores `None → "contributor"`)

Both are single-commit reverts with no data migration. The platform is not live — forward-fix is preferred.

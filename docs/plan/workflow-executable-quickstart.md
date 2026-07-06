# Workflow Executable — Quickstart (Local Deploy Desktop Cluster)

**Status**: FINAL  
**Date**: 2026-07-05  
**Prerequisite**: Existing Docker Desktop K8s cluster with AgentShield deployed; `kubectl`, `helm`, and `docker` in PATH.

---

## Step 1 — Build and push updated images

After all Phase W1–W5 changes are applied:

```bash
cd /Users/kkalyan/repo/agent-platform

# Build + push all changed images
bash scripts/deploy-cpe2e.sh

# The script builds and tags:
#   registry.internal/agentshield/registry-api:0.2.56
#   registry.internal/agentshield/studio:0.1.43
#   registry.internal/agentshield/declarative-runner:0.1.7
```

---

## Step 2 — Deploy via Helm

```bash
# Upgrade the platform (tags baked into charts/agentshield/values.yaml)
helm upgrade --install agentshield charts/agentshield \
  --namespace agentshield-platform \
  --wait --timeout 5m

# Verify pods are running
kubectl get pods -n agentshield-platform
```

---

## Step 3 — Run Alembic migrations

The migration runs automatically via the init container on registry-api startup. Verify:

```bash
# Check migration applied
kubectl exec -n agentshield-platform \
  $(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}') \
  -- python -c "
import asyncio, asyncpg
async def main():
    conn = await asyncpg.connect('postgresql://agentshield:agentshield@postgres:5432/agentshield')
    tables = await conn.fetch(\"SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename\")
    print([r['tablename'] for r in tables])
    await conn.close()
asyncio.run(main())
"

# Expected: includes 'agent_graphs', 'agent_graph_versions', 'workflow_members'
# Expected: 'workflows' now refers to composite workflows table
```

---

## Step 4 — Smoke test the rename (CP-Wa)

Run the Phase W1 checkpoint smoke script:

```bash
API_POD=$(kubectl get pods -n agentshield-platform \
  -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}')

# 1. Verify old canvas-graph endpoint is at /api/v1/agent-graphs/
kubectl exec -n agentshield-platform "$API_POD" -- \
  python3 -c "
import httpx, json
r = httpx.get('http://localhost:8000/api/v1/agent-graphs', timeout=5)
print('agent-graphs status:', r.status_code)
assert r.status_code == 200
print('PASS: agent-graphs endpoint reachable')
"

# 2. Verify new composite workflows endpoint is at /api/v1/workflows/
kubectl exec -n agentshield-platform "$API_POD" -- \
  python3 -c "
import httpx, json
r = httpx.get('http://localhost:8000/api/v1/workflows', timeout=5)
print('workflows status:', r.status_code)
assert r.status_code == 200
print('PASS: composite workflows endpoint reachable')
"
```

---

## Step 5 — Create a composite workflow and run it (CP-Wb + CP-Wc)

Ensure at least two agents are deployed in the same team. Then:

```bash
API_POD=$(kubectl get pods -n agentshield-platform \
  -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n agentshield-platform "$API_POD" -- \
  python3 -c "
import httpx, json, time

BASE = 'http://localhost:8000/api/v1'

# 1. List available agents
agents_r = httpx.get(f'{BASE}/agents', params={'status': 'active'}, timeout=5)
agents = agents_r.json().get('items', agents_r.json())
# Use first two agents (or adjust names as needed)
a1 = agents[0]
a2 = agents[1] if len(agents) > 1 else agents[0]
print(f'Using agents: {a1[\"name\"]} + {a2[\"name\"]}')

# 2. Create composite workflow
wf_r = httpx.post(f'{BASE}/workflows', json={
  'name': f'test-workflow-{int(time.time())}',
  'team': a1['team'],
  'execution_shape': 'sequential',
  'orchestration': 'sequential',
}, timeout=5)
assert wf_r.status_code == 201, f'create failed: {wf_r.text}'
wf = wf_r.json()
print(f'Created workflow: {wf[\"id\"]}')

# 3. Add member agents
for i, agent in enumerate([a1, a2], start=1):
    m_r = httpx.post(f'{BASE}/workflows/{wf[\"id\"]}/members', json={
        'agent_id': str(agent['id']),
        'role': 'worker',
        'position': i,
    }, timeout=5)
    assert m_r.status_code == 201, f'add member failed: {m_r.text}'
    print(f'Added member: {agent[\"name\"]} at position {i}')

# 4. Trigger a run
run_r = httpx.post(f'{BASE}/workflows/{wf[\"id\"]}/runs', json={
    'input_payload': {'message': 'test e2e run from quickstart'},
    'trigger_type': 'manual',
    'run_by': 'quickstart-test',
}, timeout=5)
assert run_r.status_code == 202, f'run failed: {run_r.text}'
run_id = run_r.json()['run_id']
print(f'Run started: {run_id}')

# 5. Poll for completion
for _ in range(30):
    tree_r = httpx.get(f'{BASE}/workflows/{wf[\"id\"]}/runs/{run_id}/tree', timeout=5)
    if tree_r.status_code == 200:
        tree = tree_r.json()
        status = tree['parent']['status']
        children = len(tree['children'])
        print(f'  status={status}, children={children}')
        if status in ('completed', 'failed'):
            break
    time.sleep(3)

assert tree['parent']['status'] == 'completed', f'Expected completed, got {tree[\"parent\"][\"status\"]}'
assert len(tree['children']) == 2
assert all(c['parent_run_id'] == run_id for c in tree['children'])
print('PASS: workflow run tree verified — parent + 2 children')
"
```

---

## Step 6 — Run the full e2e suite

```bash
# Run the new workflow composite suite
bash scripts/e2e/suite-29-workflow-composite.sh

# Run all suites to ensure no regression
bash scripts/e2e/run-all.sh
```

---

## Step 7 — Verify Studio

```bash
# Open Studio in browser (find the NodePort)
kubectl get svc -n agentshield-platform agentshield-studio

# Navigate to:
#   /agent-graphs        — old canvas builder (renamed from /workflows)
#   /workflows           — new composite workflow list
#   /workflows/new       — new workflow builder page (node-picker for existing agents)
```

---

## Common Troubleshooting

**Migration failed (`relation "workflows" does not exist`):**  
Check that migration 0026 ran before 0027. Verify with:
```bash
kubectl exec -n agentshield-platform "$API_POD" -- \
  python3 -c "
import asyncio, asyncpg
async def main():
    conn = await asyncpg.connect('postgresql://agentshield:agentshield@postgres:5432/agentshield')
    rows = await conn.fetch('SELECT version_num FROM alembic_version')
    print([r['version_num'] for r in rows])
asyncio.run(main())
"
```

**`/api/v1/agent-graphs` returns 404:**  
The registry-api pod is running the old image. Check `kubectl get deployment -n agentshield-platform registry-api -o jsonpath='{.spec.template.spec.containers[0].image}'` — must show the new tag `0.2.56`.

**Composite workflow run stuck in `queued`:**  
The orchestrator background task requires the member agents to be running (deployed pods). Verify with `kubectl get deployments -n agents-<team>`. If no pods exist, deploy the member agents first via Studio or the API.

**TypeScript build errors in Studio:**  
```bash
cd /Users/kkalyan/repo/agent-platform/studio
npx tsc --noEmit
```
Fix all errors before building the Studio image.

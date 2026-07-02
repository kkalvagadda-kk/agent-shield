# Phase A-B-C: Build and Deploy Commands

**Assumes:** Existing AgentShield cluster running at `NAMESPACE=agentshield-platform`. Port-forward access to registry-api on 8000.

---

## Phase A

### 1. Implement all A1-A8 tasks

Follow `docs/plan/tasks.md` Phase A tasks. One `npm run typecheck` per task.

### 2. Build Studio (studio:0.1.25)

```bash
cd studio
npm run build         # must exit 0, zero TS errors
cd ..
docker build -t registry.internal/agentshield/studio:0.1.25 studio/
```

### 3. Build Registry-API (registry-api:0.2.26) — A4 backend change

```bash
docker build -t registry.internal/agentshield/registry-api:0.2.26 services/registry-api/
```

### 4. Update deploy script

```bash
# In scripts/deploy-cpe2e.sh:
REGISTRY_API_TAG="0.2.26"
STUDIO_TAG="0.1.25"
```

### 5. Deploy

```bash
kubectl set image deployment/agentshield-registry-api \
  registry-api=registry.internal/agentshield/registry-api:0.2.26 \
  -n agentshield-platform

kubectl set image deployment/agentshield-studio \
  studio=registry.internal/agentshield/studio:0.1.25 \
  -n agentshield-platform

kubectl rollout status deployment/agentshield-registry-api -n agentshield-platform --timeout=90s
kubectl rollout status deployment/agentshield-studio -n agentshield-platform --timeout=90s
```

### 6. Smoke test Phase A

```bash
kubectl port-forward svc/agentshield-registry-api -n agentshield-platform 8000:8000 &
PF=$!

# A4: Approve with no grantee_teams → 0 grants
curl -s -X POST http://localhost:8000/api/v1/admin/publish-requests/<PR_ID>/approve \
  -H "Content-Type: application/json" \
  -d '{}' | jq '{approved, grants_created}'
# Expected: { "approved": true, "grants_created": 0 }

kill $PF
```

Then open Studio in browser (port-forward on 5173) and verify:
- Sidebar shows "Evaluate" not "Test"
- `/admin/artifacts` shows all agents/tools/skills
- Publish Queue shows agent names not UUIDs
- Catalog agent cards show Chat + Deploy buttons

---

## Phase B

### Prerequisites

Phase A must be deployed.

### 1. Implement B1-B4 tasks

Follow `docs/plan/tasks.md` Phase B tasks.

### 2. Build Registry-API (registry-api:0.2.27) — B1 new router

```bash
docker build -t registry.internal/agentshield/registry-api:0.2.27 services/registry-api/
```

### 3. Verify chat endpoint locally

```bash
# Start registry-api locally (or port-forward) on 8001
docker run --rm -p 8001:8000 \
  -e DATABASE_URL="postgresql+asyncpg://postgres:DevPass2024@host.docker.internal:5432/agentshield" \
  registry.internal/agentshield/registry-api:0.2.27

# Without token → 401
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8001/api/v1/agents/customer-intelligence-agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
# Expected: 401
```

### 4. Build Studio (studio:0.1.26)

```bash
cd studio
npm run build
cd ..
docker build -t registry.internal/agentshield/studio:0.1.26 studio/
```

### 5. Update deploy script

```bash
# In scripts/deploy-cpe2e.sh:
REGISTRY_API_TAG="0.2.27"
STUDIO_TAG="0.1.26"
```

### 6. Deploy

```bash
kubectl set image deployment/agentshield-registry-api \
  registry-api=registry.internal/agentshield/registry-api:0.2.27 \
  -n agentshield-platform

kubectl set image deployment/agentshield-studio \
  studio=registry.internal/agentshield/studio:0.1.26 \
  -n agentshield-platform

kubectl rollout status deployment/agentshield-registry-api -n agentshield-platform --timeout=90s
kubectl rollout status deployment/agentshield-studio -n agentshield-platform --timeout=90s
```

### 7. Smoke test Phase B

```bash
kubectl port-forward svc/agentshield-registry-api -n agentshield-platform 8000:8000 &
PF=$!

# 401 without token
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/api/v1/agents/customer-intelligence-agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
# Expected: 401

kill $PF
```

Then open Studio and verify:
- `/my-agents` shows granted agents with Running/Not-deployed status
- `/agents/customer-intelligence-agent/chat` shows clean chat UI (no eval controls)
- Sidebar My Agents section shows agent names

---

## Phase C

### 1. Write suite-14

Follow `docs/plan/tasks.md` Phase C tasks.

### 2. Run suite

```bash
NAMESPACE=agentshield-platform bash scripts/e2e/suite-14-consumer-chat.sh
# Expected: T-S14-001 PASS, T-S14-002 PASS, T-S14-003 PASS, T-S14-004 PASS
# T-S14-005, T-S14-006 MANUAL
# Exit 0
```

### 3. Run full e2e suite (regression check)

```bash
NAMESPACE=agentshield-platform bash scripts/e2e/run-all.sh 2>&1 | tail -30
```

All previously passing suites must still pass. New Suite 14 must show T-S14-001 through T-S14-004 PASS.

---

## Rollback

If either rollout degrades:

```bash
# Rollback to previous registry-api
kubectl rollout undo deployment/agentshield-registry-api -n agentshield-platform
# Rollback to previous studio
kubectl rollout undo deployment/agentshield-studio -n agentshield-platform

kubectl rollout status deployment/agentshield-registry-api -n agentshield-platform --timeout=60s
kubectl rollout status deployment/agentshield-studio -n agentshield-platform --timeout=60s
```

Previous tags (pre-Phase-A): `registry-api:0.2.25`, `studio:0.1.24`. The `deploy-cpe2e.sh` script retains these as comments for rollback reference.

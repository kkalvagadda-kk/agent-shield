#!/usr/bin/env bash
# scripts/e2e/suite-52-reconcile-drift.sh
#
# E2E Suite 52: Deploy-controller recovers 'running' deployments whose k8s
# Deployment vanished after a cluster wipe.
# Tests T-S52-001.
#
# What this proves (the gap: the poll loop only reconciled pending/suspending/
# terminating, so a restored 'running' row with no k8s Deployment sat pod-less
# forever — "Agent pod is unreachable"). Fix, sandbox path: _handle_sandbox_
# running_drift marks the drifted row 'terminated' so the developer redeploys
# (production self-heals via a separate re-materialize path, not covered here —
# it would provision a real pod).
#   T-S52-001 — a 'running' sandbox deployment whose k8s_deployment_name does not
#               exist in the cluster is marked 'terminated' by the controller
#               within a few poll cycles.
#
# Live integration test: relies on the deploy-controller poll loop (5s interval).
#
# Usage:
#   bash scripts/e2e/suite-52-reconcile-drift.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 52: Reconcile drift recovery (sandbox) ==="
echo "  Pod: $API_POD"
echo ""

# Seed a running sandbox deployment pointing at a k8s Deployment that does not exist.
DEP_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, datetime, uuid
from db import AsyncSessionLocal
from sqlalchemy import select
from models import Agent, AgentVersion, Deployment

AG='s52-drift-agent'
async def m():
    async with AsyncSessionLocal() as db:
        a=(await db.execute(select(Agent).where(Agent.name==AG))).scalar_one_or_none()
        if not a:
            a=Agent(name=AG, team='platform', agent_type='declarative', status='active')
            db.add(a); await db.flush()
        v=AgentVersion(agent_id=a.id, version_number=1, config={}, tools=[])
        db.add(v); await db.flush()
        now=datetime.datetime.now(datetime.timezone.utc)
        dep=Deployment(agent_id=a.id, version_id=v.id, environment='sandbox',
                       status='running', k8s_namespace='agents-platform',
                       k8s_deployment_name='s52-bogus-'+uuid.uuid4().hex[:8],
                       deployed_at=now)
        db.add(dep); await db.flush()
        did=str(dep.id); await db.commit()
        print(did)
asyncio.run(m())
" 2>/dev/null | grep -v Defaulted | tail -1)

echo "  Seeded running deployment (bogus k8s name): $DEP_ID"
echo "  Waiting for controller to detect drift + mark terminated..."

STATUS=""
for i in $(seq 1 12); do
  sleep 4
  STATUS=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio
from db import AsyncSessionLocal
from sqlalchemy import select
from models import Deployment
async def m():
    async with AsyncSessionLocal() as db:
        d=(await db.execute(select(Deployment).where(Deployment.id=='$DEP_ID'))).scalar_one_or_none()
        print(d.status if d else 'GONE')
asyncio.run(m())
" 2>/dev/null | grep -v Defaulted | tail -1)
  echo "    [$((i*4))s] status=$STATUS"
  [ "$STATUS" = "terminated" ] && break
done

# cleanup
kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio
from db import AsyncSessionLocal
from sqlalchemy import select
from models import Agent, AgentVersion, Deployment
AG='s52-drift-agent'
async def m():
    async with AsyncSessionLocal() as db:
        a=(await db.execute(select(Agent).where(Agent.name==AG))).scalar_one_or_none()
        if a:
            for d in (await db.execute(select(Deployment).where(Deployment.agent_id==a.id))).scalars().all():
                await db.delete(d)
            for v in (await db.execute(select(AgentVersion).where(AgentVersion.agent_id==a.id))).scalars().all():
                await db.delete(v)
            await db.delete(a); await db.commit()
asyncio.run(m())
" 2>/dev/null || true

echo ""
PASS=0; FAIL=0
if [ "$STATUS" = "terminated" ]; then
  echo "  PASS: T-S52-001 drifted running sandbox deployment marked terminated"
  PASS=1
else
  echo "  FAIL: status=$STATUS (expected terminated)"
  FAIL=1
fi

echo ""
echo "=== Suite 52 done: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]

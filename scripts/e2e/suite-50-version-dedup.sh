#!/usr/bin/env bash
# scripts/e2e/suite-50-version-dedup.sh
#
# E2E Suite 50: Deploy only mints a new version when the snapshot changed
# Tests T-S50-001 through T-S50-003.
#
# What this proves (the bug: deploy_agent's auto-version path bumped
# version_number on EVERY deploy without comparing the config/tools snapshot to
# the latest version, so no-op redeploys created byte-identical duplicate
# versions — serper-agent-4 had 16 versions, 14 of them no-change dups. Fix:
# reuse the latest version when the canonical snapshot is unchanged, all envs):
#   T-S50-001 — first sandbox deploy creates version 1.
#   T-S50-002 — a second UNCHANGED deploy reuses v1 (still exactly 1 version).
#   T-S50-003 — after the agent config changes, a deploy creates version 2.
#
# Usage:
#   bash scripts/e2e/suite-50-version-dedup.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 50: Version dedup on deploy ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, httpx
from db import AsyncSessionLocal
from sqlalchemy import select, func
from models import Agent, AgentVersion, Deployment

AG='s50-ver-agent'; BASE='http://localhost:8000/api/v1'
H={'X-User-Team':'platform'}

async def vcount(db, agent_id):
    return (await db.execute(select(func.count(AgentVersion.id)).where(AgentVersion.agent_id==agent_id))).scalar()

async def m():
    # clean slate
    async with AsyncSessionLocal() as db:
        old=(await db.execute(select(Agent).where(Agent.name==AG))).scalar_one_or_none()
        if old:
            for d in (await db.execute(select(Deployment).where(Deployment.agent_id==old.id))).scalars().all():
                await db.delete(d)
            for v in (await db.execute(select(AgentVersion).where(AgentVersion.agent_id==old.id))).scalars().all():
                await db.delete(v)
            await db.delete(old); await db.commit()

    httpx.post(f'{BASE}/agents/', json={'name':AG,'team':'platform','agent_type':'declarative',
               'metadata':{'instructions':'be helpful'}}, timeout=8)

    def deploy():
        return httpx.post(f'{BASE}/agents/{AG}/deploy', json={'environment':'sandbox'}, headers=H, timeout=15)

    r1=deploy()
    async with AsyncSessionLocal() as db:
        a=(await db.execute(select(Agent).where(Agent.name==AG))).scalar_one()
        c1=await vcount(db, a.id)
    r2=deploy()  # UNCHANGED redeploy
    async with AsyncSessionLocal() as db:
        c2=await vcount(db, a.id)
    # change the agent config, then deploy again
    async with AsyncSessionLocal() as db:
        a2=(await db.execute(select(Agent).where(Agent.name==AG))).scalar_one()
        md=dict(a2.metadata_ or {}); md['instructions']='be VERY helpful'; a2.metadata_=md
        await db.commit()
    r3=deploy()  # CHANGED redeploy
    async with AsyncSessionLocal() as db:
        c3=await vcount(db, a.id)

    t1 = (c1==1)
    t2 = (c2==1)   # no bump on unchanged
    t3 = (c3==2)   # bump on change

    # cleanup
    async with AsyncSessionLocal() as db:
        a3=(await db.execute(select(Agent).where(Agent.name==AG))).scalar_one()
        for d in (await db.execute(select(Deployment).where(Deployment.agent_id==a3.id))).scalars().all():
            await db.delete(d)
        for v in (await db.execute(select(AgentVersion).where(AgentVersion.agent_id==a3.id))).scalars().all():
            await db.delete(v)
        await db.delete(a3); await db.commit()

    print(f'T1={t1} T2={t2} T3={t3} counts(after_deploy1={c1},after_unchanged={c2},after_change={c3}) http={r1.status_code}/{r2.status_code}/{r3.status_code}')

asyncio.run(m())
" 2>/dev/null | tail -1)

echo "    → $RESULT"

PASS=0; FAIL=0
case "$RESULT" in
  *"T1=True T2=True T3=True"*)
    echo "  PASS: T-S50-001 first deploy creates version 1"
    echo "  PASS: T-S50-002 unchanged redeploy reuses v1 (no bump)"
    echo "  PASS: T-S50-003 changed deploy creates version 2"
    PASS=3 ;;
  *) echo "  FAIL: $RESULT"; FAIL=1 ;;
esac

echo ""
echo "=== Suite 50 done: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]

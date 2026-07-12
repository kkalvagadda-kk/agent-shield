#!/usr/bin/env bash
# scripts/e2e/suite-48-feedback-dashboard.sh
#
# E2E Suite 48: User-feedback ratio on the observability dashboard (Phase 3a)
# Tests T-S48-001 through T-S48-003.
#
# What this proves (the gap: thumbs feedback was pushed ONLY to Langfuse as a
# score — no local column — so the M2 dashboard could not show a satisfaction
# ratio without a live Langfuse call. Fix: playground_runs.user_feedback SMALLINT
# written in submit_run_feedback; GET /observability/dashboard aggregates
# up/down/ratio from PlaygroundRun joined to Agent(team)):
#   T-S48-001 — POST /playground/runs/{id}/feedback persists user_feedback on the
#               PlaygroundRun (reload from DB proves the round-trip, not in-memory).
#   T-S48-002 — get_dashboard() aggregates feedback: 2 up + 1 down => up=2 down=1
#               ratio≈0.667, sourced from PlaygroundRun (not Langfuse).
#   T-S48-003 — cleanup: remove seeded rows.
#
# Usage:
#   bash scripts/e2e/suite-48-feedback-dashboard.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 48: User-feedback ratio dashboard panel ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, datetime, httpx
from db import AsyncSessionLocal
from sqlalchemy import select, text as sa_text
from models import Agent, PlaygroundRun
from routers.observability import get_dashboard

AG='s48-fb-agent'; SUB='e2e-s48'; TEAM='platform'

async def m():
    async with AsyncSessionLocal() as db:
        # seed team assignment + agent
        await db.execute(sa_text(
            'INSERT INTO user_team_assignments (user_sub, team_name) VALUES (:s,:t) '
            'ON CONFLICT DO NOTHING'), {'s': SUB, 't': TEAM})
        a = (await db.execute(select(Agent).where(Agent.name==AG))).scalar_one_or_none()
        if not a:
            a = Agent(name=AG, team=TEAM, agent_type='declarative', status='active')
            db.add(a); await db.flush()
        now = datetime.datetime.now(datetime.timezone.utc)
        runs=[]
        for i in range(3):
            r = PlaygroundRun(user_id=SUB, agent_name=AG, context='playground',
                              status='completed', started_at=now)
            db.add(r); await db.flush(); runs.append(r)
        await db.commit()

        # T-S48-001: POST feedback via the real HTTP endpoint (2 up, 1 down)
        scores=[1,1,-1]
        for r,s in zip(runs,scores):
            httpx.post(f'http://localhost:8000/api/v1/playground/runs/{r.id}/feedback',
                       json={'score': s}, headers={'X-User-Sub': SUB}, timeout=8)
        # reload run[0] to prove persistence
        f0 = (await db.execute(select(PlaygroundRun).where(PlaygroundRun.id==runs[0].id))).scalar_one()
        await db.refresh(f0)
        t1 = (f0.user_feedback == 1)

        # T-S48-002: dashboard aggregation via the endpoint function
        dash = await get_dashboard(agent_name=AG, period='7d', from_date=None,
                                   to_date=None, claims={'sub': SUB}, db=db)
        fb = dash.feedback
        t2 = (fb.up==2 and fb.down==1 and abs((fb.ratio or 0)-2/3) < 0.01)

        # cleanup
        for r in runs:
            await db.delete((await db.execute(select(PlaygroundRun).where(PlaygroundRun.id==r.id))).scalar_one())
        await db.delete((await db.execute(select(Agent).where(Agent.name==AG))).scalar_one())
        await db.execute(sa_text('DELETE FROM user_team_assignments WHERE user_sub=:s'), {'s': SUB})
        await db.commit()
        print(f'T1={t1} T2={t2} up={fb.up} down={fb.down} ratio={fb.ratio}')

asyncio.run(m())
" 2>/dev/null | tail -1)

echo "    → $RESULT"

PASS=0; FAIL=0
case "$RESULT" in
  *"T1=True T2=True"*)
    echo "  PASS: T-S48-001 user_feedback persisted on PlaygroundRun"
    echo "  PASS: T-S48-002 dashboard feedback aggregation (up=2 down=1 ratio≈0.67)"
    PASS=2 ;;
  *) echo "  FAIL: $RESULT"; FAIL=1 ;;
esac

echo ""
echo "=== Suite 48 done: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]

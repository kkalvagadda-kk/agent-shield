#!/usr/bin/env bash
# scripts/e2e/suite-49-judge-agentrun-score.sh
#
# E2E Suite 49: Judge score reaches AgentRun (M5 Score column producer)
# Tests T-S49-001 through T-S49-002.
#
# What this proves (the gap: the judge's _write_score only ever patched
# PlaygroundRun, but the M5 catalog "Score" column reads AgentRun.judge_score —
# so production runs always showed "—". Fix: _write_score now follows the
# langfuse_trace_id and also patches the AgentRun for that trace, and consumer/
# production chat completion fires the judge):
#   T-S49-001 — _write_score(run_id, score, langfuse_trace_id=T) sets
#               PlaygroundRun.judge_score AND the AgentRun sharing trace T.
#   T-S49-002 — cleanup.
#
# Deterministic: calls _write_score directly (no LLM provider needed). The full
# score_run→LLM path is exercised by a real chat (manual M5 verification).
#
# Usage:
#   bash scripts/e2e/suite-49-judge-agentrun-score.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 49: Judge score reaches AgentRun ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, datetime
from db import AsyncSessionLocal
from sqlalchemy import select
from models import Agent, PlaygroundRun, AgentRun
from judge import _write_score

TRACE='s49feedcafe0000000000000000000049'
AG='s49-judge-agent'

async def m():
    async with AsyncSessionLocal() as db:
        a = (await db.execute(select(Agent).where(Agent.name==AG))).scalar_one_or_none()
        if not a:
            a = Agent(name=AG, team='platform', agent_type='declarative', status='active')
            db.add(a); await db.flush()
        now = datetime.datetime.now(datetime.timezone.utc)
        pr = PlaygroundRun(user_id='e2e-s49', agent_name=AG, context='production',
                           status='completed', started_at=now, langfuse_trace_id=TRACE)
        ar = AgentRun(agent_name=AG, user_id='e2e-s49', input='hi', context='production',
                      status='completed', started_at=now, team='platform',
                      langfuse_trace_id=TRACE)
        db.add(pr); db.add(ar); await db.commit()
        prid, arid = pr.id, ar.id

    # Act: judge writes score following the trace id.
    await _write_score(prid, score=0.87, reason='good', status='completed',
                       langfuse_trace_id=TRACE)

    async with AsyncSessionLocal() as db:
        fp = (await db.execute(select(PlaygroundRun).where(PlaygroundRun.id==prid))).scalar_one()
        fa = (await db.execute(select(AgentRun).where(AgentRun.id==arid))).scalar_one()
        # PlaygroundRun.judge_score is Numeric (Decimal); AgentRun.judge_score is Float — cast both.
        t1 = (abs(float(fp.judge_score or 0)-0.87) < 0.001 and abs(float(fa.judge_score or 0)-0.87) < 0.001)
        # cleanup
        await db.delete(fp); await db.delete(fa)
        await db.delete((await db.execute(select(Agent).where(Agent.name==AG))).scalar_one())
        await db.commit()
        print(f'T1={t1} playground={fp.judge_score} agentrun={fa.judge_score}')

asyncio.run(m())
" 2>/dev/null | tail -1)

echo "    → $RESULT"

PASS=0; FAIL=0
case "$RESULT" in
  *"T1=True"*)
    echo "  PASS: T-S49-001 judge_score written to BOTH PlaygroundRun and AgentRun via trace id"
    PASS=1 ;;
  *) echo "  FAIL: $RESULT"; FAIL=1 ;;
esac

echo ""
echo "=== Suite 49 done: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]

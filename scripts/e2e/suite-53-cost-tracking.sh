#!/usr/bin/env bash
# scripts/e2e/suite-53-cost-tracking.sh
#
# E2E Suite 53: Cost tracking Path A — persist Langfuse GENERATION cost/tokens
# onto agent_runs and aggregate them in the cost console.
#
# What this proves (the gap: Langfuse computes per-LLM-call cost on every OTEL
# GENERATION span, but nothing copied it into agent_runs.cost_usd, so every cost
# query returned 0). The cost_backfill sweep writes it back; GET /costs
# aggregates it, env-scoped so sandbox spend never dilutes production.
#   T-S53-001 — cost_backfill._sweep_once() writes cost_usd/prompt_tokens/
#               completion_tokens onto a completed run that has a trace but no
#               cost yet (Langfuse fetch stubbed for determinism).
#   T-S53-002 — GET /observability/costs totals + by_agent reflect the persisted
#               cost, and a run not tied to this environment is excluded.
#
# Usage:
#   bash scripts/e2e/suite-53-cost-tracking.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 53: Cost tracking (backfill + console) ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, datetime, uuid
from db import AsyncSessionLocal
from sqlalchemy import select, text
from models import Agent, AgentVersion, Deployment, AgentRun

TEAM='platform'
AG='s53-cost-agent'
SUB='s53-user-'+uuid.uuid4().hex[:8]
now=datetime.datetime.now(datetime.timezone.utc)

async def main():
    out={}
    async with AsyncSessionLocal() as db:
        await db.execute(text('INSERT INTO user_team_assignments (user_sub, team_name) VALUES (:s,:t) ON CONFLICT DO NOTHING'), {'s':SUB,'t':TEAM})
        a=(await db.execute(select(Agent).where(Agent.name==AG))).scalar_one_or_none()
        if not a:
            a=Agent(name=AG, team=TEAM, agent_type='declarative', status='active')
            db.add(a); await db.flush()
        v=AgentVersion(agent_id=a.id, version_number=1, config={}, tools=[])
        db.add(v); await db.flush()
        sd=Deployment(agent_id=a.id, version_id=v.id, environment='sandbox',
                      status='running', k8s_namespace='agents-platform',
                      k8s_deployment_name='s53-sbx', deployed_at=now)
        db.add(sd); await db.flush(); sbx_id=sd.id
        # run 1: sandbox-scoped, completed, has trace, NO cost yet -> sweep target
        tid='s53'+uuid.uuid4().hex[:28]
        r1=AgentRun(agent_name=AG, team=TEAM, status='completed', context='production',
                    sandbox_deployment_id=sbx_id, langfuse_trace_id=tid,
                    started_at=now, completed_at=now)
        db.add(r1); await db.flush(); r1_id=str(r1.id)
        # run 2: NOT tied to this env (no deployment id) but already costed ->
        # must be EXCLUDED from the sandbox view's totals.
        r2=AgentRun(agent_name=AG, team=TEAM, status='completed', context='production',
                    langfuse_trace_id='s53x'+uuid.uuid4().hex[:28],
                    cost_usd=9.99, started_at=now, completed_at=now)
        db.add(r2); await db.flush()
        await db.commit()

    # --- T-S53-001: stub Langfuse fetch, run the sweep, assert writeback ---
    # Scope the stub to OUR seeded trace only — _sweep_once() sweeps EVERY
    # uncosted run in the window, so an unconditional stub would write the fake
    # cost onto real runs cluster-wide. Real runs get None -> skipped.
    import tracing
    tracing.fetch_trace_cost_tokens = lambda t: (
        {'cost_usd':0.0125,'prompt_tokens':1546,'completion_tokens':401,'model':'claude-sonnet-4-6'}
        if t == tid else None
    )
    import cost_backfill
    await cost_backfill._sweep_once()
    async with AsyncSessionLocal() as db:
        r=(await db.execute(select(AgentRun).where(AgentRun.id==uuid.UUID(r1_id)))).scalar_one()
        out['t1_cost']=float(r.cost_usd) if r.cost_usd is not None else None
        out['t1_ptok']=r.prompt_tokens
        out['t1_ctok']=r.completion_tokens

    # --- T-S53-002: sandbox cost console = swept run only; run 2 excluded ---
    import routers.observability as obs
    async def fake_team(claims, db): return TEAM
    obs._resolve_team=fake_team
    obs._spend_by_model=lambda ids, frm: []
    async with AsyncSessionLocal() as db:
        data=await obs.get_costs(period='30d', environment='sandbox', from_date=None, to_date=None, claims={'sub':SUB}, db=db)
    out['t2_total']=round(data.total_cost_usd,4)
    out['t2_agents']=[(x.agent_name,round(x.cost_usd,4)) for x in data.by_agent if x.agent_name==AG]

    # cleanup
    async with AsyncSessionLocal() as db:
        a=(await db.execute(select(Agent).where(Agent.name==AG))).scalar_one_or_none()
        if a:
            for r in (await db.execute(select(AgentRun).where(AgentRun.agent_name==AG))).scalars().all():
                await db.delete(r)
            for d in (await db.execute(select(Deployment).where(Deployment.agent_id==a.id))).scalars().all():
                await db.delete(d)
            for v in (await db.execute(select(AgentVersion).where(AgentVersion.agent_id==a.id))).scalars().all():
                await db.delete(v)
            await db.delete(a)
        await db.execute(text('DELETE FROM user_team_assignments WHERE user_sub=:s'), {'s':SUB})
        await db.commit()

    print('RESULT', out)

asyncio.run(main())
" 2>&1 | grep -v Defaulted | grep '^RESULT' | tail -1)

echo "  $RESULT"
echo ""

PASS=0; FAIL=0

# T-S53-001: sweep wrote the stubbed cost + tokens onto the production run.
if echo "$RESULT" | grep -q "'t1_cost': 0.0125" \
   && echo "$RESULT" | grep -q "'t1_ptok': 1546" \
   && echo "$RESULT" | grep -q "'t1_ctok': 401"; then
  echo "  PASS: T-S53-001 sweep persisted cost_usd + tokens onto the run"
  PASS=$((PASS+1))
else
  echo "  FAIL: T-S53-001 sweep did not write expected cost/tokens"
  FAIL=$((FAIL+1))
fi

# T-S53-002: by_agent for our agent = 0.0125 (the swept run ONLY). r2 is the
# SAME agent with cost 9.99 but no deployment id — if the env filter leaked it
# in, by_agent would read 10.0025. 0.0125 proves both aggregation + exclusion.
# (Total is team+env-wide so it also reflects other platform sandbox runs — not
# asserted here.)
if echo "$RESULT" | grep -q "('s53-cost-agent', 0.0125)"; then
  echo "  PASS: T-S53-002 cost console env-scoped (9.99 non-env run excluded)"
  PASS=$((PASS+1))
else
  echo "  FAIL: T-S53-002 by_agent wrong (non-env 9.99 run may have leaked in)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "=== Suite 53 done: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]

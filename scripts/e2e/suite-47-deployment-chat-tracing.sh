#!/usr/bin/env bash
# scripts/e2e/suite-47-deployment-chat-tracing.sh
#
# E2E Suite 47: Deployment-pinned chat Langfuse trace creation
# Tests T-S47-001 through T-S47-003.
#
# What this proves (the bug: start_deployment_chat created PlaygroundRun/AgentRun
# rows but NEVER called trace_create_run — so deployment-pinned chats, the primary
# "Chat" button on DeploymentOverviewPage, had an empty Trace column and a trace
# that was never opened. start_chat and start_deployment_chat had drifted: only
# one was traced. Fix: shared _create_traced_chat_run helper used by both):
#   T-S47-001 — _create_traced_chat_run populates langfuse_trace_id on the
#               PlaygroundRun (was NULL on the deployment-pinned path).
#   T-S47-002 — the SAME trace_id is written to both the PlaygroundRun and the
#               AgentRun (so /observability + the run list both resolve it).
#   T-S47-003 — cleanup: remove the seeded agent.
#
# Requires Langfuse enabled in the registry-api pod (it is in CPE2E — the pod
# carries LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST). If tracing were disabled the
# helper returns None by design and T-S47-001 would (correctly) report SKIP.
#
# Usage:
#   bash scripts/e2e/suite-47-deployment-chat-tracing.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-47-deployment-chat-tracing.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

cleanup() {
  echo ""
  echo "==> Cleanup..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
try: httpx.delete('http://localhost:8000/api/v1/agents/s47-trace-a', timeout=5)
except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Suite 47: Deployment-pinned chat trace creation ==="
echo "  Pod: $API_POD"
echo ""

# Seed one agent + version so Deployment.version_id FK is satisfiable.
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
httpx.post('http://localhost:8000/api/v1/agents/',
    json={'name': 's47-trace-a', 'team': 'platform', 'agent_type': 'declarative'}, timeout=5)
httpx.post('http://localhost:8000/api/v1/agents/s47-trace-a/versions',
    json={'eval_passed': True, 'adversarial_eval_passed': True}, timeout=5)
" 2>/dev/null || true

echo "[T-S47-001/002] _create_traced_chat_run opens a trace and wires it to both rows"
RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, datetime
from db import AsyncSessionLocal
from sqlalchemy import select
from models import Agent, AgentVersion, Deployment, PlaygroundRun, AgentRun
from routers.chat import _create_traced_chat_run

async def m():
    async with AsyncSessionLocal() as db:
        a = (await db.execute(select(Agent).where(Agent.name == 's47-trace-a'))).scalar_one_or_none()
        if not a:
            print('SKIP: seed agent missing'); return
        va = (await db.execute(
            select(AgentVersion).where(AgentVersion.agent_id == a.id).limit(1))).scalar_one_or_none()
        if not va:
            print('SKIP: agent version missing'); return

        now = datetime.datetime.now(datetime.timezone.utc)
        dep = Deployment(agent_id=a.id, version_id=va.id, environment='sandbox',
                         status='running', k8s_namespace='agents-s47',
                         k8s_deployment_name='s47-trace-a', deployed_at=now)
        db.add(dep)
        await db.flush()

        # The helper commits internally (mirrors the real chat POST path).
        run, agent_run, trace_id = await _create_traced_chat_run(
            db, agent=a, deployment=dep, user_sub='e2e-s47',
            preferred_username='e2e-user', caller_team='platform',
            message='trace me', session_id='s47-sess', context='playground',
            is_production=False,
        )

        # Reload from the DB to prove the trace_id was persisted, not just set in-memory.
        fresh = (await db.execute(
            select(PlaygroundRun).where(PlaygroundRun.id == run.id))).scalar_one()
        fresh_ar = (await db.execute(
            select(AgentRun).where(AgentRun.id == agent_run.id))).scalar_one()

        if trace_id is None:
            print('SKIP: Langfuse disabled in pod (trace_id None)');
        t1 = trace_id is not None and fresh.langfuse_trace_id == trace_id
        t2 = trace_id is not None and fresh_ar.langfuse_trace_id == trace_id

        # cleanup the committed run/agent_run/deployment rows
        await db.delete(fresh); await db.delete(fresh_ar); await db.delete(
            (await db.execute(select(Deployment).where(Deployment.id == dep.id))).scalar_one())
        await db.commit()
        print(f'T1={t1} T2={t2}')

asyncio.run(m())
" 2>/dev/null | tail -1)

echo "    → $RESULT"

PASS=0; FAIL=0
case "$RESULT" in
  *"T1=True T2=True"*) echo "  PASS: T-S47-001 run.langfuse_trace_id populated"; \
                       echo "  PASS: T-S47-002 same trace_id on AgentRun"; PASS=2 ;;
  *SKIP*) echo "  SKIP: $RESULT (Langfuse not enabled / seed missing)" ;;
  *) echo "  FAIL: $RESULT"; FAIL=1 ;;
esac

echo ""
echo "=== Suite 47 done: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]

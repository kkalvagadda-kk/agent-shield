#!/usr/bin/env bash
# scripts/e2e/suite-46-chat-deployment-pinning.sh
#
# E2E Suite 46: Chat deployment pinning (wrong-deployment routing fix)
# Tests T-S46-001 through T-S46-004.
#
# What this proves (the bug: consumer chat re-resolved "most recent running"
# deployment at stream time instead of the deployment the run was pinned to at
# POST time — so a redeploy or a second running deployment routed chat to the
# WRONG pod):
#   T-S46-001 — _deployment_for_run returns the deployment stored on the run,
#               NOT the most-recent running one. A second, newer running
#               deployment must NOT change where an in-flight run streams.
#   T-S46-002 — _pinned_deployment rejects a deployment id that belongs to a
#               different agent (cross-agent routing guard).
#   T-S46-003 — _pinned_deployment accepts a running deployment of the right
#               agent and returns exactly that one.
#   T-S46-004 — Cleanup test agents.
#
# The Deployment + PlaygroundRun rows are inserted, flushed (visible in-session),
# asserted against, then ROLLED BACK — nothing is committed, so no cleanup of
# those rows is needed. Only the API-created agents are deleted at the end.
#
# Usage:
#   bash scripts/e2e/suite-46-chat-deployment-pinning.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-46-chat-deployment-pinning.sh
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
for n in ('s46-pin-a', 's46-pin-b'):
    try: httpx.delete(f'http://localhost:8000/api/v1/agents/{n}', timeout=5)
    except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Suite 46: Chat deployment pinning ==="
echo "  Pod: $API_POD"
echo ""

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; ((PASS++)) || true; }
fail() { echo "  FAIL: $1"; ((FAIL++)) || true; }

# ---------------------------------------------------------------------------
# Seed: two agents (a + b) each with a version, so Deployment.version_id FK is
# satisfiable. Created via the public API (persisted), deleted in cleanup.
# ---------------------------------------------------------------------------
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
for n in ('s46-pin-a', 's46-pin-b'):
    httpx.post('http://localhost:8000/api/v1/agents/',
        json={'name': n, 'team': 'platform', 'agent_type': 'declarative'}, timeout=5)
    httpx.post(f'http://localhost:8000/api/v1/agents/{n}/versions',
        json={'eval_passed': True, 'adversarial_eval_passed': True}, timeout=5)
" 2>/dev/null || true

# ---------------------------------------------------------------------------
# T-S46-001 + 002 + 003 run in one session so flushed (uncommitted) rows are
# visible to the chat helpers, then everything is rolled back.
# ---------------------------------------------------------------------------
echo "[T-S46-001/002/003] pinning helpers resolve the run's own deployment"
RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, datetime, uuid
from db import AsyncSessionLocal
from sqlalchemy import select
from models import Agent, AgentVersion, Deployment, PlaygroundRun
from routers.chat import _deployment_for_run, _running_deployment, _pinned_deployment

async def m():
    async with AsyncSessionLocal() as db:
        a = (await db.execute(select(Agent).where(Agent.name == 's46-pin-a'))).scalar_one_or_none()
        b = (await db.execute(select(Agent).where(Agent.name == 's46-pin-b'))).scalar_one_or_none()
        if not a or not b:
            print('SKIP: seed agents missing'); return
        va = (await db.execute(
            select(AgentVersion).where(AgentVersion.agent_id == a.id).limit(1))).scalar_one_or_none()
        if not va:
            print('SKIP: agent version missing'); return

        now = datetime.datetime.now(datetime.timezone.utc)
        older = now - datetime.timedelta(hours=1)

        # Two RUNNING sandbox deployments of agent a; d_new is more recent.
        d_old = Deployment(agent_id=a.id, version_id=va.id, environment='sandbox',
                           status='running', k8s_namespace='agents-s46',
                           k8s_deployment_name='s46-pin-a-old', deployed_at=older)
        d_new = Deployment(agent_id=a.id, version_id=va.id, environment='sandbox',
                           status='running', k8s_namespace='agents-s46',
                           k8s_deployment_name='s46-pin-a-new', deployed_at=now)
        db.add_all([d_old, d_new])
        await db.flush()

        # A run pinned to the OLDER deployment.
        run = PlaygroundRun(user_id='e2e-s46', agent_name='s46-pin-a', context='playground',
                            sandbox=True, status='running', started_at=now,
                            input_message='x', deployment_id=d_old.id)
        db.add(run)
        await db.flush()

        # T-S46-001: the run must resolve to its OWN (older) deployment, even
        # though a newer running deployment exists. _running_deployment (the old
        # buggy path) would pick d_new — the two MUST differ here or the test is
        # not discriminating.
        pinned = await _deployment_for_run(db, run)
        most_recent = await _running_deployment(db, a.id, context='playground')
        t1 = (pinned is not None and pinned.id == d_old.id
              and most_recent is not None and most_recent.id == d_new.id)

        # T-S46-002: d_old belongs to agent a; pinning it under agent b must fail.
        cross = await _pinned_deployment(db, b, str(d_old.id), is_production=False)
        t2 = cross is None

        # T-S46-003: pinning d_new under its own agent returns exactly d_new.
        ok = await _pinned_deployment(db, a, str(d_new.id), is_production=False)
        t3 = ok is not None and ok.id == d_new.id

        await db.rollback()
        print(f'T1={t1} T2={t2} T3={t3}')

asyncio.run(m())
" 2>/dev/null | tail -1)

echo "    → $RESULT"
case "$RESULT" in
  SKIP*) echo "  SKIP: $RESULT" ;;
  "T1=True T2=True T3=True") pass "T-S46-001/002/003: run pins to its own deployment; cross-agent rejected" ;;
  *) fail "T-S46-001/002/003: $RESULT" ;;
esac

# ---------------------------------------------------------------------------
# T-S46-004: Cleanup handled by the EXIT trap.
# ---------------------------------------------------------------------------
echo "[T-S46-004] Cleanup via trap"
pass "T-S46-004: cleanup scheduled"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Suite 46 Results"
echo "  PASS=$PASS  FAIL=$FAIL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

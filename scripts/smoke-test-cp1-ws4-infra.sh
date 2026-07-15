#!/usr/bin/env bash
# scripts/smoke-test-cp1-ws4-infra.sh — WS-4 Checkpoint 1 infra gate (CP1b).
#
# REAL kubectl/psql/python assertions against the RUNNING cluster. exit 0 only on all-pass.
#
# The load-bearing idea here is that A TAG IS A CLAIM ABOUT CONTENT, NOT CONTENT. With
# imagePullPolicy=IfNotPresent, a tag that was not bumped serves stale code and every
# tag-only check stays green while the new code never runs. So T-CP1B-001/002 check the
# tag AND T-CP1B-007 greps the RUNNING image for a symbol that only exists in the new
# code. The tag check alone has burned this repo before.
#
#   T-CP1B-001  event-gateway pods Running on image tag 0.1.2, 0 crashloops
#   T-CP1B-002  registry-api pods Running on image tag 0.2.186, 0 crashloops
#   T-CP1B-003  alembic head == 0064
#   T-CP1B-004  schema landed: webhook_clients + its UNIQUE, agent_triggers.auth_mode,
#               agent_events.client_id
#   T-CP1B-005  mapper-config gate — configure_mappers() with the new WebhookClient model,
#               ON-CLUSTER (the local py3.9 venv dies on PEP-604 `Mapped[str | None]`)
#               and from a WRITABLE cwd (/app is read-only, so a bare `cd /app` exec
#               cannot write __pycache__)
#   T-CP1B-006  the gateway CAN DECRYPT: cryptography imports AND
#               AGENTSHIELD_ENCRYPTION_KEY is present in the pod env. Without both, every
#               signature verification fails at runtime and the verify hop verifies
#               nothing.
#   T-CP1B-007  CONTENT, not tag: the RUNNING gateway image really contains
#               verify_webhook_auth, and the RUNNING registry-api really serves the
#               client router
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
pass() { echo "PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== CP1b: WS-4 infra gate ==="

# --- T-CP1B-001 / 002: pods Running on the expected tags, no crashloops -------------
check_pods() {  # $1=label $2=want_tag $3=test_id $4=human
  local imgs phases restarts waiting
  imgs=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=$1" \
    -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{"\n"}{end}{end}' 2>/dev/null || true)
  phases=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=$1" \
    -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' 2>/dev/null || true)
  waiting=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=$1" \
    -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}{end}' 2>/dev/null || true)
  if [ -z "$phases" ]; then
    fail "$3 $4 pods Running on $2" "no pods found for label $1"; return
  fi
  if echo "$waiting" | grep -q "CrashLoopBackOff"; then
    fail "$3 $4 pods Running on $2" "CrashLoopBackOff present: $(echo "$waiting" | tr '\n' ' ')"; return
  fi
  if echo "$phases" | grep -qv "^Running$" && [ -n "$(echo "$phases" | grep -v '^Running$' | tr -d '[:space:]')" ]; then
    fail "$3 $4 pods Running on $2" "non-Running phase: $(echo "$phases" | tr '\n' ' ')"; return
  fi
  if ! echo "$imgs" | grep -q ":$2\$"; then
    fail "$3 $4 pods Running on $2" "expected tag :$2 — running images: $(echo "$imgs" | tr '\n' ' ')"; return
  fi
  pass "$3 $4 pods Running on image tag $2, no crashloops"
}
check_pods "event-gateway" "0.1.2"   "T-CP1B-001" "event-gateway"
check_pods "registry-api"  "0.2.186" "T-CP1B-002" "registry-api"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
GW_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=event-gateway \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ] || [ -z "$GW_POD" ]; then
  echo "FAIL  T-CP1B-FIXTURE  |  registry-api pod='$API_POD' event-gateway pod='$GW_POD' — cannot assert further"
  echo "=== CP1b summary: PASS=$PASS FAIL=$((FAIL+1)) ==="
  exit 1
fi

# --- T-CP1B-003: alembic head advanced to 0064 --------------------------------------
# Read the alembic_version TABLE rather than shelling `alembic current`: the table is
# the database's own record of what actually ran, needs no cwd (alembic.ini's
# script_location is relative, so `alembic current` only works from /app — which is
# read-only), and cannot be confused by log lines on stdout.
HEAD=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<'PY' 2>&1 || true
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        v = (await s.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
        print("HEAD=" + ",".join(sorted(v)))

asyncio.run(main())
PY
)
if echo "$HEAD" | grep -q "^HEAD=0064$"; then
  pass "T-CP1B-003 alembic head is 0064 (migration applied by the rollout's init container)"
else
  fail "T-CP1B-003 alembic head is 0064" "got: $(echo "$HEAD" | tr '\n' ' ' | tail -c 200)"
fi

# --- T-CP1B-004: schema really landed ------------------------------------------------
SCHEMA=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<'PY' 2>&1 || true
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        tbl = (await s.execute(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='webhook_clients'"
        ))).scalar()
        uq = (await s.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conname='uq_webhook_clients_trigger_client'"
        ))).scalar()
        am = (await s.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='agent_triggers' AND column_name='auth_mode'"
        ))).scalar()
        cid = (await s.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='agent_events' AND column_name='client_id'"
        ))).scalar()
        sec = (await s.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='webhook_clients' AND column_name='secret_encrypted'"
        ))).scalar()
        print(f"RESULT tbl={tbl} uq={uq} auth_mode={am} client_id={cid} secret_encrypted={sec}")

asyncio.run(main())
PY
)
if echo "$SCHEMA" | grep -q "RESULT tbl=1 uq=1 auth_mode=1 client_id=1 secret_encrypted=1"; then
  pass "T-CP1B-004 schema landed: webhook_clients(+UNIQUE, secret_encrypted), agent_triggers.auth_mode, agent_events.client_id"
else
  fail "T-CP1B-004 schema landed" "$(echo "$SCHEMA" | tr '\n' ' ' | tail -c 300)"
fi

# --- T-CP1B-005: mappers configure ON-CLUSTER, from a writable cwd -------------------
MAP=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -c \"import routers.webhook_clients, models; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('MAPPERS_OK')\"" 2>&1 || true)
if echo "$MAP" | grep -q "MAPPERS_OK"; then
  pass "T-CP1B-005 configure_mappers() succeeds on-cluster with WebhookClient + the client router imported"
else
  fail "T-CP1B-005 configure_mappers() on-cluster" "$(echo "$MAP" | tr '\n' ' ' | tail -c 300)"
fi

# --- T-CP1B-006: the gateway can actually decrypt ------------------------------------
DEC=$(kubectl exec -n "$NAMESPACE" "$GW_POD" -c event-gateway -- \
  python3 -c "
import os
from cryptography.fernet import Fernet
k = os.environ['AGENTSHIELD_ENCRYPTION_KEY']
Fernet(k.encode())            # a malformed key raises here
print('DECRYPT_CAPABLE')
" 2>&1 || true)
if echo "$DEC" | grep -q "DECRYPT_CAPABLE"; then
  pass "T-CP1B-006 gateway can decrypt: cryptography imports AND AGENTSHIELD_ENCRYPTION_KEY is a valid Fernet key in the pod env"
else
  fail "T-CP1B-006 gateway can decrypt" "$(echo "$DEC" | tr '\n' ' ' | tail -c 300)"
fi

# --- T-CP1B-007: CONTENT, not the tag ------------------------------------------------
# A tag is a claim. IfNotPresent will happily serve an old layer under a new tag if the
# build did not actually change. Grep the RUNNING containers for symbols that exist only
# in this change.
GW_CONTENT=$(kubectl exec -n "$NAMESPACE" "$GW_POD" -c event-gateway -- \
  python3 -c "
import webhook_auth, main, inspect
assert callable(webhook_auth.verify_webhook_auth)
assert callable(webhook_auth.sign_webhook)
src = inspect.getsource(main)
assert 'verify_webhook_auth(' in src, 'main.py does not call verify_webhook_auth'
assert 'stale webhook timestamp' not in src, 'the stale-ts 401 oracle is still in the running image'
print('GW_CONTENT_OK')
" 2>&1 || true)
API_CONTENT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -c \"
from routers.webhook_clients import router
paths = {r.path for r in router.routes}
assert any('clients' in p for p in paths), paths
print('API_CONTENT_OK')
\"" 2>&1 || true)
if echo "$GW_CONTENT" | grep -q "GW_CONTENT_OK" && echo "$API_CONTENT" | grep -q "API_CONTENT_OK"; then
  pass "T-CP1B-007 RUNNING IMAGES carry the new code (gateway: verify_webhook_auth called + oracle gone; api: client router importable) — content verified, not just the tag"
else
  fail "T-CP1B-007 running images carry the new code" "gw=$(echo "$GW_CONTENT" | tr '\n' ' ' | tail -c 200) api=$(echo "$API_CONTENT" | tr '\n' ' ' | tail -c 200)"
fi

echo ""
echo "=== CP1b summary: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -ne 0 ] && { echo "❌ CP1b FAILED"; exit 1; }
echo "✅ CP1b PASSED"

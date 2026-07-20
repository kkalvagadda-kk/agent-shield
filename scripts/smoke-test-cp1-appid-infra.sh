#!/usr/bin/env bash
# scripts/smoke-test-cp1-appid-infra.sh — Webhook Application Identity (Decision 30) CP1 infra gate.
#
# REAL kubectl/psql/python assertions against the RUNNING cluster. exit 0 only on all-pass.
#
# A TAG IS A CLAIM ABOUT CONTENT, NOT CONTENT. With imagePullPolicy=IfNotPresent, a tag that
# was not actually rebuilt serves stale code while every tag-only check stays green. So this
# checks the tag AND greps the RUNNING image for symbols that exist only in this change
# (matches the precedent/lesson already captured in scripts/smoke-test-cp1-ws4-infra.sh).
#
#   T-CP1B-APPID-001  registry-api pods Running on image tag 0.2.211, 0 crashloops
#   T-CP1B-APPID-002  alembic head == 0070
#   T-CP1B-APPID-003  schema landed: applications table (+ uq_applications_team_name,
#                     idx_applications_team), widened ck_arg_role/ck_arg_grantee_type CHECKs
#   T-CP1B-APPID-004  mapper-config gate — configure_mappers() with the new Application model
#                     and both new routers importable, ON-CLUSTER, from a writable cwd
#   T-CP1B-APPID-005  CONTENT, not tag: the RUNNING registry-api image really serves both new
#                     route families (/artifacts/.../grants, /teams/{team}/applications)
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
pass() { echo "PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== CP1b: Webhook Application Identity infra gate ==="

# --- T-CP1B-APPID-001: pods Running on the expected tag, no crashloops --------------
check_pods() {  # $1=label $2=want_tag $3=test_id $4=human
  local imgs phases waiting
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
check_pods "registry-api" "0.2.211" "T-CP1B-APPID-001" "registry-api"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FAIL  T-CP1B-APPID-FIXTURE  |  no Running registry-api pod found — cannot assert further"
  echo "=== CP1b summary: PASS=$PASS FAIL=$((FAIL+1)) ==="
  exit 1
fi

# --- T-CP1B-APPID-002: alembic head advanced to 0070 --------------------------------
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
if echo "$HEAD" | grep -q "^HEAD=0070$"; then
  pass "T-CP1B-APPID-002 alembic head is 0070 (0069+0070 applied by the rollout's init container)"
else
  fail "T-CP1B-APPID-002 alembic head is 0070" "got: $(echo "$HEAD" | tr '\n' ' ' | tail -c 200)"
fi

# --- T-CP1B-APPID-003: schema really landed ------------------------------------------
SCHEMA=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<'PY' 2>&1 || true
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        tbl = (await s.execute(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='applications'"
        ))).scalar()
        uq = (await s.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conname='uq_applications_team_name'"
        ))).scalar()
        idx = (await s.execute(text(
            "SELECT count(*) FROM pg_indexes WHERE indexname='idx_applications_team'"
        ))).scalar()
        role_def = (await s.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_arg_role'"
        ))).scalar() or ""
        grantee_def = (await s.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_arg_grantee_type'"
        ))).scalar() or ""
        role_ok = 1 if "invoker" in role_def else 0
        grantee_ok = 1 if "application" in grantee_def else 0
        print(f"RESULT tbl={tbl} uq={uq} idx={idx} role_widened={role_ok} grantee_widened={grantee_ok}")

asyncio.run(main())
PY
)
if echo "$SCHEMA" | grep -q "RESULT tbl=1 uq=1 idx=1 role_widened=1 grantee_widened=1"; then
  pass "T-CP1B-APPID-003 schema landed: applications(+uq_applications_team_name, idx_applications_team), ck_arg_role/ck_arg_grantee_type widened"
else
  fail "T-CP1B-APPID-003 schema landed" "$(echo "$SCHEMA" | tr '\n' ' ' | tail -c 300)"
fi

# --- T-CP1B-APPID-004: mappers configure ON-CLUSTER, from a writable cwd ------------
MAP=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -c \"import routers.artifact_grants, routers.applications, models; from sqlalchemy.orm import configure_mappers; configure_mappers(); assert hasattr(models, 'Application'); print('MAPPERS_OK')\"" 2>&1 || true)
if echo "$MAP" | grep -q "MAPPERS_OK"; then
  pass "T-CP1B-APPID-004 configure_mappers() succeeds on-cluster with Application + both new routers imported"
else
  fail "T-CP1B-APPID-004 configure_mappers() on-cluster" "$(echo "$MAP" | tr '\n' ' ' | tail -c 300)"
fi

# --- T-CP1B-APPID-005: CONTENT, not the tag ------------------------------------------
API_CONTENT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -c \"
from main import app
paths = {r.path for r in app.routes}
assert any('/artifacts/{artifact_type}/{artifact_id}/grants' in p for p in paths), sorted(paths)
assert any('/teams/{team}/applications' in p for p in paths), sorted(paths)
print('API_CONTENT_OK')
\"" 2>&1 || true)
if echo "$API_CONTENT" | grep -q "API_CONTENT_OK"; then
  pass "T-CP1B-APPID-005 RUNNING registry-api image serves both new route families — content verified, not just the tag"
else
  fail "T-CP1B-APPID-005 running image carries the new routes" "$(echo "$API_CONTENT" | tr '\n' ' ' | tail -c 300)"
fi

echo ""
echo "=== CP1b summary: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -ne 0 ] && { echo "❌ CP1b FAILED"; exit 1; }
echo "✅ CP1b PASSED"

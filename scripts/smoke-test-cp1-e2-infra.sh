#!/usr/bin/env bash
# scripts/smoke-test-cp1-e2-infra.sh
#
# Eval v2 E-2 (side-effect record/mock seam) — CP1 INFRA smoke test.
#
# Proves the substrate the seam needs is actually ON THE CLUSTER — the cheap, fast
# check you run before the slow behavioural gate (suite-74), so a stale image or an
# unapplied migration fails in seconds instead of 40 minutes.
#
#   1. the four E-2 images are the expected tags AND their pods are healthy
#      (registry-api, studio, declarative-runner, eval-runner). Tags are READ from
#      scripts/deploy-cpe2e.sh + charts/agentshield/values.yaml — never hardcoded here,
#      so this file cannot drift behind a bump.
#   2. alembic is at 0063 (the E-2 migration)
#   3. both 0063 columns exist: tools.side_effecting + playground_runs.eval_mode
#   4. the classification is actually backfilled (both classes present) — a column of
#      all-false would serve the seam nothing to act on
#
# declarative-runner has no platform Deployment (it is the image agent pods run), so it
# is asserted via the deploy-controller's configured tag + values.yaml; the seam
# actually executing in a real agent pod is what suite-74 proves.
#
# Usage: bash scripts/smoke-test-cp1-e2-infra.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
DEPLOY_SH="$REPO_ROOT/scripts/deploy-cpe2e.sh"
VALUES="$REPO_ROOT/charts/agentshield/values.yaml"

PASS=0; FAIL=0
ok()   { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

tag_of() { grep -E "^$1=" "$DEPLOY_SH" | head -1 | cut -d'"' -f2; }

REGISTRY_API_TAG=$(tag_of REGISTRY_API_TAG)
STUDIO_TAG=$(tag_of STUDIO_TAG)
EVAL_RUNNER_TAG=$(tag_of EVAL_RUNNER_TAG)
DECLARATIVE_RUNNER_TAG=$(tag_of DECLARATIVE_RUNNER_TAG)

echo "=== E-2 CP1 infra smoke test ==="
echo "  expected tags (read from scripts/deploy-cpe2e.sh):"
echo "    registry-api=$REGISTRY_API_TAG studio=$STUDIO_TAG"
echo "    eval-runner=$EVAL_RUNNER_TAG declarative-runner=$DECLARATIVE_RUNNER_TAG"
echo ""

# ---- 1. images + health -------------------------------------------------------------
check_deploy() {
  local dep="$1" want="$2" label="$3"
  local imgs ready
  imgs=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=$label" \
    --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.spec.containers[0].image}{"\n"}{end}' 2>/dev/null || true)
  if [ -z "$imgs" ]; then
    bad "T-E2I-00x $label pod running the expected image" "no Running pod for $label"
    return
  fi
  if echo "$imgs" | grep -q ":${want}$"; then
    ready=$(kubectl get deploy "$dep" -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
    if [ "${ready:-0}" -ge 1 ]; then
      ok "T-E2I-00x $label healthy on :$want" "readyReplicas=$ready"
    else
      bad "T-E2I-00x $label healthy on :$want" "image ok but readyReplicas=${ready:-0}"
    fi
  else
    bad "T-E2I-00x $label pod running the expected image" \
        "want :$want, running: $(echo "$imgs" | tr '\n' ' ')"
  fi
}

check_deploy agentshield-registry-api "$REGISTRY_API_TAG" registry-api
check_deploy agentshield-studio "$STUDIO_TAG" studio

# eval-runner + declarative-runner ship as Job/agent-pod images, configured (not
# deployed) on the platform — assert the CONFIGURED tag matches the intended one.
for pair in "evalRunnerImage:$EVAL_RUNNER_TAG:eval-runner" \
            "declarativeRunnerTag:$DECLARATIVE_RUNNER_TAG:declarative-runner"; do
  key="${pair%%:*}"; rest="${pair#*:}"; want="${rest%%:*}"; label="${rest#*:}"
  if grep -qE "^\s*${key}:\s*\"?[^\"]*${want}\"?\s*$" "$VALUES"; then
    ok "T-E2I-00x $label configured at $want in values.yaml" "$key => $want"
  else
    bad "T-E2I-00x $label configured at $want in values.yaml" \
        "values.yaml $key does not carry $want: $(grep -E "^\s*${key}:" "$VALUES" | head -2 | tr '\n' ' ')"
  fi
done

# ---- 2/3/4. migration + columns + backfill ------------------------------------------
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  bad "T-E2I-00x alembic 0063 + columns" "no registry-api pod to query"
else
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c 'cat > /tmp/e2_infra.py' <<'PY'
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        rev = (await s.execute(text("select version_num from alembic_version"))).scalar()
        print(f"ALEMBIC {rev}")
        cols = dict((await s.execute(text(
            "select table_name||'.'||column_name, data_type from information_schema.columns "
            "where (table_name='tools' and column_name='side_effecting') "
            "or (table_name='playground_runs' and column_name='eval_mode')"))).all())
        print(f"COLS {cols}")
        cls = dict((await s.execute(text(
            "select side_effecting, count(*) from tools group by 1"))).all())
        print(f"CLASSIFICATION {cls}")

asyncio.run(main())
PY
  OUT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    bash -c 'cd /app && PYTHONPATH=/app python3 /tmp/e2_infra.py' 2>&1 || true)
  echo "$OUT" | sed 's/^/    /'

  if echo "$OUT" | grep -q "^ALEMBIC 0063"; then
    ok "T-E2I-00x alembic head is 0063 (the E-2 migration is applied)" \
       "$(echo "$OUT" | grep '^ALEMBIC')"
  else
    bad "T-E2I-00x alembic head is 0063 (the E-2 migration is applied)" \
        "$(echo "$OUT" | grep '^ALEMBIC' || echo 'could not read alembic_version')"
  fi

  if echo "$OUT" | grep -q "tools.side_effecting" && echo "$OUT" | grep -q "playground_runs.eval_mode"; then
    ok "T-E2I-00x both 0063 columns present (tools.side_effecting + playground_runs.eval_mode)" \
       "$(echo "$OUT" | grep '^COLS')"
  else
    bad "T-E2I-00x both 0063 columns present (tools.side_effecting + playground_runs.eval_mode)" \
        "$(echo "$OUT" | grep '^COLS' || echo 'columns query failed')"
  fi

  if echo "$OUT" | grep -q "^CLASSIFICATION" \
     && echo "$OUT" | grep "^CLASSIFICATION" | grep -q "True" \
     && echo "$OUT" | grep "^CLASSIFICATION" | grep -q "False"; then
    ok "T-E2I-00x side_effecting is backfilled with BOTH classes (the seam has real data to read)" \
       "$(echo "$OUT" | grep '^CLASSIFICATION')"
  else
    bad "T-E2I-00x side_effecting is backfilled with BOTH classes (the seam has real data to read)" \
        "$(echo "$OUT" | grep '^CLASSIFICATION' || echo 'classification query failed')"
  fi
fi

echo ""
echo "=== E-2 CP1 infra smoke: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ E-2 CP1 infra smoke FAILED"
  exit 1
fi
echo "✅ E-2 CP1 infra smoke PASSED"

#!/usr/bin/env bash
# scripts/smoke-test-cp1-e3-infra.sh
#
# Eval v2 E-3 (scheduled eval — job_spec datasets + side-effect assertions) — CP1
# INFRA smoke test.
#
# Proves the substrate the scheduled eval needs is actually ON THE CLUSTER — the
# cheap, fast check you run before the slow behavioural gate (suite-75), so a stale
# image or an unapplied migration fails in seconds instead of 40 minutes.
#
#   1. the four E-3 images are the expected tags AND their pods are healthy
#      (registry-api, studio, eval-runner, declarative-runner). Tags are READ from
#      scripts/deploy-cpe2e.sh + charts/agentshield/values.yaml — never hardcoded here,
#      so this file cannot drift behind a bump.
#   2. alembic is at 0063 — E-3 owns NO migration (e3/tasks.md §R3): every column it
#      needs shipped with E-0/E-2. 0063 is therefore the EXPECTED head, and a higher
#      head means someone added a migration E-3 does not own.
#   3. the columns E-3 reads/writes exist: eval_run_results.trigger_payload +
#      .dimension_scores (E-0) and playground_runs.eval_mode + .trigger_payload (E-2)
#   4. playground_datasets.mode actually ADMITS 'scheduled' — the CHECK constraint is
#      what makes a scheduled dataset storable at all; a constraint that never listed
#      it would 500 every author before any of E-3's logic ran
#
# eval-runner + declarative-runner have no platform Deployment (they are Job/agent-pod
# images), so they are asserted via their configured tags in values.yaml; the branch
# actually executing against a real agent pod is what suite-75 proves.
#
# Usage: bash scripts/smoke-test-cp1-e3-infra.sh
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

echo "=== E-3 CP1 infra smoke test ==="
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
    bad "T-E3I-00x $label pod running the expected image" "no Running pod for $label"
    return
  fi
  if echo "$imgs" | grep -q ":${want}$"; then
    ready=$(kubectl get deploy "$dep" -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
    if [ "${ready:-0}" -ge 1 ]; then
      ok "T-E3I-00x $label healthy on :$want" "readyReplicas=$ready"
    else
      bad "T-E3I-00x $label healthy on :$want" "image ok but readyReplicas=${ready:-0}"
    fi
  else
    bad "T-E3I-00x $label pod running the expected image" \
        "want :$want, running: $(echo "$imgs" | tr '\n' ' ')"
  fi
}

check_deploy agentshield-registry-api "$REGISTRY_API_TAG" registry-api
check_deploy agentshield-studio "$STUDIO_TAG" studio

for pair in "evalRunnerImage:$EVAL_RUNNER_TAG:eval-runner" \
            "declarativeRunnerTag:$DECLARATIVE_RUNNER_TAG:declarative-runner"; do
  key="${pair%%:*}"; rest="${pair#*:}"; want="${rest%%:*}"; label="${rest#*:}"
  if grep -qE "^\s*${key}:\s*\"?[^\"]*${want}\"?\s*$" "$VALUES"; then
    ok "T-E3I-00x $label configured at $want in values.yaml" "$key => $want"
  else
    bad "T-E3I-00x $label configured at $want in values.yaml" \
        "values.yaml $key does not carry $want: $(grep -E "^\s*${key}:" "$VALUES" | head -2 | tr '\n' ' ')"
  fi
done

# ---- 1b. what the LAST eval Job actually RAN (drift observation, not a gate) ---------
#
# The two checks above assert the tag eval-runner is CONFIGURED with — they cannot see
# what a Job actually ran, because eval-runner has no platform Deployment to inspect.
# That blind spot is exactly how E-3 shipped undeployed: values.yaml can name a tag whose
# image was never built, and every check stays green. This surfaces the last Job's real
# image so the drift is at least VISIBLE here rather than only in a 40-minute gate.
# Informational (a fresh cluster has no Jobs, and the tag may legitimately have just been
# bumped ahead of a deploy) — suite-75 is the check that actually fails on stale runner
# behaviour.
LAST_EVAL_IMG=$(kubectl get pods -n "$NAMESPACE" -l job-name \
  --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{range .items[*]}{.spec.containers[0].image}{"\n"}{end}' 2>/dev/null \
  | grep "eval-runner" | tail -1 || true)
if [ -n "$LAST_EVAL_IMG" ]; then
  if echo "$LAST_EVAL_IMG" | grep -q ":${EVAL_RUNNER_TAG}$"; then
    echo "  OBSERVED last eval Job ran $LAST_EVAL_IMG (matches the configured tag)"
  else
    echo "  ⚠️  OBSERVED last eval Job ran $LAST_EVAL_IMG but values.yaml configures :$EVAL_RUNNER_TAG"
    echo "      → if that tag was never built+pushed, the NEXT Job will run the OLD code too."
    echo "        Run: bash scripts/deploy-cp1-e3.sh   (build+deploy via deploy-cpe2e.sh)"
  fi
else
  echo "  OBSERVED no eval Job pods on this cluster yet (nothing to compare)"
fi

# ---- 2/3/4. migration head + columns + the scheduled mode CHECK ----------------------
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  bad "T-E3I-00x alembic 0063 + columns" "no registry-api pod to query"
else
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c 'cat > /tmp/e3_infra.py' <<'PY'
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        rev = (await s.execute(text("select version_num from alembic_version"))).scalar()
        print(f"ALEMBIC {rev}")
        # E-3 owns no migration: every column it touches shipped with E-0/E-2.
        cols = sorted(dict((await s.execute(text(
            "select table_name||'.'||column_name, data_type from information_schema.columns "
            "where (table_name='eval_run_results' and column_name in "
            "      ('trigger_payload','dimension_scores','eval_detail','run_id')) "
            "or (table_name='playground_runs' and column_name in "
            "      ('eval_mode','trigger_type','trigger_payload','input_payload'))"))).all()))
        print(f"COLS {cols}")
        # The CHECK on playground_datasets.mode is what makes a scheduled dataset
        # storable. Read the real constraint text rather than trusting the model.
        chk = (await s.execute(text(
            "select pg_get_constraintdef(c.oid) from pg_constraint c "
            "join pg_class t on t.oid = c.conrelid "
            "where t.relname='playground_datasets' and c.contype='c' "
            "and pg_get_constraintdef(c.oid) like '%mode%'"))).scalars().all()
        print(f"MODECHECK {chk}")

asyncio.run(main())
PY
  OUT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    bash -c 'cd /app && PYTHONPATH=/app python3 /tmp/e3_infra.py' 2>&1 || true)
  echo "$OUT" | sed 's/^/    /'

  if echo "$OUT" | grep -q "^ALEMBIC 0063"; then
    ok "T-E3I-00x alembic head is 0063 (E-3 owns NO migration — every column it needs shipped with E-0/E-2)" \
       "$(echo "$OUT" | grep '^ALEMBIC')"
  else
    bad "T-E3I-00x alembic head is 0063 (E-3 owns NO migration — every column it needs shipped with E-0/E-2)" \
        "$(echo "$OUT" | grep '^ALEMBIC' || echo 'could not read alembic_version') — a different head means either the E-2 migration is unapplied or someone added a migration E-3 does not own"
  fi

  MISSING_COLS=""
  for col in eval_run_results.trigger_payload eval_run_results.dimension_scores \
             eval_run_results.eval_detail eval_run_results.run_id \
             playground_runs.eval_mode playground_runs.trigger_type \
             playground_runs.trigger_payload playground_runs.input_payload; do
    echo "$OUT" | grep -q "$col" || MISSING_COLS="$MISSING_COLS $col"
  done
  if [ -z "$MISSING_COLS" ]; then
    ok "T-E3I-00x every column the scheduled eval reads/writes is present (E-0 eval_run_results.* + E-2 playground_runs.*)" \
       "$(echo "$OUT" | grep '^COLS')"
  else
    bad "T-E3I-00x every column the scheduled eval reads/writes is present" \
        "MISSING:$MISSING_COLS"
  fi

  if echo "$OUT" | grep "^MODECHECK" | grep -q "scheduled"; then
    ok "T-E3I-00x playground_datasets.mode CHECK admits 'scheduled' (a scheduled dataset is storable)" \
       "$(echo "$OUT" | grep '^MODECHECK')"
  else
    bad "T-E3I-00x playground_datasets.mode CHECK admits 'scheduled' (a scheduled dataset is storable)" \
        "$(echo "$OUT" | grep '^MODECHECK' || echo 'constraint query failed')"
  fi
fi

echo ""
echo "=== E-3 CP1 infra smoke: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ E-3 CP1 infra smoke FAILED"
  exit 1
fi
echo "✅ E-3 CP1 infra smoke PASSED"

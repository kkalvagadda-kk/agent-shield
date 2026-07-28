#!/usr/bin/env bash
# scripts/checkpoints/cp3a-slice0-mvp-gate.sh
#
# Eval Slice 0 MVP gate. Asserts the things a green test run does NOT:
#
#   1. the SERVED artifacts carry the code (a tag bumped but never built shipped an
#      ImagePullBackOff this very slice — docs/bugs/three-tag-sites-eks-build-vs-helm-deploy.md);
#   2. no new symbol is an orphan (DoD rule 3);
#   3. the gap ledger still carries an OPEN entry for the deferred item — a ledger
#      that quietly closes a deferral is worse than no ledger;
#   4. the blast-radius neighbours pass, not just the new tests.
#
# Read-only except for the test runs it delegates to. Never deploys — if the live
# tags are wrong it FAILS and tells you to run scripts/deploy-eks.sh, because a
# checkpoint that silently redeploys hides the drift it exists to catch.
#
# Usage: KUBECONFIG=~/.kube/test-cluster-kube-config.yaml bash scripts/checkpoints/cp3a-slice0-mvp-gate.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; REPO_ROOT="$(dirname "$REPO_ROOT")"
cd "$REPO_ROOT"

P=0; F=0
pass() { echo "PASS  $1"; [ -n "${2:-}" ] && echo "        $2"; P=$((P+1)); }
fail() { echo "FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; F=$((F+1)); }

echo "=== CP3a: Eval Slice 0 MVP gate ==="
echo ""

# --------------------------------------------------------------------------
# 1. The three tag sites agree, and the LIVE pods run them.
#    deploy-eks.sh drives the BUILD, values.yaml drives the DEPLOY. When they
#    drift, helm reports "deployed" and the pod sits in ImagePullBackOff.
# --------------------------------------------------------------------------
api_cpe2e=$(grep -oE '^REGISTRY_API_TAG="[^"]+"' scripts/deploy-cpe2e.sh | head -1 | cut -d'"' -f2)
api_eks=$(grep -oE '^REGISTRY_API_TAG="[^"]+"' scripts/deploy-eks.sh | head -1 | cut -d'"' -f2)
studio_cpe2e=$(grep -oE '^STUDIO_TAG="[^"]+"' scripts/deploy-cpe2e.sh | head -1 | cut -d'"' -f2)
studio_eks=$(grep -oE '^STUDIO_TAG="[^"]+"' scripts/deploy-eks.sh | head -1 | cut -d'"' -f2)
studio_build=$(grep -oE 'STUDIO_BUILD = "[^"]+"' studio/src/lib/build.ts | cut -d'"' -f2)

if [ "$api_cpe2e" = "$api_eks" ] && [ "$studio_cpe2e" = "$studio_eks" ] && [ "$studio_eks" = "$studio_build" ]; then
  pass "T-CP3A-001 all tag sites agree" \
       "registry-api=$api_eks studio=$studio_eks (deploy-cpe2e.sh == deploy-eks.sh == build.ts)"
else
  fail "T-CP3A-001 tag sites disagree — the build and the deploy will target different images" \
       "deploy-cpe2e.sh: api=$api_cpe2e studio=$studio_cpe2e
        deploy-eks.sh:   api=$api_eks studio=$studio_eks
        build.ts:        studio=$studio_build"
fi

live_api=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].spec.containers[0].image}' 2>/dev/null | sed 's/.*://')
live_studio=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].spec.containers[0].image}' 2>/dev/null | sed 's/.*://')
if [ "$live_api" = "$api_eks" ] && [ "$live_studio" = "$studio_eks" ]; then
  pass "T-CP3A-002 the LIVE pods run the tags this checkout declares" "api=$live_api studio=$live_studio"
else
  fail "T-CP3A-002 live pods do not match the declared tags — run scripts/deploy-eks.sh" \
       "live: api=$live_api studio=$live_studio | declared: api=$api_eks studio=$studio_eks"
fi

# --------------------------------------------------------------------------
# 2. The SERVED backend carries the code, not just the tag.
# --------------------------------------------------------------------------
POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$POD" ]; then
  fail "T-CP3A-003 no Running registry-api pod — cannot verify served code"
else
  # Assert on DISTINCTIVE strings, one grep per file, counted in bash. An earlier
  # version of this check piped through `bc` — which is not installed in the
  # container — so it silently evaluated to 0 and failed a pod that was correct.
  # A gate that fails for its own reasons trains people to ignore it.
  n_prov=$(kubectl exec -n "$NAMESPACE" "$POD" -c registry-api -- \
    sh -c 'grep -c "agent_latest" /app/routers/admin.py' 2>/dev/null | tr -d '\r' || echo 0)
  n_deny_ev=$(kubectl exec -n "$NAMESPACE" "$POD" -c registry-api -- \
    sh -c 'grep -c "sa.false()" /app/routers/eval_runner.py' 2>/dev/null | tr -d '\r' || echo 0)
  n_deny_ds=$(kubectl exec -n "$NAMESPACE" "$POD" -c registry-api -- \
    sh -c 'grep -c "sa.false()" /app/routers/datasets.py' 2>/dev/null | tr -d '\r' || echo 0)
  if [ "${n_prov:-0}" -ge 1 ] && [ "${n_deny_ev:-0}" -ge 1 ] && [ "${n_deny_ds:-0}" -ge 1 ]; then
    pass "T-CP3A-003 the RUNNING pod carries Slice 0" \
         "admin.py agent_latest=$n_prov | deny-by-default: eval_runner=$n_deny_ev datasets=$n_deny_ds (BOTH routes required)"
  else
    fail "T-CP3A-003 the running image does NOT carry Slice 0 — a tag was bumped without a rebuild" \
         "admin.py agent_latest=$n_prov (want >=1) | eval_runner sa.false()=$n_deny_ev (want >=1) | datasets sa.false()=$n_deny_ds (want >=1)"
  fi
fi

# --------------------------------------------------------------------------
# 3. No orphans (DoD rule 3): every new symbol has a reader OUTSIDE its definition.
# --------------------------------------------------------------------------
orphans=""
# NOTE the explicit `if` + `return 0`. Written first as `[ cond ] && orphans=...`,
# which returns NON-ZERO whenever the condition is false — and as the last command
# in a function under `set -e`, that aborted the whole gate after the first symbol
# with a reader. The gate reported three passes and silently skipped everything
# after it. A checkpoint that exits early looks identical to one that passed.
check_reader() { # $1 symbol, $2 path fragment of the file that DEFINES it
  local n
  n=$(grep -rl "$1" studio/src services/registry-api 2>/dev/null | grep -v "$2" | wc -l | tr -d ' ')
  if [ "${n:-0}" -eq 0 ]; then
    orphans="${orphans} $1"
  fi
  return 0
}
check_reader "verdictOf"              "studio/src/lib/evalVerdict"
check_reader "thresholdLabel"         "studio/src/lib/evalVerdict"
check_reader "passesGate"             "studio/src/lib/evalVerdict"
check_reader "StatCard"               "studio/src/components/shared/StatCard.tsx"
check_reader "last_eval_pass_threshold" "services/registry-api/schemas.py"
check_reader "eval_source"            "services/registry-api/schemas.py"
if [ -z "$orphans" ]; then
  pass "T-CP3A-004 every new symbol has a live reader outside its definition"
else
  fail "T-CP3A-004 orphaned symbol(s) — built but never wired (DoD rule 3)" "$orphans"
fi

# --------------------------------------------------------------------------
# 4. The ledger still carries the DEFERRED item, open.
#    A ledger that quietly closes a deferral is how debt becomes a surprise.
# --------------------------------------------------------------------------
LEDGER="docs/testing/manual-ui-e2e-test-plan.md"
if grep -q "team-scoped" "$LEDGER" && grep -q "Decision 33 option B" "$LEDGER"; then
  pass "T-CP3A-005 Decision 33 option B (team-scoped eval reads) is still recorded as deferred"
else
  fail "T-CP3A-005 the deferred item vanished from the ledger" \
       "Decision 33 option B must stay visible until it is built or explicitly dropped."
fi

# --------------------------------------------------------------------------
# 5. Both new suites are registered, else they run in NO group and NO full run.
# --------------------------------------------------------------------------
if grep -q "suite-89-publish-queue-verdict.sh" scripts/test-manifest.txt \
   && grep -q "eval-verdict-publish-queue.spec.ts" scripts/test-manifest.txt; then
  pass "T-CP3A-006 the new API suite and browser spec are both registered"
else
  fail "T-CP3A-006 a new test is unregistered — it will run in no group and no full run" \
       "$(grep -nE 'suite-89|eval-verdict-publish-queue' scripts/test-manifest.txt || echo '  (neither found)')"
fi

echo ""
echo "=== CP3a: PASS=$P FAIL=$F ==="
echo ""
echo "Not asserted here (run them separately — they are slow and have their own output):"
echo "  bash scripts/e2e/suite-89-publish-queue-verdict.sh"
echo "  bash scripts/run-tests.sh --layer api     --group eval"
echo "  bash scripts/run-tests.sh --layer browser --group eval"
echo "  cd studio && npm run typecheck && npm run test"
exit $([ "$F" -eq 0 ] && echo 0 || echo 1)

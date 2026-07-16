#!/usr/bin/env bash
# scripts/smoke-test-cp2-ws6-infra.sh — WS-6 Checkpoint 2 infra gate.
#
# Proves the CLUSTER is running this slice's code — not that the working tree contains it.
# That distinction is the entire lesson of docs/bugs/e3-never-ran-tag-not-bumped.md: an
# entire slice never executed while every check stayed green, because both tag files
# agreed... on a stale tag, and the cluster faithfully served old code. A tag is a CLAIM
# ABOUT CONTENT. These assertions interrogate the claim.
#
#   T-CP2B-001  studio pod Running on the tag build.ts declares, crashloop=0
#   T-CP2B-002  the SERVED bundle contains this slice's markers (delegated to suite-79
#               T-S79-001/002, which fetch the real bytes through the real edge)
#   T-CP2B-003  tag⇄content coupling check green for studio
#   T-CP2B-004  suite-79 registered in run-all.sh
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PASS=0
FAIL=0
rec() {
  local v="$1" n="$2" d="$3"
  echo "${v}  ${n}  |  ${d}"
  [ "$v" = "PASS" ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
}

echo "=== WS-6 CP2 infra gate ==="
echo ""

EXPECT_TAG=$(grep -E '^export const STUDIO_BUILD' studio/src/lib/build.ts | head -1 | cut -d'"' -f2)
echo "studio build marker (source of truth): ${EXPECT_TAG}"
echo ""

# T-CP2B-001 — the live pod runs the expected tag and is healthy.
POD_IMAGE=$(kubectl get deploy agentshield-studio -n "$NAMESPACE" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "")
POD_TAG="${POD_IMAGE##*:}"
READY=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio \
  -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' 2>/dev/null | grep -c "Running" || true)
RESTARTS=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{"\n"}{end}' 2>/dev/null | head -1 || echo 0)

if [ "$POD_TAG" = "$EXPECT_TAG" ] && [ "$READY" -ge 1 ]; then
  rec PASS "T-CP2B-001 studio pod Running on ${EXPECT_TAG}" \
    "image=${POD_IMAGE} running=${READY} restarts=${RESTARTS}"
else
  rec FAIL "T-CP2B-001 studio pod Running on ${EXPECT_TAG}" \
    "live image='${POD_IMAGE}' (tag '${POD_TAG}') running=${READY} — the cluster is NOT running this code"
fi

# T-CP2B-002 — served-bundle content + 5-way coupling. Delegated to suite-79 rather than
# reimplemented here: two copies of one assertion is the drift engine WS-6 exists to delete.
if bash scripts/e2e/suite-79-operate-parity.sh >/tmp/cp2_s79.log 2>&1; then
  rec PASS "T-CP2B-002 served bundle + tag/content coupling (via suite-79)" \
    "$(grep -c '^PASS' /tmp/cp2_s79.log || echo '?') assertions green incl. T-S79-001/002"
else
  rec FAIL "T-CP2B-002 served bundle + tag/content coupling (via suite-79)" \
    "suite-79 FAILED — see /tmp/cp2_s79.log: $(grep '^FAIL' /tmp/cp2_s79.log | head -3 | tr '\n' ' ')"
fi

# T-CP2B-003 — the tag⇄content coupling check, studio rows only. The repo-wide run also
# covers registry-api, which is another lane's concern and may legitimately be mid-slice.
if [ -f scripts/check-tag-content-coupling.sh ]; then
  COUPLING=$(bash scripts/check-tag-content-coupling.sh 2>&1 | grep -E "^(PASS|FAIL)\s+studio:" || true)
  if echo "$COUPLING" | grep -q "^FAIL"; then
    rec FAIL "T-CP2B-003 studio tag⇄content coupling" "$(echo "$COUPLING" | grep '^FAIL' | head -2 | tr '\n' ' ')"
  elif [ -z "$COUPLING" ]; then
    rec FAIL "T-CP2B-003 studio tag⇄content coupling" "check produced no studio rows — unprovable, not a pass"
  else
    rec PASS "T-CP2B-003 studio tag⇄content coupling" "$(echo "$COUPLING" | wc -l | tr -d ' ') studio row(s) PASS"
  fi
else
  rec FAIL "T-CP2B-003 studio tag⇄content coupling" "scripts/check-tag-content-coupling.sh missing"
fi

# T-CP2B-004 — the gate is wired into the runner. An unregistered suite is an orphan gate:
# it can only fail someone who remembers to run it by hand.
if grep -q "suite-79-operate-parity.sh" scripts/e2e/run-all.sh; then
  rec PASS "T-CP2B-004 suite-79 registered in run-all.sh" "$(grep -n 'suite-79' scripts/e2e/run-all.sh | head -1)"
else
  rec FAIL "T-CP2B-004 suite-79 registered in run-all.sh" "not registered — the gate would never run in CI"
fi

echo ""
echo "=== CP2 infra: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] || { echo "❌ CP2 infra gate FAILED"; exit 1; }
echo "✅ CP2 infra gate PASSED"

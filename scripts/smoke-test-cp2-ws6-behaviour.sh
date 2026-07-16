#!/usr/bin/env bash
# scripts/smoke-test-cp2-ws6-behaviour.sh — WS-6 Checkpoint 2 behaviour gate.
#
# Content-grep proves PRESENCE, never CORRECTNESS. suite-79 greps the served bundle; this
# gate drives the actual UI in a real browser. Both are needed and neither substitutes for
# the other: a grep passes against dead code, and a route can be stolen out from under a
# component while every marker still matches.
#
#   typecheck + Vitest (318 green at WS-6)
#   Playwright approvals-badge.spec.ts        — count → route → reload (real backend)
#   Playwright catalog-overview-parity.spec.ts — the SAME shared overview testid on BOTH
#                                                pages (the parity proof)
#   suite-79                                   — parity grep + served bundle + coupling
#
# Playwright is a SEPARATE gate from run-all.sh and needs the https gateway (Secure
# Keycloak cookies break over an http port-forward).
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PASS=0
FAIL=0
rec() {
  local v="$1" n="$2" d="$3"
  echo "${v}  ${n}  |  ${d}"
  [ "$v" = "PASS" ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
}

echo "=== WS-6 CP2 behaviour gate ==="
echo ""

# 1. Typecheck.
if (cd studio && npm run typecheck >/tmp/cp2_tsc.log 2>&1); then
  rec PASS "T-CP2C-001 studio typecheck" "tsc --noEmit clean"
else
  rec FAIL "T-CP2C-001 studio typecheck" "$(tail -5 /tmp/cp2_tsc.log | tr '\n' ' ')"
fi

# 2. Vitest. Do NOT delete or skip a test to make this pass (CLAUDE.md).
if (cd studio && npm run test >/tmp/cp2_vitest.log 2>&1); then
  rec PASS "T-CP2C-002 Vitest" "$(grep -oE 'Tests +[0-9]+ passed' /tmp/cp2_vitest.log | tail -1)"
else
  rec FAIL "T-CP2C-002 Vitest" "$(grep -E '✗|FAIL|Tests ' /tmp/cp2_vitest.log | tail -3 | tr '\n' ' ')"
fi

# 3+4. Playwright — the two WS-6 specs. Scoped to this slice's specs on purpose: the full
# suite carries pre-existing failures from other lanes (e.g. deployment-overview.spec.ts
# drives the REMOVED /agents/:name/deploy route; the eval-* specs move with registry-api),
# and folding those in would make this gate report other people's breakage as WS-6's.
# They are recorded in the gap ledger instead of being silently absorbed here.
for spec in approvals-badge catalog-overview-parity; do
  if bash scripts/studio-e2e.sh "e2e/${spec}.spec.ts" >"/tmp/cp2_pw_${spec}.log" 2>&1; then
    rec PASS "T-CP2C-003 Playwright ${spec}" "$(grep -oE '[0-9]+ passed' "/tmp/cp2_pw_${spec}.log" | tail -1)"
  else
    rec FAIL "T-CP2C-003 Playwright ${spec}" "$(grep -E '✘|failed' "/tmp/cp2_pw_${spec}.log" | head -3 | tr '\n' ' ')"
  fi
done

# 5. suite-79.
if bash scripts/e2e/suite-79-operate-parity.sh >/tmp/cp2_s79b.log 2>&1; then
  rec PASS "T-CP2C-004 suite-79 operate parity" "$(grep 'summary' /tmp/cp2_s79b.log | tail -1)"
else
  rec FAIL "T-CP2C-004 suite-79 operate parity" "$(grep '^FAIL' /tmp/cp2_s79b.log | head -3 | tr '\n' ' ')"
fi

echo ""
echo "=== CP2 behaviour: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] || { echo "❌ CP2 behaviour gate FAILED"; exit 1; }
echo "✅ CP2 behaviour gate PASSED"

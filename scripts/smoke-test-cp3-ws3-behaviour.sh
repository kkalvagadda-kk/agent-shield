#!/usr/bin/env bash
# scripts/smoke-test-cp3-ws3-behaviour.sh
#
# WS-3 Checkpoint 3 — BEHAVIOUR smoke (CP3c). Proves the operate surface behaves
# and the full scheduled slice is green:
#
#   (1) studio typecheck + Vitest (incl. OverviewScheduled.test.tsx) green
#   (2) Playwright scheduled-overview.spec.ts — operate render + save->reload->assert
#       the alert config persisted (runs via scripts/studio-e2e.sh against the
#       deployed 0.1.136 studio)
#   (3) suite-71-scheduled-e2e.sh fully green (T-S71-000..005) — real scheduled
#       durable daemon agent + workflow + async reviewer resume + alert-on-failure
#   (4) the gap-analysis TODO-2 correction is in place (alerting marked shipped)
#
# exit 0 only if every step passes. Run AFTER the studio 0.1.136 deploy (CP3a).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== WS-3 CP3c: behaviour smoke ==="
echo ""

# ── (1) studio typecheck + Vitest ─────────────────────────────────────────────
echo "--- (1) studio typecheck + Vitest ---"
if ( cd "$REPO_ROOT/studio" && npm run typecheck && npm run test ); then
  ok "T-CP3C-001 studio typecheck + Vitest" "tsc --noEmit clean + vitest run green"
else
  bad "T-CP3C-001 studio typecheck + Vitest" "typecheck or vitest failed"
fi

# ── (2) Playwright scheduled-overview spec ────────────────────────────────────
echo "--- (2) Playwright scheduled-overview.spec.ts ---"
if bash "$REPO_ROOT/scripts/studio-e2e.sh" e2e/scheduled-overview.spec.ts; then
  ok "T-CP3C-002 scheduled-overview Playwright" "operate render + save->reload->assert alert config"
else
  bad "T-CP3C-002 scheduled-overview Playwright" "scheduled-overview.spec.ts failed"
fi

# ── (3) suite-71 full ─────────────────────────────────────────────────────────
echo "--- (3) suite-71-scheduled-e2e.sh (T-S71-000..005) ---"
if bash "$REPO_ROOT/scripts/e2e/suite-71-scheduled-e2e.sh"; then
  ok "T-CP3C-003 suite-71 scheduled e2e" "all T-S71-000..005 green"
else
  bad "T-CP3C-003 suite-71 scheduled e2e" "suite-71 failed"
fi

# ── (4) gap-analysis TODO-2 correction present ────────────────────────────────
echo "--- (4) gap-analysis TODO-2 alerting-shipped correction ---"
GAP="$REPO_ROOT/docs/design/todo/execution-models-gap-analysis.md"
if grep -q "shipped" "$GAP" && grep -q "dispatch_failure_alert" "$GAP"; then
  ok "T-CP3C-004 TODO-2 alerting shipped" "$(grep -n 'shipped' "$GAP" | head -1)"
else
  bad "T-CP3C-004 TODO-2 alerting shipped" "expected 'shipped' + 'dispatch_failure_alert' in gap-analysis"
fi

echo ""
echo "=== CP3c behaviour smoke: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] || exit 1

#!/usr/bin/env bash
# scripts/smoke-test-cp1-e2-behaviour.sh
#
# Eval v2 E-2 (side-effect record/mock seam) — CP1 BEHAVIOUR smoke test.
#
# Runs the E-2 acceptance gate: scripts/e2e/suite-74-eval-v2-side-effects.sh — the
# NO-FAKES proof that a real durable eval in `eval_mode=record` against a real deployed
# agent pod RECORDS a real side-effecting tool call and returns a mock INSTEAD of
# invoking it, while a live control run genuinely delivers it, and that the real
# score_side_effects scorer scores the real recorded calls.
#
# Infra first: the gate takes tens of minutes, so a stale image or an unapplied
# migration should fail in seconds. This runs the infra smoke test first and stops on
# failure rather than burning the window.
#
# Usage: bash scripts/smoke-test-cp1-e2-behaviour.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== E-2 CP1 behaviour smoke — infra precondition ==="
bash "$REPO_ROOT/scripts/smoke-test-cp1-e2-infra.sh"

echo ""
echo "=== E-2 CP1 behaviour smoke — the no-fakes gate (suite-74) ==="
bash "$REPO_ROOT/scripts/e2e/suite-74-eval-v2-side-effects.sh"

echo ""
echo "✅ E-2 CP1 behaviour smoke PASSED (suite-74 green)"

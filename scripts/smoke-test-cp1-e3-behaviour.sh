#!/usr/bin/env bash
# scripts/smoke-test-cp1-e3-behaviour.sh
#
# Eval v2 E-3 (scheduled eval — job_spec datasets + side-effect assertions) — CP1
# BEHAVIOUR smoke test.
#
# Runs the E-3 acceptance gate: scripts/e2e/suite-75-eval-v2-scheduled.sh — the
# NO-FAKES proof that a REAL scheduled dataset launches against a REAL agent with a
# REAL armed schedule trigger (it 422'd universally before E-3), that the REAL
# eval-runner Job drives a REAL durable run of a REAL deployed daemon pod WITH THE JOB
# SPEC AS THE INPUT, that the write is RECORDED rather than delivered, that the real
# scorers persist real dimension_scores re-read from the DB — and that the REAL
# /internal/runs/start scheduled door still DELIVERS live (the no-fake-schedule
# control).
#
# Infra first: the gate takes tens of minutes, so a stale image or an unapplied
# migration should fail in seconds. This runs the infra smoke test first and stops on
# failure rather than burning the window.
#
# Usage: bash scripts/smoke-test-cp1-e3-behaviour.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== E-3 CP1 behaviour smoke — infra precondition ==="
bash "$REPO_ROOT/scripts/smoke-test-cp1-e3-infra.sh"

echo ""
echo "=== E-3 CP1 behaviour smoke — the no-fakes gate (suite-75) ==="
bash "$REPO_ROOT/scripts/e2e/suite-75-eval-v2-scheduled.sh"

echo ""
echo "✅ E-3 CP1 behaviour smoke PASSED (suite-75 green)"

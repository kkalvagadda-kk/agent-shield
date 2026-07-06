#!/usr/bin/env bash
# Checkpoint CP-Wc — Full end-to-end (Decision 22, phase W5)
# Builds the Decision-22 images, deploys the full platform, then proves the
# composite-workflow feature + no regression: suite-29 green, full e2e suite
# green, Studio TypeScript clean.
#
# Tags (bumped for Decision 22): registry-api 0.2.59, studio 0.1.43,
# declarative-runner 0.1.7 (all wired in scripts/deploy-cpe2e.sh + values.yaml).
#
# Usage: bash scripts/smoke-test-cp-wc-full-e2e.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
echo "=== Checkpoint CP-Wc: Full E2E (Decision 22) ==="

echo "[1/4] Studio TypeScript type-check..."
( cd studio && npx tsc --noEmit )
echo "  ok: tsc clean"

echo "[2/4] Build + deploy full platform (all images + helm upgrade)..."
bash scripts/deploy-cpe2e.sh
kubectl rollout status deploy/agentshield-registry-api -n agentshield-platform --timeout=180s
kubectl rollout status deploy/agentshield-studio       -n agentshield-platform --timeout=180s

echo "[3/4] Composite-workflow suite (suite-29)..."
bash scripts/e2e/suite-29-workflow-composite.sh

echo "[4/4] Full regression (all suites, incl. suite-29)..."
bash scripts/e2e/run-all.sh

echo "=== CP-Wc complete ==="
echo "PASS"

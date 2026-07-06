#!/usr/bin/env bash
# deploy-cp3-full-platform.sh — Execution-modes Checkpoint 3 (Full Platform).
#
# NOTE ON NAMING: `deploy-cp3.sh` already exists and means a DIFFERENT CP3 — it
# enables the Safety Orchestrator + scanners (the original platform checkpoint
# sequence CP1 infra → CP2 deploy → CP3 safety). This script is the
# execution-modes roadmap's CP3: prove scheduler + alerting + event-gateway
# together. Kept as a separate file to avoid clobbering the safety CP3.
#
# Reuses the canonical deploy (deploy-cpe2e.sh — builds all images + helm
# upgrade), then waits for the CP3-critical components (scheduler 2 replicas,
# event-gateway) to be Ready.
#
# Usage:
#   bash scripts/deploy-cp3-full-platform.sh
set -euo pipefail

NAMESPACE="agentshield-platform"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> CP3 Full-Platform Deploy (execution-modes) — $(date)"
echo "[1/2] Running canonical deploy (all images + helm upgrade)..."
bash scripts/deploy-cpe2e.sh

echo ""
echo "[2/2] Waiting for CP3-critical components..."
kubectl rollout status deploy/agentshield-registry-api  -n "$NAMESPACE" --timeout=180s
kubectl rollout status deploy/agentshield-scheduler      -n "$NAMESPACE" --timeout=180s
kubectl rollout status deploy/agentshield-event-gateway  -n "$NAMESPACE" --timeout=180s

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CP3 full-platform deploy complete."
echo "  Next: bash scripts/smoke-test-cp3-infra.sh && bash scripts/smoke-test-cp3-behaviour.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

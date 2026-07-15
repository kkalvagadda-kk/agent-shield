#!/usr/bin/env bash
# scripts/deploy-cp3-ws3.sh
#
# WS-3 Checkpoint 3 deploy wrapper (CP3a).
#
# Scope of CP3 (Execution Models v2 · WS-3 · Phases 6–7 — the operate surface):
#   * OverviewScheduled.tsx now renders next-fire + a rolled-up schedule-health
#     badge from the existing GET /agents/{name}/health producer, plus an
#     alert-config summary (alert_email / alert_on_failure) read off the trigger.
#   * studio-only image bump 0.1.135 -> 0.1.136 (registry-api 0.2.179 and
#     declarative-runner 0.1.44 are UNCHANGED — no backend/runner change in WS-3).
#
# This is a THIN, idempotent wrapper. It does NOT run bare helm/docker/kubectl for
# the deploy — the canonical build+push+helm is scripts/deploy-cpe2e.sh (the
# studio tag is already bumped to 0.1.136 in BOTH deploy-cpe2e.sh and
# charts/agentshield/values.yaml). Re-running is safe: same tag => no-op image,
# helm upgrade converges.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== WS-3 CP3 deploy (scheduled operate surface — studio 0.1.136) ==="
echo "  namespace: $NAMESPACE"
echo "  studio tag: 0.1.136 (read from scripts/deploy-cpe2e.sh + values.yaml)"
echo "  registry-api: 0.2.179 (unchanged) · declarative-runner: 0.1.44 (unchanged)"
echo ""

echo "--> Building + deploying via canonical scripts/deploy-cpe2e.sh ..."
bash scripts/deploy-cpe2e.sh

echo ""
echo "--> Waiting for studio rollout ..."
kubectl rollout status deploy/agentshield-studio -n "$NAMESPACE" --timeout=300s

echo ""
echo "=== CP3 deploy complete. Now run: ==="
echo "  bash scripts/smoke-test-cp3-ws3-infra.sh && bash scripts/smoke-test-cp3-ws3-behaviour.sh"

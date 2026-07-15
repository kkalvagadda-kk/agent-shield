#!/usr/bin/env bash
# scripts/deploy-cp1-e2.sh
#
# Eval v2 E-2 (side-effect record/mock seam) — CP1 deploy wrapper.
#
# Delegates to scripts/deploy-cpe2e.sh (the ONE build+deploy path — never bare
# helm/docker/kubectl) and then waits for the rollouts E-2 depends on. It does NOT
# bump any image tag: the tags are owned by scripts/deploy-cpe2e.sh + values.yaml and
# bumped by the implementation task (E-2 T017). This wrapper only builds, deploys, and
# proves the rollout landed.
#
# E-2's rollout set:
#   registry-api        — serves tools.side_effecting, persists playground_runs.eval_mode,
#                         threads eval_mode into the durable dispatch, scores side_effect
#   declarative-runner  — REQUIRED: the record/mock seam lives in sdk/agentshield_sdk/
#                         (graph_builder + durable), pip-bundled into this image. Without
#                         this rebuild the agent pods run OLD SDK code and the seam never runs.
#   eval-runner         — sets eval_mode=record for items asserting side effects, posts
#                         the recorded calls to /eval/score
#   studio              — renders the recorded calls + the side_effect dimension
#
# Usage: bash scripts/deploy-cp1-e2.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== E-2 CP1 deploy — delegating to scripts/deploy-cpe2e.sh ==="
bash "$REPO_ROOT/scripts/deploy-cpe2e.sh"

echo ""
echo "=== waiting for the E-2 rollouts ==="
# Agent pods (declarative-runner) are reconciled per-deployment by the deploy-controller,
# not by a platform Deployment, so they are not rollout-status-able here; the behaviour
# smoke test proves the seam actually runs in a real agent pod.
for d in agentshield-registry-api agentshield-studio agentshield-deploy-controller; do
  if kubectl get deploy "$d" -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "  → rollout status $d"
    kubectl rollout status "deploy/$d" -n "$NAMESPACE" --timeout=300s
  else
    echo "  → $d not present, skipping"
  fi
done

echo ""
echo "✅ E-2 CP1 deploy complete."
echo "   Next: bash scripts/smoke-test-cp1-e2-infra.sh   (images + alembic 0063 + columns)"
echo "         bash scripts/smoke-test-cp1-e2-behaviour.sh (suite-74, the no-fakes gate)"

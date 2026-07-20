#!/usr/bin/env bash
# scripts/deploy-cp2-eval.sh
#
# Eval v2 E-0 — Checkpoint 2 deploy (real reactive parity, no fakes).
#
# Builds registry-api + eval-runner + studio at the E-0 tags, helm-upgrades the
# chart (tags baked into charts/agentshield/values.yaml), and waits for all
# three rollouts. The eval-runner image MUST be present for suite-61's real
# EvalRun Job to run — that is why CP2 rebuilds it (CP1 only needed registry-api).
#
# Prereq: the platform is already deployed and CP1 has applied migrations
# 0059/0060 (deploy-cp1-eval.sh). This is an incremental checkpoint deploy.
#
# After this: run
#   bash scripts/smoke-test-cp2-eval-real.sh && bash scripts/smoke-test-cp2-eval-parity.sh
set -euo pipefail

RELEASE="agentshield"
CHART="charts/agentshield"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TIMEOUT="10m"

REGISTRY_API_TAG="0.2.169"   # E-0: eval-runner mode dispatch + /eval/score door
EVAL_RUNNER_TAG="0.1.5"      # E-0: reads MODE, scores via /eval/score, records dimension_scores
STUDIO_TAG="0.1.133"         # E-0: dataset mode selector + per-dimension result column

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> [CP2] Building registry-api:${REGISTRY_API_TAG} ..."
docker build -t "registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}" services/registry-api/

echo "==> [CP2] Building eval-runner:${EVAL_RUNNER_TAG} (real EvalRun Job image) ..."
docker build -t "registry.internal/agentshield/eval-runner:${EVAL_RUNNER_TAG}" services/eval-runner/

echo "==> [CP2] Building studio:${STUDIO_TAG} ..."
docker build -t "registry.internal/agentshield/studio:${STUDIO_TAG}" studio/

echo "==> [CP2] helm dependency build (best effort) ..."
helm dependency build "$CHART" >/dev/null 2>&1 || true

echo "==> [CP2] helm upgrade ${RELEASE} (tags baked in values.yaml) ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --reuse-values \
  --timeout "$TIMEOUT" \
  --wait

echo "==> [CP2] Waiting for rollouts ..."
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"
kubectl rollout status deployment/agentshield-studio       -n "$NAMESPACE" --timeout="$TIMEOUT" || echo "  (studio starting)"

# Keep Langfuse trace-link SSO working after any deploy (self-skips if langfuse not deployed).
bash "$(dirname "$0")/reconcile-langfuse-hostalias.sh" "$NAMESPACE"

echo "==> [CP2] pods:"
kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api
kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio

echo ""
echo "[CP2] deploy complete. Verify with:"
echo "  bash scripts/smoke-test-cp2-eval-real.sh && bash scripts/smoke-test-cp2-eval-parity.sh"

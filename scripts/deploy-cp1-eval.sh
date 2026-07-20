#!/usr/bin/env bash
# scripts/deploy-cp1-eval.sh
#
# Eval v2 E-0 — Checkpoint 1 deploy (schema + judge door).
#
# Builds registry-api at the E-0 tag, helm-upgrades the chart (image tags are
# baked into charts/agentshield/values.yaml), and lets the registry-api
# alembic-migrate init container run `alembic upgrade head` — applying migrations
# 0059 (playground_datasets.mode / eval_runs.mode+dimension_weights+pass_threshold)
# and 0060 (eval_run_results dimension columns). Then waits for the pod Ready.
#
# Prereq: the platform is already deployed (this is an incremental checkpoint
# deploy, not a fresh bootstrap — use scripts/deploy-cpe2e.sh for that).
#
# After this: run
#   bash scripts/smoke-test-cp1-eval-schema.sh && bash scripts/smoke-test-cp1-eval-score.sh
set -euo pipefail

RELEASE="agentshield"
CHART="charts/agentshield"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TIMEOUT="10m"

REGISTRY_API_TAG="0.2.169"   # E-0: migrations 0059/0060 + score_response/score_composite + /eval/score

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> [CP1] Building registry-api:${REGISTRY_API_TAG} (E-0 schema + judge door) ..."
docker build -t "registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}" services/registry-api/

echo "==> [CP1] helm dependency build (best effort) ..."
helm dependency build "$CHART" >/dev/null 2>&1 || true

echo "==> [CP1] helm upgrade ${RELEASE} (tags baked in values.yaml) ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --reuse-values \
  --timeout "$TIMEOUT" \
  --wait

echo "==> [CP1] Rolling out registry-api (alembic-migrate init runs upgrade head -> 0059/0060) ..."
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"

# Keep Langfuse trace-link SSO working after any deploy (self-skips if langfuse not deployed).
bash "$(dirname "$0")/reconcile-langfuse-hostalias.sh" "$NAMESPACE"

echo "==> [CP1] registry-api pod:"
kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api

echo ""
echo "[CP1] deploy complete. Verify with:"
echo "  bash scripts/smoke-test-cp1-eval-schema.sh && bash scripts/smoke-test-cp1-eval-score.sh"

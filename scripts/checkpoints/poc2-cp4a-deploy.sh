#!/usr/bin/env bash
set -euo pipefail
# CP4a — POC-2 deploy on EKS via the SANCTIONED Helm path. Builds+pushes both
# images to ECR, then deploys with SKIP_BUILD. NEVER `kubectl set image/env` (drift).
echo "=== Checkpoint 4a: POC-2 deploy (registry-api 0.2.189 + studio 0.1.141) ==="
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
export AWS_PROFILE="${AWS_PROFILE:-kkalyan-aws-key}"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/test-cluster-kube-config.yaml}"
ECR="517602344783.dkr.ecr.us-west-2.amazonaws.com"
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin "$ECR" >/dev/null
docker buildx inspect eksbuilder >/dev/null 2>&1 || docker buildx create --name eksbuilder >/dev/null
docker buildx use eksbuilder
echo "--- build+push registry-api:0.2.189 ---"
docker buildx build --platform linux/amd64 -t "$ECR/agentshield/registry-api:0.2.189" services/registry-api/ --push
echo "--- build+push studio:0.1.141 ---"
docker buildx build --platform linux/amd64 -t "$ECR/agentshield/studio:0.1.141" studio/ --push
echo "--- deploy via sanctioned Helm path (SKIP_BUILD) ---"
SKIP_BUILD=1 bash scripts/deploy-eks.sh
echo "--- assert both images live ---"
ri=$(kubectl get deploy agentshield-registry-api -n agentshield-platform -o jsonpath='{.spec.template.spec.containers[0].image}')
st=$(kubectl get deploy agentshield-studio -n agentshield-platform -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "registry-api: $ri"; echo "studio: $st"
echo "$ri" | grep -q '0.2.189' || { echo "FAIL: registry-api not 0.2.189"; exit 1; }
echo "$st" | grep -q '0.1.141' || { echo "FAIL: studio not 0.1.141"; exit 1; }
echo "PASS"

#!/usr/bin/env bash
# setup-envoy-gateway.sh — Install Envoy Gateway controller (idempotent).
#
# Run once per cluster. The controller lives in envoy-gateway-system namespace
# and watches for GatewayClass/Gateway/HTTPRoute resources in all namespaces.
#
# Usage:
#   bash scripts/setup-envoy-gateway.sh
#   EG_VERSION=v1.8.2 bash scripts/setup-envoy-gateway.sh
set -euo pipefail

EG_VERSION="${EG_VERSION:-v1.8.2}"
EG_NAMESPACE="envoy-gateway-system"
EG_RELEASE="eg"

echo "=== Envoy Gateway Setup (${EG_VERSION}) ==="

# Check if already installed
if helm status "$EG_RELEASE" -n "$EG_NAMESPACE" &>/dev/null; then
  CURRENT=$(helm get metadata "$EG_RELEASE" -n "$EG_NAMESPACE" -o json 2>/dev/null | grep -o '"version":"[^"]*"' | head -1 || true)
  echo "  Already installed: ${CURRENT:-unknown version}"
  echo "  To upgrade: helm upgrade $EG_RELEASE oci://docker.io/envoyproxy/gateway-helm --version $EG_VERSION -n $EG_NAMESPACE"
  echo "  Skipping install."
else
  echo "[1/3] Installing Envoy Gateway ${EG_VERSION}..."
  helm install "$EG_RELEASE" oci://docker.io/envoyproxy/gateway-helm \
    --version "$EG_VERSION" \
    -n "$EG_NAMESPACE" --create-namespace
fi

echo "[2/3] Waiting for controller deployment..."
kubectl wait --timeout=120s -n "$EG_NAMESPACE" \
  deployment/envoy-gateway --for=condition=Available

echo "[3/3] Verifying CRDs..."
CRDS_FOUND=$(kubectl get crd gatewayclasses.gateway.networking.k8s.io 2>/dev/null && echo "yes" || echo "no")
if [ "$CRDS_FOUND" = "yes" ]; then
  echo "  Gateway API CRDs installed."
else
  echo "  WARNING: Gateway API CRDs not found. Controller may still be initializing."
fi

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Deploy the platform: bash scripts/deploy-cpe2e.sh"
echo "  2. Start the gateway proxy:"
echo "     bash scripts/gateway-proxy.sh"
echo "  3. Access: http://agentshield.127.0.0.1.nip.io:8080"
echo ""
echo "No /etc/hosts needed — nip.io resolves agentshield.127.0.0.1.nip.io → 127.0.0.1 via public DNS."
echo ""
echo "Note: On kind/Docker Desktop clusters, the Gateway LoadBalancer IP is internal."
echo "Use gateway-proxy.sh to expose it on localhost:8080. On EKS, the LB gets a real"
echo "external IP and no proxy is needed."

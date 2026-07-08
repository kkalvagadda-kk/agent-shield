#!/usr/bin/env bash
# gateway-proxy.sh — Port-forward the Envoy Gateway to localhost.
#
# On kind/Docker Desktop, the Gateway LoadBalancer IP is internal to Docker's
# network and unreachable from the Mac host. This script forwards localhost:8443
# to the Gateway HTTPS service so all path-based routes work from the browser.
#
# HTTPS is required: browsers need a secure context for Web Crypto API (Keycloak PKCE).
# Self-signed cert → browser shows warning once, click through to proceed.
#
# On EKS, this script is unnecessary — the LB gets a real external address.
#
# Usage:
#   bash scripts/gateway-proxy.sh          # foreground (Ctrl+C to stop)
#   bash scripts/gateway-proxy.sh &        # background
#
# Then access: https://agentshield.127.0.0.1.nip.io:8443
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PORT="${GATEWAY_PORT:-8443}"

# Find the Envoy Gateway service (auto-generated name by the controller)
GW_SVC=$(kubectl get svc -n envoy-gateway-system -l "gateway.envoyproxy.io/owning-gateway-name=agentshield-gateway" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$GW_SVC" ]; then
  # Fallback: search by partial name
  GW_SVC=$(kubectl get svc -n envoy-gateway-system --no-headers | grep "agentshield-gateway" | awk '{print $1}' | head -1)
fi

if [ -z "$GW_SVC" ]; then
  echo "ERROR: Cannot find Gateway service in envoy-gateway-system."
  echo "  Ensure Gateway is deployed: kubectl get gateway -n $NAMESPACE"
  exit 1
fi

echo "Forwarding localhost:${PORT} → ${GW_SVC}:443 (envoy-gateway-system)"
echo ""
echo "Access (HTTPS — accept self-signed cert warning in browser):"
echo "  Studio:        https://agentshield.127.0.0.1.nip.io:${PORT}"
echo "  Registry API:  https://agentshield.127.0.0.1.nip.io:${PORT}/api/v1/agents"
echo "  Keycloak:      https://agentshield.127.0.0.1.nip.io:${PORT}/realms/agentshield"
echo "  Langfuse:      https://langfuse.127.0.0.1.nip.io:${PORT}"
echo "  MinIO Console: https://agentshield.127.0.0.1.nip.io:${PORT}/minio/"
echo "  Webhooks:      https://agentshield.127.0.0.1.nip.io:${PORT}/webhooks/"
echo ""
echo "Press Ctrl+C to stop."
echo ""

kubectl port-forward -n envoy-gateway-system "svc/${GW_SVC}" "${PORT}:443"

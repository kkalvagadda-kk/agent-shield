#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="agentshield-platform"
NETWORK_POLICY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infra/network-policies"

# Validate argument
if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/onboard-team.sh <team-name>"
  exit 1
fi

TEAM="$1"

if ! echo "${TEAM}" | grep -qE '^[a-z0-9-]+$'; then
  echo "ERROR: Team name must match ^[a-z0-9-]+$ (lowercase letters, numbers, hyphens only)"
  exit 1
fi

TEAM_NS="agents-${TEAM}"

echo "=== Onboarding team: ${TEAM} (namespace: ${TEAM_NS}) ==="

# Step 1: Create namespace if it doesn't exist
echo ""
echo "[1/5] Creating namespace ${TEAM_NS}..."
if kubectl get namespace "${TEAM_NS}" &>/dev/null; then
  echo "    Namespace ${TEAM_NS} already exists, skipping"
else
  kubectl create namespace "${TEAM_NS}"
  echo "    OK: Namespace ${TEAM_NS} created"
fi

# Step 2: Apply default-deny NetworkPolicy in the new namespace
echo ""
echo "[2/5] Applying default-deny NetworkPolicy to ${TEAM_NS}..."
if [[ ! -f "${NETWORK_POLICY_DIR}/platform-default-deny.yaml" ]]; then
  echo "    ERROR: ${NETWORK_POLICY_DIR}/platform-default-deny.yaml not found"
  exit 1
fi
sed "s/namespace: agentshield-platform/namespace: ${TEAM_NS}/g" \
  "${NETWORK_POLICY_DIR}/platform-default-deny.yaml" \
  | kubectl apply -n "${TEAM_NS}" -f -
echo "    OK: default-deny NetworkPolicy applied"

# Step 3: Apply egress NetworkPolicy in the new namespace
echo ""
echo "[3/5] Applying egress NetworkPolicy to ${TEAM_NS}..."
if [[ ! -f "${NETWORK_POLICY_DIR}/agents-allow-egress.yaml" ]]; then
  echo "    ERROR: ${NETWORK_POLICY_DIR}/agents-allow-egress.yaml not found"
  exit 1
fi
sed "s/namespace: agents-[a-z0-9-]*/namespace: ${TEAM_NS}/g" \
  "${NETWORK_POLICY_DIR}/agents-allow-egress.yaml" \
  | kubectl apply -n "${TEAM_NS}" -f -
echo "    OK: egress NetworkPolicy applied"

# Step 4: Create Keycloak role for the team
echo ""
echo "[4/5] Creating Keycloak role 'agentshield-${TEAM}'..."
KC_POD=$(kubectl get pod -n "${NAMESPACE}" -l app.kubernetes.io/name=keycloak \
  -o jsonpath='{.items[0].metadata.name}')
KC_ADMIN_PASSWORD=$(kubectl get secret agentshield-secrets -n "${NAMESPACE}" \
  -o jsonpath='{.data.keycloak-admin-password}' | base64 -d)

# Login to kcadm and check/create role
ROLE_EXISTS=$(kubectl exec -n "${NAMESPACE}" "${KC_POD}" -- \
  bash -c "
    /opt/keycloak/bin/kcadm.sh config credentials \
      --server http://localhost:8080 \
      --realm master \
      --user admin \
      --password '${KC_ADMIN_PASSWORD}' 2>/dev/null
    /opt/keycloak/bin/kcadm.sh get roles -r agentshield 2>/dev/null | grep -c 'agentshield-${TEAM}' || true
  " 2>/dev/null)

if [[ "${ROLE_EXISTS}" -gt 0 ]]; then
  echo "    Role 'agentshield-${TEAM}' already exists, skipping"
else
  kubectl exec -n "${NAMESPACE}" "${KC_POD}" -- \
    bash -c "
      /opt/keycloak/bin/kcadm.sh config credentials \
        --server http://localhost:8080 \
        --realm master \
        --user admin \
        --password '${KC_ADMIN_PASSWORD}' 2>/dev/null
      /opt/keycloak/bin/kcadm.sh create roles -r agentshield -s name=agentshield-${TEAM}
    " 2>/dev/null
  echo "    OK: Keycloak role 'agentshield-${TEAM}' created"
fi

# Step 5: Register the team via Registry API
echo ""
echo "[5/5] Registering team '${TEAM}' via Registry API..."
REGISTRY_POD=$(kubectl get pod -n "${NAMESPACE}" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}')

REGISTER_OUT=$(kubectl exec -n "${NAMESPACE}" "${REGISTRY_POD}" -- \
  python3 -c "
import urllib.request, urllib.error, json

payload = json.dumps({'name': '${TEAM}', 'namespace': '${TEAM_NS}'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/teams/',
    data=payload,
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    r = urllib.request.urlopen(req, timeout=10)
    print('OK:' + str(r.status))
except urllib.error.HTTPError as e:
    print('HTTP:' + str(e.code))
except Exception as ex:
    print('ERR:' + str(ex))
" 2>/dev/null)

if echo "${REGISTER_OUT}" | grep -q "^OK:"; then
  echo "    OK: Team '${TEAM}' registered in Registry API"
elif echo "${REGISTER_OUT}" | grep -q "HTTP:409"; then
  echo "    WARNING: Team '${TEAM}' already exists in Registry API (409), skipping"
else
  echo "    FAIL: Registry API registration returned: ${REGISTER_OUT}"
  exit 1
fi

echo ""
echo "=== Team '${TEAM}' onboarded successfully ==="
echo "    Namespace: ${TEAM_NS}"
echo "    Keycloak role: agentshield-${TEAM}"
echo "    NetworkPolicies: default-deny + egress applied"

#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="agentshield-platform"

# Parse optional --namespace argument
while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

echo "=== AgentShield Post-Install Checks (namespace: ${NAMESPACE}) ==="

# 1. All pods in namespace are Running
echo ""
echo "[1/6] Checking all pods in ${NAMESPACE} are Running..."
BAD_PODS=$(kubectl get pods -n "${NAMESPACE}" --no-headers 2>/dev/null \
  | grep -v -E '^\S+\s+[0-9]+/[0-9]+\s+Running\s+' || true)
if [[ -n "${BAD_PODS}" ]]; then
  echo "    FAIL: Some pods are not Running:"
  echo "${BAD_PODS}" | sed 's/^/    /'
  exit 1
fi
echo "    OK: All pods Running"

# 2. Keycloak realm agentshield exists
echo ""
echo "[2/6] Checking Keycloak realm 'agentshield'..."
KC_POD=$(kubectl get pod -n "${NAMESPACE}" -l app.kubernetes.io/name=keycloak \
  -o jsonpath='{.items[0].metadata.name}')
KC_STATUS=$(kubectl exec -n "${NAMESPACE}" "${KC_POD}" -- \
  bash -c 'curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/realms/agentshield' 2>/dev/null || echo "000")
if [[ "${KC_STATUS}" != "200" ]]; then
  echo "    FAIL: Keycloak realm check returned HTTP ${KC_STATUS}"
  exit 1
fi
echo "    OK: Keycloak realm 'agentshield' exists (HTTP 200)"

# 3. Registry API health check (via kubectl exec + python3)
echo ""
echo "[3/6] Checking Registry API health..."
REGISTRY_POD=$(kubectl get pod -n "${NAMESPACE}" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}')
REGISTRY_STATUS=$(kubectl exec -n "${NAMESPACE}" "${REGISTRY_POD}" -- \
  python3 -c "
import urllib.request
try:
    r = urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=5)
    print(r.status)
except Exception as e:
    print('ERR: ' + str(e))
" 2>/dev/null)
if [[ "${REGISTRY_STATUS}" != "200" ]]; then
  echo "    FAIL: Registry API health check returned: ${REGISTRY_STATUS}"
  exit 1
fi
echo "    OK: Registry API healthy (HTTP 200)"

# 4. PostgreSQL reachable
echo ""
echo "[4/6] Checking PostgreSQL..."
PG_POD=$(kubectl get pod -n "${NAMESPACE}" -l app.kubernetes.io/name=postgresql \
  -o jsonpath='{.items[0].metadata.name}')
PG_OUT=$(kubectl exec -n "${NAMESPACE}" "${PG_POD}" -- \
  psql -U postgres -c "\l" 2>&1 || true)
if echo "${PG_OUT}" | grep -q "List of databases"; then
  echo "    OK: PostgreSQL reachable"
else
  echo "    FAIL: PostgreSQL check failed:"
  echo "${PG_OUT}" | sed 's/^/    /'
  exit 1
fi

# 5. Redis responds to PING
echo ""
echo "[5/6] Checking Redis..."
REDIS_POD=$(kubectl get pod -n "${NAMESPACE}" -l app.kubernetes.io/name=redis \
  -o jsonpath='{.items[0].metadata.name}')
REDIS_OUT=$(kubectl exec -n "${NAMESPACE}" "${REDIS_POD}" -- \
  redis-cli ping 2>/dev/null || true)
if [[ "${REDIS_OUT}" == "PONG" ]]; then
  echo "    OK: Redis PONG received"
else
  echo "    FAIL: Redis did not respond with PONG (got: ${REDIS_OUT})"
  exit 1
fi

# 6. MinIO bucket agentshield exists
echo ""
echo "[6/6] Checking MinIO bucket 'agentshield'..."
MINIO_POD=$(kubectl get pod -n "${NAMESPACE}" -l app.kubernetes.io/name=minio \
  -o jsonpath='{.items[0].metadata.name}')
MINIO_OUT=$(kubectl exec -n "${NAMESPACE}" "${MINIO_POD}" -- \
  bash -c 'mc alias set local http://localhost:9000 "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" --quiet && mc ls local/' \
  2>/dev/null || true)
if echo "${MINIO_OUT}" | grep -q "agentshield"; then
  echo "    OK: MinIO bucket 'agentshield' exists"
else
  echo "    FAIL: MinIO bucket 'agentshield' not found. Output:"
  echo "${MINIO_OUT}" | sed 's/^/    /'
  exit 1
fi

echo ""
echo "=== AgentShield Post-Install Summary ==="
echo "Registry API:     http://localhost:8000 (after: kubectl port-forward svc/agentshield-registry-api -n agentshield-platform 8000:8000)"
echo "Keycloak Admin:   http://localhost:8080 (after: kubectl port-forward svc/agentshield-keycloak -n agentshield-platform 8080:80)"
echo "MinIO Console:    http://localhost:9001 (after: kubectl port-forward svc/agentshield-minio -n agentshield-platform 9001:9001)"

#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="agentshield-platform"
RELEASE="agentshield"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "==> Checkpoint 1 — Infrastructure Smoke Tests"
echo "    Namespace: $NAMESPACE"
echo ""

# ── 1. All pods Running ────────────────────────────────────────────────────────
echo "--- Pods ---"
NOT_RUNNING=$(kubectl get pods -n "$NAMESPACE" \
  --field-selector='status.phase!=Running' \
  --no-headers 2>/dev/null | { grep -v "Completed" || true; } | wc -l | tr -d ' ')
if [[ "$NOT_RUNNING" -eq 0 ]]; then
  pass "All pods in $NAMESPACE are Running"
else
  fail "$NOT_RUNNING pod(s) not in Running state:"
  kubectl get pods -n "$NAMESPACE" --field-selector='status.phase!=Running' 2>/dev/null || true
fi

# ── 2. PostgreSQL: 5 databases exist ──────────────────────────────────────────
echo ""
echo "--- PostgreSQL ---"
PG_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=postgresql \
  --no-headers -o custom-columns=":metadata.name" | head -1)

if [[ -z "$PG_POD" ]]; then
  fail "No PostgreSQL pod found"
else
  PG_PASS=$(kubectl get secret postgres-passwords -n "$NAMESPACE" \
    -o jsonpath='{.data.keycloak}' 2>/dev/null | base64 -d)
  EXPECTED_DBS=(agentshield keycloak langfuse langgraph appsmith)
  DB_LIST=$(kubectl exec -n "$NAMESPACE" "$PG_POD" -- \
    sh -c "PGPASSWORD='${PG_PASS}' psql -U postgres -At -c \"SELECT datname FROM pg_database WHERE datistemplate=false;\"" 2>/dev/null)

  for db in "${EXPECTED_DBS[@]}"; do
    if echo "$DB_LIST" | grep -qx "$db"; then
      pass "Database '$db' exists"
    else
      fail "Database '$db' NOT found"
    fi
  done
fi

# ── 3. Keycloak: OIDC discovery endpoint responds 200 ─────────────────────────
echo ""
echo "--- Keycloak ---"
# Keycloak image (quay.io) has no curl/wget; use registry-api pod (has python3).
KC_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=keycloak \
  --no-headers -o custom-columns=":metadata.name" | head -1)
REGISTRY_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --no-headers -o custom-columns=":metadata.name" | head -1)

if [[ -z "$KC_POD" ]]; then
  fail "No Keycloak pod found"
elif [[ -z "$REGISTRY_POD" ]]; then
  fail "No registry-api pod available to test Keycloak endpoint"
else
  OIDC_STATUS=$(kubectl exec -n "$NAMESPACE" "$REGISTRY_POD" -- \
    python3 -c "import urllib.request; r=urllib.request.urlopen('http://${RELEASE}-keycloak/realms/agentshield/.well-known/openid-configuration'); print(r.getcode())" \
    2>/dev/null || echo "000")

  if [[ "$OIDC_STATUS" == "200" ]]; then
    pass "Keycloak OIDC discovery endpoint → HTTP $OIDC_STATUS"
  else
    fail "Keycloak OIDC discovery endpoint → HTTP $OIDC_STATUS (expected 200)"
  fi
fi

# ── 4. Redis: PING → PONG ─────────────────────────────────────────────────────
echo ""
echo "--- Redis ---"
REDIS_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=redis \
  --no-headers -o custom-columns=":metadata.name" | grep master | head -1)

if [[ -z "$REDIS_POD" ]]; then
  fail "No Redis master pod found"
else
  REDIS_PASS=$(kubectl get secret redis-password -n "$NAMESPACE" \
    -o jsonpath='{.data.redis-password}' 2>/dev/null | base64 -d)
  PONG=$(kubectl exec -n "$NAMESPACE" "$REDIS_POD" -- \
    redis-cli --no-auth-warning -a "$REDIS_PASS" ping 2>/dev/null | tr -d '\r\n' || echo "")

  if [[ "$PONG" == "PONG" ]]; then
    pass "Redis PING → PONG"
  else
    fail "Redis PING → '$PONG' (expected PONG)"
  fi
fi

# ── 5. MinIO: agentshield bucket exists ───────────────────────────────────────
echo ""
echo "--- MinIO ---"
MINIO_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=minio \
  --no-headers -o custom-columns=":metadata.name" | head -1)

if [[ -z "$MINIO_POD" ]]; then
  fail "No MinIO pod found"
else
  # Use mc alias pointing to localhost inside the pod
  BUCKET_LIST=$(kubectl exec -n "$NAMESPACE" "$MINIO_POD" -- \
    sh -c 'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" --quiet && mc ls local/' \
    2>/dev/null || echo "")

  if echo "$BUCKET_LIST" | grep -q "agentshield"; then
    pass "MinIO 'agentshield' bucket exists"
  else
    fail "MinIO 'agentshield' bucket NOT found (buckets: $(echo "$BUCKET_LIST" | tr '\n' ' '))"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

if [[ "$FAIL" -gt 0 ]]; then
  echo "FAIL"
  exit 1
fi

echo "PASS"

#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="agentshield-platform"
PASS=0
FAIL=0

pass() { echo "[PASS] $1"; ((PASS++)); }
fail() { echo "[FAIL] $1"; ((FAIL++)); }

echo "==> Checkpoint 1 — Registry API Smoke Tests"
echo "    Namespace: $NAMESPACE"
echo ""

# Resolve registry-api pod for exec-based curl (avoids needing port-forward)
REGISTRY_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --no-headers -o custom-columns=":metadata.name" | head -1)

if [[ -z "$REGISTRY_POD" ]]; then
  echo "[FATAL] No registry-api pod found in $NAMESPACE"
  exit 1
fi

echo "    Pod: $REGISTRY_POD"
BASE="http://localhost:8000"

kexec() {
  # Run curl inside the registry-api pod
  kubectl exec -n "$NAMESPACE" "$REGISTRY_POD" -- curl -s "$@"
}

# ── 1. Health check ───────────────────────────────────────────────────────────
echo ""
echo "--- Health ---"
HEALTH=$(kexec "$BASE/health" 2>/dev/null)
STATUS=$(echo "$HEALTH" | jq -r '.status' 2>/dev/null || echo "")

if [[ "$STATUS" == "ok" ]]; then
  pass "GET /health → {status: ok}"
else
  fail "GET /health → '$HEALTH' (expected {status: ok})"
fi

# ── 2. POST /api/v1/agents → 201 ─────────────────────────────────────────────
echo ""
echo "--- Agents CRUD ---"
CREATE_RESP=$(kexec -s -o - -w "\n%{http_code}" \
  -X POST "$BASE/api/v1/agents" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "echo-agent",
    "display_name": "Echo Agent",
    "description": "Smoke test agent",
    "runtime": "python",
    "image": "agentshield/echo-agent:latest"
  }' 2>/dev/null)

HTTP_CODE=$(echo "$CREATE_RESP" | tail -1)
BODY=$(echo "$CREATE_RESP" | head -n -1)
AGENT_NAME=$(echo "$BODY" | jq -r '.name' 2>/dev/null || echo "")

if [[ "$HTTP_CODE" == "201" ]] && [[ "$AGENT_NAME" == "echo-agent" ]]; then
  pass "POST /api/v1/agents → HTTP 201, name='$AGENT_NAME'"
elif [[ "$HTTP_CODE" == "409" ]]; then
  # Already exists from a previous run — treat as pass
  pass "POST /api/v1/agents → HTTP 409 (already exists, idempotent OK)"
  AGENT_NAME="echo-agent"
else
  fail "POST /api/v1/agents → HTTP $HTTP_CODE body='$BODY'"
  AGENT_NAME="echo-agent"
fi

# ── 3. GET /api/v1/agents → lists echo-agent ─────────────────────────────────
LIST_RESP=$(kexec "$BASE/api/v1/agents" 2>/dev/null)
FOUND=$(echo "$LIST_RESP" | jq '[.[] | select(.name=="echo-agent")] | length' 2>/dev/null || echo "0")

if [[ "$FOUND" -ge 1 ]]; then
  pass "GET /api/v1/agents → lists echo-agent"
else
  fail "GET /api/v1/agents → echo-agent not found in response: $LIST_RESP"
fi

# ── 4. GET /api/v1/tools → 200 (empty list ok) ───────────────────────────────
echo ""
echo "--- Tools ---"
TOOLS_RESP=$(kexec -s -o - -w "\n%{http_code}" "$BASE/api/v1/tools" 2>/dev/null)
TOOLS_CODE=$(echo "$TOOLS_RESP" | tail -1)
TOOLS_BODY=$(echo "$TOOLS_RESP" | head -n -1)

if [[ "$TOOLS_CODE" == "200" ]]; then
  COUNT=$(echo "$TOOLS_BODY" | jq 'length' 2>/dev/null || echo "?")
  pass "GET /api/v1/tools → HTTP 200 ($COUNT items)"
else
  fail "GET /api/v1/tools → HTTP $TOOLS_CODE"
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

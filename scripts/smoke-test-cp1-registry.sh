#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="agentshield-platform"
PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "==> Checkpoint 1 — Registry API Smoke Tests"
echo "    Namespace: $NAMESPACE"
echo ""

REGISTRY_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --no-headers -o custom-columns=":metadata.name" | head -1)

if [[ -z "$REGISTRY_POD" ]]; then
  echo "[FATAL] No registry-api pod found in $NAMESPACE"
  exit 1
fi

echo "    Pod: $REGISTRY_POD"

# Run an HTTP request inside the registry-api pod using Python3.
# BODY_B64: optional base64-encoded JSON request body (avoids shell quoting issues).
# Output: first line = HTTP status code, remaining lines = response body.
pyreq() {
  local METHOD="$1"
  local API_PATH="$2"
  local BODY_B64="${3:-}"

  local PY
  if [[ -n "$BODY_B64" ]]; then
    PY="import urllib.request, base64
body = base64.b64decode('${BODY_B64}')
req = urllib.request.Request('http://localhost:8000${API_PATH}', data=body, method='${METHOD}')
req.add_header('Content-Type', 'application/json')
try:
    r = urllib.request.urlopen(req)
    print(r.getcode()); print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code); print(e.read().decode())"
  else
    PY="import urllib.request
req = urllib.request.Request('http://localhost:8000${API_PATH}', method='${METHOD}')
try:
    r = urllib.request.urlopen(req)
    print(r.getcode()); print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code); print(e.read().decode())"
  fi

  kubectl exec -n "$NAMESPACE" "$REGISTRY_POD" -- python3 -c "$PY" 2>/dev/null
}

# Extract a JSON field using local python3.
jval() {
  echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$2',''))" 2>/dev/null || echo ""
}

# Count items in a JSON list/dict (supports {items:[]} envelope or plain list).
jcount_name() {
  local json="$1"
  local name="$2"
  echo "$json" | python3 -c "
import sys,json
data=json.load(sys.stdin)
items=data.get('items',data) if isinstance(data,dict) else data
print(sum(1 for a in items if a.get('name')=='${name}'))
" 2>/dev/null || echo "0"
}

jlen() {
  echo "$1" | python3 -c "
import sys,json
data=json.load(sys.stdin)
items=data.get('items',data) if isinstance(data,dict) else data
print(len(items) if isinstance(items,list) else 0)
" 2>/dev/null || echo "?"
}

# ── 1. Health check ───────────────────────────────────────────────────────────
echo ""
echo "--- Health ---"
HEALTH_OUT=$(pyreq GET /health)
HTTP_CODE=$(echo "$HEALTH_OUT" | head -1)
BODY=$(echo "$HEALTH_OUT" | tail -n +2)
STATUS=$(jval "$BODY" status)

if [[ "$STATUS" == "ok" ]]; then
  pass "GET /health → HTTP $HTTP_CODE {status: ok}"
else
  fail "GET /health → HTTP $HTTP_CODE '$BODY'"
fi

# ── 2. POST /api/v1/agents/ → 201 ────────────────────────────────────────────
# Schema requires: name (str), team (str). Optional: description, agent_type.
echo ""
echo "--- Agents CRUD ---"
AGENT_JSON='{"name":"smoke-echo-agent","team":"platform","description":"Smoke test agent","agent_type":"sdk"}'
AGENT_B64=$(printf '%s' "$AGENT_JSON" | base64 | tr -d '\n')
CREATE_OUT=$(pyreq POST /api/v1/agents/ "$AGENT_B64")
HTTP_CODE=$(echo "$CREATE_OUT" | head -1)
BODY=$(echo "$CREATE_OUT" | tail -n +2)
AGENT_NAME=$(jval "$BODY" name)

if [[ "$HTTP_CODE" == "201" ]] && [[ "$AGENT_NAME" == "smoke-echo-agent" ]]; then
  pass "POST /api/v1/agents/ → HTTP 201, name='$AGENT_NAME'"
elif [[ "$HTTP_CODE" == "409" ]]; then
  pass "POST /api/v1/agents/ → HTTP 409 (already exists, idempotent OK)"
  AGENT_NAME="smoke-echo-agent"
else
  fail "POST /api/v1/agents/ → HTTP $HTTP_CODE body='$BODY'"
  AGENT_NAME="smoke-echo-agent"
fi

# ── 3. GET /api/v1/agents/ → lists smoke-echo-agent ──────────────────────────
LIST_OUT=$(pyreq GET /api/v1/agents/)
LIST_CODE=$(echo "$LIST_OUT" | head -1)
LIST_BODY=$(echo "$LIST_OUT" | tail -n +2)
FOUND=$(jcount_name "$LIST_BODY" smoke-echo-agent)

if [[ "$FOUND" -ge 1 ]]; then
  pass "GET /api/v1/agents/ → HTTP $LIST_CODE, smoke-echo-agent present"
else
  fail "GET /api/v1/agents/ → HTTP $LIST_CODE, smoke-echo-agent not found (body=$LIST_BODY)"
fi

# ── 4. GET /api/v1/workflows/ → 200 (empty list ok) ─────────────────────────
echo ""
echo "--- Workflows ---"
WORKFLOWS_OUT=$(pyreq GET /api/v1/workflows/)
WORKFLOWS_CODE=$(echo "$WORKFLOWS_OUT" | head -1)
WORKFLOWS_BODY=$(echo "$WORKFLOWS_OUT" | tail -n +2)

if [[ "$WORKFLOWS_CODE" == "200" ]]; then
  COUNT=$(jlen "$WORKFLOWS_BODY")
  pass "GET /api/v1/workflows/ → HTTP 200 ($COUNT items)"
else
  fail "GET /api/v1/workflows/ → HTTP $WORKFLOWS_CODE body='$WORKFLOWS_BODY'"
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

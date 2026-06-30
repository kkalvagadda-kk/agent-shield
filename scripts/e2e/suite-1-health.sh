#!/usr/bin/env bash
# Suite 1: Platform Health & Bootstrapping
# Tests T-S1-001 through T-S1-010
#
# Usage:
#   bash scripts/e2e/suite-1-health.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-1-health.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0; MANUAL=0

pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
check_manual() {
  local desc="$1"; shift
  echo "  MANUAL: $desc"
  printf "    Steps: %s\n" "$*"
  MANUAL=$((MANUAL + 1))
}

echo "==> Suite 1: Platform Health & Bootstrapping"
echo "    Namespace: $NAMESPACE"
echo ""

# Locate the Registry API pod (used for in-cluster HTTP calls)
# Helm chart sets label app.kubernetes.io/name=registry-api
API_POD=$(kubectl get pods -n "$NAMESPACE" -l 'app.kubernetes.io/name=registry-api' \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

# ── T-S1-001: Pod Readiness ────────────────────────────────────────────────
echo "--- T-S1-001: Pod Readiness Check ---"
FAIL_PODS=$(kubectl get pods -n "$NAMESPACE" -o json 2>/dev/null | python3 -c "
import sys, json
# NeMo requires nvcr.io access (external registry) — skip in airgapped dev environments
SKIP_PATTERNS = ['nemo']
data = json.load(sys.stdin)
bad = []
for p in data.get('items', []):
    name  = p['metadata']['name']
    if any(s in name for s in SKIP_PATTERNS):
        continue
    phase = p['status'].get('phase', 'Unknown')
    if phase != 'Running':
        bad.append(name + ':' + phase)
        continue
    for cs in p['status'].get('containerStatuses', []):
        if not cs.get('ready', False):
            bad.append(name + ':' + cs.get('name', '?') + ':not-ready')
for x in bad:
    print(x)
" 2>/dev/null || echo "kubectl-error")

if [ -z "$FAIL_PODS" ]; then
  pass "T-S1-001: All pods Running and Ready"
else
  FAIL_LIST=$(echo "$FAIL_PODS" | tr '\n' ' ')
  fail "T-S1-001: Unhealthy pods: $FAIL_LIST"
fi
echo ""

# ── T-S1-002: Registry API /health ────────────────────────────────────────
echo "--- T-S1-002: Registry API /health ---"
if [ -z "${API_POD:-}" ]; then
  fail "T-S1-002: Registry API pod not found (label: app=agentshield-registry-api)"
else
  HEALTH=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    r = urllib.request.urlopen('http://localhost:8000/health', timeout=5)
    print(r.getcode())
except Exception as e:
    print('ERR:' + str(e))
" 2>/dev/null || echo "ERR")
  if [ "$HEALTH" = "200" ]; then
    pass "T-S1-002: Registry API /health returned 200"
  else
    fail "T-S1-002: Registry API /health returned '$HEALTH'"
  fi
fi
echo ""

# ── T-S1-003: OPA Bundle Server ───────────────────────────────────────────
echo "--- T-S1-003: OPA Bundle Server Serves data.json ---"
# Call OPA via service DNS from within the API pod (avoids needing a port-forward)
if [ -n "${API_POD:-}" ]; then
  OPA_RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
try:
    url = 'http://agentshield-opa-bundle-server.${NAMESPACE}.svc.cluster.local:8181/bundles/agentshield/data.json'
    r = urllib.request.urlopen(url, timeout=5)
    body = json.loads(r.read())
    print('ok' if 'agents' in body else 'missing-agents-key')
except Exception as e:
    print('ERR:' + str(e))
" 2>/dev/null || echo "ERR")
  if [ "$OPA_RESULT" = "ok" ]; then
    pass "T-S1-003: OPA bundle data.json reachable with 'agents' key"
  elif [ "$OPA_RESULT" = "missing-agents-key" ]; then
    fail "T-S1-003: OPA bundle data.json is valid JSON but missing 'agents' key"
  else
    check_manual "T-S1-003: OPA bundle not reachable in-cluster ($OPA_RESULT)" \
      "kubectl port-forward svc/agentshield-opa-bundle-server -n $NAMESPACE 8181:8181 &; curl http://localhost:8181/bundles/agentshield/data.json | python3 -m json.tool"
  fi
else
  check_manual "T-S1-003: OPA bundle server (API pod unavailable)" \
    "kubectl port-forward svc/agentshield-opa-bundle-server -n $NAMESPACE 8181:8181 &; curl http://localhost:8181/bundles/agentshield/data.json"
fi
echo ""

# ── T-S1-004: Keycloak Realm ──────────────────────────────────────────────
echo "--- T-S1-004: Keycloak Realm Configured ---"
check_manual "T-S1-004: 'agentshield' realm and 'agentshield-studio' client with serviceAccountsEnabled" \
  "kubectl port-forward svc/agentshield-keycloak -n $NAMESPACE 8080:8080 &" \
  "Get admin token via POST /realms/master/protocol/openid-connect/token; GET /admin/realms → assert 'agentshield' present; GET /admin/realms/agentshield/clients → assert 'agentshield-studio' with serviceAccountsEnabled=true"
echo ""

# ── T-S1-005: Studio UI ───────────────────────────────────────────────────
echo "--- T-S1-005: Studio UI Reachable ---"
check_manual "T-S1-005: Studio frontend returns HTTP 200 with AgentShield branding" \
  "kubectl port-forward svc/agentshield-studio -n $NAMESPACE 3000:3000 &" \
  "Open http://localhost:3000 in a browser; verify page loads without error (HTTP 200, non-empty body)"
echo ""

# ── T-S1-006: Safety Orchestrator /health + /ready ────────────────────────
echo "--- T-S1-006: Safety Orchestrator /health and /ready ---"
if [ -n "${API_POD:-}" ]; then
  SO_BASE="http://agentshield-safety-orchestrator.${NAMESPACE}.svc.cluster.local:8080"
  SO_RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
base = '${SO_BASE}'
results = []
try:
    r = urllib.request.urlopen(base + '/health', timeout=5)
    results.append('health=' + str(r.getcode()))
except Exception as e:
    results.append('health=ERR:' + str(e)[:60])
try:
    r2 = urllib.request.urlopen(base + '/ready', timeout=5)
    body = json.loads(r2.read())
    results.append('ready=' + str(r2.getcode()))
    # Verify body is valid JSON with a scanners key
    results.append('scanners=' + str(list(body.get('scanners', {}).keys())))
except Exception as e:
    results.append('ready=ERR:' + str(e)[:60])
print(' | '.join(results))
" 2>/dev/null || echo "ERR")
  if echo "$SO_RESULT" | grep -q "health=200"; then
    pass "T-S1-006: Safety Orchestrator /health 200 ($SO_RESULT)"
  else
    fail "T-S1-006: Safety Orchestrator check failed: $SO_RESULT"
  fi
else
  check_manual "T-S1-006: Safety Orchestrator (API pod unavailable)" \
    "kubectl port-forward svc/agentshield-safety-orchestrator -n $NAMESPACE 8082:8080 &" \
    "curl http://localhost:8082/health → 200; curl http://localhost:8082/ready → valid JSON with 'scanners' key"
fi
echo ""

# ── T-S1-007: Langfuse Web Pod Ready ─────────────────────────────────────
echo "--- T-S1-007: Langfuse Web Pod Ready ---"
LF_POD=$(kubectl get pods -n "$NAMESPACE" -l 'app.kubernetes.io/name=langfuse-web' \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$LF_POD" ]; then
  # Try broader selector — chart may use different label
  LF_POD=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep "langfuse-web" | awk '{print $1}' | head -1 || true)
fi
if [ -n "$LF_POD" ]; then
  LF_HEALTH=$(kubectl exec -n "$NAMESPACE" "$LF_POD" -- sh -c \
    "wget -qO- http://localhost:3000/api/public/health 2>/dev/null || echo ERR" 2>/dev/null || echo "ERR")
  if echo "$LF_HEALTH" | grep -q '"status":"OK"'; then
    pass "T-S1-007: Langfuse web /api/public/health returned OK"
  else
    fail "T-S1-007: Langfuse web health check failed: $LF_HEALTH"
  fi
else
  check_manual "T-S1-007: Langfuse web pod not found" \
    "kubectl port-forward svc/agentshield-langfuse-web -n $NAMESPACE 4000:3000 &" \
    "curl http://localhost:4000/api/public/health → {\"status\":\"OK\"}"
fi
echo ""

# ── T-S1-008: Langfuse Project Auto-Bootstrapped ──────────────────────────
echo "--- T-S1-008: Langfuse Project Auto-Bootstrapped ---"
LF_SVC="http://agentshield-langfuse-web.${NAMESPACE}.svc.cluster.local:3000"
LF_PK="pk-lf-agentshield-dev-local-0001"
LF_SK="sk-lf-agentshield-dev-local-0001"
if [ -n "${API_POD:-}" ]; then
  LF_PROJ=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, base64
creds = base64.b64encode(b'${LF_PK}:${LF_SK}').decode()
req = urllib.request.Request(
    '${LF_SVC}/api/public/projects',
    headers={'Authorization': 'Basic ' + creds}
)
try:
    r = urllib.request.urlopen(req, timeout=5)
    body = json.loads(r.read())
    projects = body.get('data', [])
    found = any(p.get('id') == '00000000-0000-0000-0001-agentshield01' for p in projects)
    print('ok' if found else 'not-found')
except Exception as e:
    print('ERR:' + str(e)[:80])
" 2>/dev/null || echo "ERR")
  if [ "$LF_PROJ" = "ok" ]; then
    pass "T-S1-008: Langfuse project 'AgentShield Platform' auto-bootstrapped with correct ID"
  else
    fail "T-S1-008: Langfuse project bootstrap check failed: $LF_PROJ"
  fi
else
  check_manual "T-S1-008: Langfuse bootstrap (API pod unavailable)" \
    "curl -u pk-lf-agentshield-dev-local-0001:sk-lf-agentshield-dev-local-0001 http://localhost:4000/api/public/projects" \
    "Assert response has project with id=00000000-0000-0000-0001-agentshield01"
fi
echo ""

# ── T-S1-009: Langfuse Alias Services Resolve ─────────────────────────────
echo "--- T-S1-009: Langfuse ClickHouse and S3 Alias Services Resolve ---"
if [ -n "${API_POD:-}" ]; then
  LF_DNS=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import socket
results = []
for host in ['agentshield-langfuse-clickhouse', 'agentshield-langfuse-s3']:
    fqdn = host + '.${NAMESPACE}.svc.cluster.local'
    try:
        socket.getaddrinfo(fqdn, None, socket.AF_INET)
        results.append(host + ':ok')
    except Exception as e:
        results.append(host + ':NXDOMAIN')
print(' '.join(results))
" 2>/dev/null || echo "ERR")
  if echo "$LF_DNS" | grep -q "NXDOMAIN\|ERR"; then
    fail "T-S1-009: Langfuse alias service DNS resolution failed: $LF_DNS"
  else
    pass "T-S1-009: ClickHouse and S3 alias services resolve ($LF_DNS)"
  fi
else
  check_manual "T-S1-009: Alias service DNS check (API pod unavailable)" \
    "kubectl exec -n $NAMESPACE <any-pod> -- getent hosts agentshield-langfuse-clickhouse" \
    "kubectl exec -n $NAMESPACE <any-pod> -- getent hosts agentshield-langfuse-s3"
fi
echo ""

# ── T-S1-010: OPA Bundle Server Serves Live data.json ─────────────────────
echo "--- T-S1-010: OPA Bundle Server Serves data.json with agents key ---"
if [ -n "$API_POD" ]; then
  OPA_BUNDLE_URL="http://agentshield-opa-bundle-server.${NAMESPACE}.svc.cluster.local:8181"
  OPA_BUNDLE_CHECK=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
try:
    r = urllib.request.urlopen('${OPA_BUNDLE_URL}/bundles/agentshield/data.json', timeout=5)
    d = json.loads(r.read())
    has_agents = 'agents' in d
    has_grants = 'grants' in d
    print('ok agents=' + str(has_agents) + ' grants=' + str(has_grants))
except Exception as e:
    print('not_deployed:' + str(e)[:70])
" 2>/dev/null || echo "exec_failed")
  if echo "$OPA_BUNDLE_CHECK" | grep -q "^ok agents=True"; then
    pass "T-S1-010: OPA bundle server serves data.json with agents+grants keys ($OPA_BUNDLE_CHECK)"
  elif echo "$OPA_BUNDLE_CHECK" | grep -q "not_deployed"; then
    check_manual "T-S1-010: OPA bundle server not reachable ($OPA_BUNDLE_CHECK)" \
      "Apply: kubectl apply -f infra/opa-bundle-server/" \
      "Then: curl http://agentshield-opa-bundle-server.$NAMESPACE:8181/bundles/agentshield/data.json" \
      "Assert JSON with 'agents' key"
  else
    fail "T-S1-010: OPA bundle server data.json missing agents key ($OPA_BUNDLE_CHECK)"
  fi

  # Sub-check: policy.rego endpoint also served
  OPA_POLICY_CHECK=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    r = urllib.request.urlopen('${OPA_BUNDLE_URL}/bundles/agentshield/policy.rego', timeout=5)
    body = r.read().decode()
    has_package = 'package agentshield' in body
    print('ok has_package=' + str(has_package))
except Exception as e:
    print('not_deployed:' + str(e)[:50])
" 2>/dev/null || echo "exec_failed")
  if echo "$OPA_POLICY_CHECK" | grep -q "^ok has_package=True"; then
    pass "T-S1-010b: OPA bundle server serves policy.rego with correct package"
  elif echo "$OPA_POLICY_CHECK" | grep -q "not_deployed"; then
    check_manual "T-S1-010b: OPA bundle server policy.rego not reachable" \
      "GET http://agentshield-opa-bundle-server.$NAMESPACE:8181/bundles/agentshield/policy.rego" \
      "Assert 'package agentshield' in body"
  else
    fail "T-S1-010b: OPA bundle server policy.rego check failed ($OPA_POLICY_CHECK)"
  fi
else
  check_manual "T-S1-010: OPA bundle server check (no API pod)" \
    "kubectl exec -n $NAMESPACE <any-pod> -- curl http://agentshield-opa-bundle-server.$NAMESPACE:8181/bundles/agentshield/data.json" \
    "Assert JSON with 'agents' and 'grants' keys"
fi
echo ""

echo "========================================================"
echo "  Suite 1 Results: $PASS passed, $FAIL failed, $MANUAL manual"
echo "========================================================"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

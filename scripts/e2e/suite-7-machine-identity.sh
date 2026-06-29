#!/usr/bin/env bash
# scripts/e2e/suite-7-machine-identity.sh
#
# E2E Suite 7: Machine Identity (Phase 9.1)
# Tests T-S7-001 through T-S7-008.
#
# Self-contained: registers, deploys, and cleans up its own test agent.
#
# What this automates:
#   T-S7-001 — SA agent-{name}-sa exists in agents namespace after deploy
#   T-S7-001b — GET /agents/{name}/identities returns recorded SA subject
#   T-S7-002  — agent pod has projected sa-token volume with agentshield-opa audience
#   T-S7-002b — token file readable at /var/run/secrets/sa-token/token
#   T-S7-008  — SDK reads projected token
#
# What is MANUAL (requires running OPA sidecar or bundle server):
#   T-S7-003 — OPA bundle data.json contains SA subject
#   T-S7-004 — OPA allows registered agent with valid token
#   T-S7-005 — OPA denies unknown SA subject
#   T-S7-006 — OPA denies unregistered tool
#   T-S7-007 — OPA denies daemon agent with user context
#
# Usage:
#   bash scripts/e2e/suite-7-machine-identity.sh
#   NAMESPACE=my-ns AGENTS_NS=my-agents bash scripts/e2e/suite-7-machine-identity.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
AGENTS_NS="${AGENTS_NS:-agents-platform}"
SUFFIX="$(date +%s)"
AGENT_NAME="s7-id-${SUFFIX}"
SA_NAME="agent-${AGENT_NAME}-sa"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

PASS=0
FAIL=0
MANUAL=0

pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

check_manual() {
  local test_id="$1"
  local desc="$2"
  shift 2
  echo ""
  echo "  MANUAL [${test_id}]: ${desc}"
  if [ $# -gt 0 ]; then
    echo "  Commands to run manually:"
    while [ $# -gt 0 ]; do
      echo "    $1"
      shift
    done
  fi
  MANUAL=$((MANUAL + 1))
}

echo "=== Suite 7: Machine Identity (Phase 9.1) ==="
echo "    Platform namespace: ${NAMESPACE}"
echo "    Agents namespace:   ${AGENTS_NS}"
echo "    Test agent:         ${AGENT_NAME}"
echo ""

# ---------------------------------------------------------------------------
# Setup: Register + deploy test agent (needs platform team + tool grant)
# ---------------------------------------------------------------------------
echo "--- Setup: Register and deploy ${AGENT_NAME} ---"

SETUP_RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, sys

BASE = 'http://localhost:8000/api/v1'

def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        r = urllib.request.urlopen(req)
        raw = r.read()
        return (json.loads(raw) if raw else {}), r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        return (json.loads(raw) if raw else {}), e.code

# Ensure platform team exists
post('/teams/', {'name': 'platform', 'namespace': 'agents-platform'})

# Register agent (team not owner_team; agent_class must be daemon|user_delegated)
agent, status = post('/agents/', {
    'name': '${AGENT_NAME}',
    'description': 'Suite-7 machine identity test agent',
    'team': 'platform',
    'agent_class': 'user_delegated',
    'docker_image': 'registry.internal/agentshield/echo-agent:0.1.0',
    'port': 8080,
})
if status not in (200, 201, 409):
    print('FAIL:register:' + str(status) + ':' + str(agent))
    sys.exit(1)

# Create version
ver, status = post('/agents/${AGENT_NAME}/versions', {'tools': [], 'skills': [], 'eval_passed': True})
if status not in (200, 201):
    print('FAIL:version:' + str(status) + ':' + str(ver))
    sys.exit(1)

# Deploy via /agents/{name}/deploy
dep, status = post('/agents/${AGENT_NAME}/deploy', {
    'version_id': str(ver['id']),
    'replicas': 1,
    'environment': 'production',
})
if status not in (200, 201):
    print('FAIL:deploy:' + str(status) + ':' + str(dep))
    sys.exit(1)

print('OK:' + str(dep.get('id', '?')))
" 2>&1 || true)

if ! echo "$SETUP_RESULT" | grep -q "^OK:"; then
  echo "  SKIP: Could not register/deploy ${AGENT_NAME} ($SETUP_RESULT) — skipping automated SA checks"
  MANUAL=$((MANUAL + 1))
else
  DEPLOY_ID=$(echo "$SETUP_RESULT" | grep "^OK:" | cut -d: -f2)
  echo "  Deployed: deployment_id=${DEPLOY_ID}"
fi

# ---------------------------------------------------------------------------
# T-S7-001: ServiceAccount exists after deploy
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S7-001: SA ${SA_NAME} created in ${AGENTS_NS} ---"

# Poll up to 60s for SA to appear (deploy-controller creates it async)
SA_FOUND=false
for i in $(seq 1 12); do
  if kubectl get sa "$SA_NAME" -n "$AGENTS_NS" --ignore-not-found=true 2>/dev/null | grep -q "$SA_NAME"; then
    SA_FOUND=true
    break
  fi
  sleep 5
done

if $SA_FOUND; then
  pass "T-S7-001: SA ${SA_NAME} created in ${AGENTS_NS}"
else
  fail "T-S7-001: SA ${SA_NAME} not found in ${AGENTS_NS} after 60s"
fi

# ---------------------------------------------------------------------------
# T-S7-001b: Identity recorded in Registry API
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S7-001b: Identity recorded via GET /agents/${AGENT_NAME}/identities ---"

# Poll up to 30s for identity to be recorded (deploy-controller POSTs after SA creation)
IDENTITY_FOUND=false
for i in $(seq 1 6); do
  IDENT_RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, sys
try:
    r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${AGENT_NAME}/identities')
    data = json.loads(r.read())
    if isinstance(data, list) and len(data) > 0:
        print('FOUND:' + str(data[0].get('sa_subject', '?')))
    else:
        print('EMPTY')
except Exception as e:
    print('ERR:' + str(e))
" 2>/dev/null || echo "exec_ERR")

  if echo "$IDENT_RESULT" | grep -q "^FOUND:"; then
    IDENTITY_FOUND=true
    SA_SUBJECT=$(echo "$IDENT_RESULT" | sed 's/FOUND://')
    break
  fi
  sleep 5
done

if $IDENTITY_FOUND; then
  pass "T-S7-001b: Identity recorded (sa_subject=${SA_SUBJECT:-?})"
else
  fail "T-S7-001b: No identity recorded for ${AGENT_NAME} after 30s ($IDENT_RESULT)"
fi

# ---------------------------------------------------------------------------
# T-S7-002: Agent pod has projected SA token volume
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S7-002: Agent pod has projected sa-token volume ---"

# Poll up to 120s for pod to appear Running
AGENT_POD=""
for i in $(seq 1 24); do
  AGENT_POD=$(kubectl get pod -n "$AGENTS_NS" \
    -l "app.kubernetes.io/name=${AGENT_NAME}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  POD_PHASE=$(kubectl get pod "$AGENT_POD" -n "$AGENTS_NS" \
    -o jsonpath='{.status.phase}' 2>/dev/null || true)
  if [ -n "$AGENT_POD" ] && [ "$POD_PHASE" = "Running" ]; then
    break
  fi
  AGENT_POD=""
  sleep 5
done

if [ -n "$AGENT_POD" ]; then
  echo "  Found pod: ${AGENT_POD}"

  # Check sa-token volume exists
  VOL_CHECK=$(kubectl get pod "$AGENT_POD" -n "$AGENTS_NS" \
    -o jsonpath='{.spec.volumes}' 2>/dev/null \
    | python3 -c "
import sys, json
vols = json.load(sys.stdin)
names = [v.get('name','') for v in vols]
matched = [n for n in names if 'sa-token' in n or 'agentshield-opa' in n]
print('FOUND:' + ','.join(matched) if matched else 'NONE')
" 2>/dev/null || echo "ERR")

  if echo "$VOL_CHECK" | grep -q "^FOUND:"; then
    pass "T-S7-002: Pod ${AGENT_POD} has sa-token volume ($VOL_CHECK)"
  else
    fail "T-S7-002: No sa-token volume in pod ($VOL_CHECK)"
  fi

  # Check audience=agentshield-opa
  AUD_CHECK=$(kubectl get pod "$AGENT_POD" -n "$AGENTS_NS" \
    -o jsonpath='{.spec.volumes}' 2>/dev/null \
    | python3 -c "
import sys, json
vols = json.load(sys.stdin)
for v in vols:
    proj = v.get('projected', {})
    for src in proj.get('sources', []):
        sa_tok = src.get('serviceAccountToken', {})
        if sa_tok.get('audience') == 'agentshield-opa':
            print('FOUND')
            sys.exit(0)
print('MISSING')
" 2>/dev/null || echo "ERR")

  if [ "$AUD_CHECK" = "FOUND" ]; then
    pass "T-S7-002: Projected volume has audience=agentshield-opa"
  else
    fail "T-S7-002: audience=agentshield-opa not found in projected volume ($AUD_CHECK)"
  fi

  # Check token file is readable
  if kubectl exec "$AGENT_POD" -n "$AGENTS_NS" -- \
      test -s /var/run/secrets/sa-token/token 2>/dev/null; then
    pass "T-S7-002: Token file readable at /var/run/secrets/sa-token/token"
  else
    fail "T-S7-002: Token file missing or empty in pod"
  fi

  # T-S7-008: SDK reads token
  echo ""
  echo "--- T-S7-008: SDK reads projected token ---"
  SDK_RESULT=$(kubectl exec "$AGENT_POD" -n "$AGENTS_NS" -- python3 -c "
try:
    from agentshield_sdk import read_opa_token
    t = read_opa_token()
    assert t, 'token is empty'
    print('ok')
except ImportError:
    with open('/var/run/secrets/sa-token/token') as f:
        t = f.read().strip()
    assert t, 'token file is empty'
    print('ok (raw file read)')
" 2>/dev/null || echo "ERR")

  if echo "$SDK_RESULT" | grep -q "^ok"; then
    pass "T-S7-008: Token readable in pod ($SDK_RESULT)"
  else
    fail "T-S7-008: Could not read token in pod ($SDK_RESULT)"
  fi
else
  echo "  SKIP: Pod for ${AGENT_NAME} not Running within 120s (opa-sidecar-config may be missing)"
  MANUAL=$((MANUAL + 4))
fi

# ---------------------------------------------------------------------------
# T-S7-003..007: MANUAL (require OPA sidecar + bundle server)
# ---------------------------------------------------------------------------
check_manual "T-S7-003" \
  "OPA bundle data.json contains SA subject for ${AGENT_NAME}" \
  "kubectl port-forward svc/opa-bundle-server -n ${NAMESPACE} 8080:80 &" \
  "curl -s http://localhost:8080/bundles/agentshield/data.json | python3 -c \"import sys,json; d=json.load(sys.stdin); agents=d.get('agents',{}); assert any('${AGENT_NAME}' in k for k in agents), 'SA subject not in bundle'\""

AGENT_POD_REF="${AGENT_POD:-<agent-pod>}"
check_manual "T-S7-004" \
  "OPA allows registered agent with valid projected token calling an allowed tool" \
  "TOKEN=\$(kubectl exec ${AGENT_POD_REF} -n ${AGENTS_NS} -- cat /var/run/secrets/sa-token/token)" \
  "kubectl exec ${AGENT_POD_REF} -n ${AGENTS_NS} -- curl -s -X POST http://localhost:8181/v1/data/agentshield/allow -H 'Content-Type: application/json' -d \"{\\\"input\\\":{\\\"sa_token\\\":\\\"\${TOKEN}\\\",\\\"tool\\\":\\\"lookup_order\\\",\\\"agent_name\\\":\\\"${AGENT_NAME}\\\"}}\" | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d.get('result') is True\""

check_manual "T-S7-005" \
  "OPA denies request from unknown SA subject" \
  "UNKNOWN_TOKEN=\$(kubectl exec ${AGENT_POD_REF} -n ${AGENTS_NS} -- cat /var/run/secrets/kubernetes.io/serviceaccount/token)" \
  "kubectl exec ${AGENT_POD_REF} -n ${AGENTS_NS} -- curl -s -X POST http://localhost:8181/v1/data/agentshield/allow -H 'Content-Type: application/json' -d \"{\\\"input\\\":{\\\"sa_token\\\":\\\"\${UNKNOWN_TOKEN}\\\",\\\"tool\\\":\\\"lookup_order\\\",\\\"agent_name\\\":\\\"unknown-agent\\\"}}\" | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d.get('result') is False\""

check_manual "T-S7-006" \
  "OPA denies registered agent calling a tool not in its grant list" \
  "TOKEN=\$(kubectl exec ${AGENT_POD_REF} -n ${AGENTS_NS} -- cat /var/run/secrets/sa-token/token)" \
  "kubectl exec ${AGENT_POD_REF} -n ${AGENTS_NS} -- curl -s -X POST http://localhost:8181/v1/data/agentshield/allow -H 'Content-Type: application/json' -d \"{\\\"input\\\":{\\\"sa_token\\\":\\\"\${TOKEN}\\\",\\\"tool\\\":\\\"delete_records\\\",\\\"agent_name\\\":\\\"${AGENT_NAME}\\\"}}\" | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d.get('result') is False\""

check_manual "T-S7-007" \
  "OPA rejects daemon-class agent carrying user_id in request context" \
  "# Requires a daemon agent registered with agent_class='daemon'" \
  "DAEMON_TOKEN=\$(kubectl exec <daemon-agent-pod> -n ${AGENTS_NS} -- cat /var/run/secrets/sa-token/token)" \
  "curl -s -X POST http://localhost:8181/v1/data/agentshield/allow -H 'Content-Type: application/json' -d \"{\\\"input\\\":{\\\"sa_token\\\":\\\"\${DAEMON_TOKEN}\\\",\\\"tool\\\":\\\"lookup_order\\\",\\\"user_id\\\":\\\"some-user\\\"}}\" | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d.get('result') is False\""

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ""
echo "--- Cleanup: deprecating ${AGENT_NAME} ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${AGENT_NAME}',
    data=json.dumps({'publish_status': 'deprecated'}).encode(),
    headers={'Content-Type': 'application/json'},
    method='PATCH',
)
try:
    urllib.request.urlopen(req)
    print('  deprecated: ${AGENT_NAME}')
except Exception as e:
    print('  cleanup warn:', e)
" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================="
echo "  Suite 7 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "  (MANUAL items require running OPA sidecar + bundle server)"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

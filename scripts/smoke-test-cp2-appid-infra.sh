#!/usr/bin/env bash
# scripts/smoke-test-cp2-appid-infra.sh — Webhook Application Identity (Decision 30)
# Checkpoint 2 infra gate (CP2b) — gateway cutover + trigger soft-auth deployed.
#
# CP2 gates Phases 4-5 (T009-T013): the event-gateway now resolves applications +
# artifact_role_grants (webhook_auth.py::lookup_application / has_active_invoker_grant)
# instead of webhook_clients, and the old webhook_clients WRITE endpoints are retired to
# 410. This gate proves the RUNNING images carry that code — not just the tags.
#
# A TAG IS A CLAIM ABOUT CONTENT, NOT CONTENT (same lesson as CP1b / smoke-test-cp1-ws4-
# infra.sh): with imagePullPolicy=IfNotPresent a tag that was not actually rebuilt serves
# stale bytes while every tag-only check stays green. So T-...-005 greps the RUNNING
# gateway image for symbols that exist only after the cutover.
#
#   T-CP2B-APPID-001  event-gateway pods Running on image tag 0.1.4, 0 crashloops
#   T-CP2B-APPID-002  registry-api pods Running on image tag 0.2.224 (carries the
#                     create_grant auth_mode flip CP2c depends on), 0 crashloops
#   T-CP2B-APPID-003  gateway /health reachable in-cluster (from the registry-api pod,
#                     over the real Service DNS agentshield-event-gateway:8091) -> 200
#   T-CP2B-APPID-004  old webhook_clients registration is retired LIVE: POST
#                     /api/v1/triggers/{uuid}/clients -> 410 (raised before any DB/auth)
#   T-CP2B-APPID-005  CONTENT, not tag: the RUNNING event-gateway image really carries the
#                     cutover — webhook_auth has lookup_application + has_active_invoker_grant
#                     and no longer has the pre-cutover lookup_webhook_client
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
pass() { echo "PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== CP2b: Webhook Application Identity infra gate (gateway cutover) ==="

# --- pod health + tag (shared shape with CP1b) --------------------------------------
check_pods() {  # $1=label $2=want_tag $3=test_id $4=human
  local imgs phases waiting
  imgs=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=$1" \
    -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{"\n"}{end}{end}' 2>/dev/null || true)
  phases=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=$1" \
    -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' 2>/dev/null || true)
  waiting=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=$1" \
    -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}{end}' 2>/dev/null || true)
  if [ -z "$phases" ]; then
    fail "$3 $4 pods Running on $2" "no pods found for label $1"; return
  fi
  if echo "$waiting" | grep -q "CrashLoopBackOff"; then
    fail "$3 $4 pods Running on $2" "CrashLoopBackOff present: $(echo "$waiting" | tr '\n' ' ')"; return
  fi
  if echo "$phases" | grep -qv "^Running$" && [ -n "$(echo "$phases" | grep -v '^Running$' | tr -d '[:space:]')" ]; then
    fail "$3 $4 pods Running on $2" "non-Running phase: $(echo "$phases" | tr '\n' ' ')"; return
  fi
  if ! echo "$imgs" | grep -q ":$2\$"; then
    fail "$3 $4 pods Running on $2" "expected tag :$2 — running images: $(echo "$imgs" | tr '\n' ' ')"; return
  fi
  pass "$3 $4 pods Running on image tag $2, no crashloops"
}
check_pods "event-gateway" "0.1.4"   "T-CP2B-APPID-001" "event-gateway"
check_pods "registry-api"  "0.2.224" "T-CP2B-APPID-002" "registry-api"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
GW_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=event-gateway \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ] || [ -z "$GW_POD" ]; then
  echo "FAIL  T-CP2B-APPID-FIXTURE  |  missing Running pod (api='$API_POD' gw='$GW_POD') — cannot assert further"
  echo "=== CP2b summary: PASS=$PASS FAIL=$((FAIL+1)) ==="
  exit 1
fi

# --- T-CP2B-APPID-003: gateway /health reachable over the real Service DNS -----------
HEALTH=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -c \"
import urllib.request
r = urllib.request.urlopen('http://agentshield-event-gateway:8091/health', timeout=10)
print('HEALTH', r.status)
\"" 2>&1 || true)
if echo "$HEALTH" | grep -q "HEALTH 200"; then
  pass "T-CP2B-APPID-003 gateway /health reachable in-cluster (agentshield-event-gateway:8091) -> 200"
else
  fail "T-CP2B-APPID-003 gateway /health reachable" "$(echo "$HEALTH" | tr '\n' ' ' | tail -c 300)"
fi

# --- T-CP2B-APPID-004: old webhook_clients registration retired LIVE (410) -----------
# The 410 is raised as the FIRST statement of create_webhook_client, before any DB or
# auth, so no bearer token is needed to observe it (get_optional_user tolerates none).
GONE=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -c \"
import json, urllib.request, urllib.error
req = urllib.request.Request(
    'http://localhost:8000/api/v1/triggers/00000000-0000-0000-0000-000000000000/clients',
    data=json.dumps({'client_id':'cp2-probe'}).encode(),
    headers={'Content-Type':'application/json'}, method='POST')
try:
    r = urllib.request.urlopen(req, timeout=10); print('STATUS', r.status)
except urllib.error.HTTPError as e:
    print('STATUS', e.code)
\"" 2>&1 || true)
if echo "$GONE" | grep -q "STATUS 410"; then
  pass "T-CP2B-APPID-004 POST /triggers/{id}/clients -> 410 (webhook_clients registration retired live)"
else
  fail "T-CP2B-APPID-004 webhook_clients registration 410" "$(echo "$GONE" | tr '\n' ' ' | tail -c 300)"
fi

# --- T-CP2B-APPID-005: CONTENT, not the tag ------------------------------------------
GW_CONTENT=$(kubectl exec -n "$NAMESPACE" "$GW_POD" -c event-gateway -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -c \"
import webhook_auth as w
assert hasattr(w, 'lookup_application'), 'missing lookup_application'
assert hasattr(w, 'has_active_invoker_grant'), 'missing has_active_invoker_grant'
assert not hasattr(w, 'lookup_webhook_client'), 'pre-cutover lookup_webhook_client still present'
print('GW_CONTENT_OK')
\"" 2>&1 || true)
if echo "$GW_CONTENT" | grep -q "GW_CONTENT_OK"; then
  pass "T-CP2B-APPID-005 RUNNING gateway image carries the applications/grants cutover — content verified, not just the tag"
else
  fail "T-CP2B-APPID-005 running gateway carries the cutover" "$(echo "$GW_CONTENT" | tr '\n' ' ' | tail -c 300)"
fi

echo ""
echo "=== CP2b summary: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -ne 0 ] && { echo "❌ CP2b FAILED"; exit 1; }
echo "✅ CP2b PASSED"

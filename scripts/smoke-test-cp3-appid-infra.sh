#!/usr/bin/env bash
# scripts/smoke-test-cp3-appid-infra.sh — Webhook Application Identity (Decision 30)
# Checkpoint 3 infra gate (CP3b) — Studio UX (Phases 6-8, T014-T022) deployed.
#
# CP3 gates the Studio layer: the shared InvokeAccessPanel + ArtifactGrantsList wired
# into the agent (SettingsTab) and workflow (WorkflowTriggersPanel) surfaces, the new
# team-scoped ApplicationsPage, and the /applications route + Sidebar entry. This gate
# proves the RUNNING studio image serves that build — not just that a tag moved.
#
# A TAG IS A CLAIM ABOUT CONTENT, NOT CONTENT (same lesson as CP1b/CP2b): with
# imagePullPolicy=IfNotPresent a tag that was not actually rebuilt serves stale bytes
# while every tag-only check stays green. Studio's Docker build runs `tsc && vite build`,
# so a TypeScript error on T014-T022 fails the build before it ever reaches a tag — that
# build is itself the type gate. Here we additionally assert the SPA route serves and the
# served bundle carries the new /applications route string.
#
#   T-CP3B-APPID-001  studio pods Running on image tag 0.1.159, 0 crashloops
#   T-CP3B-APPID-002  studio SPA shell serves (GET / -> 200) at the edge
#   T-CP3B-APPID-003  /applications client route serves the SPA shell (GET -> 200)
#   T-CP3B-APPID-004  CONTENT, not tag: the served JS bundle references the /applications
#                     route (proves the deployed build actually carries Phase 8)
#
# EKS: the edge is the internal NLB (the ELB DNS). Override BASE_URL for docker-desktop
# (https://agentshield.127.0.0.1.nip.io:8443). Uses curl -k (self-signed gateway cert).
set -uo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
WANT_TAG="${STUDIO_TAG:-0.1.159}"

# Resolve the edge base URL. Default: the EKS Envoy NLB (the ELB DNS the studio route owns).
if [ -z "${BASE_URL:-}" ]; then
  ELB=$(kubectl get svc -n envoy-gateway-system \
    -l gateway.envoyproxy.io/owning-gateway-name=agentshield-gateway \
    -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
  if [ -n "$ELB" ]; then BASE_URL="https://${ELB}"; else BASE_URL="https://agentshield.127.0.0.1.nip.io:8443"; fi
fi

PASS=0; FAIL=0
pass() { echo "PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== CP3b: Webhook Application Identity infra gate (Studio UX) ==="
echo "    base URL: $BASE_URL"
echo ""

# --- T-CP3B-APPID-001: studio pods Running on the expected tag, no crashloops ---------
imgs=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio \
  -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{"\n"}{end}{end}' 2>/dev/null || true)
phases=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio \
  -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' 2>/dev/null || true)
waiting=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio \
  -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}{end}' 2>/dev/null || true)
if [ -z "$phases" ]; then
  fail "T-CP3B-APPID-001 studio pods Running on $WANT_TAG" "no studio pods found"
elif echo "$waiting" | grep -q "CrashLoopBackOff"; then
  fail "T-CP3B-APPID-001 studio pods Running on $WANT_TAG" "CrashLoopBackOff: $(echo "$waiting" | tr '\n' ' ')"
elif echo "$phases" | grep -qv "^Running$" && [ -n "$(echo "$phases" | grep -v '^Running$' | tr -d '[:space:]')" ]; then
  fail "T-CP3B-APPID-001 studio pods Running on $WANT_TAG" "non-Running: $(echo "$phases" | tr '\n' ' ')"
elif ! echo "$imgs" | grep -q ":$WANT_TAG\$"; then
  fail "T-CP3B-APPID-001 studio pods Running on $WANT_TAG" "expected :$WANT_TAG — running: $(echo "$imgs" | tr '\n' ' ')"
else
  pass "T-CP3B-APPID-001 studio pods Running on image tag $WANT_TAG, no crashloops"
fi

# --- T-CP3B-APPID-002: SPA shell serves at the edge -----------------------------------
code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 15 "$BASE_URL/" 2>/dev/null || echo 000)
[ "$code" = "200" ] && pass "T-CP3B-APPID-002 studio SPA shell serves (GET / -> 200)" \
  || fail "T-CP3B-APPID-002 studio SPA shell serves" "GET / -> $code"

# --- T-CP3B-APPID-003: /applications client route serves the SPA shell ----------------
code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 15 "$BASE_URL/applications" 2>/dev/null || echo 000)
[ "$code" = "200" ] && pass "T-CP3B-APPID-003 /applications route serves (GET -> 200)" \
  || fail "T-CP3B-APPID-003 /applications route serves" "GET /applications -> $code"

# --- T-CP3B-APPID-004: CONTENT — the served JS bundle references the /applications route
# The SPA shell references its hashed bundle; fetch it and grep for the route string that
# only exists once Phase 8 (T021) shipped. Minified, so we match the literal "/applications".
# STREAM curl -> grep (never capture the ~1.3MB minified bundle into a bash variable — a
# NUL byte in the JS truncates the var and yields a false negative) and --compressed
# (nginx may gzip it; grepping raw gzip bytes would also false-negative).
asset=$(curl -sk --max-time 15 "$BASE_URL/" 2>/dev/null | grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' | head -1)
if [ -z "$asset" ]; then
  fail "T-CP3B-APPID-004 served bundle references /applications" "could not find /assets/index-*.js in shell"
else
  # Count matches into a var: grep -c reads the WHOLE stream, so curl finishes
  # normally (no SIGPIPE that `set -o pipefail` + `grep -q` would turn into a
  # false negative). --compressed so nginx gzip is decoded before grep.
  hits=$(curl -sk --compressed --max-time 30 "$BASE_URL$asset" 2>/dev/null | grep -c '/applications' || true)
  if [ "${hits:-0}" -gt 0 ]; then
    pass "T-CP3B-APPID-004 served bundle ($asset) references the /applications route ($hits hit(s))"
  else
    fail "T-CP3B-APPID-004 served bundle references /applications" "route string absent in $asset (stale build?)"
  fi
fi

echo ""
echo "=== CP3b summary: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]

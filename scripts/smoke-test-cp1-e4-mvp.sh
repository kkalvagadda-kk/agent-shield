#!/usr/bin/env bash
# scripts/smoke-test-cp1-e4-mvp.sh
#
# Eval v2 E-4 — [CP1c]: the MVP gate.
#
# A TAG IS A CLAIM ABOUT CONTENT — THIS VERIFIES THE CONTENT.
# `docs/bugs/e3-never-ran-tag-not-bumped.md`: E-3's code never executed for an ENTIRE
# slice because a tag was never bumped, while every static check stayed green (both tag
# files agreed on a stale tag, and the cluster matched it). Six assertions then failed
# at once, far from the cause. So this gate does not ask "do the tags agree?" (that is
# [CP1e]'s job) — it asks the RUNNING artifacts what code they actually contain.
#
# It deliberately does NOT re-run the expensive real eval: suite-77 owns that
# (T-S77-000..010 + T-S77-999 + T-S77-COMPLETE), and duplicating a ~30-minute real
# eval-runner Job in a second script would buy nothing but drift between two copies of
# the same assertions. This gate verifies the DEPLOYED CONTENT and the cheap gates, then
# points at suite-77 for behaviour.
#
# Usage:
#   bash scripts/smoke-test-cp1-e4-mvp.sh            # content + cheap gates
#   RUN_SUITE=1 bash scripts/smoke-test-cp1-e4-mvp.sh  # ...and the full suite-77
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

NAMESPACE="${NAMESPACE:-agentshield-platform}"
DEPLOY_SH="scripts/deploy-cpe2e.sh"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== E-4 CP1c — MVP gate (deployed CONTENT, not just tags) ==="
echo ""

REGISTRY_API_TAG=$(grep -E '^REGISTRY_API_TAG=' "$DEPLOY_SH" | head -1 | cut -d'"' -f2)
STUDIO_TAG=$(grep -E '^STUDIO_TAG=' "$DEPLOY_SH" | head -1 | cut -d'"' -f2)
EVAL_RUNNER_TAG=$(grep -E '^EVAL_RUNNER_TAG=' "$DEPLOY_SH" | head -1 | cut -d'"' -f2)

echo "--- the DEPLOYED registry-api actually carries E-4's code ---"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  bad "CP1c a registry-api pod is running" "no Running registry-api pod in $NAMESPACE"
else
  RUNNING_IMG=$(kubectl get pod -n "$NAMESPACE" "$API_POD" -o jsonpath='{.spec.containers[0].image}')
  if [ "$RUNNING_IMG" = "registry.internal/agentshield/registry-api:$REGISTRY_API_TAG" ]; then
    ok "CP1c the running registry-api pod is on the tag deploy-cpe2e.sh pins" "$RUNNING_IMG"
  else
    bad "CP1c the running registry-api pod is on the tag deploy-cpe2e.sh pins" \
        "pod runs '$RUNNING_IMG' but deploy-cpe2e.sh pins '$REGISTRY_API_TAG'"
  fi

  SF=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- sh -c "grep -c 'def score_filter' /app/judge.py" 2>/dev/null || true)
  SI=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- sh -c "grep -c 'def score_injection' /app/judge.py" 2>/dev/null || true)
  WD=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- sh -c "grep -c 'def _webhook_driving_message' /app/routers/playground.py" 2>/dev/null || true)
  IP=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- sh -c "grep -c 'input_payload=body.payload' /app/routers/playground.py" 2>/dev/null || true)
  if [ "${SF:-0}" -ge 1 ] && [ "${SI:-0}" -ge 1 ] && [ "${WD:-0}" -ge 1 ] && [ "${IP:-0}" -ge 1 ]; then
    ok "CP1c the DEPLOYED registry-api image CONTAINS E-4's code" \
       "score_filter=$SF score_injection=$SI _webhook_driving_message=$WD input_payload=body.payload=$IP"
  else
    bad "CP1c the DEPLOYED registry-api image CONTAINS E-4's code" \
        "score_filter=$SF score_injection=$SI _webhook_driving_message=$WD input_payload=body.payload=$IP — the tag moved but the content did not"
  fi

  # THE ROUTE ACTUALLY BINDS TO THE HANDLER. A decorator binds to the NEXT function, so
  # a helper inserted under `@router.post("/test-event")` silently STEALS the route: the
  # door then returns 200 + an echo of its own request body for every input, including a
  # nonexistent agent, and the real handler becomes unreachable. The pod starts clean and
  # every static check stays green. Only a real request finds it.
  PROBE=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import httpx
r = httpx.post('http://localhost:8000/api/v1/playground/test-event',
               json={'agent_name':'cp1c-nonexistent-probe','payload':{'a':1}})
print(f'{r.status_code}|{type(r.json()).__name__}')
" 2>/dev/null || true)
  P_STATUS="${PROBE%%|*}"; P_TYPE="${PROBE##*|}"
  if [ "$P_STATUS" = "404" ] && [ "$P_TYPE" = "dict" ]; then
    ok "CP1c POST /playground/test-event is bound to the REAL handler (404 for an unknown agent, JSON object body)" \
       "status=$P_STATUS json type=$P_TYPE"
  else
    bad "CP1c POST /playground/test-event is bound to the REAL handler" \
        "status=$P_STATUS json type=$P_TYPE — want 404/dict. A 200 returning a str means a helper STOLE the route and the door is echoing its input"
  fi
fi

echo ""
echo "--- the eval-runner IMAGE the Job will launch actually carries E-4's branch ---"
# The image that matters is the one registry-api hands to the Job (EVAL_RUNNER_IMAGE),
# not the chart's decorative pin. Read it from the RUNNING pod's env, then inspect that
# exact image — miss this and the Job runs the OLD runner while everything else is green.
JOB_IMG=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- sh -c 'echo -n "$EVAL_RUNNER_IMAGE"' 2>/dev/null || true)
if [ -z "$JOB_IMG" ]; then
  bad "CP1c the registry-api pod knows which eval-runner image to launch" "EVAL_RUNNER_IMAGE is empty in the pod env"
else
  if [ "$JOB_IMG" = "registry.internal/agentshield/eval-runner:$EVAL_RUNNER_TAG" ]; then
    ok "CP1c the eval Job will launch the tag deploy-cpe2e.sh pins" "EVAL_RUNNER_IMAGE=$JOB_IMG"
  else
    bad "CP1c the eval Job will launch the tag deploy-cpe2e.sh pins" \
        "pod env says '$JOB_IMG' but deploy-cpe2e.sh pins '$EVAL_RUNNER_TAG' — the Job would run the OLD runner"
  fi
  RUNNER_CONTENT=$(docker run --rm --entrypoint sh "$JOB_IMG" -c "
echo -n \$(grep -c 'def _run_webhook_item' /app/main.py):\$(grep -c 'def _resolve_item_handler' /app/main.py):\$(grep -c '\"reactive\": _run_reactive_item' /app/main.py)
" 2>/dev/null || true)
  WEBHOOK_N="${RUNNER_CONTENT%%:*}"
  REST="${RUNNER_CONTENT#*:}"; MAP_N="${REST%%:*}"; REACT_N="${REST##*:}"
  if [ "${WEBHOOK_N:-0}" -ge 1 ] && [ "${MAP_N:-0}" -ge 1 ] && [ "${REACT_N:-0}" -ge 1 ]; then
    ok "CP1c the eval-runner IMAGE contains the webhook branch AND the fail-closed handler map" \
       "_run_webhook_item=$WEBHOOK_N _resolve_item_handler=$MAP_N reactive-registered=$REACT_N in $JOB_IMG"
  else
    bad "CP1c the eval-runner IMAGE contains the webhook branch AND the fail-closed handler map" \
        "_run_webhook_item=$WEBHOOK_N _resolve_item_handler=$MAP_N reactive-registered=$REACT_N in $JOB_IMG — the tag moved but the content did not (this is the E-3 bug verbatim)"
  fi
fi

echo ""
echo "--- the SERVED studio bundle actually carries E-4's UI ---"
STUDIO_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$STUDIO_POD" ]; then
  bad "CP1c a studio pod is running" "no Running studio pod in $NAMESPACE"
else
  S_IMG=$(kubectl get pod -n "$NAMESPACE" "$STUDIO_POD" -o jsonpath='{.spec.containers[0].image}')
  if [ "$S_IMG" = "registry.internal/agentshield/studio:$STUDIO_TAG" ]; then
    ok "CP1c the running studio pod is on the tag deploy-cpe2e.sh pins" "$S_IMG"
  else
    bad "CP1c the running studio pod is on the tag deploy-cpe2e.sh pins" \
        "pod runs '$S_IMG' but deploy-cpe2e.sh pins '$STUDIO_TAG'"
  fi
  # grep the SERVED bundle, not the source — that is the artifact a browser gets.
  HITS=$(kubectl exec -n "$NAMESPACE" "$STUDIO_POD" -- sh -c \
    'grep -l "webhook-trigger-payload" /usr/share/nginx/html/assets/*.js 2>/dev/null | head -1' 2>/dev/null || true)
  HITS2=$(kubectl exec -n "$NAMESPACE" "$STUDIO_POD" -- sh -c \
    'grep -l "injection-asr" /usr/share/nginx/html/assets/*.js 2>/dev/null | head -1' 2>/dev/null || true)
  if [ -n "$HITS" ] && [ -n "$HITS2" ]; then
    ok "CP1c the SERVED studio bundle contains E-4's editor + injection evidence" \
       "webhook-trigger-payload and injection-asr both present in $(basename "$HITS")"
  else
    bad "CP1c the SERVED studio bundle contains E-4's editor + injection evidence" \
        "webhook-trigger-payload=[$HITS] injection-asr=[$HITS2] — the bundle a browser gets is pre-E-4"
  fi
fi

echo ""
echo "--- the cheap gates ---"
if (cd studio && npm run typecheck >/dev/null 2>&1); then
  ok "CP1c studio typecheck" "tsc --noEmit clean"
else
  bad "CP1c studio typecheck" "tsc --noEmit reported errors"
fi

if (cd studio && npm run test >/dev/null 2>&1); then
  ok "CP1c studio Vitest" "all component tests green"
else
  bad "CP1c studio Vitest" "component tests FAILED"
fi

for f in services/registry-api/routers/playground.py services/registry-api/judge.py \
         services/registry-api/schemas.py services/registry-api/routers/eval_runner.py \
         services/eval-runner/main.py; do
  if python3 -c "import ast,sys; ast.parse(open('$f').read())" 2>/dev/null; then
    ok "CP1c ast.parse $(basename "$f")" "parses"
  else
    bad "CP1c ast.parse $(basename "$f")" "SyntaxError"
  fi
done

if grep -q "suite-77" scripts/e2e/run-all.sh && [ -x scripts/e2e/suite-77-eval-v2-webhook.sh ]; then
  ok "CP1c suite-77 is registered in run-all.sh and executable" "the gate runs in CI"
else
  bad "CP1c suite-77 is registered in run-all.sh and executable" "an unregistered suite is a gate that never runs"
fi

if [ "${RUN_SUITE:-0}" = "1" ]; then
  echo ""
  echo "--- suite-77 (the real behaviour gate — ~30-45 min) ---"
  if bash scripts/e2e/suite-77-eval-v2-webhook.sh; then
    ok "CP1c suite-77 green" "T-S77-000..010 + T-S77-999 + T-S77-COMPLETE all pass"
  else
    bad "CP1c suite-77 green" "suite-77 FAILED — see its output above"
  fi
else
  echo ""
  echo "  NOTE  suite-77 not run (RUN_SUITE=1 to include it). It is the BEHAVIOUR gate:"
  echo "        this script only proves the deployed artifacts carry E-4's code."
fi

echo ""
echo "=== E-4 CP1c MVP gate: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ E-4 CP1c MVP gate FAILED"
  exit 1
fi
echo "✅ E-4 CP1c MVP gate PASSED (deployed CONTENT verified in all three images; route binds to the real handler; cheap gates green)"

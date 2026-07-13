#!/usr/bin/env bash
# Suite 54: Governed tool-schema build (regression for KeyError graph-build crash)
#
# Root cause (fixed in declarative-runner:0.1.39 / SDK): the governed tool wrapper's
# executor emitted a __signature__ with a parameter absent from __annotations__
# (`**kwargs` for python tools; the `params` catch-all for arg-less HTTP tools), so
# LangChain 1.x introspection raised KeyError('kwargs')/KeyError('params') at graph
# build and the agent pod CrashLooped at startup.
#
# This suite runs INSIDE a declarative-runner pod (the layer that actually builds the
# graph — registry-api never imports the SDK) and proves the shipped image binds all
# four tool shapes and keeps the injected graph_state out of the model-facing schema.
# Tests T-S54-001 through T-S54-004.
set -euo pipefail

PASS=0; FAIL=0
pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "==> Suite 54: Governed tool-schema build"
echo ""

# Find any running declarative-runner pod (agents run in agents-* namespaces).
RUNNER=$(kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{" "}{.status.phase}{" "}{.spec.containers[*].image}{"\n"}{end}' 2>/dev/null \
  | grep 'declarative-runner' | grep ' Running ' | head -1 || true)
if [ -z "${RUNNER:-}" ]; then
  echo "  SKIP: no running declarative-runner pod found (deploy a declarative agent first)"
  echo "==> Suite 54 SKIPPED"
  exit 0
fi
RNS=$(echo "$RUNNER" | awk '{print $1}')
RPOD=$(echo "$RUNNER" | awk '{print $2}')
RCONTAINER=$(kubectl get pod -n "$RNS" "$RPOD" -o jsonpath='{.spec.containers[0].name}')
echo "--- Target: $RNS/$RPOD (container=$RCONTAINER) ---"

OUT=$(kubectl exec -n "$RNS" "$RPOD" -c "$RCONTAINER" -- python3 -c '
import inspect
from langchain_core.tools import tool as lc_tool
from agentshield_sdk.tool_executor import HttpToolExecutor, PythonToolExecutor
from agentshield_sdk.graph_builder import _wrap_tool_with_governance

def model_props(fn):
    governed = _wrap_tool_with_governance(fn, "s54-agent")
    # invariant: every signature param has an annotation
    sig = inspect.signature(governed)
    missing = [n for n in sig.parameters if n not in getattr(governed, "__annotations__", {})]
    assert not missing, f"unannotated params: {missing}"
    lc = lc_tool(governed)  # raised KeyError before the fix
    props = list(lc.tool_call_schema.model_json_schema().get("properties", {}))
    assert "graph_state" not in props, "graph_state leaked into model schema"
    return props

py_none = PythonToolExecutor(name="s54_calc", risk="high",
    python_code="def main(**k): return 1", description="calc").as_tool_callable()
py_schema = PythonToolExecutor(name="s54_refund", risk="high",
    python_code="def main(**k): return 1", description="refund",
    input_schema={"type":"object","required":["order_id","amount"],
                  "properties":{"order_id":{"type":"string"},"amount":{"type":"number"}}}).as_tool_callable()
http_tmpl = HttpToolExecutor(name="s54_http", risk="high", method="POST",
    url="https://x/{{order_id}}", headers={}, body_template="", description="h").as_tool_callable()
http_none = HttpToolExecutor(name="s54_http_noargs", risk="high", method="GET",
    url="https://x/status", headers={}, body_template="", description="h").as_tool_callable()

print("T1", model_props(py_none))
print("T2", model_props(py_schema))
print("T3", model_props(http_tmpl))
print("T4", model_props(http_none))
print("ALL_OK")
' 2>&1) || true

echo "$OUT" | sed "s/^/    /"

echo "$OUT" | grep -q "^ALL_OK$" || { echo "  (assertion script did not complete)"; }

# T-S54-001: python tool with NO input_schema binds (was KeyError('kwargs'))
if echo "$OUT" | grep -q "^T1 \['kwargs'\]"; then
  pass "T-S54-001 python high-risk tool without input_schema builds graph schema"
else fail "T-S54-001 python without input_schema — expected model props ['kwargs']"; fi

# T-S54-002: python tool WITH input_schema yields named, typed params
if echo "$OUT" | grep -q "^T2 \['order_id', 'amount'\]"; then
  pass "T-S54-002 python tool derives named params from input_schema"
else fail "T-S54-002 python input_schema — expected ['order_id', 'amount']"; fi

# T-S54-003: HTTP template tool unchanged
if echo "$OUT" | grep -q "^T3 \['order_id'\]"; then
  pass "T-S54-003 HTTP templated tool builds (unchanged)"
else fail "T-S54-003 HTTP template — expected ['order_id']"; fi

# T-S54-004: arg-less HTTP tool binds (was KeyError('params'))
if echo "$OUT" | grep -q "^T4 \['params'\]"; then
  pass "T-S54-004 arg-less HTTP tool builds graph schema"
else fail "T-S54-004 arg-less HTTP — expected model props ['params']"; fi

echo ""
echo "==> Suite 54: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

#!/usr/bin/env bash
# scripts/seed-e2e-fixtures.sh — the always-running agents the e2e suites assume exist.
#
# WHY THIS EXISTS
# ---------------
# Three bash suites and nine Playwright specs were red for one reason: they assume a
# deployed, RUNNING agent and nothing creates one. They were being read as twelve separate
# failures. They are one missing fixture.
#
#   suite-45-hitl-e2e                 needs `hitl-agent` — reactive, web_search (risk=high)
#   suite-59-workflow-orchestrations  needs wf-router / wf-payout / wf-confirm / wf-supervisor
#   suite-60-single-agent-durable-hitl needs `wf-payout` durable + running
#   9 Playwright specs                need any agent that completes a turn (deployment
#                                     overview, HITL chat, History dock, workflow console)
#
# Measured before writing this: `web_search` exists at risk=high; wf-payout / wf-confirm /
# wf-supervisor exist as agent ROWS with **zero running deployments**; wf-router and
# hitl-agent do not exist at all. So the gap is mostly DEPLOY, not create — which is why
# "the agents are there" kept being the wrong conclusion.
#
# WHY NOT seed-defaults.sh
# ------------------------
# That script seeds PLATFORM defaults for a fresh install (tools, skills, the demo agents).
# Test scaffolding is a different concern with a different lifetime, and folding it in would
# mean every install carries e2e fixtures. Separate script, called deliberately.
#
# NOTE seed-defaults.sh sends NO Authorization header, so its agent creates have been
# failing silently since R2 gated POST /agents/. Not fixed here — different blast radius,
# and it deserves its own change. Recorded in docs/testing/red-test-ledger.md.
#
# IDEMPOTENT. 409 on create = already there; an already-running deployment is left alone.
# Safe to re-run, and meant to be: the cluster loses pods to eviction and node pressure.
#
#   bash scripts/seed-e2e-fixtures.sh
#   NAMESPACE=agentshield-platform bash scripts/seed-e2e-fixtures.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
TEAM="${TEAM:-platform}"

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || { echo "ERROR: no Running registry-api pod in $NAMESPACE"; exit 1; }

# Agent create/deploy are gated (R2/R3). Call e2e_set_token BARE — a command substitution
# swallows its abort (lib/e2e-auth.sh).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/e2e/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

echo "=== Seeding e2e fixture agents (team=$TEAM) ==="

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env E2E_TOKEN="$E2E_TOKEN" TEAM="$TEAM" python3 - <<'PY'
import json, os, sys, time, urllib.error, urllib.request

BASE = "http://localhost:8000/api/v1"
TEAM = os.environ["TEAM"]
H = {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["E2E_TOKEN"]}

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=H, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else {})
        except Exception:
            return e.code, {"raw": raw[:200].decode("utf-8", "replace")}

# name -> (execution_shape, [tool names to bind])
#
# `hitl-agent` binds web_search deliberately: suite-45 asserts the OPA bundle carries it at
# risk=high, which is what routes its calls to the HITL queue. An agent without it makes
# every one of that suite's cases unreachable rather than failing — which is how it read as
# "3 passed" while proving nothing.
FIXTURES = {
    "hitl-agent":     ("reactive", ["web_search"]),
    "wf-router":      ("durable",  []),
    "wf-payout":      ("durable",  ["web_search"]),
    "wf-confirm":     ("durable",  []),
    "wf-supervisor":  ("durable",  []),
}

def items_of(body):
    """Endpoints here are not consistent: some return {"items": [...]}, some a bare list.
    Normalising once beats a .get() that explodes on whichever one you did not test."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        return body.get("items", [])
    return []

# Resolve tool ids once.
_, listing = call("GET", "/tools/?limit=200")
tool_id = {t["name"]: t["id"] for t in items_of(listing)}

created = deployed = already = 0
problems = []

for name, (shape, tools) in FIXTURES.items():
    sc, body = call("POST", "/agents/", {
        "name": name, "team": TEAM, "agent_type": "declarative",
        "execution_shape": shape, "agent_class": "user_delegated",
        "description": f"e2e fixture — always-running {shape} agent",
        "metadata": {"instructions": "You are a test fixture. Answer briefly."},
    })
    if sc == 201:
        created += 1
    elif sc != 409:
        problems.append(f"{name}: create -> {sc} {body}")
        continue

    for t in tools:
        if t in tool_id:
            call("POST", f"/agents/{name}/tools", {"tool_id": tool_id[t]})
        else:
            problems.append(f"{name}: tool {t!r} not in the registry")

    # Already running? Leave it. Re-deploying a healthy fixture is how a green suite turns
    # red for thirty seconds in the middle of somebody else's run.
    _, deps = call("GET", f"/agents/{name}/deployments")
    items = items_of(deps)
    if any(d.get("status") == "running" for d in items):
        already += 1
        continue

    # eval_passed: the publish gate (Decision 20) and the deploy gate both read it. A
    # fixture that cannot pass its own gates is not a fixture.
    sc, ver = call("POST", f"/agents/{name}/versions", {
        "image_tag": "registry.internal/agentshield/declarative-runner:seed",
        "eval_passed": True, "adversarial_eval_passed": True,
        "notes": "e2e fixture seed",
    })
    vid = ver.get("id")
    if not vid:
        _, vlist = call("GET", f"/agents/{name}/versions")
        vitems = items_of(vlist)
        vid = vitems[0].get("id") if vitems else None
    if not vid:
        problems.append(f"{name}: no version to deploy ({sc} {ver})")
        continue

    sc, dep = call("POST", f"/agents/{name}/deploy", {
        "version_id": vid, "environment": "sandbox", "replicas": 1,
    })
    if sc in (200, 201, 202):
        deployed += 1
    else:
        problems.append(f"{name}: deploy -> {sc} {dep}")

print(f"created={created} deployed={deployed} already_running={already}")
for p in problems:
    print("  PROBLEM " + p)
sys.exit(1 if problems else 0)
PY

rc=$?
echo ""
echo "=== waiting for fixture pods to become Running (up to 180s) ==="
for _ in $(seq 1 36); do
  up="$(kubectl get pods -n "agents-${TEAM}" --no-headers 2>/dev/null \
        | grep -cE '^(hitl-agent|wf-router|wf-payout|wf-confirm|wf-supervisor)-.*Running' || true)"
  echo "  running fixture pods: ${up}/5"
  [ "${up:-0}" -ge 5 ] && break
  sleep 5
done
exit "$rc"

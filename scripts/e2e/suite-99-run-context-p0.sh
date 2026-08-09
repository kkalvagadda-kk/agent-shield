#!/usr/bin/env bash
# scripts/e2e/suite-99-run-context-p0.sh
#
# E2E Suite 99: identity propagation P0 — RunContext primitive + signing-key wiring.
#
# WHAT P0 IS, AND WHAT IT IS NOT
# ------------------------------
# P0 builds the identity OBJECT and gets its signing key to every process that will need
# it. It deliberately threads NOTHING through a live run — that is P1. So this suite must
# not assert that a run carries identity; it asserts that the primitive is correct, the
# three vendored copies agree, and the key is actually mounted where P1 will expect it.
#
# WHY THE COPIES ARE CHECKED AT ALL
# ---------------------------------
# `run_context.py` exists three times (registry-api, declarative-runner, sdk) because no
# shared package spans services/* and sdk/ — each vendors its own dependencies. Three
# copies that drift are worse than one that is awkward: a token minted by registry-api and
# rejected by the runner would surface as an OPA denial naming a TOOL, three layers from
# the actual cause. T-S99-004 compares them mechanically so drift fails a test, not a
# production run.
#
# CASES
#   T-S99-001 — the signing key is mounted in registry-api          (env is non-empty)
#   T-S99-002 — mint -> verify round-trips inside the real pod      (not on a laptop)
#   T-S99-003 — a TAMPERED token is rejected                        (the security property)
#   T-S99-004 — the three vendored copies agree                     (byte-compare the core)
#   T-S99-005 — a token signed with the WRONG key is rejected
#   T-S99-006 — an EXPIRED token is rejected
#   T-S99-007 — extend() appends a hop and never replaces the chain (audit trail)
#   T-S99-008 — a missing key RAISES rather than degrading to unsigned
#
# 003/005/006/008 are the ones that matter. A run-context implementation that accepts a
# forged or expired token, or silently falls back to unsigned, is worse than having no
# identity at all: every downstream hop would treat attacker-supplied identity as verified
# and OPA would authorize it.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
CONTAINER="registry-api"

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || { echo "ERROR: no Running registry-api pod in $NAMESPACE"; exit 1; }

echo "=== Suite 99: identity P0 — RunContext primitive + key wiring ==="
echo "  Namespace: $NAMESPACE"
echo "  Pod:       $API_POD"
echo ""

PASS=0; FAIL=0
record() {
  if [ "$1" = "PASS" ]; then echo "PASS  $2"; PASS=$((PASS+1)); else echo "FAIL  $2"; FAIL=$((FAIL+1)); fi
}

# ── T-S99-001 — the key is actually mounted ───────────────────────────────────
KEYLEN="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- python3 -c '
import os; print(len(os.environ.get("AGENTSHIELD_INTERNAL_SIGNING_KEY","")))' 2>/dev/null | tr -d '\r\n' || echo 0)"
if [ "${KEYLEN:-0}" -gt 0 ]; then
  record PASS "T-S99-001 AGENTSHIELD_INTERNAL_SIGNING_KEY is mounted in registry-api  |  ${KEYLEN} chars"
else
  record FAIL "T-S99-001 AGENTSHIELD_INTERNAL_SIGNING_KEY is mounted in registry-api  |  EMPTY. The chart mounts it via secretKeyRef(optional) from Secret agentshield-run-context; deploy-cpe2e.sh / deploy-eks.sh create it. Without it registry-api cannot mint a run context and P1 has nothing to thread."
fi

# ── T-S99-002/003/005/006/007/008 — the primitive, exercised IN THE POD ───────
# In the pod, not on the host: the point is that the module works with the interpreter and
# the key the platform actually runs, which a laptop run cannot tell you.
RESULT="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- python3 -c '
import sys, os
sys.path.insert(0, "/app")
import run_context as rc

out = []
def check(tid, ok, detail):
    # Verdict precomputed: nesting escaped quotes inside an f-string inside a
    # single-quoted bash string is a SyntaxError, and it is the second time that
    # escaping has bitten this repo (see suite-42 T-S42-004).
    verdict = "PASS" if ok else "FAIL"
    out.append(f"{tid}|{verdict}|{detail}")

ctx = rc.RunContext(user_sub="s99-user", user_team="platform", origin="playground")

# 002 — round trip
try:
    v = rc.verify(rc.mint(ctx))
    check("T-S99-002", v.user_sub == "s99-user" and v.origin == "playground", f"user_sub={v.user_sub} origin={v.origin}")
except Exception as e:
    check("T-S99-002", False, f"raised {e!r}")

# 003 — tamper
try:
    t = rc.mint(ctx)
    p, _, s = t.rpartition(".")
    flipped = p[:-1] + ("A" if p[-1] != "A" else "B")
    try:
        rc.verify(flipped + "." + s); check("T-S99-003", False, "a TAMPERED payload verified")
    except rc.RunContextError:
        check("T-S99-003", True, "tampered payload rejected")
except Exception as e:
    check("T-S99-003", False, f"raised {e!r}")

# 005 — wrong key
try:
    t = rc.mint(ctx)
    real = os.environ["AGENTSHIELD_INTERNAL_SIGNING_KEY"]
    os.environ["AGENTSHIELD_INTERNAL_SIGNING_KEY"] = real + "-tampered"
    try:
        rc.verify(t); check("T-S99-005", False, "a token signed with another key verified")
    except rc.RunContextError:
        check("T-S99-005", True, "wrong-key token rejected")
    finally:
        os.environ["AGENTSHIELD_INTERNAL_SIGNING_KEY"] = real
except Exception as e:
    check("T-S99-005", False, f"raised {e!r}")

# 006 — expiry
try:
    try:
        rc.verify(rc.mint(ctx, ttl_seconds=-1)); check("T-S99-006", False, "an EXPIRED token verified")
    except rc.RunContextError:
        check("T-S99-006", True, "expired token rejected")
except Exception as e:
    check("T-S99-006", False, f"raised {e!r}")

# 007 — extend appends, never replaces
try:
    t = rc.extend(rc.extend(rc.mint(ctx), "agent-a"), "agent-b")
    chain = rc.verify(t).actor_chain
    check("T-S99-007", chain == ["agent-a", "agent-b"], f"chain={chain}")
except Exception as e:
    check("T-S99-007", False, f"raised {e!r}")

# 008 — a missing key must RAISE, never silently produce an unsigned token
try:
    real = os.environ.pop("AGENTSHIELD_INTERNAL_SIGNING_KEY")
    try:
        rc.mint(ctx); check("T-S99-008", False, "minted a token with NO signing key present")
    except rc.RunContextError:
        check("T-S99-008", True, "missing key raises instead of degrading to unsigned")
    finally:
        os.environ["AGENTSHIELD_INTERNAL_SIGNING_KEY"] = real
except Exception as e:
    check("T-S99-008", False, f"raised {e!r}")

print("\n".join(out))
' 2>&1 || true)"

for tid in T-S99-002 T-S99-003 T-S99-005 T-S99-006 T-S99-007 T-S99-008; do
  line="$(echo "$RESULT" | grep "^${tid}|" || true)"
  if [ -z "$line" ]; then
    record FAIL "${tid} produced no result  |  in-pod driver output: $(echo "$RESULT" | tail -3 | tr '\n' ' ')"
  elif [ "$(echo "$line" | cut -d'|' -f2)" = "PASS" ]; then
    record PASS "${tid} $(echo "$line" | cut -d'|' -f3)"
  else
    record FAIL "${tid} $(echo "$line" | cut -d'|' -f3)"
  fi
done

# ── T-S99-004 — the three vendored copies agree ───────────────────────────────
# Compares the shared core (everything from the first import to the end of extend()), not
# the docstrings — each copy documents why it exists and must not mint. Comparing whole
# files would fail on prose and train people to skip the check.
DIFFS="$(python3 - <<'PY'
import hashlib, pathlib, re
FILES = {
    "registry-api": "services/registry-api/run_context.py",
    "declarative-runner": "services/declarative-runner/run_context.py",
    "sdk": "sdk/agentshield_sdk/run_context.py",
}
def core(path):
    t = pathlib.Path(path).read_text()
    start = t.index("from __future__")
    end = t.index("def extend(")
    body = t[start:] if end < start else t[start:]
    # cut at the end of extend(): the SDK copy appends its ContextVar section after it
    m = re.search(r"\n(# -{10,}\n# Per-run ContextVars)", body)
    if m:
        body = body[:m.start()]
    # rstrip before hashing: the SDK copy appends its ContextVar section after extend(),
    # so its core ends with one extra newline. A one-byte difference in trailing
    # whitespace is not drift, and reporting it as drift would train people to ignore
    # this check — which is the only thing standing between a mint/verify mismatch and an
    # OPA denial that names a tool three layers from the cause.
    body = body.rstrip() + "\n"
    return hashlib.sha256(body.encode()).hexdigest(), len(body)
h = {k: core(v) for k, v in FILES.items()}
uniq = {v[0] for v in h.values()}
if len(uniq) == 1:
    print("IDENTICAL " + next(iter(uniq))[:12])
else:
    print("DRIFT " + " ".join(f"{k}={v[0][:8]}({v[1]}b)" for k, v in h.items()))
PY
)"
case "$DIFFS" in
  IDENTICAL*) record PASS "T-S99-004 the three vendored run_context.py copies agree  |  core sha ${DIFFS#IDENTICAL }" ;;
  *)          record FAIL "T-S99-004 the three vendored run_context.py copies agree  |  ${DIFFS}. A token minted by one and rejected by another surfaces as an OPA denial naming a TOOL, three layers from the cause." ;;
esac

echo ""
echo "=== Suite 99 Results: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

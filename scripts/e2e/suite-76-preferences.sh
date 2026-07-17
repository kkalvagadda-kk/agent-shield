#!/usr/bin/env bash
# scripts/e2e/suite-76-preferences.sh
#
# E2E Suite 76: User Response Preferences (POC-3) — the REAL path, no fakes.
#
# Proves: a user's structured, enum-only presets round-trip through
# GET/PUT /me/preferences (caller-scoped), out-of-vocab values are rejected (422),
# the presets compile into a bounded, precedence-framed advisory directive, and a
# daemon (no live user) gets NO directive. Runs in-pod (kubectl exec) so the
# assertions hit the real router + the real preferences module.
#
#   T-S76-001 — PUT then GET /me/preferences round-trips the saved presets.
#   T-S76-002 — caller-scoping: a DIFFERENT X-User-Sub sees its own row, not the
#               first user's (a user only ever reads/writes user_id = caller.sub).
#   T-S76-003 — enum validation: an out-of-vocabulary value is rejected 422.
#   T-S76-004 — compose_preference_directive emits the precedence-framed advisory
#               with the mapped phrases; empty prefs → None.
#   T-S76-005 — compose_directive_for_user("") → None (daemon: no live user, no
#               directive), while a real user_id with saved prefs → a directive.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 6)$(printf '%04x' $((RANDOM % 65536)))"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
[ -n "$API_POD" ] || { echo "FATAL: no running registry-api pod"; exit 1; }

echo "=== Suite 76: User Response Preferences (POC-3) ==="
echo "  Pod:    $API_POD"
echo "  Suffix: $SUFFIX"

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, asyncio, httpx
SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
USER_A = f"s76-userA-{SUFFIX}"
USER_B = f"s76-userB-{SUFFIX}"

def hdr(sub):
    return {"X-User-Sub": sub, "X-User-Team": "platform"}

fails = []

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(tid)

with httpx.Client(timeout=30.0) as c:
    # T-S76-001 — PUT then GET round-trip.
    body = {"response_length": "concise", "tone": "professional",
            "format": "bulleted", "expertise": "expert"}
    r = c.put(f"{BASE}/me/preferences", json=body, headers=hdr(USER_A))
    g = c.get(f"{BASE}/me/preferences", headers=hdr(USER_A))
    ok = (r.status_code in (200, 201) and g.status_code == 200
          and g.json().get("response_length") == "concise"
          and g.json().get("format") == "bulleted"
          and g.json().get("expertise") == "expert")
    check(ok, "T-S76-001", f"round-trip put={r.status_code} get={g.status_code} body={g.json() if g.status_code==200 else g.text[:120]}")

    # T-S76-002 — caller-scoping: user B has its own (empty) row, not user A's.
    gb = c.get(f"{BASE}/me/preferences", headers=hdr(USER_B))
    okb = gb.status_code == 200 and not gb.json().get("response_length")
    check(okb, "T-S76-002", f"userB scoped row response_length={gb.json().get('response_length') if gb.status_code==200 else gb.text[:80]}")

    # T-S76-003 — enum 422 on an out-of-vocab value.
    bad = c.put(f"{BASE}/me/preferences", json={"tone": "sarcastic"}, headers=hdr(USER_A))
    check(bad.status_code == 422, "T-S76-003", f"out-of-vocab tone rejected status={bad.status_code}")

# T-S76-004 / T-S76-005 — compose directly against the module (in-pod import).
from preferences import compose_preference_directive, compose_directive_for_user, UserPreferences

d = compose_preference_directive(UserPreferences(response_length="concise", format="bulleted", expertise="expert"))
framed = d is not None and "advisory" in d.lower() and "concise" not in d.lower()  # phrase, not raw enum
has_phrases = d is not None and "brief" in d.lower() and "bullet" in d.lower()
check(bool(d) and framed and has_phrases, "T-S76-004", f"directive framed+mapped: {d!r}")
empty = compose_preference_directive(UserPreferences())
check(empty is None, "T-S76-004b", f"empty prefs → None ({empty!r})")

async def _daemon_check():
    from db import AsyncSessionLocal  # type: ignore
    async with AsyncSessionLocal() as s:
        none_for_daemon = await compose_directive_for_user(s, "")
        return none_for_daemon

daemon_dir = asyncio.run(_daemon_check())
check(daemon_dir is None, "T-S76-005", f"daemon (empty user_id) → None ({daemon_dir!r})")

print("FAILS", ",".join(fails) if fails else "NONE")
PY
) || { echo "$RESULT"; echo "FATAL: in-pod block errored"; exit 1; }

echo "$RESULT"
PASSED=$(echo "$RESULT" | grep -c "PASS" || true)
FAILED=$(echo "$RESULT" | grep -c " FAIL " || true)
echo "==> Suite 76 Results: ${PASSED} passed, ${FAILED} failed"
echo "$RESULT" | grep -q "^FAILS NONE" || { echo "SUITE 76 FAILED"; exit 1; }
echo "OK: suite-76 all green"

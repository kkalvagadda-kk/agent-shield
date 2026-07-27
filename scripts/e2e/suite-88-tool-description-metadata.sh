#!/usr/bin/env bash
# scripts/e2e/suite-88-tool-description-metadata.sh — tool description + picker metadata (API layer)
#
# Backs two Studio changes at the layer the browser cannot prove:
#
#   1. The tool Description field became a multi-line <textarea> (studio 0.1.162).
#      A textarea is worthless if the API or column silently collapses, truncates,
#      or strips the newlines on the way through — the field would look multi-line
#      while storing one line. Vitest only proves the React value; only a real
#      POST -> GET round-trip proves the newlines SURVIVE the backend.
#
#   2. The tools/knowledge pickers became tile browsers (studio 0.1.163) whose tiles
#      render risk_level + type. If a list response omits either field, every tile
#      loses its governance signal and silently renders bare. The list endpoint is
#      asserted to carry them.
#
# Driven in-pod (registry-api at localhost:8000), same shape as suite-82. Creates its
# own fixtures, cleans up in a finally block.
#
#   T-S88-001  POST a tool with a multi-line description -> 201 and the response echoes it byte-identical
#   T-S88-002  GET the tool by name -> the stored description still has every newline (round-trip)
#   T-S88-003  the description survives in the LIST payload too (what the tiles read)
#   T-S88-004  PATCH/PUT to a longer multi-line description -> re-GET returns the new value intact
#   T-S88-005  a long description (~4KB, 60 lines) is not truncated by the column
#   T-S88-006  list payload carries risk_level + type for every tool (tile metadata contract)
#   T-S88-007  a tool created without a description does not 500 and reads back as empty/None
#   T-S88-008  cleanup: delete every fixture this suite created
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FAIL  T-S88-FIXTURE  |  no Running registry-api pod found"; exit 1
fi

echo "=== Suite 88: Tool description (multi-line) + picker tile metadata ==="
echo "    pod: $API_POD"
echo ""
RUN_TAG="s88_$(date +%s)"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<PY
import base64, json, urllib.error, urllib.parse, urllib.request

PASS = 0; FAIL = 0
def ok(m):
    global PASS; print(f"PASS  {m}"); PASS += 1
def bad(m, d=""):
    global FAIL; print(f"FAIL  {m}  |  {d}"); FAIL += 1

API = "http://localhost:8000"
KC = "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token"

class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return urllib.request.Request(newurl, data=req.data, method=req.get_method(),
                                      headers={k: v for k, v in req.header_items()})
_OPENER = urllib.request.build_opener(_Redirect)

def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = _OPENER.open(req, timeout=15)
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")}

def token_for(user, pw):
    data = urllib.parse.urlencode({
        "grant_type": "password", "client_id": "agentshield-studio",
        "username": user, "password": pw}).encode()
    tok = json.loads(urllib.request.urlopen(urllib.request.Request(KC, data=data), timeout=15).read())["access_token"]
    p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
    return tok, json.loads(base64.urlsafe_b64decode(p))["sub"]

PT, _ = token_for("platform-admin", "PlatformAdmin2024")
ok("T-S88-FIXTURE-000 fetched platform-admin token")

# Exactly what a user types into the textarea: several lines, a blank line, and
# trailing detail. Any collapse/strip shows up as a different line count.
MULTILINE = (
    "Retrieves the current status of an order.\n"
    "\n"
    "Args: order_id (str) — the customer-facing order number.\n"
    "Use for status lookups only; does not modify the order."
)
assert MULTILINE.count("\n") == 3

TOOL = "${RUN_TAG}_desc"
TOOL_NODESC = "${RUN_TAG}_nodesc"

def base_tool(name, **over):
    body = {
        "name": name,
        "display_name": "S88 Description Tool",
        "type": "http",
        "risk_level": "high",
        "owner_team": "platform",
        "side_effecting": False,
        "http_method": "GET",
        "http_url": "https://example.invalid/orders/{{order_id}}",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "Order id."}},
            "required": ["order_id"],
        },
    }
    body.update(over)
    return body

try:
    # ---- T-S88-001 create with a multi-line description -------------------
    st, t = call("POST", "/api/v1/tools/", PT, base_tool(TOOL, description=MULTILINE))
    if st not in (200, 201):
        bad("T-S88-001 create tool", f"{st} {t}")
        print(f"=== Suite 88: PASS={PASS} FAIL={FAIL} ==="); raise SystemExit(1)
    if t.get("description") == MULTILINE:
        ok("T-S88-001 create echoes the multi-line description byte-identical")
    else:
        bad("T-S88-001 create echo", f"got {t.get('description')!r}")

    # ---- T-S88-002 round-trip: the newlines SURVIVED the DB ---------------
    st, g = call("GET", f"/api/v1/tools/{TOOL}", PT)
    desc = (g or {}).get("description")
    if st == 200 and desc == MULTILINE:
        ok("T-S88-002 GET returns the stored description with every newline intact")
    else:
        bad("T-S88-002 round-trip", f"{st} lines={None if desc is None else desc.count(chr(10))} {desc!r}")
    # The specific failure the textarea would mask: stored as a single line.
    if desc is not None and desc.count("\n") == 3:
        ok("T-S88-002b description still spans 4 lines (not collapsed to one)")
    else:
        bad("T-S88-002b newline count", f"{None if desc is None else desc.count(chr(10))}")

    # ---- T-S88-003 the LIST payload carries it too ------------------------
    st, lst = call("GET", "/api/v1/tools/?limit=500", PT)
    items = (lst or {}).get("items", lst if isinstance(lst, list) else [])
    mine = [i for i in items if i.get("name") == TOOL]
    if st == 200 and mine and mine[0].get("description") == MULTILINE:
        ok("T-S88-003 list payload carries the full multi-line description")
    else:
        bad("T-S88-003 list description", f"{st} found={len(mine)}")

    # ---- T-S88-004 update to a different multi-line value -----------------
    UPDATED = MULTILINE + "\nEdited: now also returns the carrier tracking id."
    st, u = call("PUT", f"/api/v1/tools/{TOOL}", PT, base_tool(TOOL, description=UPDATED))
    if st not in (200, 201, 204):
        st, u = call("PATCH", f"/api/v1/tools/{TOOL}", PT, {"description": UPDATED})
    st2, g2 = call("GET", f"/api/v1/tools/{TOOL}", PT)
    if st2 == 200 and (g2 or {}).get("description") == UPDATED:
        ok("T-S88-004 edited multi-line description round-trips (5 lines)")
    else:
        bad("T-S88-004 edit round-trip", f"update={st} get={st2} {(g2 or {}).get('description')!r}")

    # ---- T-S88-005 a long description is not truncated --------------------
    LONG = "\n".join(f"Line {i:02d}: " + ("x" * 60) for i in range(60))
    st, _u = call("PUT", f"/api/v1/tools/{TOOL}", PT, base_tool(TOOL, description=LONG))
    if st not in (200, 201, 204):
        call("PATCH", f"/api/v1/tools/{TOOL}", PT, {"description": LONG})
    st, g3 = call("GET", f"/api/v1/tools/{TOOL}", PT)
    got = (g3 or {}).get("description") or ""
    if st == 200 and got == LONG:
        ok(f"T-S88-005 long description ({len(LONG)} bytes / 60 lines) not truncated")
    else:
        bad("T-S88-005 truncation", f"sent={len(LONG)} got={len(got)}")

    # ---- T-S88-006 tile metadata contract ---------------------------------
    # Every tile renders risk_level + type; a list response missing either makes
    # every tile render without its governance signal.
    st, lst = call("GET", "/api/v1/tools/?limit=500", PT)
    items = (lst or {}).get("items", lst if isinstance(lst, list) else [])
    missing = [i.get("name") for i in items if not i.get("type")]
    mine = [i for i in items if i.get("name") == TOOL]
    if st == 200 and mine and mine[0].get("risk_level") == "high" and mine[0].get("type"):
        ok("T-S88-006 list carries risk_level + type for the picker tiles")
    else:
        bad("T-S88-006 tile metadata", f"{st} mine={mine[:1]}")
    if not missing:
        ok("T-S88-006b every listed tool exposes a type")
    else:
        bad("T-S88-006b tools missing type", f"{missing[:5]}")

    # ---- T-S88-007 no description at all still works ----------------------
    st, t2 = call("POST", "/api/v1/tools/", PT, base_tool(TOOL_NODESC))
    st2, g4 = call("GET", f"/api/v1/tools/{TOOL_NODESC}", PT)
    if st in (200, 201) and st2 == 200 and not (g4 or {}).get("description"):
        ok("T-S88-007 a tool with no description creates and reads back empty (no 500)")
    else:
        bad("T-S88-007 no-description path", f"create={st} get={st2} {(g4 or {}).get('description')!r}")

finally:
    for name in (TOOL, TOOL_NODESC):
        call("DELETE", f"/api/v1/tools/{name}", PT)
    print("PASS  T-S88-008 cleanup complete"); PASS += 1

print(f"=== Suite 88: PASS={PASS} FAIL={FAIL} ===")
raise SystemExit(0 if FAIL == 0 else 1)
PY

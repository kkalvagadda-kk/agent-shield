#!/usr/bin/env bash
# scripts/checkpoints/poc-2b-cp1-smoke.sh
#
# POC-2b Checkpoint 1 (backend) smoke — run AFTER `bash scripts/deploy-eks.sh`
# has rolled out registry-api 0.2.190 / declarative-runner 0.1.55 (user-gated;
# shared-cluster hazard, No-Merge-to-Main).
#
# Gate (tasks.md CP1b):
#   1. `bash scripts/e2e/suite-75-context-storage.sh` exits 0 (PASS / justified
#      capacity-SKIP only) AND the three POC-2b cases T-S75-009/010/011 each ran
#      with 0 FAIL.
#   2. POST /workflows/{id}/runs/stream returns `content-type: text/event-stream`
#      and at least one `data:` frame carries `"type":"agent_start"` with an
#      `author` — a capacity-INDEPENDENT plumbing check (agent_start is emitted
#      per member BEFORE dispatch, so it holds even with zero warm pods).
#   3. A drain `POST /workflows/{id}/runs` yields a terminal tree whose child
#      agent_names match the streamed agent_start authors (drain parity).
#
# The stream/drain plumbing check (2+3) provisions its OWN tiny 2-member reactive
# workflow (members need NOT deploy — the assertion is about frame plumbing +
# tree parity, not agent execution) and cleans it up. No fakes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FATAL: registry-api pod not found in $NAMESPACE"
  exit 1
fi
echo "=== POC-2b CP1 backend smoke (pod: $API_POD) ==="

# ---------------------------------------------------------------------------
# Part 1 — suite-75 must exit 0 and the three POC-2b cases must have 0 FAIL.
# Capture output to a var (NOT `| tee`, which would mask the suite's exit code —
# a known past bug); grep the tally lines the suite prints.
# ---------------------------------------------------------------------------
echo ""
echo "--- Part 1: suite-75-context-storage.sh (T-S75-009/010/011) ---"
set +e
SUITE_OUT="$(bash scripts/e2e/suite-75-context-storage.sh 2>&1)"
SUITE_RC=$?
set -e
echo "$SUITE_OUT"

FAILED=0
if [ "$SUITE_RC" -ne 0 ]; then
  echo "FAIL: suite-75 exited $SUITE_RC (expected 0 — PASS/justified-SKIP only)"
  FAILED=1
fi
for id in T-S75-009 T-S75-010 T-S75-011; do
  if printf '%s\n' "$SUITE_OUT" | grep -q "FAIL: ${id}"; then
    echo "FAIL: ${id} reported a FAIL"
    FAILED=1
  elif ! printf '%s\n' "$SUITE_OUT" | grep -Eq "(PASS|SKIP): ${id}"; then
    echo "FAIL: ${id} did not run (no PASS/SKIP tally line)"
    FAILED=1
  else
    echo "OK: ${id} ran with 0 FAIL"
  fi
done

# ---------------------------------------------------------------------------
# Part 2 + 3 — stream content-type + author-tagged agent_start frame, and drain
# parity. Capacity-independent (members need not be deployed). Emits a single
# `SMOKE <PASS|FAIL|SKIP> <detail>` line the bash layer below grades.
# ---------------------------------------------------------------------------
echo ""
echo "--- Part 2/3: /runs/stream content-type + agent_start author + drain parity ---"
SMOKE_OUT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  python3 - <<'PY' 2>/dev/null || true
import asyncio, uuid, json, base64, httpx
from sqlalchemy import select
from db import AsyncSessionLocal
from models import Agent, AgentRun

ROOT = "http://localhost:8000"; BASE = ROOT + "/api/v1"
SFX = uuid.uuid4().hex[:8]
A1 = f"cp1-m1-{SFX}"; A2 = f"cp1-m2-{SFX}"
MEMBERS = {A1, A2}

def emit(v, d=""):
    print(f"SMOKE {v} {d}")

async def get_token():
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token",
                data={"grant_type": "password", "client_id": "agentshield-studio",
                      "username": "platform-admin", "password": "PlatformAdmin2024"})
        if r.status_code != 200:
            return None, None
        tok = r.json()["access_token"]
        p = tok.split(".")[1]; p += "=" * (4 - len(p) % 4)
        sub = json.loads(base64.urlsafe_b64decode(p)).get("sub")
        return tok, sub
    except Exception:
        return None, None

async def provider_id(c):
    r = await c.get(f"{BASE}/llm-providers/", params={"team": "platform"})
    if r.status_code >= 300:
        return None
    items = r.json(); items = items if isinstance(items, list) else items.get("items", [])
    return items[0]["id"] if items else None

async def main():
    token, sub = await get_token()
    if not token:
        emit("SKIP", "no keycloak token (runs/stream is JWT-guarded)")
        return
    auth = {"Authorization": f"Bearer {token}"}
    hdr = {"X-User-Sub": sub, "X-User-Team": "platform"}
    wid = None
    try:
        async with httpx.AsyncClient(timeout=60, headers=hdr) as c:
            pid = await provider_id(c)
            if not pid:
                emit("SKIP", "no LLM provider for team platform")
                return
            for n in (A1, A2):
                await c.post(f"{BASE}/agents/", json={
                    "name": n, "team": "platform", "agent_type": "declarative",
                    "execution_shape": "reactive", "memory_enabled": True,
                    "metadata": {"instructions": "Reply in one short sentence.",
                                 "llm_provider_id": pid, "tools": []}})
            r = await c.post(f"{BASE}/workflows", json={
                "name": f"cp1-wf-{SFX}", "team": "platform",
                "orchestration": "sequential", "execution_shape": "reactive"})
            if r.status_code >= 300:
                emit("SKIP", f"workflow create http={r.status_code}")
                return
            wid = r.json()["id"]
            for i, n in enumerate((A1, A2)):
                g = await c.get(f"{BASE}/agents/{n}")
                await c.post(f"{BASE}/workflows/{wid}/members",
                             json={"agent_id": g.json()["id"], "position": i + 1})

        # ---- stream: content-type + agent_start author (no warm pods needed) ----
        ctype = ""; starts = set()
        async with httpx.AsyncClient(timeout=120) as c:
            async with c.stream("POST", f"{BASE}/workflows/{wid}/runs/stream",
                                json={"message": "hello"}, headers=auth) as resp:
                ctype = resp.headers.get("content-type", "")
                if resp.status_code == 200:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            ev = json.loads(line[6:].strip())
                        except Exception:
                            continue
                        if ev.get("type") == "agent_start" and ev.get("author"):
                            starts.add(ev["author"])
                        if ev.get("type") == "done":
                            break
        if "text/event-stream" not in ctype:
            emit("FAIL", f"stream content-type is '{ctype}', expected text/event-stream")
            return
        if not starts:
            emit("FAIL", "no data frame carried type=agent_start with an author")
            return

        # ---- drain parity: tree child agent_names match streamed authors ----
        async with httpx.AsyncClient(timeout=60, headers=hdr) as c:
            rr = await c.post(f"{BASE}/workflows/{wid}/runs",
                              json={"input_payload": {"message": "hello"}, "run_by": "cp1"})
            drain_run = rr.json().get("run_id") or rr.json().get("id")
        status = "timeout"
        for _ in range(24):
            await asyncio.sleep(5)
            async with AsyncSessionLocal() as s:
                status = (await s.execute(select(AgentRun.status)
                          .where(AgentRun.id == uuid.UUID(drain_run)))).scalar() or "n/a"
            if status in ("completed", "failed", "cancelled"):
                break
        async with httpx.AsyncClient(timeout=30, headers=hdr) as c:
            tr = await c.get(f"{BASE}/workflows/{wid}/runs/{drain_run}/tree")
        children = tr.json().get("children", []) if tr.status_code == 200 else []
        drain_names = {ch.get("agent_name") for ch in children if ch.get("agent_name")}
        # Parity is over the members that actually ran on BOTH paths (a sequential
        # run may stop at the first failing member with no warm pods — the streamed
        # authors and drain children still describe the SAME member set).
        if drain_names and drain_names == starts:
            emit("PASS", f"content-type={ctype.split(';')[0]}; agent_start authors={sorted(starts)}; "
                         f"drain tree children match (parity), drain status={status}")
        elif not drain_names:
            emit("FAIL", f"drain tree had no children (status={status})")
        else:
            emit("FAIL", f"drain parity mismatch: stream={sorted(starts)} drain={sorted(drain_names)}")
    finally:
        async with httpx.AsyncClient(timeout=30, headers=hdr) as c:
            if wid:
                try:
                    await c.delete(f"{BASE}/workflows/{wid}")
                except Exception:
                    pass
            for n in (A1, A2):
                try:
                    await c.delete(f"{BASE}/agents/{n}")
                except Exception:
                    pass

asyncio.run(main())
PY
)
echo "$SMOKE_OUT"

case "$SMOKE_OUT" in
  *"SMOKE PASS"*) echo "OK: stream/drain plumbing verified" ;;
  *"SMOKE SKIP"*) echo "SKIP: stream/drain plumbing (environment gap — see detail above)" ;;
  *"SMOKE FAIL"*) echo "FAIL: stream/drain plumbing check failed"; FAILED=1 ;;
  *)             echo "FAIL: stream/drain plumbing produced no verdict"; FAILED=1 ;;
esac

echo ""
if [ "$FAILED" -ne 0 ]; then
  echo "=== POC-2b CP1 backend smoke: FAIL ==="
  exit 1
fi
echo "=== POC-2b CP1 backend smoke: PASS ==="

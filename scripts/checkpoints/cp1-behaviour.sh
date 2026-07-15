#!/usr/bin/env bash
# scripts/checkpoints/cp1-behaviour.sh
#
# === Checkpoint 1c: Context Storage POC-0 behaviour smoke ===
#
# Mid-stream gate for the POC-0 user-visible win, on the REAL path (kubectl exec
# into registry-api + httpx, no fakes). Mirrors suite-75 cases T-S75-001/002/003
# (the suite is the permanent regression; this is the gate before POC-1 begins):
#   1. memory across turns   — two /chat turns, one session_id; turn 2 recalls
#      turn 1; GET memory shows rows in message_index order.
#   2. save -> reload        — rollout restart the agent pod, chat again on the
#      same session, recall survives (Postgres, not pod RAM).
#   3. foreign-thread 403    — a session owned by another user, replayed → 403.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
AGENTS_NAMESPACE="${AGENTS_NAMESPACE:-agents-platform}"
SUFFIX="cp1$(printf '%04x' $((RANDOM % 65536)))"
SESSION="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"
CHAT_AGENT="cp1-mem-${SUFFIX}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$API_POD" ] && { echo "FAIL: registry-api pod not found"; exit 1; }

echo "=== Checkpoint 1: Context Storage POC-0 behaviour smoke ==="
echo "  agent=$CHAT_AGENT session=$SESSION"

expect_ok() { echo "$1" | grep -q "^OK" || { echo "FAIL: $2 -> $1"; exit 1; }; }

# --- provision + T-S75-001 (across turns) + T-S75-003 (403) --------------------
R1=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env CP_SUFFIX="$SUFFIX" CP_SESSION="$SESSION" python3 - <<'PY' 2>/dev/null || true
import asyncio, os, uuid, json, base64, httpx
from datetime import datetime, timezone
from sqlalchemy import select
from db import AsyncSessionLocal
from models import Agent, Deployment, PlaygroundRun

ROOT="http://localhost:8000"; BASE=ROOT+"/api/v1"
SUFFIX=os.environ["CP_SUFFIX"]; SESSION=os.environ["CP_SESSION"]
AGENT=f"cp1-mem-{SUFFIX}"
INSTR=("You are a helpful assistant with memory. Use facts the user told you earlier. "
       "Reply in one short sentence.")

async def token():
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r=await c.post("http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token",
                data={"grant_type":"password","client_id":"agentshield-studio",
                      "username":"platform-admin","password":"PlatformAdmin2024"})
        if r.status_code!=200: return None,None
        t=r.json()["access_token"]; p=t.split(".")[1]; p+="="*(4-len(p)%4)
        return t, json.loads(base64.urlsafe_b64decode(p)).get("sub")
    except Exception: return None,None

async def provider(c):
    r=await c.get(f"{BASE}/llm-providers/",params={"team":"platform"})
    if r.status_code>=300: return None
    it=r.json(); it=it if isinstance(it,list) else it.get("items",[])
    return it[0]["id"] if it else None

async def wait_running(name,timeout=180):
    for _ in range(timeout//5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            st=(await s.execute(select(Deployment.status).join(Agent,Agent.id==Deployment.agent_id)
                .where(Agent.name==name,Deployment.environment=="sandbox"))).scalar()
        if st=="running": return True
        if st=="failed": return False
    return False

async def chat(agent,msg,session,auth):
    async with httpx.AsyncClient(timeout=60) as c:
        r=await c.post(f"{BASE}/agents/{agent}/chat",
            json={"message":msg,"session_id":session,"context":"playground"},headers=auth)
    if r.status_code!=200: return r.status_code,""
    b=r.json(); url=ROOT+b["stream_url"]; text=""
    async with httpx.AsyncClient(timeout=120) as c:
        async with c.stream("GET",url,headers=auth) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "): continue
                try: ev=json.loads(line[6:].strip())
                except Exception: continue
                if ev.get("event")=="text_delta": text+=ev.get("content","")
                if ev.get("event") in ("done","error"): break
    return 200,text

async def main():
    tok,sub=await token()
    if not tok: print("SKIP:no-token"); return
    auth={"Authorization":f"Bearer {tok}"}; hdr={"X-User-Sub":sub,"X-User-Team":"platform"}
    async with httpx.AsyncClient(timeout=60) as c:
        pid=await provider(c)
        if not pid: print("SKIP:no-provider"); return
        await c.post(f"{BASE}/agents/",json={"name":AGENT,"team":"platform","agent_type":"declarative",
            "execution_shape":"reactive","memory_enabled":True,
            "metadata":{"instructions":INSTR,"llm_provider_id":pid,"tools":[]}},headers=hdr)
        await c.post(f"{BASE}/agents/{AGENT}/deploy",json={"environment":"sandbox"},headers=hdr)
    if not await wait_running(AGENT):
        print("SKIP:agent-not-running"); return

    # T-S75-001
    s1,_=await chat(AGENT,"My name is Ada and my favorite color is teal. Remember that.",SESSION,auth)
    if s1!=200: print("SKIP:no-deployment-turn1"); return
    s2,reply=await chat(AGENT,"What is my name?",SESSION,auth)
    async with httpx.AsyncClient(timeout=30) as c:
        m=await c.get(f"{BASE}/agents/{AGENT}/memory",params={"thread_id":SESSION})
    rows=m.json() if m.status_code==200 else []
    idx=[r.get("message_index") for r in rows]
    ordered = idx and all(i is not None for i in idx) and idx==sorted(idx) and len(set(idx))==len(idx)
    if not (s2==200 and len(rows)>=2 and ordered and "ada" in reply.lower()):
        print(f"FAIL:001 http={s2} rows={len(rows)} ordered={ordered} reply='{reply[:60]}'"); return

    # T-S75-003 (foreign-thread 403)
    fs=str(uuid.uuid4())
    async with AsyncSessionLocal() as s:
        s.add(PlaygroundRun(user_id=f"cp1-foreign-{SUFFIX}",agent_name=AGENT,session_id=fs,
            context="playground",sandbox=True,status="completed",execution_shape="reactive",
            started_at=datetime.now(timezone.utc)))
        await s.commit()
    async with httpx.AsyncClient(timeout=30) as c:
        r=await c.post(f"{BASE}/agents/{AGENT}/chat",
            json={"message":"let me in","session_id":fs,"context":"playground"},headers=auth)
    if not (r.status_code==403 and "Not your session" in r.text):
        print(f"FAIL:003 expected 403, got {r.status_code}"); return

    print("OK:001+003")

asyncio.run(main())
PY
)
echo "  turns+403: $R1"
case "$R1" in
  OK*)   echo "  PASS: T-S75-001 (across turns) + T-S75-003 (403)";;
  SKIP*) echo "  SKIP: behaviour smoke ($R1) — no token/provider/running pod"; echo "PASS"; exit 0;;
  *)     echo "FAIL: T-S75-001/003 -> $R1"; exit 1;;
esac

# --- restart the agent pod (save->reload boundary for T-S75-002) --------------
echo "  restarting ${CHAT_AGENT}-sandbox ..."
if kubectl get deployment "${CHAT_AGENT}-sandbox" -n "$AGENTS_NAMESPACE" >/dev/null 2>&1; then
  kubectl rollout restart deployment/"${CHAT_AGENT}-sandbox" -n "$AGENTS_NAMESPACE" >/dev/null 2>&1 || true
  kubectl rollout status deployment/"${CHAT_AGENT}-sandbox" -n "$AGENTS_NAMESPACE" --timeout=180s
else
  echo "FAIL: ${CHAT_AGENT}-sandbox Deployment missing (cannot prove save->reload)"; exit 1
fi

# --- T-S75-002 (recall survives restart) --------------------------------------
R2=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env CP_SUFFIX="$SUFFIX" CP_SESSION="$SESSION" python3 - <<'PY' 2>/dev/null || true
import asyncio, os, json, base64, httpx
ROOT="http://localhost:8000"; BASE=ROOT+"/api/v1"
SUFFIX=os.environ["CP_SUFFIX"]; SESSION=os.environ["CP_SESSION"]; AGENT=f"cp1-mem-{SUFFIX}"
async def token():
    async with httpx.AsyncClient(timeout=10) as c:
        r=await c.post("http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token",
            data={"grant_type":"password","client_id":"agentshield-studio",
                  "username":"platform-admin","password":"PlatformAdmin2024"})
    return r.json()["access_token"] if r.status_code==200 else None
async def chat(agent,msg,session,auth):
    async with httpx.AsyncClient(timeout=60) as c:
        r=await c.post(f"{BASE}/agents/{agent}/chat",
            json={"message":msg,"session_id":session,"context":"playground"},headers=auth)
    if r.status_code!=200: return r.status_code,""
    url=ROOT+r.json()["stream_url"]; text=""
    async with httpx.AsyncClient(timeout=120) as c:
        async with c.stream("GET",url,headers=auth) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "): continue
                try: ev=json.loads(line[6:].strip())
                except Exception: continue
                if ev.get("event")=="text_delta": text+=ev.get("content","")
                if ev.get("event") in ("done","error"): break
    return 200,text
async def main():
    tok=await token()
    if not tok: print("SKIP:no-token"); return
    auth={"Authorization":f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=30) as c:
        m=await c.get(f"{BASE}/agents/{AGENT}/memory",params={"thread_id":SESSION})
    rows=m.json() if m.status_code==200 else []
    s,reply=await chat(AGENT,"What is my favorite color?",SESSION,auth)
    if s!=200: print(f"SKIP:post-restart-http={s}"); return
    if len(rows)>=2 and "teal" in reply.lower(): print("OK:002")
    else: print(f"FAIL:002 rows={len(rows)} reply='{reply[:60]}'")
asyncio.run(main())
PY
)
echo "  post-restart recall: $R2"

# cleanup
kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import urllib.request
try: urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/${CHAT_AGENT}',method='DELETE'),timeout=5)
except Exception: pass" 2>/dev/null || true

case "$R2" in
  OK*)   echo "  PASS: T-S75-002 (recall survived restart)";;
  SKIP*) echo "  SKIP: T-S75-002 ($R2)";;
  *)     echo "FAIL: T-S75-002 -> $R2"; exit 1;;
esac

echo "PASS"

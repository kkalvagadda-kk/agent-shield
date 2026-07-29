#!/usr/bin/env bash
# scripts/e2e/suite-91-memory-save-recall-decouple.sh
#
# E2E Suite 91: decouple conversation-SAVE from agent-RECALL (memory_enabled).
#
# Before: memory_enabled gated the SAVE, so a memory-off agent persisted NOTHING and
# the user's History was empty. Now the transcript is ALWAYS saved (History works for
# every agent) and memory_enabled gates only whether the AGENT RECALLS prior turns
# (the runner's for_agent_context read).
#
# Proves, on a memory-DISABLED agent:
#   T-S91-001 — POST /memory saves the turn (200/201, not the old 400 "not enabled")
#   T-S91-002 — the user's History read (no for_agent_context) RETURNS the transcript
#   T-S91-003 — the agent-recall read (for_agent_context=true) returns EMPTY (no recall)
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$API_POD" ] && { echo "ERROR: no registry-api pod"; exit 1; }

echo "=== Suite 91: memory save/recall decouple ==="
RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import asyncio, json, uuid
import httpx
from sqlalchemy import text
from db import AsyncSessionLocal

BASE="http://localhost:8000"
NAME=f"e2e-decouple-{uuid.uuid4().hex[:8]}"
SUB="e2e-decouple-user"
TID=f"thr-{uuid.uuid4().hex[:8]}"

async def mk_agent_memory_off():
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            "insert into agents (id,name,team,created_by,memory_enabled,agent_type) "
            "values (gen_random_uuid(),:n,'platform',:s,false,'declarative')"), {"n":NAME,"s":SUB})
        await db.commit()

async def cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(text("delete from agent_memory where agent_name=:n"), {"n":NAME})
        await db.execute(text("delete from agents where name=:n"), {"n":NAME})
        await db.commit()

async def main():
    out={}
    await mk_agent_memory_off()
    try:
        with httpx.Client(base_url=BASE, timeout=15) as c:
            H={"X-User-Sub":SUB}
            # 001 — save a turn on a memory-OFF agent → must succeed (was 400 before)
            r=c.post(f"/api/v1/agents/{NAME}/memory", headers=H, json={
                "thread_id":TID,"session_id":TID,"user_id":SUB,
                "messages":[{"role":"user","content":"my name is Ada"},
                            {"role":"assistant","content":"nice to meet you Ada"}]})
            out["001_save_ok"] = r.status_code in (200,201)
            # 002 — the user's History read returns the transcript (ungated)
            r=c.get(f"/api/v1/agents/{NAME}/memory", headers=H, params={"thread_id":TID})
            out["002_history_visible"] = r.status_code==200 and len(r.json())>=2
            # 003 — the agent-recall read (for_agent_context) is EMPTY for a memory-off agent
            r=c.get(f"/api/v1/agents/{NAME}/memory", headers=H,
                    params={"thread_id":TID,"scope":"agent","for_agent_context":"true"})
            out["003_recall_gated"] = r.status_code==200 and len(r.json())==0
    finally:
        await cleanup()
    print(json.dumps(out))

asyncio.run(main())
PY
)
echo "  Raw: $RESULT"
python3 - "$RESULT" <<'PY'
import json,sys
res=json.loads(sys.argv[1]) if sys.argv[1].strip() else {}
checks=["001_save_ok","002_history_visible","003_recall_gated"]
for k in checks: print(f"  [{'PASS' if res.get(k) else 'FAIL'}] {k}")
sys.exit(0 if all(res.get(k) for k in checks) else 1)
PY
echo "=== Suite 91 PASSED ==="

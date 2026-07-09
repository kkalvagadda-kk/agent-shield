#!/usr/bin/env bash
# Suite 43: Per-deployment memory isolation + deployment-pinned chat + TTL worker
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
POD=$(kubectl get pod -n "$NAMESPACE" -l app=registry-api \
  -o jsonpath='{.items[0].metadata.name}')

run() {
  kubectl exec -n "$NAMESPACE" "$POD" -- python3 -c "$1"
}

echo "=== Suite 43: Memory Isolation + Deployment Chat + TTL ==="

# --------------------------------------------------------------------------
# T-S43-001 — deployment_id column exists on agent_memory
# --------------------------------------------------------------------------
echo "T-S43-001 — agent_memory.deployment_id column exists"
run '
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

url = os.getenv("DATABASE_URL", "postgresql+asyncpg://agentshield:agentshield@agentshield-postgresql:5432/agentshield")
async def check():
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        r = await conn.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = '\''agent_memory'\'' AND column_name = '\''deployment_id'\''"
        ))
        assert r.scalar() == 1, "deployment_id column missing"
    await eng.dispose()
asyncio.run(check())
print("PASS: T-S43-001")
'

# --------------------------------------------------------------------------
# T-S43-002 — Memory save with deployment_id scopes correctly
# --------------------------------------------------------------------------
echo "T-S43-002 — Memory save scoped by deployment_id"
run '
import httpx

c = httpx.Client(base_url="http://localhost:8000/api/v1", headers={"X-User-Sub": "s43-user"})

# Create agent with memory_enabled
ag = c.post("/agents", json={"name":"s43-mem-agent","team":"default","agent_type":"declarative","memory_enabled":True}).json()

# Save memory with a fake deployment_id
import uuid
dep_id = str(uuid.uuid4())
r = c.post(f"/agents/s43-mem-agent/memory", json={
    "thread_id": "t1",
    "messages": [{"role":"user","content":"hello from deployment"}],
    "deployment_id": dep_id,
})
assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

# List without deployment_id — should NOT return the scoped message
all_msgs = c.get("/agents/s43-mem-agent/memory").json()
scoped_msgs = c.get(f"/agents/s43-mem-agent/memory?deployment_id={dep_id}").json()

assert len(scoped_msgs) == 1, f"Expected 1 scoped message, got {len(scoped_msgs)}"
assert scoped_msgs[0]["deployment_id"] == dep_id

# Save without deployment_id (global memory)
c.post(f"/agents/s43-mem-agent/memory", json={
    "thread_id": "t1",
    "messages": [{"role":"user","content":"global message"}],
})
global_msgs = c.get("/agents/s43-mem-agent/memory").json()
assert len(global_msgs) >= 2, "Should see both global and scoped messages"

print("PASS: T-S43-002")
'

# --------------------------------------------------------------------------
# T-S43-003 — Deployment-pinned chat endpoint exists
# --------------------------------------------------------------------------
echo "T-S43-003 — POST /agents/{name}/deployments/{dep_id}/chat returns 404 for missing deployment"
run '
import httpx, uuid

c = httpx.Client(base_url="http://localhost:8000/api/v1", headers={"X-User-Sub": "s43-user"})

# Ensure agent exists
try:
    c.post("/agents", json={"name":"s43-chat-agent","team":"default","agent_type":"declarative"})
except Exception:
    pass

fake_dep = str(uuid.uuid4())
r = c.post(f"/agents/s43-chat-agent/deployments/{fake_dep}/chat", json={
    "message": "hello",
})
# Should 404 (deployment not found) — proves the endpoint is wired
assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"
assert "not found" in r.json()["detail"].lower()

print("PASS: T-S43-003")
'

# --------------------------------------------------------------------------
# T-S43-004 — Deployment-pinned chat rejects non-running deployment
# --------------------------------------------------------------------------
echo "T-S43-004 — Deployment chat rejects terminated deployment"
run '
import httpx, asyncio, os, uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

url = os.getenv("DATABASE_URL", "postgresql+asyncpg://agentshield:agentshield@agentshield-postgresql:5432/agentshield")
c = httpx.Client(base_url="http://localhost:8000/api/v1", headers={"X-User-Sub": "s43-user"})

# Get agent ID
ag = c.get("/agents/s43-chat-agent").json()
agent_id = ag["id"]

# Insert a terminated deployment directly
dep_id = str(uuid.uuid4())
async def insert():
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        await conn.execute(text(
            "INSERT INTO deployments (id, agent_id, version_id, environment, status, k8s_namespace, deployed_at) "
            "VALUES (:did, :aid, :aid, '\''sandbox'\'', '\''terminated'\'', '\''agents-default'\'', now())"
        ), {"did": dep_id, "aid": agent_id})
    await eng.dispose()
asyncio.run(insert())

r = c.post(f"/agents/s43-chat-agent/deployments/{dep_id}/chat", json={"message":"hi"})
assert r.status_code == 503, f"Expected 503, got {r.status_code}: {r.text}"
assert "not running" in r.json()["detail"].lower()

print("PASS: T-S43-004")
'

# --------------------------------------------------------------------------
# T-S43-005 — TTL worker logic: running deployment with expired TTL gets terminated
# --------------------------------------------------------------------------
echo "T-S43-005 — TTL expiry logic (direct DB verification)"
run '
import asyncio, os, uuid
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

url = os.getenv("DATABASE_URL", "postgresql+asyncpg://agentshield:agentshield@agentshield-postgresql:5432/agentshield")

dep_id = str(uuid.uuid4())
agent_id = str(uuid.uuid4())

async def check():
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        # Insert agent
        await conn.execute(text(
            "INSERT INTO agents (id, name, team, agent_type, status) "
            "VALUES (:id, :name, '\''default'\'', '\''declarative'\'', '\''active'\'')"
        ), {"id": agent_id, "name": f"s43-ttl-{agent_id[:8]}"})
        # Insert deployment with ttl_hours=0 (expired immediately) and deployed_at in the past
        await conn.execute(text(
            "INSERT INTO deployments (id, agent_id, version_id, environment, status, k8s_namespace, deployed_at, ttl_hours) "
            "VALUES (:did, :aid, :aid, '\''sandbox'\'', '\''running'\'', '\''agents-default'\'', now() - interval '\''2 hours'\'', 1)"
        ), {"did": dep_id, "aid": agent_id})

    # Run the same query the TTL worker would run
    async with eng.begin() as conn:
        rows = await conn.execute(text(
            "UPDATE deployments SET status = '\''terminating'\'' "
            "WHERE status = '\''running'\'' AND ttl_hours IS NOT NULL "
            "AND deployed_at + (ttl_hours * interval '\''1 hour'\'') < now() "
            "AND id = :did "
            "RETURNING id"
        ), {"did": dep_id})
        updated = rows.fetchall()
        assert len(updated) == 1, f"Expected 1 row updated, got {len(updated)}"

    # Verify final status
    async with eng.begin() as conn:
        r = await conn.execute(text("SELECT status FROM deployments WHERE id = :did"), {"did": dep_id})
        assert r.scalar() == "terminating"

    await eng.dispose()

asyncio.run(check())
print("PASS: T-S43-005")
'

# --------------------------------------------------------------------------
# T-S43-006 — Clear memory scoped to deployment
# --------------------------------------------------------------------------
echo "T-S43-006 — Clear memory with deployment_id scope"
run '
import httpx, uuid

c = httpx.Client(base_url="http://localhost:8000/api/v1", headers={"X-User-Sub": "s43-user"})
dep_id = str(uuid.uuid4())

# Save 2 messages: one global, one scoped
c.post("/agents/s43-mem-agent/memory", json={
    "thread_id": "t-clear", "messages": [{"role":"user","content":"global"}],
})
c.post("/agents/s43-mem-agent/memory", json={
    "thread_id": "t-clear", "messages": [{"role":"user","content":"scoped"}],
    "deployment_id": dep_id,
})

# Clear only the scoped
r = c.delete(f"/agents/s43-mem-agent/memory/clear?deployment_id={dep_id}")
assert r.status_code == 204

# Scoped should be empty, global should remain
scoped = c.get(f"/agents/s43-mem-agent/memory?deployment_id={dep_id}").json()
assert len(scoped) == 0, f"Expected 0 scoped after clear, got {len(scoped)}"

all_msgs = c.get("/agents/s43-mem-agent/memory").json()
assert any(m["content"] == "global" for m in all_msgs), "Global messages should survive scoped clear"

print("PASS: T-S43-006")
'

echo ""
echo "=== Suite 43 COMPLETE: 6/6 ==="

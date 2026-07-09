#!/usr/bin/env bash
# Suite 44: Version Management — config snapshot on deploy, workflow snapshot, upgrade/rollback
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}')

run() {
  kubectl exec -n "$NAMESPACE" "$POD" -- python3 -c "$1"
}

echo "=== Suite 44: Version Management ==="

# --------------------------------------------------------------------------
# T-S44-001..004,009 — Agent version management (single block for state continuity)
# --------------------------------------------------------------------------
echo "T-S44-001..004,009 — Agent: config snapshot, redeploy, upgrade, immutability, delete cascade"
run '
import httpx, uuid

c = httpx.Client(base_url="http://localhost:8000", follow_redirects=True,
                 headers={"X-User-Sub": "test-suite-44"})

AGENT_NAME = "ver-test-44-" + uuid.uuid4().hex[:6]

# --- T-S44-001: Create + deploy = version with config snapshot ---
r = c.post("/api/v1/agents/", json={
    "name": AGENT_NAME,
    "team": "platform",
    "agent_type": "declarative",
    "execution_shape": "reactive",
    "metadata": {
        "instructions": "You are a test agent for version management.",
        "tools": ["web-search", "calculator"]
    }
})
assert r.status_code == 201, f"create failed: {r.status_code} {r.text}"

dep_r = c.post(f"/api/v1/agents/{AGENT_NAME}/deploy", json={
    "environment": "sandbox", "replicas": 1
})
assert dep_r.status_code == 201, f"deploy failed: {dep_r.status_code} {dep_r.text}"
dep = dep_r.json()
assert "version_id" in dep, f"deploy missing version_id: {dep}"

versions = c.get(f"/api/v1/agents/{AGENT_NAME}/versions").json()
assert len(versions) >= 1, f"expected >=1 version, got {len(versions)}"
v1 = versions[0]
assert v1["version_number"] == 1

config = v1.get("config") or {}
assert config.get("instructions") == "You are a test agent for version management.", \
    f"config.instructions wrong: {config}"
assert config.get("tools") == ["web-search", "calculator"], \
    f"config.tools wrong: {config}"
print("T-S44-001 PASS: deploy created v1 with config snapshot")

# --- T-S44-002: Edit + redeploy = new version ---
c.put(f"/api/v1/agents/{AGENT_NAME}", json={
    "metadata": {
        "instructions": "Updated instructions for v2.",
        "tools": ["web-search", "calculator", "code-exec"]
    }
})

dep2 = c.post(f"/api/v1/agents/{AGENT_NAME}/deploy", json={
    "environment": "sandbox", "replicas": 1
}).json()

versions = c.get(f"/api/v1/agents/{AGENT_NAME}/versions").json()
assert len(versions) == 2, f"expected 2 versions, got {len(versions)}"

v2 = versions[0]
assert v2["version_number"] == 2
config2 = v2.get("config") or {}
assert config2.get("instructions") == "Updated instructions for v2."
assert "code-exec" in config2.get("tools", [])

v1 = versions[1]
config1 = v1.get("config") or {}
assert config1.get("instructions") == "You are a test agent for version management."
assert dep2["version_id"] == v2["id"]
print("T-S44-002 PASS: redeploy created v2, v1 unchanged")

# --- T-S44-003: Upgrade to v1 = rollback ---
deps = c.get(f"/api/v1/agents/{AGENT_NAME}/deployments").json()
active = [d for d in deps if d["status"] not in ("terminated",)]
assert len(active) >= 1
dep_id = active[0]["id"]

resp = c.patch(f"/api/v1/agents/{AGENT_NAME}/deployments/{dep_id}",
               json={"action": "upgrade", "version_id": v1["id"]})
assert resp.status_code == 200, f"upgrade failed: {resp.status_code} {resp.text}"
assert resp.json()["version_id"] == v1["id"]
print("T-S44-003 PASS: upgrade to v1 succeeded")

# --- T-S44-004: Edit agent does NOT mutate existing versions ---
c.put(f"/api/v1/agents/{AGENT_NAME}", json={
    "metadata": {"instructions": "Third edit.", "tools": ["brand-new"]}
})
versions = c.get(f"/api/v1/agents/{AGENT_NAME}/versions").json()
v2_check = [v for v in versions if v["version_number"] == 2][0]
v1_check = [v for v in versions if v["version_number"] == 1][0]
assert v1_check.get("config", {}).get("instructions") == "You are a test agent for version management."
assert v2_check.get("config", {}).get("instructions") == "Updated instructions for v2."
print("T-S44-004 PASS: existing versions immutable after agent edit")

# --- T-S44-009: Delete version cascade ---
v2_id = v2_check["id"]
resp = c.delete(f"/api/v1/agents/{AGENT_NAME}/versions/{v2_id}")
assert resp.status_code == 200, f"delete failed: {resp.status_code} {resp.text}"
result = resp.json()
term_count = result.get("terminated_deployments", 0)

versions_after = c.get(f"/api/v1/agents/{AGENT_NAME}/versions").json()
ids = [v["id"] for v in versions_after]
assert v2_id not in ids, "v2 should be deleted"
print(f"T-S44-009 PASS: delete version cascade (terminated {term_count})")

# --- Cleanup ---
deps = c.get(f"/api/v1/agents/{AGENT_NAME}/deployments").json()
for d in deps:
    if d["status"] not in ("terminated",):
        did = d["id"]
        c.patch(f"/api/v1/agents/{AGENT_NAME}/deployments/{did}",
                json={"action": "terminate"})
c.delete(f"/api/v1/agents/{AGENT_NAME}")
print("Agent cleanup done")
'

# --------------------------------------------------------------------------
# T-S44-005..008 — Workflow version + deploy tests
# --------------------------------------------------------------------------
echo "T-S44-005..008 — Workflow: snapshot, deploy, invalid deploy, re-snapshot"
run '
import httpx, uuid

c = httpx.Client(base_url="http://localhost:8000", follow_redirects=True,
                 headers={"X-User-Sub": "test-suite-44"})

SUFFIX = uuid.uuid4().hex[:6]
HELPER1 = f"ver-test-44-h1-{SUFFIX}"
HELPER2 = f"ver-test-44-h2-{SUFFIX}"
WF_NAME = f"ver-test-44-wf-{SUFFIX}"

# Setup agents
c.post("/api/v1/agents/", json={
    "name": HELPER1, "team": "platform",
    "agent_type": "declarative", "execution_shape": "reactive"
})
c.post("/api/v1/agents/", json={
    "name": HELPER2, "team": "platform",
    "agent_type": "declarative", "execution_shape": "reactive"
})

# Create workflow
wf = c.post("/api/v1/workflows", json={
    "name": WF_NAME, "team": "platform",
    "orchestration": "sequential", "execution_shape": "reactive"
}).json()
wf_id = wf["id"]

helper = c.get(f"/api/v1/agents/{HELPER1}").json()
c.post(f"/api/v1/workflows/{wf_id}/members", json={
    "agent_id": helper["id"], "role": "worker"
})

# --- T-S44-005: Snapshot version ---
ver = c.post(f"/api/v1/workflows/{wf_id}/versions", json={}).json()
assert ver["version_number"] == 1
members = ver.get("members", [])
assert len(members) == 1, f"expected 1 member, got {len(members)}"
assert members[0]["agent_name"] == HELPER1
assert ver["orchestration"] == "sequential"
print("T-S44-005 PASS: workflow v1 snapshots members + orchestration")

# --- T-S44-006: Deploy with valid version ---
versions = c.get(f"/api/v1/workflows/{wf_id}/versions").json()
v1 = versions[0]
dep = c.post(f"/api/v1/workflows/{wf_id}/deploy", json={
    "version_id": v1["id"], "environment": "sandbox"
}).json()
assert dep.get("version_id") == v1["id"], f"deploy version_id mismatch: {dep}"
print("T-S44-006 PASS: workflow deploy with version_id succeeds")

# --- T-S44-007: Deploy with invalid version_id ---
resp = c.post(f"/api/v1/workflows/{wf_id}/deploy", json={
    "version_id": "00000000-0000-0000-0000-000000000000",
    "environment": "sandbox"
})
assert resp.status_code in (404, 422), f"expected 404/422, got {resp.status_code}"
print("T-S44-007 PASS: deploy with bad version_id rejected")

# --- T-S44-008: Add member + re-snapshot = v2 ---
helper2 = c.get(f"/api/v1/agents/{HELPER2}").json()
c.post(f"/api/v1/workflows/{wf_id}/members", json={
    "agent_id": helper2["id"], "role": "worker"
})
ver2 = c.post(f"/api/v1/workflows/{wf_id}/versions", json={}).json()
assert ver2["version_number"] == 2
members2 = ver2.get("members", [])
assert len(members2) == 2, f"expected 2 members, got {len(members2)}"

versions = c.get(f"/api/v1/workflows/{wf_id}/versions").json()
v1_check = [v for v in versions if v["version_number"] == 1][0]
m1 = v1_check.get("members", [])
assert len(m1) == 1, f"v1 should still have 1 member, got {len(m1)}"
print("T-S44-008 PASS: v2 has 2 members, v1 unchanged")

# --- Cleanup ---
c.delete(f"/api/v1/workflows/{wf_id}")
c.delete(f"/api/v1/agents/{HELPER1}")
c.delete(f"/api/v1/agents/{HELPER2}")
print("Workflow cleanup done")
'

echo "=== Suite 44: ALL PASSED ==="

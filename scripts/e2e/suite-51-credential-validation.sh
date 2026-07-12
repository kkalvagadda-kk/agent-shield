#!/usr/bin/env bash
# Suite 51: Auth-config credential value validation
# Proves the credential save path rejects error-shaped / malformed secret values
# (the bug where an httpx "Client error '403 Forbidden' ..." string got stored as
# the API key) while still persisting a real credential's value.
set -euo pipefail

POD=$(kubectl get pod -n agentshield-platform -l app=registry-api \
  -o jsonpath='{.items[0].metadata.name}')

run() {
  kubectl exec -n agentshield-platform "$POD" -- python3 -c "$1"
}

echo "=== Suite 51: Credential Validation ==="

# --------------------------------------------------------------------------
# T-S51-001 — Valid credential persists and stores the real value
# --------------------------------------------------------------------------
echo "T-S51-001 — Valid api_key credential is accepted + K8s secret holds real value"
run '
import base64, httpx, uuid
from kubernetes import client, config
c = httpx.Client(base_url="http://localhost:8000/api/v1")
name = "s49-valid-" + uuid.uuid4().hex[:8]
real = "sk-realKey-" + uuid.uuid4().hex
r = c.post("/auth-configs/", json={"name":name,"type":"api_key","credentials":{"serper_api_key":real}})
assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
cid = r.json()["id"]
# Verify the K8s secret holds the REAL value, not garbage
config.load_incluster_config()
v1 = client.CoreV1Api()
sec = v1.read_namespaced_secret(f"auth-config-{cid}", "agentshield-platform")
stored = base64.b64decode(sec.data["serper_api_key"]).decode()
assert stored == real, f"Secret value mismatch: {stored!r}"
c.delete(f"/auth-configs/{cid}")
print("PASS: T-S51-001")
'

# --------------------------------------------------------------------------
# T-S51-002 — HTTP-error-shaped value is REJECTED (the actual bug), not stored
# --------------------------------------------------------------------------
echo "T-S51-002 — httpx 403 error string rejected with 422, nothing persisted"
run '
import httpx, uuid
c = httpx.Client(base_url="http://localhost:8000/api/v1")
name = "s49-bad-" + uuid.uuid4().hex[:8]
bad = "Client error '"'"'403 Forbidden'"'"' for url '"'"'https://google.serper.dev/search'"'"'"
r = c.post("/auth-configs/", json={"name":name,"type":"api_key","credentials":{"serper_api_key":bad}})
assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
# Nothing should have been created
lst = c.get("/auth-configs/", params={"limit":200}).json()["items"]
assert not any(a["name"] == name for a in lst), "Rejected config must not persist"
print("PASS: T-S51-002")
'

# --------------------------------------------------------------------------
# T-S51-003 — Empty / too-long / multi-line inline values rejected
# --------------------------------------------------------------------------
echo "T-S51-003 — empty, oversized, and multi-line inline values rejected (422)"
run '
import httpx, uuid
c = httpx.Client(base_url="http://localhost:8000/api/v1")
def try_bad(val, label):
    name = "s49-" + label + "-" + uuid.uuid4().hex[:6]
    r = c.post("/auth-configs/", json={"name":name,"type":"api_key","credentials":{"k":val}})
    assert r.status_code == 422, f"{label}: expected 422, got {r.status_code}: {r.text}"
try_bad("   ", "empty")
try_bad("a"*2000, "toolong")
try_bad("line1\nline2", "multiline")
print("PASS: T-S51-003")
'

# --------------------------------------------------------------------------
# T-S51-004 — mTLS PEM (legitimately multi-line + long) is still accepted
# --------------------------------------------------------------------------
echo "T-S51-004 — mtls multi-line PEM credential accepted"
run '
import httpx, uuid
c = httpx.Client(base_url="http://localhost:8000/api/v1")
name = "s49-mtls-" + uuid.uuid4().hex[:8]
pem = "-----BEGIN CERTIFICATE-----\n" + ("MIIB" + "a"*300) + "\n-----END CERTIFICATE-----"
r = c.post("/auth-configs/", json={"name":name,"type":"mtls","credentials":{"tls_cert":pem}})
assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
c.delete(f"/auth-configs/{r.json()[\"id\"]}")
print("PASS: T-S51-004")
'

# --------------------------------------------------------------------------
# T-S51-005 — Update path also rejects an error-shaped value
# --------------------------------------------------------------------------
echo "T-S51-005 — PUT with error-shaped value rejected, original secret preserved"
run '
import base64, httpx, uuid
from kubernetes import client, config
c = httpx.Client(base_url="http://localhost:8000/api/v1")
name = "s49-upd-" + uuid.uuid4().hex[:8]
good = "goodkey-" + uuid.uuid4().hex
r = c.post("/auth-configs/", json={"name":name,"type":"api_key","credentials":{"k":good}})
assert r.status_code == 201, r.text
cid = r.json()["id"]
bad = "Server error '"'"'502 Bad Gateway'"'"' for url '"'"'https://x'"'"'"
r2 = c.put(f"/auth-configs/{cid}", json={"credentials":{"k":bad}})
assert r2.status_code == 422, f"Expected 422, got {r2.status_code}: {r2.text}"
# Original secret must be untouched
config.load_incluster_config()
v1 = client.CoreV1Api()
sec = v1.read_namespaced_secret(f"auth-config-{cid}", "agentshield-platform")
stored = base64.b64decode(sec.data["k"]).decode()
assert stored == good, f"Secret was clobbered: {stored!r}"
c.delete(f"/auth-configs/{cid}")
print("PASS: T-S51-005")
'


# --------------------------------------------------------------------------
# T-S51-006 — Invalid credential KEY (not a valid env-var name) is rejected
# --------------------------------------------------------------------------
echo "T-S51-006 — hyphenated credential key rejected (would be dropped by envFrom)"
run '
import httpx, uuid
c = httpx.Client(base_url="http://localhost:8000/api/v1")
name = "s51-badkey-" + uuid.uuid4().hex[:8]
# "serper-dev" is a valid-looking name but an INVALID env var (hyphen) — K8s
# envFrom would silently drop it, so the agent never gets the credential.
r = c.post("/auth-configs/", json={"name":name,"type":"api_key","credentials":{"serper-dev":"realkey123"}})
assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
lst = c.get("/auth-configs/").json()
assert not any(a["name"] == name for a in lst), "Rejected config must not persist"
print("PASS: T-S51-006")
'

echo ""
echo "=== Suite 51 COMPLETE: 6/6 ==="

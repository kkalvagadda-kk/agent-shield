#!/usr/bin/env bash
# Suite 51: Auth-config credential value validation
# Proves the credential save path rejects error-shaped / malformed secret values
# (the bug where an httpx "Client error '403 Forbidden' ..." string got stored as
# the API key) while still persisting a real credential's value.
set -euo pipefail

POD=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')

run() {
  kubectl exec -n agentshield-platform "$POD" -c registry-api -- python3 -c "$1"
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
cid = r.json()["id"]
c.delete(f"/auth-configs/{cid}")
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
lst = c.get("/auth-configs/", params={"limit":200}).json()["items"]
assert not any(a["name"] == name for a in lst), "Rejected config must not persist"
print("PASS: T-S51-006")
'

# --------------------------------------------------------------------------
# T-S51-007 — has_credentials distinguishes an empty shell from a real credential
# This is the serper-dev root cause: a credential linked to a tool but with no
# value stored (has_credentials=false, no K8s secret) → tool auth fails with 403.
# --------------------------------------------------------------------------
echo "T-S51-007 — has_credentials reflects whether a value is actually stored"
run '
import httpx, uuid
from kubernetes import client, config
from kubernetes.client.rest import ApiException
c = httpx.Client(base_url="http://localhost:8000/api/v1")
config.load_incluster_config()
v1 = client.CoreV1Api()

# (a) Credential created WITHOUT a value = empty shell → has_credentials false, no secret.
name = "s51-shell-" + uuid.uuid4().hex[:8]
r = c.post("/auth-configs/", json={"name":name,"type":"api_key"})
assert r.status_code == 201, f"create shell: {r.status_code}: {r.text}"
cid = r.json()["id"]
assert r.json()["has_credentials"] is False, f"empty shell must be has_credentials=false: {r.json()}"
# GET/list must agree.
got = next(a for a in c.get("/auth-configs/", params={"limit":200}).json()["items"] if a["id"]==cid)
assert got["has_credentials"] is False, f"list shows shell as has_credentials: {got}"
# No K8s secret should exist for an empty shell.
try:
    v1.read_namespaced_secret(f"auth-config-{cid}", "agentshield-platform")
    raise AssertionError("empty shell must NOT have a K8s secret")
except ApiException as e:
    assert e.status == 404, f"unexpected secret lookup status: {e.status}"

# (b) Updating the shell WITH a value flips has_credentials → true and creates the secret.
real = "sk-real-" + uuid.uuid4().hex
r2 = c.put(f"/auth-configs/{cid}", json={"credentials":{"serper_api_key":real}})
assert r2.status_code == 200, f"update shell: {r2.status_code}: {r2.text}"
assert r2.json()["has_credentials"] is True, f"after value, has_credentials must be true: {r2.json()}"
sec = v1.read_namespaced_secret(f"auth-config-{cid}", "agentshield-platform")
import base64
assert base64.b64decode(sec.data["serper_api_key"]).decode() == real, "secret not materialized on update"
c.delete(f"/auth-configs/{cid}")
print("PASS: T-S51-007")
'

echo ""
echo "=== Suite 51 COMPLETE: 7/7 ==="

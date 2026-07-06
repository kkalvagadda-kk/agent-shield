#!/usr/bin/env bash
# scripts/e2e/suite-17-eval-gate.sh
#
# E2E Suite 17: Eval Gate placement (Decision 20)
#
# Proves the pre-publish evaluation loop from the developer's perspective:
#   create → deploy-to-SANDBOX (ungated) → evaluate → publish (eval-gated).
#
#   T-S17-001 — deploy to environment=sandbox WITHOUT eval_passed → 201 (ungated;
#               also proves ck_deployments_env accepts 'sandbox')
#   T-S17-002 — deploy same eval_passed=false version to environment=production → 422 (still gated)
#   T-S17-003 — publish blocked when latest version eval_passed=false → 422 eval_not_passed
#   T-S17-004 — publish succeeds after PATCH eval_passed=true → 202; publish_status=pending_review
#   T-S17-005 — publish an agent with no versions → 422 no_version_to_publish
#   T-S17-006 — adversarial gate: risky-tool version, adversarial_eval_passed=false → 422; after true → 202
#   T-S17-007 — Studio-equivalent flow: create version WITHOUT eval_passed, then sandbox-deploy → 201
#
# Usage:
#   bash scripts/e2e/suite-17-eval-gate.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-17-eval-gate.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

cleanup() {
  echo ""
  echo "==> Cleanup..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
for name in ['eval-gate-s17-agent', 'eval-gate-s17-noversion', 'eval-gate-s17-risky', 'eval-gate-s17-auto', 'eval-gate-s17-fail']:
    try:
        urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/' + name, method='DELETE'), timeout=5)
    except Exception: pass
" 2>/dev/null || true
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
base = 'http://localhost:8000/api/v1/playground/datasets'
for user in ('smoke-user', 'dev'):
    try:
        r = urllib.request.urlopen(urllib.request.Request(base, headers={'X-User-Sub': user}), timeout=5)
        for ds in json.loads(r.read()):
            if ds.get('name','') in ('s17auto-ds', 's17fail-ds'):
                try:
                    urllib.request.urlopen(urllib.request.Request(base + '/' + str(ds['id']), headers={'X-User-Sub': user}, method='DELETE'), timeout=5)
                except Exception: pass
    except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

PASS=0
FAIL=0

run_test() {
  local desc="$1"; shift
  if kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "$@" 2>/dev/null; then
    echo "  PASS: $desc"; PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"; FAIL=$((FAIL + 1))
  fi
}

echo "=== Suite 17: Eval Gate placement (Decision 20) ==="
echo ""

# ---------------------------------------------------------------------------
# Setup: fresh agent + one version (eval_passed defaults false)
# ---------------------------------------------------------------------------
echo "--- Setup: eval-gate-s17-agent + a version (eval_passed=false) ---"
VERSION_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, urllib.error
base = 'http://localhost:8000/api/v1'
# recreate agent cleanly
try:
    urllib.request.urlopen(urllib.request.Request(base + '/agents/eval-gate-s17-agent', method='DELETE'))
except urllib.error.HTTPError:
    pass
req = urllib.request.Request(base + '/agents/',
    data=json.dumps({'name': 'eval-gate-s17-agent', 'team': 'platform', 'description': 's17 eval gate'}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'}, method='POST')
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    if e.code != 409: raise
# create a version WITHOUT eval_passed (defaults false)
req = urllib.request.Request(base + '/agents/eval-gate-s17-agent/versions',
    data=json.dumps({'image_tag': 'registry.internal/s17:v1'}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
r = urllib.request.urlopen(req)
d = json.loads(r.read())
assert d.get('eval_passed') in (False, None), f'version should default eval_passed false: {d.get(\"eval_passed\")}'
print(d['id'])
" 2>/dev/null || true)
echo "  version id=${VERSION_ID:0:8}..."

# ---------------------------------------------------------------------------
# T-S17-001: deploy to sandbox WITHOUT eval_passed → 201
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S17-001: deploy to environment=sandbox (ungated) ---"
if [ -n "$VERSION_ID" ]; then
  run_test "T-S17-001 deploy {environment:sandbox} eval_passed=false → 201 (+ CHECK accepts 'sandbox')" "
import urllib.request, json
body = json.dumps({'version_id': '${VERSION_ID}', 'environment': 'sandbox'}).encode()
req = urllib.request.Request('http://localhost:8000/api/v1/agents/eval-gate-s17-agent/deploy',
    data=body, headers={'Content-Type': 'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=10)
assert r.status == 201, f'expected 201 got {r.status}'
d = json.loads(r.read())
assert d.get('environment') == 'sandbox', f'environment should be sandbox: {d.get(\"environment\")}'
assert d.get('status') == 'pending', f'status should be pending: {d.get(\"status\")}'
print('sandbox deploy ungated (201); ck_deployments_env accepts sandbox')
"
else
  echo "  FAIL: T-S17-001 — no version created in setup"; FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S17-002: deploy same version to production → 422 (still gated)
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S17-002: deploy to environment=production (still gated) ---"
if [ -n "$VERSION_ID" ]; then
  run_test "T-S17-002 deploy {environment:production} eval_passed=false → 422" "
import urllib.request, json, urllib.error
body = json.dumps({'version_id': '${VERSION_ID}', 'environment': 'production'}).encode()
req = urllib.request.Request('http://localhost:8000/api/v1/agents/eval-gate-s17-agent/deploy',
    data=body, headers={'Content-Type': 'application/json'}, method='POST')
try:
    urllib.request.urlopen(req, timeout=10)
    raise AssertionError('expected 422, got 2xx')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'expected 422 got {e.code}'
    detail = json.loads(e.read()).get('detail', '')
    assert 'eval' in str(detail).lower(), f'422 should mention eval: {detail}'
    print('production deploy still gated on eval_passed (422)')
"
fi

# ---------------------------------------------------------------------------
# T-S17-003: publish blocked, eval not passed → 422 eval_not_passed
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S17-003: publish blocked (eval not passed) ---"
run_test "T-S17-003 POST /agents/eval-gate-s17-agent/publish → 422 eval_not_passed" "
import urllib.request, json, urllib.error
req = urllib.request.Request('http://localhost:8000/api/v1/agents/eval-gate-s17-agent/publish',
    data=json.dumps({}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
try:
    urllib.request.urlopen(req, timeout=5)
    raise AssertionError('expected 422, got 2xx')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'expected 422 got {e.code}'
    err = json.loads(e.read()).get('detail', {})
    assert isinstance(err, dict) and err.get('error') == 'eval_not_passed', f'wrong error: {err}'
    print('publish blocked: eval_not_passed')
"

# ---------------------------------------------------------------------------
# T-S17-004: publish succeeds after eval passes → 202
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S17-004: publish succeeds after eval_passed=true ---"
if [ -n "$VERSION_ID" ]; then
  run_test "T-S17-004 PATCH eval_passed=true then publish → 202 + publish_status=pending_review" "
import urllib.request, json
base = 'http://localhost:8000/api/v1'
# PATCH the version to eval_passed=true
req = urllib.request.Request(base + '/agents/eval-gate-s17-agent/versions/${VERSION_ID}',
    data=json.dumps({'eval_passed': True}).encode(),
    headers={'Content-Type': 'application/json'}, method='PATCH')
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 200, f'patch expected 200 got {r.status}'
# now publish
req = urllib.request.Request(base + '/agents/eval-gate-s17-agent/publish',
    data=json.dumps({}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 202, f'publish expected 202 got {r.status}'
d = json.loads(r.read())
assert d.get('publish_request_id'), f'missing publish_request_id: {d}'
# verify agent transitioned
r2 = urllib.request.urlopen(base + '/agents/eval-gate-s17-agent', timeout=5)
a = json.loads(r2.read())
assert a.get('publish_status') == 'pending_review', f'publish_status: {a.get(\"publish_status\")}'
print('publish accepted after eval passed (202); publish_status=pending_review')
"
fi

# ---------------------------------------------------------------------------
# T-S17-005: publish agent with no versions → 422 no_version_to_publish
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S17-005: publish agent with no versions ---"
run_test "T-S17-005 publish eval-gate-s17-noversion (no versions) → 422 no_version_to_publish" "
import urllib.request, json, urllib.error
base = 'http://localhost:8000/api/v1'
try:
    urllib.request.urlopen(urllib.request.Request(base + '/agents/eval-gate-s17-noversion', method='DELETE'))
except urllib.error.HTTPError:
    pass
req = urllib.request.Request(base + '/agents/',
    data=json.dumps({'name': 'eval-gate-s17-noversion', 'team': 'platform', 'description': 'no versions'}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    if e.code != 409: raise
req = urllib.request.Request(base + '/agents/eval-gate-s17-noversion/publish',
    data=json.dumps({}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
try:
    urllib.request.urlopen(req, timeout=5)
    raise AssertionError('expected 422, got 2xx')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'expected 422 got {e.code}'
    err = json.loads(e.read()).get('detail', {})
    assert isinstance(err, dict) and err.get('error') == 'no_version_to_publish', f'wrong error: {err}'
    print('publish blocked: no_version_to_publish')
"

# ---------------------------------------------------------------------------
# T-S17-006: adversarial gate
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S17-006: adversarial gate on publish ---"
run_test "T-S17-006 risky-tool version, adversarial_eval_passed=false → 422; after true → 202" "
import urllib.request, json, urllib.error
base = 'http://localhost:8000/api/v1'
try:
    urllib.request.urlopen(urllib.request.Request(base + '/agents/eval-gate-s17-risky', method='DELETE'))
except urllib.error.HTTPError:
    pass
req = urllib.request.Request(base + '/agents/',
    data=json.dumps({'name': 'eval-gate-s17-risky', 'team': 'platform', 'description': 'risky'}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    if e.code != 409: raise
# version with a high-risk tool, eval passed but adversarial NOT passed
vbody = {'image_tag': 'registry.internal/s17risky:v1',
         'eval_passed': True, 'adversarial_eval_passed': False,
         'tools': [{'name': 'issue_refund', 'risk': 'high'}]}
req = urllib.request.Request(base + '/agents/eval-gate-s17-risky/versions',
    data=json.dumps(vbody).encode(), headers={'Content-Type': 'application/json'}, method='POST')
vid = json.loads(urllib.request.urlopen(req).read())['id']
# publish blocked on adversarial
req = urllib.request.Request(base + '/agents/eval-gate-s17-risky/publish',
    data=json.dumps({}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
try:
    urllib.request.urlopen(req, timeout=5)
    raise AssertionError('expected 422, got 2xx')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'expected 422 got {e.code}'
    err = json.loads(e.read()).get('detail', {})
    assert err.get('error') == 'adversarial_eval_not_passed', f'wrong error: {err}'
# now pass adversarial and re-publish
req = urllib.request.Request(base + '/agents/eval-gate-s17-risky/versions/' + vid,
    data=json.dumps({'adversarial_eval_passed': True}).encode(),
    headers={'Content-Type': 'application/json'}, method='PATCH')
urllib.request.urlopen(req, timeout=5)
req = urllib.request.Request(base + '/agents/eval-gate-s17-risky/publish',
    data=json.dumps({}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 202, f'expected 202 after adversarial passed got {r.status}'
print('adversarial gate: 422 then 202 after passing')
"

# ---------------------------------------------------------------------------
# T-S17-007: Studio-equivalent flow — create version w/o eval, sandbox deploy
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S17-007: create version WITHOUT eval_passed, then sandbox-deploy ---"
run_test "T-S17-007 POST version (no eval_passed) → eval_passed=false; deploy sandbox → 201" "
import urllib.request, json
base = 'http://localhost:8000/api/v1'
req = urllib.request.Request(base + '/agents/eval-gate-s17-agent/versions',
    data=json.dumps({'image_tag': 'registry.internal/s17:v9'}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
d = json.loads(urllib.request.urlopen(req).read())
assert d.get('eval_passed') in (False, None), f'new version should not force eval_passed: {d.get(\"eval_passed\")}'
vid = d['id']
req = urllib.request.Request(base + '/agents/eval-gate-s17-agent/deploy',
    data=json.dumps({'version_id': vid, 'environment': 'sandbox'}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=10)
assert r.status == 201, f'sandbox deploy expected 201 got {r.status}'
print('Studio flow: version created without eval, sandbox-deployed (201)')
"

# ---------------------------------------------------------------------------
# T-S17-008: Auto-set eval_passed — passing EvalRun sets version.eval_passed
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S17-008: Auto-set eval_passed from passing EvalRun ---"
run_test "T-S17-008 EvalRun completed overall_score=0.85 → version.eval_passed=True" "
import urllib.request, json, urllib.error
base = 'http://localhost:8000/api/v1'
# Create a fresh agent + version (eval_passed defaults false)
try:
    urllib.request.urlopen(urllib.request.Request(base + '/agents/eval-gate-s17-auto', method='DELETE'))
except urllib.error.HTTPError:
    pass
req = urllib.request.Request(base + '/agents/',
    data=json.dumps({'name': 'eval-gate-s17-auto', 'team': 'platform', 'description': 'auto eval_passed test'}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'}, method='POST')
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    if e.code != 409: raise
req = urllib.request.Request(base + '/agents/eval-gate-s17-auto/versions',
    data=json.dumps({'image_tag': 'registry.internal/s17auto:v1'}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
ver = json.loads(urllib.request.urlopen(req).read())
ver_id = ver['id']
assert ver.get('eval_passed') in (False, None), f'version should start with eval_passed=false: {ver.get(\"eval_passed\")}'
# Create a dataset (required by create_eval_run)
req = urllib.request.Request(base + '/playground/datasets',
    data=json.dumps({'name': 's17auto-ds', 'items': [{'input': 'hello', 'expected': 'hi'}]}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'}, method='POST')
ds = json.loads(urllib.request.urlopen(req).read())
ds_id = ds['id']
# Create an EvalRun for that version
req = urllib.request.Request(base + '/playground/eval-runs',
    data=json.dumps({'agent_name': 'eval-gate-s17-auto', 'agent_version_id': ver_id, 'dataset_id': ds_id}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'}, method='POST')
try:
    run_resp = urllib.request.urlopen(req)
    er = json.loads(run_resp.read())
except urllib.error.HTTPError as e:
    # K8s Job creation will fail in test env; read the error detail
    body_bytes = e.read()
    raise AssertionError(f'create eval-run failed {e.code}: {body_bytes}')
er_id = er['id']
# PATCH EvalRun to completed with passing score
req = urllib.request.Request(base + '/playground/eval-runs/' + er_id,
    data=json.dumps({'status': 'completed', 'overall_score': 0.85, 'total_items': 1, 'passed_count': 1, 'failed_count': 0}).encode(),
    headers={'Content-Type': 'application/json'}, method='PATCH')
urllib.request.urlopen(req, timeout=5)
# GET version and assert eval_passed is now True
r = urllib.request.urlopen(base + '/versions/' + ver_id, timeout=5)
v = json.loads(r.read())
assert v.get('eval_passed') == True, f'expected eval_passed=True after passing EvalRun, got: {v.get(\"eval_passed\")}'
print('auto-set eval_passed=True after passing EvalRun (score=0.85 >= 0.7)')
"

# ---------------------------------------------------------------------------
# T-S17-009: Auto-set eval_passed — failing score does NOT set eval_passed
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S17-009: Failing EvalRun does NOT set eval_passed ---"
run_test "T-S17-009 EvalRun completed overall_score=0.3 → version.eval_passed remains False" "
import urllib.request, json, urllib.error
base = 'http://localhost:8000/api/v1'
# Create a fresh agent + version (eval_passed defaults false)
try:
    urllib.request.urlopen(urllib.request.Request(base + '/agents/eval-gate-s17-fail', method='DELETE'))
except urllib.error.HTTPError:
    pass
req = urllib.request.Request(base + '/agents/',
    data=json.dumps({'name': 'eval-gate-s17-fail', 'team': 'platform', 'description': 'failing score test'}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'}, method='POST')
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    if e.code != 409: raise
req = urllib.request.Request(base + '/agents/eval-gate-s17-fail/versions',
    data=json.dumps({'image_tag': 'registry.internal/s17fail:v1'}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
ver = json.loads(urllib.request.urlopen(req).read())
ver_id = ver['id']
# Create a dataset
req = urllib.request.Request(base + '/playground/datasets',
    data=json.dumps({'name': 's17fail-ds', 'items': [{'input': 'hello', 'expected': 'hi'}]}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'}, method='POST')
ds = json.loads(urllib.request.urlopen(req).read())
ds_id = ds['id']
# Create an EvalRun for that version
req = urllib.request.Request(base + '/playground/eval-runs',
    data=json.dumps({'agent_name': 'eval-gate-s17-fail', 'agent_version_id': ver_id, 'dataset_id': ds_id}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'}, method='POST')
try:
    er = json.loads(urllib.request.urlopen(req).read())
except urllib.error.HTTPError as e:
    body_bytes = e.read()
    raise AssertionError(f'create eval-run failed {e.code}: {body_bytes}')
er_id = er['id']
# PATCH EvalRun to completed with FAILING score (0.3 < 0.7 threshold)
req = urllib.request.Request(base + '/playground/eval-runs/' + er_id,
    data=json.dumps({'status': 'completed', 'overall_score': 0.3, 'total_items': 1, 'passed_count': 0, 'failed_count': 1}).encode(),
    headers={'Content-Type': 'application/json'}, method='PATCH')
urllib.request.urlopen(req, timeout=5)
# GET version and assert eval_passed is still False
r = urllib.request.urlopen(base + '/versions/' + ver_id, timeout=5)
v = json.loads(r.read())
assert v.get('eval_passed') in (False, None), f'expected eval_passed=False after failing EvalRun, got: {v.get(\"eval_passed\")}'
print('eval_passed unchanged (False) after failing EvalRun (score=0.3 < 0.7)')
"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ""
echo "--- Cleanup ---"
for ag in eval-gate-s17-agent eval-gate-s17-noversion eval-gate-s17-risky eval-gate-s17-auto eval-gate-s17-fail; do
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/$ag', method='DELETE'))
    print('deleted $ag')
except Exception:
    pass
" 2>/dev/null || true
done

# Delete s17 datasets (best-effort)
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
base = 'http://localhost:8000/api/v1/playground/datasets'
for user in ('smoke-user', 'dev'):
    req = urllib.request.Request(base, headers={'X-User-Sub': user})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        datasets = json.loads(r.read())
        for ds in datasets:
            if ds.get('name','') in ('s17auto-ds', 's17fail-ds'):
                dreq = urllib.request.Request(base + '/' + str(ds['id']),
                    headers={'X-User-Sub': user}, method='DELETE')
                try:
                    urllib.request.urlopen(dreq, timeout=5)
                    print(f'deleted dataset {ds[\"name\"]}')
                except Exception:
                    pass
    except Exception:
        pass
" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================="
echo "  Suite 17 Results: PASS=${PASS}  FAIL=${FAIL}"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

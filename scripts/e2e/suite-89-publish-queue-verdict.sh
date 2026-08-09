#!/usr/bin/env bash
# scripts/e2e/suite-89-publish-queue-verdict.sh — publish-queue verdict + deny-by-default reads
#
# Two defects, one suite, both at the API layer the browser cannot reach.
#
#   1. THE PUBLISH QUEUE CAN SHOW THE WRONG VERSION'S EVAL.
#      `routers/admin.py` resolved each request's eval by EvalRun.agent_name only —
#      `.order_by(agent_name, completed_at.desc()).distinct(agent_name)` — and keyed the
#      result map by ASSET_ID. So `PublishRequest.source_version_id` was never read, and
#      every pending request for one agent received the SAME (latest) eval. A reviewer
#      approves a release on that number. Slice 0 resolves per REQUEST, against the pinned
#      version, and reports WHERE the score came from via `eval_source`.
#
#   2. TWO LISTINGS RETURNED THE WHOLE TABLE TO AN ANONYMOUS CALLER.
#      `list_eval_runs` and `list_datasets` filtered inside `if caller:` with no `else`.
#      Both use `get_optional_user` (returns None rather than raising) and registry-api
#      installs NO global auth middleware (main.py = CORS + trace-ID only). No identity
#      meant no filter. `agents.py` fixed this exact class and left the comment
#      "previously a missing caller skipped the filter entirely and leaked every agent";
#      these two were skipped because they have no publish_status for that template.
#      EVERY EXISTING SUITE AUTHENTICATES, which is why nothing ever caught it.
#
#   T-S89-001  request pins v2, eval exists on v1 only -> eval_source="none", NO score borrowed
#   T-S89-002  request pins v2, eval exists on v2      -> eval_source="version", that run's id
#   T-S89-003  request pins no version                 -> eval_source="agent_latest"
#   T-S89-004  last_eval_pass_threshold is the RUN's own threshold (0.9), never 0.7
#   T-S89-005  GET /playground/eval-runs  with NO auth headers -> [] (was: every run)
#   T-S89-006  GET /playground/datasets   with NO auth headers -> [] (was: every dataset)
#   T-S89-007  an AUTHENTICATED caller still sees exactly their own runs (no over-correction)
#   T-S89-008  cleanup
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
# Phase filter is mandatory: this cluster carries thousands of Evicted pods and a bare
# `.items[0]` picks a dead one ~9 times in 10, reporting an infra error as a product
# failure (docs/bugs/e2e-suites-that-could-never-run.md).
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FAIL  T-S89-FIXTURE  |  no Running registry-api pod found"; exit 1
fi

echo "=== Suite 89: Publish-queue verdict + deny-by-default reads ==="
echo "    pod: $API_POD"
echo ""
RUN_TAG="s89-$(date +%s)"

# QUOTED heredoc — the shell must not expand this body. Unquoted, backticks inside
# Python comments become command substitution and are silently deleted from the source
# the interpreter sees. Fixtures arrive via the ENVIRONMENT, never spliced into source.
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && RUN_TAG='$RUN_TAG' PYTHONPATH=/app python3 -" <<'PY'
import base64, json, os, urllib.error, urllib.parse, urllib.request

PASS = 0; FAIL = 0
def ok(m):
    global PASS; print(f"PASS  {m}"); PASS += 1
def bad(m, d=""):
    global FAIL; print(f"FAIL  {m}  |  {d}"); FAIL += 1

RUN_TAG = os.environ["RUN_TAG"]
API = "http://localhost:8000"
KC = "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token"

class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return urllib.request.Request(newurl, data=req.data, method=req.get_method(),
                                      headers={k: v for k, v in req.header_items()})
_OPENER = urllib.request.build_opener(_Redirect)

def call(method, path, token=None, body=None, anon=False):
    """anon=True sends NEITHER Authorization NOR X-User-Sub — the case that leaked."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token and not anon:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = _OPENER.open(req, timeout=20)
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
    tok = json.loads(urllib.request.urlopen(urllib.request.Request(KC, data=data), timeout=20).read())["access_token"]
    p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
    return tok, json.loads(base64.urlsafe_b64decode(p))["sub"]

PT, PSUB = token_for("platform-admin", "PlatformAdmin2024")
ok("T-S89-FIXTURE-000 fetched platform-admin token")

created = {"agents": [], "datasets": [], "requests": []}

try:
    # ---------------------------------------------------------------- fixtures
    AGENT = f"{RUN_TAG}-agent"
    st, agent = call("POST", "/api/v1/agents/", PT, {
        "name": AGENT, "team": "default", "agent_type": "declarative",
        "description": "suite-89 publish-queue verdict fixture",
    })
    assert st in (200, 201), f"create agent -> {st} {str(agent)[:200]}"
    created["agents"].append(AGENT)

    # Two versions of the SAME agent. The whole bug is that these are conflated.
    vids = []
    for tag in ("v1", "v2"):
        st, ver = call("POST", f"/api/v1/agents/{AGENT}/versions", PT, {
            "version_tag": tag, "image_tag": f"stub:{tag}", "tools": [],
        })
        assert st in (200, 201), f"create version {tag} -> {st} {str(ver)[:200]}"
        vids.append(ver["id"])
    V1, V2 = vids

    # A dataset + a COMPLETED eval run pinned to v1 ONLY, with an explicit
    # pass_threshold of 0.9 (not the platform 0.7) so T-S89-004 can tell them apart.
    st, ds = call("POST", "/api/v1/playground/datasets", PT, {
        "name": f"{RUN_TAG}-ds", "mode": "reactive",
        "items": [{"kind": "reactive", "input_message": "ping", "expected_output": "pong"}],
    })
    assert st in (200, 201), f"create dataset -> {st} {str(ds)[:200]}"
    created["datasets"].append(ds["id"])

    st, run_v1 = call("POST", "/api/v1/playground/eval-runs", PT, {
        "dataset_id": ds["id"], "agent_name": AGENT,
        "agent_version_id": V1, "pass_threshold": 0.9,
    })
    assert st in (200, 201), f"create eval run -> {st} {str(run_v1)[:200]}"
    # Drive it to a completed, SCORING state through the internal update door.
    st, _ = call("PATCH", f"/api/v1/playground/eval-runs/{run_v1['id']}", PT, {
        "status": "completed", "overall_score": 0.85,
        "total_items": 1, "passed_count": 0, "failed_count": 1,
    })
    assert st in (200, 204), f"complete eval run -> {st}"
    ok("T-S89-FIXTURE-001 agent with two versions + a completed eval on v1 only")

    def publish_request(version_id):
        """A publish request is created by POST /agents/{name}/publish, which ALWAYS
        pins source_version_id to the version being published. The route is gated on
        `eval_passed`, so the fixture marks the target version passed first — the same
        manual attestation path an operator uses (PATCH /versions). That is deliberate:
        it produces a request pinning a version that has NO eval of its own, which is
        exactly the state the queue mis-renders."""
        st, _v = call("PATCH", f"/api/v1/agents/{AGENT}/versions/{version_id}", PT,
                      {"eval_passed": True})
        assert st in (200, 204), f"mark version eval_passed -> {st} {str(_v)[:200]}"
        st, pr = call("POST", f"/api/v1/agents/{AGENT}/publish", PT, {
            "version_id": version_id, "dependency_declaration": {},
        })
        # 202 Accepted — publishing enqueues a review, it does not publish inline.
        assert st in (200, 201, 202), f"publish -> {st} {str(pr)[:200]}"
        pr_id = pr.get("publish_request_id") or pr.get("id")
        assert pr_id, f"publish response carried no request id: {str(pr)[:200]}"
        created["requests"].append(pr_id)
        return {"id": pr_id}

    def queue_row(pr_id):
        st, q = call("GET", "/api/v1/admin/publish-requests", PT)
        assert st == 200, f"list publish requests -> {st}"
        items = q.get("items", q) if isinstance(q, dict) else q
        for row in items:
            if row.get("id") == pr_id:
                return row
        raise AssertionError(f"publish request {pr_id} not in queue")

    # ------------------------------------------- T-S89-001  the headline bug
    pr_v2 = publish_request(V2)
    row = queue_row(pr_v2["id"])
    if row.get("eval_source") == "none" and row.get("last_eval_score") is None:
        ok("T-S89-001 request pinning v2 does NOT borrow v1's eval (eval_source=none)")
    else:
        bad("T-S89-001 the queue borrowed another version's eval",
            f"eval_source={row.get('eval_source')!r} score={row.get('last_eval_score')!r} "
            f"(expected 'none'/None; the eval belongs to v1, the request pins v2)")

    # ------------------------------------------- T-S89-002  the version join
    st, run_v2 = call("POST", "/api/v1/playground/eval-runs", PT, {
        "dataset_id": ds["id"], "agent_name": AGENT,
        "agent_version_id": V2, "pass_threshold": 0.9,
    })
    assert st in (200, 201), f"create v2 eval run -> {st}"
    call("PATCH", f"/api/v1/playground/eval-runs/{run_v2['id']}", PT, {
        "status": "completed", "overall_score": 0.85,
        "total_items": 1, "passed_count": 0, "failed_count": 1,
    })
    row = queue_row(pr_v2["id"])
    if row.get("eval_source") == "version" and row.get("last_eval_run_id") == run_v2["id"]:
        ok("T-S89-002 an eval on the PINNED version resolves as eval_source=version")
    else:
        bad("T-S89-002 the pinned version's eval did not resolve",
            f"eval_source={row.get('eval_source')!r} run_id={row.get('last_eval_run_id')!r} "
            f"expected 'version'/{run_v2['id']}")

    # ------------------------------------------- T-S89-004  the run's OWN threshold
    if row.get("last_eval_pass_threshold") == 0.9:
        ok("T-S89-004 last_eval_pass_threshold is the run's own 0.9, not the platform 0.7")
    else:
        bad("T-S89-004 the queue reported the wrong threshold",
            f"last_eval_pass_threshold={row.get('last_eval_pass_threshold')!r} expected 0.9 "
            f"(a 0.85 score renders 'passed' against 0.7 while the gate refuses it)")

    # ------------------------------------------- T-S89-003  the legacy fallback
    # POST /agents/{name}/publish ALWAYS pins source_version_id, so a NULL row cannot
    # be produced through the API — it exists only on rows written before that pinning
    # landed. The fallback branch is still live code serving those rows, so it is
    # inserted directly rather than left unexercised. Cleaned up in the finally block.
    import asyncio, uuid as _uuid
    from sqlalchemy import text as _text
    from db import AsyncSessionLocal

    LEGACY_ID = str(_uuid.uuid4())
    async def _insert_legacy():
        async with AsyncSessionLocal() as s:
            await s.execute(_text(
                "INSERT INTO publish_requests "
                "(id, asset_id, asset_type, submitted_by, submitted_at, status, "
                " highest_risk_level, dependency_declaration, source_version_id) "
                "VALUES (:i, :a, 'agent', :u, now(), 'pending_review', 'low', '{}'::jsonb, NULL)"
            ), {"i": LEGACY_ID, "a": agent["id"], "u": PSUB})
            await s.commit()
    asyncio.run(_insert_legacy())
    created["legacy"] = LEGACY_ID

    row_none = queue_row(LEGACY_ID)
    if row_none.get("eval_source") == "agent_latest" and row_none.get("last_eval_score") is not None:
        ok("T-S89-003 a legacy request pinning no version falls back to agent_latest, LABELLED")
    else:
        bad("T-S89-003 the unpinned fallback did not resolve or was not labelled",
            f"eval_source={row_none.get('eval_source')!r} score={row_none.get('last_eval_score')!r} "
            f"(expected 'agent_latest' + a score — the fallback must be visible, not silent)")

    # ------------------------------------------- T-S89-005/006  the anonymous read
    st, anon_runs = call("GET", "/api/v1/playground/eval-runs", None, anon=True)
    n = len(anon_runs) if isinstance(anon_runs, list) else -1
    if st == 200 and n == 0:
        ok("T-S89-005 anonymous GET /playground/eval-runs returns [] (deny-by-default)")
    else:
        bad("T-S89-005 anonymous caller read eval runs",
            f"status={st} rows={n} — no JWT and no X-User-Sub must yield 0 rows, "
            f"not an unfiltered full-table read")

    # TIGHTENED IN 0.2.281 — this case used to accept '200 []'. The dataset router now derives
    # its caller from the credential on EVERY route, so an anonymous read is refused outright
    # rather than answered with a filtered-to-empty list. 401 is strictly stronger: an empty
    # 200 is indistinguishable from "you own nothing", so it told an anonymous caller that the
    # endpoint was theirs to call. It also cannot silently widen if the filter is ever dropped
    # again, which is the failure this case was originally written for. '200 []' is no longer
    # accepted — the assertion was inverted, not deleted.
    st, anon_ds = call("GET", "/api/v1/playground/datasets", None, anon=True)
    if st == 401:
        ok("T-S89-006 anonymous GET /playground/datasets is 401 (credential required, 0.2.281)")
    else:
        n = len(anon_ds) if isinstance(anon_ds, list) else -1
        bad("T-S89-006 anonymous caller read datasets",
            f"status={st} rows={n} — expected 401; datasets carry test inputs and expected outputs")

    # ------------------------------------------- T-S89-007  no over-correction
    st, mine = call("GET", "/api/v1/playground/eval-runs", PT)
    ids = {r.get("id") for r in mine} if isinstance(mine, list) else set()
    if st == 200 and run_v1["id"] in ids and run_v2["id"] in ids:
        ok("T-S89-007 an authenticated caller still sees their own runs")
    else:
        bad("T-S89-007 the deny-by-default branch over-corrected",
            f"status={st} own runs missing from {len(ids)} returned")

finally:
    if created.get("legacy"):
        try:
            import asyncio as _a
            from sqlalchemy import text as _t
            from db import AsyncSessionLocal as _S
            async def _rm():
                async with _S() as s:
                    await s.execute(_t("DELETE FROM publish_requests WHERE id = :i"),
                                    {"i": created["legacy"]})
                    await s.commit()
            _a.run(_rm())
        except Exception as exc:
            print(f"WARN  legacy fixture cleanup failed: {exc}")
    for ds_id in created["datasets"]:
        call("DELETE", f"/api/v1/playground/datasets/{ds_id}", PT)
    for name in created["agents"]:
        call("DELETE", f"/api/v1/agents/{name}", PT)
    ok("T-S89-008 cleanup complete")

print("")
print(f"=== Suite 89: PASS={PASS} FAIL={FAIL} ===")
raise SystemExit(1 if FAIL else 0)
PY

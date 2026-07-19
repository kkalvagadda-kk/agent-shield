#!/usr/bin/env bash
# scripts/e2e/suite-76-webhook-client-signing.sh
#
# E2E Suite 76: WS-4 — per-application webhook client-id + allowlist + HMAC signing.
#
# This is WS-4's CP1 acceptance gate. THE NO-FAKES RULE IS THE ACCEPTANCE. The claim
# under test is that the REAL event-gateway authenticates a REAL signed request from a
# REAL registered application and refuses everything else indistinguishably — so the
# only honest proof is a REAL client registered through the REAL registration API,
# signing a REAL body, POSTed to the REAL running event-gateway Service in-cluster (the
# same door a real sender hits), asserted against REAL committed agent_events rows.
#
#   create a REAL daemon agent + a REAL running deployment (the dispatch precondition,
#   exactly as suite-28 does) → create REAL webhook triggers through the REAL trigger
#   API → register REAL clients through the REAL POST /api/v1/triggers/{id}/clients →
#   sign with the PRODUCT'S OWN sign_webhook (extracted from
#   services/event-gateway/webhook_auth.py at runtime — see below) → POST to the REAL
#   gateway → re-read the REAL committed agent_events rows FROM THE DB.
#
# NO monkeypatched verify, NO mocked httpx, NO hand-crafted webhook_clients/agent_events
# rows, NO fabricated signatures. Every assertion reads state a real HMAC verification
# produced.
#
# THE SIGNER IS NOT A COPY. `sign_webhook` is extracted by AST from the real
# services/event-gateway/webhook_auth.py and prepended to this driver verbatim, so the
# test signs with the EXACT bytes the product ships as its sender reference. A test that
# reimplements the signature can agree with a broken product (or disagree with a correct
# one) — this one cannot drift, because there is only one implementation.
#
#   T-S76-000 — PARITY (the anti-drift assertion; the repo's #1 bug class). Exactly ONE
#               `def verify_webhook_auth`, exactly TWO call sites, ZERO per-handler
#               copies, and ZERO occurrences of the old distinct stale-ts 401 body.
#               Cheap, so it runs first. See docs/bugs/side-effecting-lost-on-declarative-
#               runner-path.md for what a second hand-maintained copy costs.
#   T-S76-001 — REAL signed POST to the AGENT hook → 202 + a REAL committed agent_events
#               row with status='matched' AND client_id = the registered client (re-read
#               FROM THE DB, not the response).
#   T-S76-002 — the SAME flow against the WORKFLOW hook → 202 + a REAL agent_events row
#               with workflow_id AND client_id set. THE PARITY PAYOFF: the workflow hook
#               gets signing with zero schema change and zero extra handler code,
#               because it calls the same verify_webhook_auth.
#   T-S76-003 — UNIFORM 401 BYTE-IDENTITY (the security property). All five failure
#               modes — unknown client, bad signature, stale timestamp, disabled client,
#               wrong-trigger client — return 401 with BYTE-IDENTICAL bodies. Asserting
#               only "each is 401" would let a per-reason body regress silently; a
#               distinct body per reason is an enumeration oracle telling an attacker
#               which check failed. Case (3) is the branch that returned a DIFFERENT
#               body ({"detail":"stale webhook timestamp"}) before WS-4 — a real
#               pre-existing oracle in the shipped product, closed here.
#   T-S76-004 — dual-mode: a trigger at auth_mode='token' still accepts the LEGACY bare
#               bearer token with NO signing headers → 202 + a real event row. Existing
#               senders must not break mid-migration.
#   T-S76-005 — a client_signed trigger REJECTS that same bare token (uniform 401),
#               proving the mode is an EXPLICIT branch and not a try-token-then-fall-back
#               priority chain. If a bare token ever succeeds here, someone reintroduced
#               the No-Bandaid anti-pattern and the upgrade buys nothing.
#   T-S76-006 — the secret is revealed EXACTLY once: the 201 carries it; a subsequent
#               GET has no `secret` KEY AT ALL (asserted absent, not merely empty).
#   T-S76-007 — UNIQUE(trigger_id, client_id): a duplicate client_id on the SAME trigger
#               → 409, while the SAME client_id on a DIFFERENT trigger → 201. The
#               allowlist is per-trigger.
#   T-S76-008 — disable/enable is a LIVE allowlist read, not a cached decision: a
#               re-enabled client is accepted again → 202 on the very next request.
#   T-S76-009 — the auth_mode UPGRADE: a fresh webhook trigger is born 'token' (its
#               handed-out webhook URL works immediately) and flips to 'client_signed'
#               when its FIRST client is registered. Read from the REAL API both times.
#               This is what keeps client_signed ⟺ "≥1 client exists" — a trigger born
#               client_signed with an empty allowlist authenticates NOBODY, and auth_mode
#               is not settable through the trigger API, so there would be no supported
#               way back. It is also why suite-28/suite-66 (which POST bare tokens to
#               triggers they just created) still pass.
#
# Fixture-unreachable is a FAIL, not a skip: if an agent/trigger/client cannot be
# created, the gate cannot be proven → hard fail. An unprovable gate is never a pass.
#
# Usage:
#   bash scripts/e2e/suite-76-webhook-client-signing.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== Suite 76: WS-4 webhook client-id + allowlist + HMAC signing (NO-FAKES) ==="

PASS=0; FAIL=0
BASH_OUT=""
bpass() { echo "PASS  $1"; PASS=$((PASS+1)); BASH_OUT="${BASH_OUT}
PASS  $1"; }
bfail() { echo "FAIL  $1"; FAIL=$((FAIL+1)); BASH_OUT="${BASH_OUT}
FAIL  $1"; }

# ---------------------------------------------------------------------------
# T-S76-000 — PARITY GREP. Runs first: it is cheap, needs no cluster, and catches
# the failure mode WS-4 exists to prevent (two hook handlers drifting apart).
# ---------------------------------------------------------------------------
GW_MAIN="services/event-gateway/main.py"
GW_AUTH="services/event-gateway/webhook_auth.py"

# NOTE: count "verify_webhook_auth(" — WITH the paren. A bare `grep -c
# "verify_webhook_auth"` also counts the import line and every docstring mention (7 in
# main.py today) and would be a meaningless assertion.
N_DEF=$(grep -c "def verify_webhook_auth" "$GW_AUTH" || true)
N_CALL=$(grep -c "verify_webhook_auth(" "$GW_MAIN" || true)
N_COPY=$(grep -c "def verify_webhook_auth" "$GW_MAIN" || true)
N_ORACLE=$(grep -c "stale webhook timestamp" "$GW_MAIN" || true)

if [ "$N_DEF" = "1" ] && [ "$N_CALL" = "2" ] && [ "$N_COPY" = "0" ] && [ "$N_ORACLE" = "0" ]; then
  bpass "T-S76-000 parity: 1 def / 2 call sites / 0 per-handler copies / 0 stale-ts oracle"
else
  bfail "T-S76-000 parity VIOLATED  |  def_in_webhook_auth=$N_DEF (want 1) call_sites_in_main=$N_CALL (want 2) copies_in_main=$N_COPY (want 0) stale_ts_oracle=$N_ORACLE (want 0)"
fi

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No running registry-api pod in namespace $NAMESPACE — the gate cannot be proven"
  echo "❌ Suite 76 FAILED (fixture unreachable — never a skip)"
  exit 1
fi
echo "  Pod: $API_POD"
echo ""

# Per-invocation paths + fixture suffix (the suite-74 lesson): a fixed /tmp/s76_out.txt
# lets two overlapping invocations (a retry, a second operator, a CI re-run against the
# same pod) share a result file and silently read each OTHER's results.
RUN_TAG="$(date +%s)$$"
RUN_SFX="s$(printf '%s' "$RUN_TAG" | tail -c 8)"
DRIVER="/tmp/s76_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s76_out_${RUN_TAG}.txt"
RUNLOG="/tmp/s76_run_${RUN_TAG}.log"

# ---------------------------------------------------------------------------
# Extract the PRODUCT'S sign_webhook and prepend it to the driver, verbatim.
# The test and every real sender therefore share ONE implementation — a drift
# between "how the suite signs" and "how the product says to sign" is impossible
# by construction rather than by discipline.
# ---------------------------------------------------------------------------
SIGNER_SRC=$(python3 - "$GW_AUTH" <<'PY'
import ast, sys
path = sys.argv[1]
src = open(path).read()
for node in ast.parse(src).body:
    if isinstance(node, ast.FunctionDef) and node.name == "sign_webhook":
        print(ast.get_source_segment(src, node))
        break
else:
    sys.exit(f"FATAL: no `def sign_webhook` in {path} — the shipped sender reference "
             f"is gone; the suite refuses to substitute its own (that is the drift "
             f"this extraction exists to prevent).")
PY
) || { echo "❌ Suite 76 FAILED (could not extract the product's sign_webhook)"; exit 1; }

{
  cat <<'HDR'
# ---------------------------------------------------------------------------
# EXTRACTED VERBATIM from services/event-gateway/webhook_auth.py at suite runtime.
# Do not edit here — this is the product's own sender reference, and that is the
# entire point: the suite signs with the exact code real applications are told to
# use, so the test cannot silently disagree with the product.
# ---------------------------------------------------------------------------
import hmac, hashlib, time
HDR
  printf '%s\n' "$SIGNER_SRC"
  cat <<'PY'
# --- end extracted product code -------------------------------------------------

import asyncio, json, os, uuid
import httpx
from sqlalchemy import text
from db import AsyncSessionLocal

BASE = "http://localhost:8000/api/v1"
# The REAL gateway Service, in-cluster — the same door a real sender hits. Never a
# mocked transport, never an in-process app.
GW = "http://agentshield-event-gateway:8091"
ADMIN = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
H = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}

SFX = os.environ["S76_SFX"]
OUT = os.environ["S76_OUT"]

AGENT = f"s76-agent-{SFX}"
WF = f"s76-wf-{SFX}"

results = []
observed = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))


def obs(msg):
    observed.append("OBSERVED " + msg)


async def last_event(agent_name, trigger_id):
    """Re-read the REAL committed agent_events row for ONE trigger.

    The gateway writes this on its own psycopg2 connection and commits BEFORE it
    responds, so reading it back through a different session proves it actually landed
    — not merely that the HTTP response claimed so.

    Keyed on trigger_id, not just agent_name: this suite drives several triggers on the
    same agent, and `received_at` has only timestamp resolution, so an agent-wide
    "latest row" query could tie and hand back a DIFFERENT trigger's event. An
    assertion that can read the wrong row is not an assertion.
    """
    async with AsyncSessionLocal() as s:
        row = (await s.execute(text(
            "SELECT status, client_id, workflow_id::text, run_id::text, filter_reason "
            "FROM agent_events WHERE agent_name = :n AND trigger_id = :t "
            "ORDER BY received_at DESC LIMIT 1"
        ), {"n": agent_name, "t": trigger_id})).first()
    if not row:
        return None
    return {"status": row[0], "client_id": row[1], "workflow_id": row[2],
            "run_id": row[3], "filter_reason": row[4]}


async def main():
    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=60)
    gw = httpx.AsyncClient(timeout=30)
    created_wf = None
    try:
        # ---------------- REAL fixtures (fail LOUD, never skip) ----------------
        # daemon: a user_delegated agent driven with no live user is denied by OPA
        # (missing_user_identity). No tools — this gate stops at authenticated
        # dispatch; WS-1/WS-2 own what the run then does.
        r = await c.post("/agents/", json={
            "name": AGENT, "team": "platform", "agent_type": "declarative",
            "execution_shape": "reactive", "agent_class": "daemon",
        })
        if r.status_code != 201:
            rec("T-S76-FIXTURE agent created", False, f"{r.status_code} {r.text[:200]}")
            return

        # A running deployment is the DISPATCH precondition — /internal/runs/start
        # requires one (suite-28 does exactly this). It is a fixture, not a mock of the
        # seam under test: the auth hop, the gateway, the signature and the event row
        # are all real. Without it every matched request would 502 and the auth
        # assertions could not be reached.
        async with AsyncSessionLocal() as s:
            aid = (await s.execute(text("SELECT id FROM agents WHERE name=:n"),
                                   {"n": AGENT})).scalar()
            vid = (await s.execute(text(
                "INSERT INTO agent_versions (agent_id, version_number, tools) "
                "VALUES (:a, 1, '[]'::jsonb) RETURNING id"), {"a": aid})).scalar()
            await s.execute(text(
                "INSERT INTO deployments (agent_id, version_id, environment, status, "
                "replicas, k8s_namespace) VALUES (:a, :v, 'production', 'running', 1, "
                "'agents-platform')"), {"a": aid, "v": vid})
            await s.commit()

        async def new_trigger():
            rr = await c.post(f"/agents/{AGENT}/triggers", json={"trigger_type": "webhook",
                                                                "enabled": True})
            if rr.status_code not in (200, 201):
                raise RuntimeError(f"trigger create failed {rr.status_code} {rr.text[:200]}")
            return rr.json()

        # A: upgraded to client_signed. B: a second trigger (wrong-trigger boundary +
        # per-trigger allowlist). TOK: never gets a client, so it stays 'token'.
        tA = await new_trigger()
        tB = await new_trigger()
        tTOK = await new_trigger()

        # ---------------- T-S76-009: born token, upgrades on first client ----------
        born = tA["auth_mode"]
        cr = await c.post(f"/triggers/{tA['id']}/clients", json={"client_id": "billing-app"})
        if cr.status_code != 201:
            rec("T-S76-FIXTURE client registered", False, f"{cr.status_code} {cr.text[:200]}")
            return
        secret_A = cr.json()["secret"]
        after = (await c.get(f"/agents/{AGENT}/triggers")).json()
        after_mode = next(t["auth_mode"] for t in after if t["id"] == tA["id"])
        still_token = next(t["auth_mode"] for t in after if t["id"] == tTOK["id"])
        rec("T-S76-009 a webhook trigger is born auth_mode='token' and UPGRADES to "
            "'client_signed' when its first client registers (a trigger with no clients "
            "stays 'token' and its handed-out URL keeps working)",
            born == "token" and after_mode == "client_signed" and still_token == "token",
            f"born={born!r} after_first_client={after_mode!r} no_client_trigger={still_token!r}")

        # ---------------- T-S76-006: secret revealed exactly once -----------------
        listed = await c.get(f"/triggers/{tA['id']}/clients")
        rows = listed.json()
        rec("T-S76-006 the signing secret is revealed EXACTLY once — the 201 carries it, "
            "and a later GET has no `secret` KEY AT ALL (absent, not empty)",
            bool(secret_A) and secret_A.startswith("whsec_")
            and listed.status_code == 200 and len(rows) == 1
            and "secret" not in rows[0],
            f"created_secret_prefix={secret_A[:6]!r} get_keys={sorted(rows[0].keys()) if rows else None}")

        # ---------------- T-S76-001: REAL signed POST → agent hook ----------------
        body = json.dumps({"event": "order.created", "id": 42}).encode()
        hdrs = sign_webhook(secret_A, body)          # the PRODUCT'S signer
        hdrs["X-Client-Id"] = "billing-app"
        # content=body, NOT json= — the signature covers these exact bytes; letting httpx
        # re-serialise a dict would change them and break the MAC.
        r1 = await gw.post(f"{GW}/hooks/{AGENT}/{tA['token']}", content=body, headers=hdrs)
        ev1 = await last_event(AGENT, tA["id"])
        rec("T-S76-001 a REAL signed request from a REAL registered client is accepted by "
            "the REAL gateway → 202 + a REAL committed agent_events row (status='matched') "
            "stamped with the resolved client_id (re-read FROM THE DB)",
            r1.status_code == 202 and ev1 is not None
            and ev1["status"] == "matched" and ev1["client_id"] == "billing-app",
            f"http={r1.status_code} body={r1.text[:160]} event={ev1}")

        # ---------------- T-S76-003: five failure modes, byte-identical -----------
        # (1) unknown client-id
        h = sign_webhook(secret_A, body); h["X-Client-Id"] = "no-such-app"
        f1 = await gw.post(f"{GW}/hooks/{AGENT}/{tA['token']}", content=body, headers=h)
        # (2) bad signature (valid client, wrong secret)
        h = sign_webhook("whsec_wrong-secret-entirely", body); h["X-Client-Id"] = "billing-app"
        f2 = await gw.post(f"{GW}/hooks/{AGENT}/{tA['token']}", content=body, headers=h)
        # (3) stale timestamp — correctly signed FOR that old ts, so ONLY freshness
        #     fails. This is the branch that returned a distinct body before WS-4.
        h = sign_webhook(secret_A, body, ts=int(time.time()) - 600); h["X-Client-Id"] = "billing-app"
        f3 = await gw.post(f"{GW}/hooks/{AGENT}/{tA['token']}", content=body, headers=h)
        # (4) disabled client — via the REAL PATCH, then a REAL request
        dis = await c.patch(f"/triggers/{tA['id']}/clients/billing-app", json={"enabled": False})
        h = sign_webhook(secret_A, body); h["X-Client-Id"] = "billing-app"
        f4 = await gw.post(f"{GW}/hooks/{AGENT}/{tA['token']}", content=body, headers=h)
        # (5) wrong-trigger: a client registered on B, signing for A's endpoint
        crB = await c.post(f"/triggers/{tB['id']}/clients", json={"client_id": "b-only-app"})
        secret_B = crB.json()["secret"]
        h = sign_webhook(secret_B, body); h["X-Client-Id"] = "b-only-app"
        f5 = await gw.post(f"{GW}/hooks/{AGENT}/{tA['token']}", content=body, headers=h)

        five = [f1, f2, f3, f4, f5]
        codes = [x.status_code for x in five]
        bodies = {x.content for x in five}
        all_401 = all(x == 401 for x in codes)
        identical = len(bodies) == 1
        matches_uniform = bodies == {b'{"detail":"invalid webhook credentials"}'}
        rec("T-S76-003 UNIFORM 401: all five failure modes (unknown client / bad signature "
            "/ stale timestamp / disabled client / wrong-trigger client) return 401 with "
            "BYTE-IDENTICAL bodies — no enumeration oracle telling an attacker which "
            "check failed",
            all_401 and identical and matches_uniform,
            f"codes={codes} distinct_bodies={len(bodies)} bodies={[b[:80] for b in bodies]} "
            f"patch_disable={dis.status_code}")

        # ---------------- T-S76-008: re-enable is a LIVE allowlist read ------------
        await c.patch(f"/triggers/{tA['id']}/clients/billing-app", json={"enabled": True})
        h = sign_webhook(secret_A, body); h["X-Client-Id"] = "billing-app"
        r8 = await gw.post(f"{GW}/hooks/{AGENT}/{tA['token']}", content=body, headers=h)
        ev8 = await last_event(AGENT, tA["id"])
        rec("T-S76-008 a RE-ENABLED client is accepted again on the very next request "
            "(202) — enable/disable is a live per-request allowlist read, not a cached "
            "decision",
            r8.status_code == 202 and ev8 is not None and ev8["client_id"] == "billing-app"
            and ev8["status"] == "matched",
            f"http={r8.status_code} event={ev8}")

        # ---------------- T-S76-005: client_signed rejects a bare token ------------
        r5 = await gw.post(f"{GW}/hooks/{AGENT}/{tA['token']}", content=body,
                           headers={"Content-Type": "application/json"})
        rec("T-S76-005 a client_signed trigger REJECTS the legacy bare token with a "
            "uniform 401 — the mode is an EXPLICIT branch, never a try-token-then-fall-"
            "back-to-signed priority chain (a 202 here means the anti-pattern is back)",
            r5.status_code == 401
            and r5.content == b'{"detail":"invalid webhook credentials"}',
            f"http={r5.status_code} body={r5.text[:160]}")

        # ---------------- T-S76-004: legacy token mode still works -----------------
        r4 = await gw.post(f"{GW}/hooks/{AGENT}/{tTOK['token']}", content=body,
                           headers={"Content-Type": "application/json"})
        ev4 = await last_event(AGENT, tTOK["id"])
        rec("T-S76-004 dual-mode: a trigger still at auth_mode='token' accepts the LEGACY "
            "bare bearer token with NO signing headers → 202 + a real event row "
            "(client_id NULL — a coarse token names no application). Existing senders "
            "do not break mid-migration",
            r4.status_code == 202 and ev4 is not None and ev4["status"] == "matched"
            and ev4["client_id"] is None,
            f"http={r4.status_code} body={r4.text[:160]} event={ev4}")

        # ---------------- T-S76-007: UNIQUE(trigger_id, client_id) -----------------
        dup = await c.post(f"/triggers/{tA['id']}/clients", json={"client_id": "billing-app"})
        same_other = await c.post(f"/triggers/{tB['id']}/clients", json={"client_id": "billing-app"})
        rec("T-S76-007 the allowlist is PER-TRIGGER: a duplicate client_id on the SAME "
            "trigger → 409, while the SAME client_id on a DIFFERENT trigger → 201",
            dup.status_code == 409 and same_other.status_code == 201,
            f"duplicate_same_trigger={dup.status_code} (want 409) "
            f"same_id_other_trigger={same_other.status_code} (want 201)")

        # ---------------- T-S76-002: the WORKFLOW hook — the parity payoff ---------
        wr = await c.post("/workflows", json={
            "name": WF, "team": "platform", "orchestration": "sequential",
            "execution_shape": "reactive", "agent_class": "daemon",
        })
        if wr.status_code not in (200, 201):
            rec("T-S76-002 workflow hook", False,
                f"FIXTURE workflow create failed {wr.status_code} {wr.text[:200]}")
        else:
            created_wf = wr.json()["id"]
            agent_id = (await c.get(f"/agents/{AGENT}")).json()["id"]
            await c.post(f"/workflows/{created_wf}/members",
                         json={"agent_id": agent_id, "position": 1})
            wt = await c.post(f"/workflows/{created_wf}/triggers",
                              json={"trigger_type": "webhook", "name": f"s76-wf-hook-{SFX}"})
            wtj = wt.json()
            wc = await c.post(f"/triggers/{wtj['id']}/clients", json={"client_id": "wf-app"})
            if wc.status_code != 201:
                rec("T-S76-002 workflow hook", False,
                    f"FIXTURE workflow client register failed {wc.status_code} {wc.text[:200]}")
            else:
                wsecret = wc.json()["secret"]
                wbody = json.dumps({"message": "hello from a signed workflow webhook"}).encode()
                wh = sign_webhook(wsecret, wbody)
                wh["X-Client-Id"] = "wf-app"
                r2 = await gw.post(f"{GW}/hooks/workflow/{WF}/{wtj['token']}",
                                   content=wbody, headers=wh)
                ev2 = await last_event(WF, wtj["id"])
                rec("T-S76-002 the SAME signing works on the WORKFLOW hook → 202 + a REAL "
                    "agent_events row with workflow_id AND client_id set — the parity "
                    "payoff: one verify_webhook_auth serves both hooks, so the workflow "
                    "hook got per-app auth with no schema change and no second handler",
                    r2.status_code == 202 and ev2 is not None
                    and ev2["status"] == "matched" and ev2["client_id"] == "wf-app"
                    and ev2["workflow_id"] is not None,
                    f"http={r2.status_code} body={r2.text[:160]} event={ev2}")

    except Exception as exc:
        # FAIL LOUD. Without this the bare try/finally writes only the cases recorded
        # BEFORE the crash, and the bash summary (PASS>0, FAIL==0) reports the suite
        # GREEN while silently dropping every remaining case. A partial run must never
        # look like a pass.
        import traceback
        rec("T-S76-999 driver ran every case without crashing", False,
            f"driver CRASHED mid-run — cases after this point never ran: "
            f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        # Write results BEFORE cleanup (the suite-69 lesson): a cleanup that throws must
        # not take the evidence with it.
        lines = [f"{'PASS' if ok else 'FAIL'}  {n}  |  {d}" for n, ok, d in results]
        lines += observed
        lines.append("SUMMARY done")
        with open(OUT, "w") as f:
            f.write("\n".join(lines) + "\n")
        try:
            if created_wf:
                for t in (await c.get(f"/workflows/{created_wf}/triggers")).json():
                    await c.delete(f"/workflows/{created_wf}/triggers/{t['id']}")
                await c.delete(f"/workflows/{created_wf}")
        except Exception:
            pass
        try:
            await c.delete(f"/agents/{AGENT}")
        except Exception:
            pass
        # agent_events rows have no delete API and this suite created them; drop this
        # invocation's own rows so the platform event log it asserts against stays real.
        try:
            async with AsyncSessionLocal() as s:
                await s.execute(text(
                    "DELETE FROM agent_events WHERE agent_name IN (:a, :w)"),
                    {"a": AGENT, "w": WF})
                await s.commit()
        except Exception:
            pass
        await c.aclose()
        await gw.aclose()


asyncio.run(main())
PY
} | kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER"

echo "  running in-pod driver against the REAL event-gateway Service…"
set +e
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app S76_SFX=$RUN_SFX S76_OUT=$OUTFILE python3 $DRIVER > $RUNLOG 2>&1"
DRIVER_RC=$?
set -e

RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
if [ -z "$RES" ]; then
  echo "ERROR: driver produced no result file (rc=$DRIVER_RC) — log tail:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -50 "$RUNLOG" 2>/dev/null || true
  echo "❌ Suite 76 FAILED (driver did not report — never a skip)"
  exit 1
fi

echo ""
while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    OBSERVED*|BOUNDARY*) echo "  $line" ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

# ---------------------------------------------------------------------------
# Completeness gate. FAIL=0 is only a pass if every gate assertion actually RAN — an
# exception, an early return, or a truncated result file otherwise yields "0 failures"
# on a half-run gate. REQUIRED_IDS is the ONE source of truth. A hardcoded case COUNT
# was tried in suite-74 and drifted the moment a case was split; a count also cannot
# say WHICH case vanished. Add a case here and nowhere else.
# ---------------------------------------------------------------------------
ALLOUT="${BASH_OUT}
${RES}"
REQUIRED_IDS="000 001 002 003 004 005 006 007 008 009"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$ALLOUT" | grep -q "T-S76-$id" || MISSING="$MISSING T-S76-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S76-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
  echo "  --- driver log tail (why it stopped) ---"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S76-COMPLETE every gate assertion ran (000-009, none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true

echo ""
echo "=== suite-76 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ Suite 76 FAILED"
  exit 1
fi
if [ "$PASS" -eq 0 ]; then
  echo "❌ Suite 76 INCONCLUSIVE (no assertions ran)"
  exit 1
fi
echo "✅ Suite 76 PASSED ($PASS assertions, all $(echo $REQUIRED_IDS | wc -w | tr -d ' ') required cases reported)"

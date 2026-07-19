#!/usr/bin/env bash
# scripts/plan-poc4/smoke-knowledge.sh
#
# POC-4 Knowledge Base / RAG — CP-2 smoke driver (the REAL path, no fakes).
#
# kubectl-exec into the registry-api pod and drive the public knowledge API end to
# end against http://localhost:8000, so the assertions hit the real router + real
# ingest pipeline + real MinIO/pgvector/embedding-sidecar:
#
#   create KB → upload a .txt (known fact) → poll to 'ready' → read chunks →
#   test-retrieval (the known fact ranks) → attach an agent (binding + tool) →
#   list bound agents.
#
# Identity is the platform's X-User-Sub / X-User-Team seam (same as the e2e suites);
# the team is resolved from the caller, never the body. Requires the embedding
# sidecar + MinIO reachable in-cluster (CP-1). Exits non-zero on any failed step.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 6)$(printf '%04x' $((RANDOM % 65536)))"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
[ -n "$API_POD" ] || { echo "FATAL: no running registry-api pod"; exit 1; }

echo "=== POC-4 smoke: Knowledge Base / RAG ==="
echo "  Pod:    $API_POD"
echo "  Suffix: $SUFFIX"

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, time, json, httpx

SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
TEAM = "platform"
USER = f"smoke-kb-{SUFFIX}"
HDR = {"X-User-Sub": USER, "X-User-Team": TEAM}

FACT = "The AgentShield mascot is a blue armadillo named Sparky."
QUERY = "What is the AgentShield mascot?"

fails = []
def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(tid)

with httpx.Client(timeout=60.0) as c:
    # 1) create KB
    r = c.post(f"{BASE}/knowledge-bases",
               json={"name": f"smoke-kb-{SUFFIX}", "description": "smoke"}, headers=HDR)
    ok = r.status_code == 201
    kb = r.json() if ok else {}
    kb_id = kb.get("id", "")
    check(ok and kb_id, "SMOKE-01", f"create KB status={r.status_code} id={kb_id}")
    if not kb_id:
        print("KBFAIL"); raise SystemExit(1)

    # 2) upload a .txt source carrying the known fact
    doc = (f"{FACT}\n\nThis is a synthetic knowledge source for the POC-4 smoke test. "
           "It contains one memorable fact so retrieval can be proven.\n\n"
           "Paragraph three exists so the document yields a real chunk.").encode()
    up = c.post(f"{BASE}/knowledge-bases/{kb_id}/sources",
                files={"file": (f"mascot-{SUFFIX}.txt", doc, "text/plain")}, headers=HDR)
    oku = up.status_code == 201 and up.json().get("status") == "pending"
    src_id = up.json().get("id", "") if up.status_code == 201 else ""
    check(oku and src_id, "SMOKE-02", f"upload source status={up.status_code} src={src_id} state={up.json().get('status') if up.status_code==201 else up.text[:120]}")

    # 3) poll to 'ready' (ingest runs in the background)
    state, chunk_count = "pending", 0
    for _ in range(60):
        g = c.get(f"{BASE}/knowledge-bases/{kb_id}/sources", headers=HDR)
        rows = g.json() if g.status_code == 200 else []
        row = next((s for s in rows if s.get("id") == src_id), {})
        state = row.get("status", "?")
        chunk_count = row.get("chunk_count", 0)
        if state in ("ready", "failed"):
            break
        time.sleep(2)
    check(state == "ready" and chunk_count > 0, "SMOKE-03",
          f"source ready state={state} chunk_count={chunk_count} err={row.get('error')}")

    # 4) chunk viewer returns the text (save→reload: read chunks back from the DB)
    ch = c.get(f"{BASE}/knowledge-bases/{kb_id}/sources/{src_id}/chunks", headers=HDR)
    chunks = ch.json() if ch.status_code == 200 else []
    okc = ch.status_code == 200 and len(chunks) >= 1 and any("Sparky" in (x.get("content") or "") for x in chunks)
    check(okc, "SMOKE-04", f"chunks status={ch.status_code} n={len(chunks)}")

    # 5) test-retrieval ranks the known fact
    sr = c.post(f"{BASE}/knowledge-bases/{kb_id}/search",
                json={"query": QUERY, "k": 5}, headers=HDR)
    hits = sr.json().get("hits", []) if sr.status_code == 200 else []
    oks = sr.status_code == 200 and any("Sparky" in (h.get("content") or "") for h in hits)
    check(oks, "SMOKE-05", f"retrieval status={sr.status_code} hits={len(hits)} top={hits[0].get('source_filename') if hits else None}")

    # 6) attach an agent (creates the binding + ensures the knowledge_search tool)
    ag = c.post(f"{BASE}/agents/",
                json={"name": f"smoke-kb-agent-{SUFFIX}", "team": TEAM,
                      "description": "smoke", "agent_type": "declarative"}, headers=HDR)
    agent_id = ag.json().get("id", "") if ag.status_code == 201 else ""
    check(bool(agent_id), "SMOKE-06", f"create agent status={ag.status_code} id={agent_id}")

    if agent_id:
        bnd = c.put(f"{BASE}/knowledge-bases/{kb_id}/agents/{agent_id}", headers=HDR)
        okb = bnd.status_code == 200 and bnd.json().get("kb_id") == kb_id
        check(okb, "SMOKE-07", f"bind agent status={bnd.status_code} body={bnd.json() if bnd.status_code==200 else bnd.text[:120]}")

        lb = c.get(f"{BASE}/knowledge-bases/{kb_id}/agents", headers=HDR)
        bound = lb.json() if lb.status_code == 200 else []
        okl = lb.status_code == 200 and any(b.get("agent_id") == agent_id for b in bound)
        check(okl, "SMOKE-08", f"list bound agents status={lb.status_code} n={len(bound)}")

print("FAILS " + (",".join(fails) if fails else "NONE"))
PY
)

echo "$RESULT"
echo ""
if echo "$RESULT" | grep -q "FAILS NONE"; then
  echo "=== POC-4 smoke: PASS ==="
  exit 0
else
  echo "=== POC-4 smoke: FAIL ==="
  exit 1
fi

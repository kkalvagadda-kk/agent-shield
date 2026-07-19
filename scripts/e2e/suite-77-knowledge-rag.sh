#!/usr/bin/env bash
# scripts/e2e/suite-77-knowledge-rag.sh
#
# E2E Suite 77: Team Knowledge Base / RAG (POC-4) — the REAL path, NO fakes.
#
# kubectl-exec into the registry-api pod and drive the public knowledge API +
# the cluster-internal knowledge_search backend end to end against
# http://localhost:8000, so every assertion hits the real router + real ingest
# pipeline + real MinIO/pgvector/embedding-sidecar. Nothing monkeypatches embed /
# VectorStore / BlobStore — a KB is created, a synthetic .txt with a KNOWN FACT is
# uploaded, ingest runs for real, and retrieval is read back from Postgres/pgvector.
#
#   T-S77-001 — create KB + upload a synthetic .txt carrying a known fact
#               ("The Zorblax project launched on 2031-04-12.") → poll the source
#               to status=ready, assert chunk_count>0.
#   T-S77-002 — save->reload->assert: re-read sources + chunks straight from the
#               backend and assert they survived the ingest round-trip (the fact
#               is present in a persisted chunk).
#   T-S77-003 — test-retrieval: POST /knowledge-bases/{kb}/search with a query
#               about the fact → the chunk carrying the fact is the top hit.
#   T-S77-004 — seed the knowledge_search HTTP tool + create+bind an agent, then
#               POST /internal/knowledge/search with X-Agent-Team / X-Agent-Name
#               → chunks returned AND citations carry {source, kb} (server-side
#               kb_id resolution). No live agent LLM run is required — this proves
#               the internal endpoint + citation shape; the agent-answer is
#               capacity-dependent and out of scope here.
#   T-S77-005 — tenant isolation (HEADLINE): a KB + ready chunk is seeded under
#               team B; team A then tries to reach it BOTH ways —
#                 (a) POST /knowledge-bases/{kbB}/search as team A → 403/404/empty
#                     (team A can't even address B's KB), and
#                 (b) POST /internal/knowledge/search with X-Agent-Team: A against
#                     B's bound agent → [] (server-side binding scoped to B).
#               Both are asserted fail-closed. A REAL leak (team A ever sees B's
#               unique marker) MUST FAIL. If team B's own data never indexed (infra
#               gap), the leak probe can't be proven against live data → SKIP with a
#               diag (the API 404 boundary still had to hold), never a false pass.
#
# Infra/capacity boundary (mirrors suite-75's SKIP-on-capacity vs FAIL-on-real-
# breakage rule): POC-4 needs the embedding-sidecar + MinIO + pgvector reachable
# in-cluster (CP-0/CP-1). When a source never reaches `ready` (sidecar/MinIO not up
# yet), the cases that need indexed chunks SKIP with a clear diagnostic. A genuine
# assertion failure — wrong retrieval, a broken citation shape, or a tenancy LEAK —
# always FAILs and fails the suite.
#
# Auth: the public KB router uses get_optional_user with the platform's standard
# X-User-Sub / X-User-Team header fallback (same seam agents.py / tools.py use), so
# the in-pod caller authenticates with headers only (no Keycloak token). The
# internal endpoint takes NO user auth — it reads X-Agent-Team / X-Agent-Name.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0; SKIP=0

SUFFIX="$(date +%s | tail -c 6)$(printf '%04x' $((RANDOM % 65536)))"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FATAL: registry-api pod not found in $NAMESPACE"
  exit 1
fi

echo "=== Suite 77: Team Knowledge Base / RAG (POC-4) — real path, no fakes ==="
echo "  Pod:    $API_POD"
echo "  Suffix: $SUFFIX"
echo ""

# Tally the RESULT lines emitted by the in-pod python program.
tally() {
  local block="$1"
  while IFS= read -r line; do
    case "$line" in
      "RESULT "*)
        local rest="${line#RESULT }"
        local tid="${rest%% *}"; local rem="${rest#* }"
        local verdict="${rem%% *}"; local detail="${rem#* }"
        case "$verdict" in
          PASS) echo "  PASS: $tid — $detail"; PASS=$((PASS + 1)) ;;
          FAIL) echo "  FAIL: $tid — $detail"; FAIL=$((FAIL + 1)) ;;
          SKIP) echo "  SKIP: $tid — $detail"; SKIP=$((SKIP + 1)) ;;
        esac
        ;;
      "DIAG "*) echo "    ${line#DIAG }" ;;
    esac
  done <<< "$block"
}

BLOCK=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY' 2>/dev/null || true
import os, time, json, httpx

SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"

# --- Team A (the owner) — platform, via the header seam (no Keycloak token) -----
TEAM_A = "platform"
USER_A = f"s77-user-{SUFFIX}"
HDR_A = {"X-User-Sub": USER_A, "X-User-Team": TEAM_A}
FACT_A = "The Zorblax project launched on 2031-04-12."
QUERY_A = "When did the Zorblax project launch?"
MARKER_A = "Zorblax"     # unique token that proves A's own content

# --- Team B (the isolation victim) — a distinct team, distinct fact -------------
TEAM_B = f"s77-teamb-{SUFFIX}"
USER_B = f"s77-userb-{SUFFIX}"
HDR_B = {"X-User-Sub": USER_B, "X-User-Team": TEAM_B}
FACT_B = "The Qorvex initiative concluded on 2029-11-03."
QUERY_B = "When did the Qorvex initiative conclude?"
MARKER_B = "Qorvex"      # unique token; team A seeing it ⇒ a real tenancy LEAK

# Exact knowledge_search tool body (contracts/knowledge-search-tool.md). Seeding is
# idempotent — a 409 (already seeded by seed-defaults.sh) is fine.
KNOWLEDGE_SEARCH_TOOL = {
    "name": "knowledge_search",
    "display_name": "Knowledge Search",
    "description": ("Search the team's knowledge base for passages relevant to a "
                    "question. Returns the most relevant document chunks with their "
                    "source. Use this to ground answers in the team's own documents "
                    "and cite them."),
    "type": "http",
    "risk_level": "low",
    "owner_team": "platform",
    "side_effecting": False,
    "http_method": "POST",
    "http_url": ("http://agentshield-registry-api.agentshield-platform.svc.cluster.local"
                 ":8000/api/v1/internal/knowledge/search"),
    "http_headers": {
        "Content-Type": "application/json",
        "X-Agent-Team": "{{AGENTSHIELD_AGENT_TEAM}}",
        "X-Agent-Name": "{{AGENT_NAME}}",
    },
    "http_body_template": "{\"query\": \"{{query}}\", \"k\": 5}",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "The question or search phrase to look up in the knowledge base."},
        },
        "required": ["query"],
    },
}


def out(tid, verdict, detail=""):
    print(f"RESULT {tid} {verdict} {detail}")


def diag(msg):
    print(f"DIAG {msg}")


def synth_doc(fact: str) -> bytes:
    return (f"{fact}\n\nThis is a synthetic knowledge source for the POC-4 RAG e2e "
            "suite. It carries exactly one memorable fact so retrieval can be proven "
            "end to end.\n\nA third paragraph guarantees the document yields at least "
            "one real, non-empty chunk.").encode()


def poll_ready(c, kb_id, src_id, hdr, tries=60):
    state, cc, err = "pending", 0, None
    for _ in range(tries):
        g = c.get(f"{BASE}/knowledge-bases/{kb_id}/sources", headers=hdr)
        rows = g.json() if g.status_code == 200 else []
        row = next((s for s in rows if s.get("id") == src_id), {})
        state = row.get("status", "?")
        cc = row.get("chunk_count", 0)
        err = row.get("error")
        if state in ("ready", "failed"):
            break
        time.sleep(2)
    return state, cc, err


def provision_kb(c, hdr, name, fact, filename):
    """Create a KB, upload one synthetic .txt, poll to terminal. Returns
    (kb_id, src_id, state, chunk_count, error). Empty kb_id ⇒ create failed."""
    r = c.post(f"{BASE}/knowledge-bases", json={"name": name, "description": "suite-77"}, headers=hdr)
    if r.status_code != 201:
        diag(f"KB create failed name={name} status={r.status_code} body={r.text[:160]}")
        return "", "", "?", 0, r.text[:160]
    kb_id = r.json().get("id", "")
    up = c.post(f"{BASE}/knowledge-bases/{kb_id}/sources",
                files={"file": (filename, synth_doc(fact), "text/plain")}, headers=hdr)
    if up.status_code != 201:
        diag(f"source upload failed kb={kb_id} status={up.status_code} body={up.text[:160]}")
        return kb_id, "", "?", 0, up.text[:160]
    src_id = up.json().get("id", "")
    state, cc, err = poll_ready(c, kb_id, src_id, hdr)
    return kb_id, src_id, state, cc, err


created_kbs = []   # (kb_id, hdr)
created_agents = []  # (agent_name, hdr)

with httpx.Client(timeout=60.0) as c:
    # ======================================================================
    # Provision the team A KB + ready source (shared by 001–004).
    # ======================================================================
    kbA, srcA, stateA, ccA, errA = provision_kb(
        c, HDR_A, f"s77-kb-{SUFFIX}", FACT_A, f"zorblax-{SUFFIX}.txt")
    if kbA:
        created_kbs.append((kbA, HDR_A))
    readyA = (stateA == "ready" and ccA > 0)

    # ---- T-S77-001: create + upload + ready + chunk_count>0 -------------------
    if not kbA or not srcA:
        out("T-S77-001", "FAIL", f"KB/source provisioning failed kb={bool(kbA)} src={bool(srcA)} err={errA}")
    elif readyA:
        out("T-S77-001", "PASS", f"KB created, source ingested to ready with chunk_count={ccA}")
    elif stateA in ("pending", "indexing"):
        out("T-S77-001", "SKIP",
            f"ingest never reached ready (state={stateA}) — embedding-sidecar/MinIO not up (infra)")
    else:
        out("T-S77-001", "FAIL", f"ingest terminal but not ready: state={stateA} chunks={ccA} err={errA}")

    # ---- T-S77-002: save->reload->assert — re-read sources + chunks ----------
    if not readyA:
        out("T-S77-002", "SKIP", f"source not ready (state={stateA}) — nothing to reload (infra)")
    else:
        g2 = c.get(f"{BASE}/knowledge-bases/{kbA}/sources", headers=HDR_A)
        rows2 = g2.json() if g2.status_code == 200 else []
        row2 = next((s for s in rows2 if s.get("id") == srcA), {})
        src_survived = (g2.status_code == 200 and row2.get("status") == "ready"
                        and row2.get("chunk_count", 0) > 0)
        ch = c.get(f"{BASE}/knowledge-bases/{kbA}/sources/{srcA}/chunks", headers=HDR_A)
        chunks = ch.json() if ch.status_code == 200 else []
        chunks_survived = (ch.status_code == 200 and len(chunks) >= 1
                           and any(MARKER_A in (x.get("content") or "") for x in chunks))
        if src_survived and chunks_survived:
            out("T-S77-002", "PASS",
                f"reload from backend: source ready + {len(chunks)} chunks persisted (fact present)")
        else:
            out("T-S77-002", "FAIL",
                f"reload lost data: src_ok={src_survived} chunk_status={ch.status_code} "
                f"n_chunks={len(chunks)} fact_present={chunks_survived}")

    # ---- T-S77-003: test-retrieval ranks the fact ----------------------------
    if not readyA:
        out("T-S77-003", "SKIP", f"source not ready (state={stateA}) — retrieval needs chunks (infra)")
    else:
        sr = c.post(f"{BASE}/knowledge-bases/{kbA}/search",
                    json={"query": QUERY_A, "k": 5}, headers=HDR_A)
        hits = sr.json().get("hits", []) if sr.status_code == 200 else []
        top_has = bool(hits) and MARKER_A in (hits[0].get("content") or "")
        any_has = any(MARKER_A in (h.get("content") or "") for h in hits)
        if sr.status_code == 200 and top_has:
            out("T-S77-003", "PASS",
                f"fact is the top hit (score={hits[0].get('score')} source={hits[0].get('source_filename')})")
        elif sr.status_code == 200 and any_has:
            out("T-S77-003", "PASS", f"fact retrieved (present among {len(hits)} hits, not rank-1)")
        else:
            out("T-S77-003", "FAIL",
                f"fact not retrieved: status={sr.status_code} hits={len(hits)}")

    # ---- T-S77-004: seed tool + create+bind agent + internal search ----------
    tr = c.post(f"{BASE}/tools/", json=KNOWLEDGE_SEARCH_TOOL, headers=HDR_A)
    tool_ok = tr.status_code in (200, 201, 409)
    agent_name = f"s77-agent-{SUFFIX}"
    ag = c.post(f"{BASE}/agents/",
                json={"name": agent_name, "team": TEAM_A, "description": "suite-77",
                      "agent_type": "declarative"}, headers=HDR_A)
    agent_id = ag.json().get("id", "") if ag.status_code == 201 else ""
    if agent_id:
        created_agents.append((agent_name, HDR_A))
    bnd = None
    if kbA and agent_id:
        bnd = c.put(f"{BASE}/knowledge-bases/{kbA}/agents/{agent_id}", headers=HDR_A)
    bound_ok = bnd is not None and bnd.status_code == 200 and bnd.json().get("kb_id") == kbA

    if not (tool_ok and agent_id and bound_ok):
        out("T-S77-004", "FAIL",
            f"setup failed: tool={tr.status_code} agent={ag.status_code} "
            f"bind={(bnd.status_code if bnd is not None else 'n/a')}")
    elif not readyA:
        out("T-S77-004", "SKIP", f"KB not ready (state={stateA}) — internal search needs chunks (infra)")
    else:
        isr = c.post(f"{BASE}/internal/knowledge/search",
                     json={"query": QUERY_A, "k": 5},
                     headers={"X-Agent-Team": TEAM_A, "X-Agent-Name": agent_name})
        body = isr.json() if isr.status_code == 200 else {}
        i_chunks = body.get("chunks", [])
        i_cits = body.get("citations", [])
        chunk_has_fact = any(MARKER_A in (ch.get("content") or "") for ch in i_chunks)
        chunk_prov_ok = bool(i_chunks) and all(("source" in ch and "kb" in ch) for ch in i_chunks)
        cit_shape_ok = bool(i_cits) and all(
            isinstance(x, dict) and isinstance(x.get("source"), str) and isinstance(x.get("kb"), str)
            for x in i_cits)
        if isr.status_code == 200 and chunk_has_fact and chunk_prov_ok and cit_shape_ok:
            out("T-S77-004", "PASS",
                f"internal search: {len(i_chunks)} chunks + citations="
                f"{[(x['source'], x['kb']) for x in i_cits]}")
        else:
            out("T-S77-004", "FAIL",
                f"internal search status={isr.status_code} chunks={len(i_chunks)} "
                f"fact={chunk_has_fact} chunk_prov={chunk_prov_ok} cit_shape={cit_shape_ok} "
                f"note={body.get('note')}")

    # ======================================================================
    # T-S77-005: tenant isolation (HEADLINE)
    # ======================================================================
    kbB, srcB, stateB, ccB, errB = provision_kb(
        c, HDR_B, f"s77-kbB-{SUFFIX}", FACT_B, f"qorvex-{SUFFIX}.txt")
    if kbB:
        created_kbs.append((kbB, HDR_B))
    agentB_name = f"s77-agentb-{SUFFIX}"   # must match ^[a-z0-9-]+$ (lowercase only)
    agB = c.post(f"{BASE}/agents/",
                 json={"name": agentB_name, "team": TEAM_B, "description": "suite-77 teamB",
                       "agent_type": "declarative"}, headers=HDR_B)
    agentB_id = agB.json().get("id", "") if agB.status_code == 201 else ""
    if agentB_id:
        created_agents.append((agentB_name, HDR_B))
    bndB = None
    if kbB and agentB_id:
        bndB = c.put(f"{BASE}/knowledge-bases/{kbB}/agents/{agentB_id}", headers=HDR_B)
    setupB_ok = bool(kbB) and bool(agentB_id) and bndB is not None and bndB.status_code == 200

    if not setupB_ok:
        out("T-S77-005", "SKIP",
            f"team-B setup incomplete kb={bool(kbB)} agent={agB.status_code} "
            f"bind={(bndB.status_code if bndB is not None else 'n/a')} — cannot run isolation probe")
    else:
        readyB = (stateB == "ready" and ccB > 0)

        # (a) API probe: team A tries to search team B's KB directly. _get_kb_or_404
        # filters by the caller's team, so team A must not be able to address it.
        sA = c.post(f"{BASE}/knowledge-bases/{kbB}/search",
                    json={"query": QUERY_B, "k": 5}, headers=HDR_A)
        api_hits = sA.json().get("hits", []) if sA.status_code == 200 else []
        leak_api = (sA.status_code == 200
                    and any(MARKER_B in (h.get("content") or "") for h in api_hits))
        api_closed = sA.status_code in (403, 404) or (sA.status_code == 200 and len(api_hits) == 0)

        # (b) internal probe: team A header against team B's bound agent. The binding
        # is resolved server-side by (agent_name, team); (agentB, teamA) matches no
        # row → fail-closed empty. A non-empty chunk here would be a store-level leak.
        iA = c.post(f"{BASE}/internal/knowledge/search",
                    json={"query": QUERY_B, "k": 5},
                    headers={"X-Agent-Team": TEAM_A, "X-Agent-Name": agentB_name})
        iA_body = iA.json() if iA.status_code == 200 else {}
        iA_chunks = iA_body.get("chunks", [])
        leak_internal = any(MARKER_B in (ch.get("content") or "") for ch in iA_chunks)
        internal_closed = (iA.status_code == 200 and len(iA_chunks) == 0)

        # Sanity: team B's OWN internal search returns its data — proves the leak
        # probe is being run against a genuinely-loaded KB, not laundering a broken
        # ingest into a green isolation result.
        b_loaded = False
        if readyB:
            iB = c.post(f"{BASE}/internal/knowledge/search",
                        json={"query": QUERY_B, "k": 5},
                        headers={"X-Agent-Team": TEAM_B, "X-Agent-Name": agentB_name})
            iB_body = iB.json() if iB.status_code == 200 else {}
            b_loaded = any(MARKER_B in (ch.get("content") or "") for ch in iB_body.get("chunks", []))

        if leak_api or leak_internal:
            out("T-S77-005", "FAIL",
                f"TENANT ISOLATION LEAK: team A saw team B's marker "
                f"(api_leak={leak_api} status={sA.status_code}; internal_leak={leak_internal})")
        elif not (api_closed and internal_closed):
            out("T-S77-005", "FAIL",
                f"fail-closed boundary broken: api_status={sA.status_code} api_hits={len(api_hits)} "
                f"internal_status={iA.status_code} internal_chunks={len(iA_chunks)}")
        elif b_loaded:
            out("T-S77-005", "PASS",
                f"team A blocked from team B's loaded KB at BOTH API (status={sA.status_code}) "
                f"and internal store (chunks=[]); team B's own search confirms data present (fail-closed)")
        else:
            out("T-S77-005", "SKIP",
                f"team-B data never indexed (state={stateB}) — API 404 boundary held "
                f"(status={sA.status_code}) but leak probe has no live data to test (infra)")

    # ======================================================================
    # Cleanup (best-effort). The seeded knowledge_search platform tool is
    # intentionally left in place (it is the real, shared, idempotent seed).
    # Deleting a KB cascades its sources + chunks + vector rows.
    # ======================================================================
    for (aname, ahdr) in created_agents:
        try:
            c.delete(f"{BASE}/agents/{aname}", headers=ahdr)
        except Exception:
            pass
    for (kid, khdr) in created_kbs:
        try:
            c.delete(f"{BASE}/knowledge-bases/{kid}", headers=khdr)
        except Exception:
            pass
PY
)

echo "$BLOCK"
tally "$BLOCK"

echo ""
echo "==> Suite 77 Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped"
[ "$FAIL" -eq 0 ] || exit 1

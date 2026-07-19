#!/usr/bin/env bash
# scripts/e2e/suite-80-agent-knowledge-binding.sh
#
# E2E Suite 80: Multi-KB agent bindings + knowledge_search as a DERIVED tool.
#
# Guards the "Knowledge Search is a special config, not a hand-picked tool"
# change: an agent can be bound to MANY knowledge bases, the internal search fans
# out across all of them, and the knowledge_search tool's presence in agent_tools
# is governed SOLELY by whether the agent still has ≥1 KB binding.
#
# kubectl-exec into the registry-api pod and drive the REAL public + internal
# routers against http://localhost:8000 (no fakes). Auth via the X-User-Sub /
# X-User-Team header seam (same as suite-77); internal search reads X-Agent-*.
#
#   T-S80-001 — bind_agent is ADDITIVE (multi-KB), not drop-then-insert: bind KB1
#               then KB2 to one agent → reverse-lookup GET
#               /knowledge-bases/agent-bindings/{agent_id} returns BOTH.
#   T-S80-002 — reverse-lookup reflects an unbind: DELETE the KB1 binding →
#               reverse-lookup returns only KB2 (KB1 gone, KB2 intact — proves the
#               earlier bind did NOT wipe sibling bindings).
#   T-S80-003 — internal search FANS OUT across the agent's KBs: two KBs with
#               distinct facts, agent bound to both → a query for fact-1 retrieves
#               KB1's fact tagged with KB1's name, a query for fact-2 retrieves
#               KB2's, each citation carrying the RIGHT {source, kb}. (Needs ingest
#               ready — SKIP on infra, never a false pass.)
#   T-S80-004 — knowledge_search is a DERIVED tool (the invariant): while a KB is
#               bound, PUT /agents/{name} with metadata.tools OMITTING
#               knowledge_search must NOT detach it (GET /agents/{name}/tools still
#               lists it); after unbinding the LAST KB and PUTting again, it IS gone.
#
# Infra boundary (mirrors suite-77): 001/002/004 need only KBs+agent+bindings and
# always run; 003 needs indexed chunks and SKIPs when the sidecar/MinIO aren't up.
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

echo "=== Suite 80: Multi-KB agent bindings + derived knowledge_search ==="
echo "  Pod:    $API_POD"
echo "  Suffix: $SUFFIX"
echo ""

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
import os, time, httpx

SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"

TEAM = "platform"
USER = f"s80-user-{SUFFIX}"
HDR = {"X-User-Sub": USER, "X-User-Team": TEAM}

FACT1 = "The Vantexa satellite reached orbit on 2033-08-19."
FACT2 = "The Brellium reactor achieved ignition on 2035-02-27."
Q1 = "When did the Vantexa satellite reach orbit?"
Q2 = "When did the Brellium reactor achieve ignition?"
M1 = "Vantexa"
M2 = "Brellium"

KNOWLEDGE_SEARCH_TOOL = {
    "name": "knowledge_search",
    "display_name": "Knowledge Search",
    "description": ("Search the team's knowledge base for passages relevant to a "
                    "question. Returns the most relevant document chunks with their source."),
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
        "properties": {"query": {"type": "string", "description": "The search phrase."}},
        "required": ["query"],
    },
}


def out(tid, verdict, detail=""):
    print(f"RESULT {tid} {verdict} {detail}")


def diag(msg):
    print(f"DIAG {msg}")


def synth_doc(fact: str) -> bytes:
    return (f"{fact}\n\nSynthetic knowledge source for the suite-80 multi-KB e2e. It "
            "carries exactly one memorable fact so per-KB retrieval can be proven.\n\n"
            "A third paragraph guarantees at least one real, non-empty chunk.").encode()


def poll_ready(c, kb_id, src_id, tries=60):
    state, cc = "pending", 0
    for _ in range(tries):
        g = c.get(f"{BASE}/knowledge-bases/{kb_id}/sources", headers=HDR)
        rows = g.json() if g.status_code == 200 else []
        row = next((s for s in rows if s.get("id") == src_id), {})
        state = row.get("status", "?"); cc = row.get("chunk_count", 0)
        if state in ("ready", "failed"):
            break
        time.sleep(2)
    return state, cc


def provision_kb(c, name, fact, filename):
    r = c.post(f"{BASE}/knowledge-bases", json={"name": name, "description": "suite-80"}, headers=HDR)
    if r.status_code != 201:
        diag(f"KB create failed name={name} status={r.status_code} body={r.text[:160]}")
        return "", "", "?", 0
    kb_id = r.json().get("id", "")
    up = c.post(f"{BASE}/knowledge-bases/{kb_id}/sources",
                files={"file": (filename, synth_doc(fact), "text/plain")}, headers=HDR)
    if up.status_code != 201:
        diag(f"upload failed kb={kb_id} status={up.status_code}")
        return kb_id, "", "?", 0
    src_id = up.json().get("id", "")
    state, cc = poll_ready(c, kb_id, src_id)
    return kb_id, src_id, state, cc


created_kbs = []
created_agents = []

with httpx.Client(timeout=60.0) as c:
    # Seed the derived tool (idempotent; 409 if already seeded).
    tr = c.post(f"{BASE}/tools/", json=KNOWLEDGE_SEARCH_TOOL, headers=HDR)
    tool_ok = tr.status_code in (200, 201, 409)

    kb1, src1, st1, cc1 = provision_kb(c, f"s80-kb1-{SUFFIX}", FACT1, f"vantexa-{SUFFIX}.txt")
    kb2, src2, st2, cc2 = provision_kb(c, f"s80-kb2-{SUFFIX}", FACT2, f"brellium-{SUFFIX}.txt")
    for k in (kb1, kb2):
        if k:
            created_kbs.append(k)

    agent_name = f"s80-agent-{SUFFIX}"
    ag = c.post(f"{BASE}/agents/",
                json={"name": agent_name, "team": TEAM, "description": "suite-80",
                      "agent_type": "declarative"}, headers=HDR)
    agent_id = ag.json().get("id", "") if ag.status_code == 201 else ""
    if agent_id:
        created_agents.append(agent_name)

    setup_ok = bool(kb1) and bool(kb2) and bool(agent_id) and tool_ok

    # ---- T-S80-001: bind is ADDITIVE (multi-KB) ------------------------------
    if not setup_ok:
        out("T-S80-001", "FAIL",
            f"setup failed kb1={bool(kb1)} kb2={bool(kb2)} agent={ag.status_code} tool={tr.status_code}")
    else:
        b1 = c.put(f"{BASE}/knowledge-bases/{kb1}/agents/{agent_id}", headers=HDR)
        b2 = c.put(f"{BASE}/knowledge-bases/{kb2}/agents/{agent_id}", headers=HDR)
        rl = c.get(f"{BASE}/knowledge-bases/agent-bindings/{agent_id}", headers=HDR)
        bound_ids = {row.get("kb_id") for row in (rl.json() if rl.status_code == 200 else [])}
        if b1.status_code == 200 and b2.status_code == 200 and {kb1, kb2} <= bound_ids:
            out("T-S80-001", "PASS", f"agent bound to BOTH KBs (reverse-lookup={sorted(bound_ids)})")
        else:
            out("T-S80-001", "FAIL",
                f"bind1={b1.status_code} bind2={b2.status_code} reverse={rl.status_code} "
                f"bound={sorted(bound_ids)} want both {kb1},{kb2} (drop-then-insert regression?)")

    # ---- T-S80-002: unbind one, sibling survives -----------------------------
    if not setup_ok:
        out("T-S80-002", "SKIP", "setup failed — nothing to unbind")
    else:
        d = c.delete(f"{BASE}/knowledge-bases/{kb1}/agents/{agent_id}", headers=HDR)
        rl2 = c.get(f"{BASE}/knowledge-bases/agent-bindings/{agent_id}", headers=HDR)
        after = {row.get("kb_id") for row in (rl2.json() if rl2.status_code == 200 else [])}
        if d.status_code == 204 and kb1 not in after and kb2 in after:
            out("T-S80-002", "PASS", f"KB1 unbound, KB2 intact (reverse-lookup={sorted(after)})")
        else:
            out("T-S80-002", "FAIL",
                f"unbind={d.status_code} after={sorted(after)} want only {kb2}")
        # Re-bind KB1 so 003 can prove fan-out across BOTH KBs.
        c.put(f"{BASE}/knowledge-bases/{kb1}/agents/{agent_id}", headers=HDR)

    # ---- T-S80-003: internal search FANS OUT across both KBs ------------------
    ready_both = (st1 == "ready" and cc1 > 0 and st2 == "ready" and cc2 > 0)
    if not setup_ok:
        out("T-S80-003", "SKIP", "setup failed")
    elif not ready_both:
        out("T-S80-003", "SKIP",
            f"ingest not ready for both (kb1={st1}/{cc1} kb2={st2}/{cc2}) — sidecar/MinIO infra")
    else:
        ihdr = {"X-Agent-Team": TEAM, "X-Agent-Name": agent_name}
        r1 = c.post(f"{BASE}/internal/knowledge/search", json={"query": Q1, "k": 5}, headers=ihdr)
        r2 = c.post(f"{BASE}/internal/knowledge/search", json={"query": Q2, "k": 5}, headers=ihdr)
        b1j = r1.json() if r1.status_code == 200 else {}
        b2j = r2.json() if r2.status_code == 200 else {}
        # fact-1 comes back for Q1, fact-2 for Q2 — proves BOTH KBs are searched.
        got1 = any(M1 in (ch.get("content") or "") for ch in b1j.get("chunks", []))
        got2 = any(M2 in (ch.get("content") or "") for ch in b2j.get("chunks", []))
        # each citation carries a {source, kb} shape.
        cits_ok = all(
            isinstance(x.get("source"), str) and isinstance(x.get("kb"), str)
            for x in (b1j.get("citations", []) + b2j.get("citations", []))
        )
        if got1 and got2 and cits_ok:
            out("T-S80-003", "PASS",
                f"fan-out proven: Q1→{M1} (cits={[c2['kb'] for c2 in b1j.get('citations', [])]}), "
                f"Q2→{M2} (cits={[c2['kb'] for c2 in b2j.get('citations', [])]})")
        else:
            out("T-S80-003", "FAIL",
                f"fan-out broken: got1={got1} got2={got2} cits_ok={cits_ok} "
                f"n1={len(b1j.get('chunks', []))} n2={len(b2j.get('chunks', []))}")

    # ---- T-S80-004: knowledge_search is a DERIVED tool (invariant) ------------
    # At this point the agent has ≥1 KB bound (kb2, and kb1 re-bound in 002).
    if not setup_ok:
        out("T-S80-004", "SKIP", "setup failed")
    else:
        def has_ks():
            g = c.get(f"{BASE}/agents/{agent_name}/tools", headers=HDR)
            items = g.json().get("items", []) if g.status_code == 200 else []
            return any(t.get("name") == "knowledge_search" for t in items)

        # PUT with metadata.tools OMITTING knowledge_search — must NOT detach it,
        # because a KB is still bound (updateAgent re-asserts the invariant).
        p1 = c.put(f"{BASE}/agents/{agent_name}",
                   json={"metadata": {"tools": []}}, headers=HDR)
        kept = has_ks()
        # Now unbind ALL KBs, then PUT again — knowledge_search must be GONE.
        for kid in (kb1, kb2):
            c.delete(f"{BASE}/knowledge-bases/{kid}/agents/{agent_id}", headers=HDR)
        p2 = c.put(f"{BASE}/agents/{agent_name}",
                   json={"metadata": {"tools": []}}, headers=HDR)
        gone = not has_ks()
        if p1.status_code == 200 and kept and p2.status_code == 200 and gone:
            out("T-S80-004", "PASS",
                "knowledge_search kept while bound (survives a tools-omitting PUT), removed after last unbind")
        else:
            out("T-S80-004", "FAIL",
                f"invariant broken: put1={p1.status_code} kept_while_bound={kept} "
                f"put2={p2.status_code} gone_after_unbind={gone}")

    # Cleanup (best-effort). Leave the shared knowledge_search tool seed in place.
    for aname in created_agents:
        try:
            c.delete(f"{BASE}/agents/{aname}", headers=HDR)
        except Exception:
            pass
    for kid in created_kbs:
        try:
            c.delete(f"{BASE}/knowledge-bases/{kid}", headers=HDR)
        except Exception:
            pass
PY
)

echo "$BLOCK"
tally "$BLOCK"

echo ""
echo "==> Suite 80 Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped"
[ "$FAIL" -eq 0 ] || exit 1

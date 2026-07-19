#!/usr/bin/env bash
# scripts/e2e/suite-79-operate-parity.sh
#
# E2E Suite 79: WS-6 — operate-surface parity (Studio).
#
# THE CLAIM UNDER TEST: there is ONE shape→Overview dispatcher, N consumers, and ZERO
# inline forks — and the bundle the cluster actually SERVES contains that code.
#
# Why this suite exists. The repo's #1 bug class is two parallel paths that drift while a
# safe default hides it (docs/bugs/side-effecting-lost-on-declarative-runner-path.md).
# WS-6 collapsed a LIVE instance of exactly that: `CatalogDetailPage` hand-wrote its own
# shape→overview chain that handled reactive/durable/scheduled and had NO event-driven
# branch at all, while the shared component set has FOUR. It failed SAFE — the catalog
# page simply rendered less — which is precisely why it survived unnoticed. A fork that
# fails loudly gets fixed in a day; a fork that fails safe lives for months.
#
# NO FAKES. Every assertion reads a real artifact:
#   - the parity cases grep the REAL working tree (not a fixture copy);
#   - the bundle cases fetch the REAL bytes the REAL edge serves to a REAL browser
#     (https gateway → Envoy → studio nginx), not the image, not the dist/ dir;
#   - the producer case calls the REAL registry-api through its REAL router and reads a
#     REAL response.
#
# WHY A SERVED-BUNDLE GREP AND NOT JUST VITEST. Vitest asserts the DOM a component
# renders in jsdom; it passes whether or not that component ever reaches a user. The
# E-3 bug (docs/bugs/e3-never-ran-tag-not-bumped.md) shipped an entire slice that NEVER
# EXECUTED while every check stayed green, because both tag files agreed... on a stale
# tag, and the cluster faithfully served old code. A tag is a CLAIM ABOUT CONTENT. Only
# an assertion against the served bytes can falsify that claim. This suite made that
# concrete: at 0.1.144 the badge testid was composed at runtime (`${key}-badge`), so the
# literal did not exist in the bundle — Vitest passed, the DOM was correct, and the
# content grep still (correctly) read ZERO. The testid is now a literal for exactly that
# reason. A marker that cannot be grepped in the shipped artifact cannot prove it shipped.
#
#   T-S79-000 — PARITY / FORK CONVERGENCE (the core claim; cheap, so it runs first).
#               ONE dispatcher definition, BOTH operate surfaces mount it, ZERO direct
#               Overview* mounts left in pages, ZERO inline endpoint forks, and the
#               dispatcher is an EXPLICIT map (not a priority chain) that fails LOUD on
#               an unknown shape. The last clause is the anti-regression guard: a silent
#               `: Reactive` fallback is how the fork lost event-driven in the first
#               place, so re-adding one must fail this gate.
#   T-S79-001 — SERVED BUNDLE CONTENT. The bytes the real edge serves contain this
#               slice's markers (approvals-badge, overview-for-shape, the build marker)
#               and NO stale build marker. This is the E-3-class assertion.
#   T-S79-002 — TAG ⇄ CONTENT COUPLING, five-way. STUDIO_BUILD (source) == STUDIO_TAG
#               (deploy script) == values.yaml pin == the LIVE pod's image tag == the
#               marker in the SERVED bundle. E-3 proved agreement between the two tag
#               FILES is not enough — they agreed on a stale tag. The live pod and the
#               served bytes are the two that cannot lie.
#   T-S79-003 — THE BADGE'S PRODUCER IS LIVE. The badge count has exactly one source:
#               listPendingApprovals → GET /api/v1/approvals/. Called against the REAL
#               API. Proves the reader is wired to a live producer (DoD #3 — no orphan);
#               WS-6 deliberately added NO new count endpoint (a second path to one fact
#               is the thing this slice deletes).
#
# SCOPE (honest boundary — read this before adding cases). This suite covers the STUDIO
# operate surface only. The WS-6 plan also specifies a registry-api agent-pod-URL
# resolver (`agent_endpoints.py`: the `environment="production"` default that no caller
# threads, silently mis-addressing every sandbox resume). That work is NOT in this suite
# and NOT in the tree: it belongs to a concurrently-running agent that owns
# services/registry-api/**. Asserting `def agent_pod_base` here would fail against code
# nobody in this lane is allowed to write, and stubbing it would be a fake. It is
# recorded as an open gap in ws6/tasks.md rather than silently implied to be done.
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
GATEWAY="${GATEWAY:-https://agentshield.127.0.0.1.nip.io:8443}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# The real interactive platform-admin Keycloak sub. Deny-by-default hides resources whose
# created_by != the caller's sub, so a wrong sub yields an empty list that reads as "no
# pending approvals" rather than as an auth failure — a false PASS.
ADMIN_SUB="${ADMIN_SUB:-75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6}"

# Per-invocation paths (the suite-74 lesson): a fixed /tmp path lets two overlapping
# invocations (a retry, a second operator, a CI re-run) read each OTHER's results.
RUN_TAG="$(date +%s)$$"
BUNDLE="/tmp/s79_bundle_${RUN_TAG}.js"
INDEX="/tmp/s79_index_${RUN_TAG}.html"
DRIVER="/tmp/s79_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s79_out_${RUN_TAG}.txt"
RUNLOG="/tmp/s79_run_${RUN_TAG}.log"

PASS=0
FAIL=0
BASH_OUT=""

# Count matches WITHOUT the `grep -c ... || echo 0` trap: `grep -c` PRINTS "0" and ALSO
# exits 1 when there are no matches, so the `|| echo 0` fires on top of grep's own output
# and the variable becomes "0\n0" — which then fails every numeric comparison and reports
# a bogus failure for correct code. (This suite hit exactly that on its first run.)
# Note the `|| true` INSIDE the braces: with `set -o pipefail`, grep's exit-1-on-no-match
# fails the whole pipeline and `set -e` then kills the suite mid-run — silently, before
# it can record a single result. Zero matches is a legitimate answer here (it is the
# expected answer for every deletion assertion), not an error.
count_matches() {
  # count_matches <pattern> <file>
  { grep -o -- "$1" "$2" 2>/dev/null || true; } | wc -l | tr -d ' '
}

# Read the marker from the EXPORT line specifically. A loose `grep -oE '"x.y.z"'` over
# build.ts matches the FIRST quoted version anywhere in the file — and build.ts's own
# doc comment cites the historical stale value ("0.1.76"), so the loose grep silently
# compared the docstring against the world and failed everything. Anchor on the export.
read_studio_build() {
  grep -E '^export const STUDIO_BUILD' studio/src/lib/build.ts | head -1 | cut -d'"' -f2
}

# Read the studio image tag from the DETAILED studio block. values.yaml has TWO
# top-level `studio:` keys (an enable-flag block and the detailed block; YAML resolves
# duplicates last-wins and the chart's own comments say the detailed one is
# authoritative). A naive "first `studio:` then first `tag:`" awk walks straight past the
# flag block into the NEXT component's tag and reports registry-api's version as
# studio's. Require an `image:` inside the same block.
read_chart_studio_tag() {
  awk '
    /^studio:/            { instudio=1; inimage=0; next }
    /^[a-zA-Z0-9_-]+:/    { if (instudio) { instudio=0; inimage=0 } }
    instudio && /^  image:/ { inimage=1; next }
    instudio && inimage && /^    tag:/ { gsub(/"/,"",$2); print $2; exit }
  ' charts/agentshield/values.yaml
}

rec() {
  # rec <PASS|FAIL> <id+name> <detail>
  local verdict="$1"; local name="$2"; local detail="$3"
  echo "${verdict}  ${name}  |  ${detail}"
  BASH_OUT="${BASH_OUT}
${verdict}  ${name}"
  if [ "$verdict" = "PASS" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
}

cleanup() { rm -f "$BUNDLE" "$INDEX" 2>/dev/null || true; }
trap cleanup EXIT

echo "=================================================================="
echo " Suite 79: WS-6 operate parity — one Overview dispatcher, 0 forks"
echo "=================================================================="
echo ""

# ---------------------------------------------------------------------------
# T-S79-000 — PARITY / FORK CONVERGENCE. The core claim, greped against the real tree.
# ---------------------------------------------------------------------------
echo "--- T-S79-000  parity / fork convergence ---"
P000_ERR=""

DISPATCHER="studio/src/components/agent-detail/OverviewForShape.tsx"
[ -f "$DISPATCHER" ] || P000_ERR="${P000_ERR} dispatcher-missing;"

# (a) Exactly ONE dispatcher definition + ONE shape map.
n_map=$(count_matches "const OVERVIEW_BY_SHAPE" "$DISPATCHER")
[ "$n_map" = "1" ] || P000_ERR="${P000_ERR} OVERVIEW_BY_SHAPE defs=${n_map} (want 1);"

# (b) BOTH operate surfaces mount the dispatcher. One mount point alone would mean the
#     fork was MOVED, not collapsed.
for page in studio/src/pages/CatalogDetailPage.tsx studio/src/pages/DeploymentOverviewPage.tsx; do
  n=$(count_matches "OverviewForShape" "$page")
  [ "$n" -ge 1 ] || P000_ERR="${P000_ERR} ${page##*/} does not mount OverviewForShape;"
done

# (c) ZERO direct Overview* mounts left in ANY page. This is the assertion that actually
#     kills the fork: the shared set must be reachable ONLY through the dispatcher.
STRAY=""
for f in studio/src/pages/*.tsx; do
  case "$f" in *.test.tsx) continue;; esac
  if grep -qE "OverviewReactive|OverviewDurable|OverviewScheduled|OverviewEventDriven" "$f" 2>/dev/null; then
    STRAY="${STRAY} ${f##*/}"
  fi
done
[ -z "$STRAY" ] || P000_ERR="${P000_ERR} direct Overview* mounts still in pages:${STRAY};"

# (d) The CatalogDetailPage inline endpoints fork is GONE. A helper that survives beside
#     its replacement is a fork with extra steps.
n_inline=$(count_matches "const endpoints" studio/src/pages/CatalogDetailPage.tsx)
[ "$n_inline" = "0" ] || P000_ERR="${P000_ERR} CatalogDetailPage still hand-builds endpoints (${n_inline});"

# (e) The dispatcher fails LOUD on an unknown shape — no silent Reactive fallback.
#     A quiet default is HOW the fork lost event-driven; re-adding one must fail here.
grep -q "console.error" "$DISPATCHER" 2>/dev/null || P000_ERR="${P000_ERR} dispatcher has no console.error (silent fallback risk);"
grep -q "overview-unsupported-shape" "$DISPATCHER" 2>/dev/null || P000_ERR="${P000_ERR} dispatcher renders no visible unsupported-shape card;"

# (f) All FOUR shapes are in the map — event_driven is the branch the fork silently lacked.
for shape in reactive durable scheduled event_driven; do
  grep -qE "^\s+${shape}:" "$DISPATCHER" 2>/dev/null || P000_ERR="${P000_ERR} shape '${shape}' missing from OVERVIEW_BY_SHAPE;"
done

if [ -z "$P000_ERR" ]; then
  rec PASS "T-S79-000 one Overview dispatcher, two consumers, zero inline forks" \
    "1 OVERVIEW_BY_SHAPE map; CatalogDetailPage + DeploymentOverviewPage both mount it; 0 direct Overview* mounts in pages; 0 inline endpoint forks; fails loud on unknown shape; all 4 shapes incl. event_driven"
else
  rec FAIL "T-S79-000 one Overview dispatcher, two consumers, zero inline forks" "$P000_ERR"
fi
echo ""

# ---------------------------------------------------------------------------
# T-S79-001 — SERVED BUNDLE CONTENT. The bytes a real browser receives.
# ---------------------------------------------------------------------------
echo "--- T-S79-001  served-bundle content (real edge) ---"
P001_ERR=""

if ! curl -sk --max-time 15 "${GATEWAY}/?cb=${RUN_TAG}" -o "$INDEX" 2>/dev/null; then
  # FAIL LOUD, never skip: an unreachable edge makes this gate unprovable, and an
  # unprovable gate is not a pass. Skipping here would silently delete the ONE assertion
  # that catches the E-3 class.
  P001_ERR="edge unreachable at ${GATEWAY} — cannot prove what is served"
else
  ASSET=$(grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' "$INDEX" | head -1)
  if [ -z "$ASSET" ]; then
    P001_ERR="no /assets/index-*.js found in served index.html"
  elif ! curl -sk --max-time 30 "${GATEWAY}${ASSET}" -o "$BUNDLE" 2>/dev/null; then
    P001_ERR="could not fetch served bundle ${ASSET}"
  else
    STUDIO_BUILD_SRC=$(read_studio_build)
    for marker in "approvals-badge" "overview-for-shape" "overview-unsupported-shape" "studio-build" "$STUDIO_BUILD_SRC"; do
      n=$(count_matches "$marker" "$BUNDLE")
      [ "$n" -gt 0 ] || P001_ERR="${P001_ERR} '${marker}' ZERO occurrences in served bundle;"
    done
  fi
fi

if [ -z "$P001_ERR" ]; then
  rec PASS "T-S79-001 served bundle carries this slice's code" \
    "asset=${ASSET}: approvals-badge + overview-for-shape + overview-unsupported-shape + studio-build + build marker all present in the bytes the edge serves"
else
  rec FAIL "T-S79-001 served bundle carries this slice's code" "$P001_ERR"
fi
echo ""

# ---------------------------------------------------------------------------
# T-S79-002 — TAG ⇄ CONTENT COUPLING (five-way). E-3: the two tag FILES agreed on a
# stale tag. The live pod and the served bytes are the two that cannot lie.
# ---------------------------------------------------------------------------
echo "--- T-S79-002  tag/content coupling (source == script == chart == pod == served) ---"
P002_ERR=""

SRC_BUILD=$(read_studio_build)
SCRIPT_TAG=$(grep -E '^STUDIO_TAG=' scripts/deploy-cpe2e.sh | head -1 | cut -d'"' -f2)
CHART_TAG=$(read_chart_studio_tag)
POD_IMAGE=$(kubectl get deploy agentshield-studio -n "$NAMESPACE" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "")
POD_TAG="${POD_IMAGE##*:}"

echo "    source(build.ts)=${SRC_BUILD}  script(STUDIO_TAG)=${SCRIPT_TAG}  chart=${CHART_TAG}  pod=${POD_TAG}"

[ -n "$SRC_BUILD" ] || P002_ERR="${P002_ERR} could not read STUDIO_BUILD from build.ts;"
[ "$SRC_BUILD" = "$SCRIPT_TAG" ] || P002_ERR="${P002_ERR} build.ts(${SRC_BUILD}) != STUDIO_TAG(${SCRIPT_TAG});"
[ "$SRC_BUILD" = "$CHART_TAG" ]  || P002_ERR="${P002_ERR} build.ts(${SRC_BUILD}) != chart pin(${CHART_TAG});"
[ "$SRC_BUILD" = "$POD_TAG" ]    || P002_ERR="${P002_ERR} build.ts(${SRC_BUILD}) != LIVE pod image(${POD_TAG}) — the cluster is not running this code;"

# The served bytes must carry THIS marker and no older one. A stale marker in the bundle
# means the edge/CDN/nginx is serving a cached older bundle even though the pod is new.
if [ -s "$BUNDLE" ]; then
  n_cur=$(count_matches "$SRC_BUILD" "$BUNDLE")
  [ "$n_cur" -gt 0 ] || P002_ERR="${P002_ERR} served bundle does NOT contain marker ${SRC_BUILD};"
else
  P002_ERR="${P002_ERR} no served bundle to check (see T-S79-001);"
fi

if [ -z "$P002_ERR" ]; then
  rec PASS "T-S79-002 tag is a true claim about content (5-way agreement)" \
    "build.ts == STUDIO_TAG == chart pin == live pod image == served-bundle marker == ${SRC_BUILD}"
else
  rec FAIL "T-S79-002 tag is a true claim about content (5-way agreement)" "$P002_ERR"
fi
echo ""

# ---------------------------------------------------------------------------
# T-S79-003 — THE BADGE'S PRODUCER IS LIVE (real API, real response).
# ---------------------------------------------------------------------------
echo "--- T-S79-003  badge producer live (real GET /approvals/) ---"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$API_POD" ]; then
  rec FAIL "T-S79-003 badge count producer is live" \
    "no registry-api pod found in ${NAMESPACE} — cannot prove the producer (NOT skipped: an unprovable gate is not a pass)"
else
  cat > "$DRIVER" <<'PYEOF'
import asyncio, json, os, sys, traceback
import httpx

BASE = "http://localhost:8000/api/v1"
ADMIN = os.environ["ADMIN_SUB"]
H = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}
OUT = os.environ["OUT"]

results = []
def rec(name, ok, detail):
    results.append((name, ok, detail))

async def main():
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=H, timeout=60) as c:
            # The badge's ONLY count source. WS-6 deliberately added no
            # getPendingApprovalsCount: a second API path to one fact is the drift
            # engine this slice exists to delete. So the badge's correctness rests
            # entirely on THIS endpoint being live and honouring THIS contract.
            #
            # Assert the contract the CLIENT actually depends on, not a guessed one.
            # registryApi.listPendingApprovals sends `status=pending` and reads
            # `data.items` (registryApi.ts:1506-1512) — the endpoint returns an
            # {items, total} ENVELOPE, not a bare list. (This suite's first draft
            # asserted a bare list, per a stale plan line claiming the endpoint
            # "returns ApprovalInboxItem[]"; that is true of the client function after
            # it unwraps, and false of the wire. A test must mirror the real caller.)
            r = await c.get("/approvals/", params={"status": "pending"})
            if r.status_code != 200:
                rec("T-S79-003 badge count producer is live", False,
                    f"GET /approvals/?status=pending -> {r.status_code} (want 200): {r.text[:200]}")
                return
            body = r.json()
            if not isinstance(body, dict) or "items" not in body:
                rec("T-S79-003 badge count producer is live", False,
                    f"response is {type(body).__name__} without an 'items' key — the badge "
                    f"reads data.items and would count undefined → silently no badge")
                return
            items = body["items"]
            if not isinstance(items, list):
                rec("T-S79-003 badge count producer is live", False,
                    f"'items' is {type(items).__name__}, not a list — the badge counts .length")
                return
            bad = [i for i in items if not isinstance(i, dict) or "id" not in i]
            if bad:
                rec("T-S79-003 badge count producer is live", False,
                    f"{len(bad)} item(s) lack an 'id' — not ApprovalInboxItem shaped")
                return
            # The filter must be honoured server-side: the badge is a PENDING count, and
            # counting decided approvals would make it permanently, quietly wrong.
            # (Unfiltered, this endpoint really does return approved/rejected rows too.)
            wrong = [i.get("status") for i in items if i.get("status") != "pending"]
            if wrong:
                rec("T-S79-003 badge count producer is live", False,
                    f"status=pending returned non-pending rows {wrong[:5]} — the badge "
                    f"would over-count and never reach 0")
                return
            # Say what was actually proven. With zero pending rows the status filter and
            # the item-shape checks are VACUOUSLY true — they assert over an empty list.
            # The producer's liveness + envelope shape are still real proof (that is this
            # case's job, and it is what the badge's no-orphan claim rests on), but a
            # green here must not be read as "the filter was exercised". Never claim a
            # green you did not observe.
            vacuous = "" if items else (
                " — NOTE: 0 pending rows in this cluster, so the status-filter and "
                "item-shape assertions were VACUOUS; the live+envelope proof stands, "
                "the filter proof does not. approvals-badge.spec.ts creates a REAL "
                "parked approval and is where a non-empty count is actually exercised."
            )
            rec("T-S79-003 badge count producer is live", True,
                f"GET /approvals/?status=pending -> 200, items[{len(items)}] all "
                f"pending + id-shaped (the badge's only count source; no second "
                f"count endpoint exists){vacuous}")
    except Exception as exc:
        # FAIL LOUD. Without this the bare try/finally writes only the cases recorded
        # BEFORE the crash, and PASS>0/FAIL==0 reads GREEN on a half-run.
        rec("T-S79-999 driver ran every case without crashing", False,
            f"driver CRASHED — cases after this point never ran: "
            f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        # Write results BEFORE any cleanup (the suite-69 lesson): a cleanup that throws
        # must not take the evidence with it.
        lines = [f"{'PASS' if ok else 'FAIL'}  {n}  |  {d}" for n, ok, d in results]
        lines.append("SUMMARY done")
        with open(OUT, "w") as f:
            f.write("\n".join(lines) + "\n")

asyncio.run(main())
PYEOF

  kubectl cp "$DRIVER" "${NAMESPACE}/${API_POD}:${DRIVER}" -c registry-api >/dev/null 2>&1 || true
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    env ADMIN_SUB="$ADMIN_SUB" OUT="$OUTFILE" python3 "$DRIVER" > "$RUNLOG" 2>&1 || true
  RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || echo "")

  if [ -z "$RES" ]; then
    rec FAIL "T-S79-003 badge count producer is live" \
      "driver produced NO result file — it died before recording anything"
    echo "  --- driver log tail ---"
    kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -20 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
  else
    while IFS= read -r line; do
      case "$line" in
        PASS*) echo "$line"; PASS=$((PASS+1)); BASH_OUT="${BASH_OUT}
${line}";;
        FAIL*) echo "$line"; FAIL=$((FAIL+1)); BASH_OUT="${BASH_OUT}
${line}";;
      esac
    done <<< "$RES"
  fi
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true
  rm -f "$DRIVER" 2>/dev/null || true
fi
echo ""

# ---------------------------------------------------------------------------
# Completeness gate. FAIL=0 is only a pass if every gate assertion actually RAN. An
# exception, an early return, or a truncated result file otherwise yields "0 failures"
# on a half-run gate. REQUIRED_IDS is the ONE source of truth: a hardcoded COUNT was
# tried in suite-74 and drifted the moment a case was split, and a count cannot say
# WHICH case vanished. Add a case here and nowhere else.
# ---------------------------------------------------------------------------
REQUIRED_IDS="000 001 002 003"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$BASH_OUT" | grep -q "T-S79-$id" || MISSING="$MISSING T-S79-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S79-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
else
  echo "PASS  T-S79-COMPLETE every gate assertion ran (000-003, none skipped)"
  PASS=$((PASS+1))
fi

echo ""
echo "=================================================================="
echo " Suite 79 summary: PASS=$PASS FAIL=$FAIL"
echo "=================================================================="

# Exit non-zero on any failure OR on a zero-pass run (inconclusive is not green).
if [ "$FAIL" -ne 0 ] || [ "$PASS" -eq 0 ]; then
  echo "❌ suite-79 FAILED (FAIL=$FAIL PASS=$PASS)"
  exit 1
fi
echo "✅ suite-79 PASSED"
exit 0

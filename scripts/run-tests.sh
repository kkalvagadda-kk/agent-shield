#!/usr/bin/env bash
# run-tests.sh — one entry point for AgentShield's e2e tests, selectable by
# LAYER (api vs browser) and by FUNCTIONAL GROUP.
#
# Why: `run-all.sh` ran all ~89 bash suites or nothing, and the Playwright specs
# were a separate all-or-nothing gate. After a change to (say) tools, there was no
# way to answer "which regression tests cover this?" except reading 130 filenames.
# scripts/test-manifest.txt now answers that, and this script executes it.
#
# The two layers stay SEPARATE runs by design:
#   api      bash suites that kubectl-exec into a pod. Test the HTTP API only —
#            they cannot catch a broken screen.
#   browser  Playwright specs against the real deployed Studio. The only layer
#            that proves a UI journey. Needs the current image deployed.
# `--layer all` runs api first, then browser, and reports them separately.
#
# Usage:
#   bash scripts/run-tests.sh --groups                    # list functional groups
#   bash scripts/run-tests.sh --list                      # list every test
#   bash scripts/run-tests.sh --list --group tools        # what WOULD run
#   bash scripts/run-tests.sh --group tools               # both layers, tools only
#   bash scripts/run-tests.sh --group hitl,workflow       # union of two groups
#   bash scripts/run-tests.sh --layer api                 # all API suites
#   bash scripts/run-tests.sh --layer browser --group eval
#   bash scripts/run-tests.sh --audit                     # find unregistered files
#   bash scripts/run-tests.sh --layer api --auto-pf       # extra args to suites
#
# `debug` group members (scratch probes) are excluded unless asked for by name.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${REPO_ROOT}/scripts/test-manifest.txt"
NAMESPACE="${NAMESPACE:-agentshield-platform}"

LAYER="all"
FILTER_GROUPS=""
MODE="run"
EXTRA_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --layer)   LAYER="${2:-}"; shift 2 ;;
    --layer=*) LAYER="${1#*=}"; shift ;;
    --group|--groups-filter) FILTER_GROUPS="${2:-}"; shift 2 ;;
    --group=*) FILTER_GROUPS="${1#*=}"; shift ;;
    --groups)  MODE="groups"; shift ;;
    --list)    MODE="list"; shift ;;
    --audit)   MODE="audit"; shift ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)         EXTRA_ARGS+=("$1"); shift ;;
  esac
done

if [ ! -f "$MANIFEST" ]; then
  echo "FATAL: manifest not found at $MANIFEST" >&2; exit 1
fi
case "$LAYER" in api|browser|all) ;; *) echo "FATAL: --layer must be api|browser|all (got '$LAYER')" >&2; exit 1 ;; esac

# ── manifest reading ────────────────────────────────────────────────────────
# Emits "layer|groups|file|title" for rows matching LAYER and GROUPS.
select_rows() {
  local want_layer="$1"
  awk -F'|' -v layer="$want_layer" -v groups="$FILTER_GROUPS" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    NF < 4 { next }
    $1 != layer { next }
    {
      if (groups == "") {
        # No filter: skip scratch/debug rows so a plain run stays meaningful.
        if ($2 ~ /(^|,)debug(,|$)/) next
        print; next
      }
      n = split(groups, want, ",")
      for (i = 1; i <= n; i++) {
        g = want[i]
        gsub(/^[ \t]+|[ \t]+$/, "", g)
        if (g != "" && $2 ~ "(^|,)" g "(,|$)") { print; next }
      }
    }
  ' "$MANIFEST"
}

# One "<group>|<layer>" line PER TEST — deliberately not deduped, because the
# counts in --groups are counts of tests, not of distinct pairs.
all_group_pairs() {
  awk -F'|' '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    NF < 4 { next }
    { n = split($2, g, ","); for (i = 1; i <= n; i++) { gsub(/^[ \t]+|[ \t]+$/, "", g[i]); print g[i] "|" $1 } }
  ' "$MANIFEST"
}

# ── --groups ────────────────────────────────────────────────────────────────
if [ "$MODE" = "groups" ]; then
  echo "Functional groups (test counts by layer):"
  echo ""
  printf "  %-12s %6s %8s\n" "GROUP" "API" "BROWSER"
  printf "  %-12s %6s %8s\n" "------------" "------" "--------"
  PAIRS="$(all_group_pairs)"
  for g in $(echo "$PAIRS" | cut -d'|' -f1 | sort -u); do
    a=$(echo "$PAIRS" | grep -cxF "${g}|api" || true)
    b=$(echo "$PAIRS" | grep -cxF "${g}|browser" || true)
    printf "  %-12s %6s %8s\n" "$g" "$a" "$b"
  done
  echo ""
  echo "Run one:   bash scripts/run-tests.sh --group <group>"
  echo "Preview:   bash scripts/run-tests.sh --list --group <group>"
  exit 0
fi

# ── --audit: every file on disk must have a manifest line ───────────────────
if [ "$MODE" = "audit" ]; then
  missing=0
  for f in "$REPO_ROOT"/scripts/e2e/suite-*.sh; do
    b="$(basename "$f")"
    grep -q "^api|[^|]*|${b}|" "$MANIFEST" || { echo "UNREGISTERED (api):     $b"; missing=$((missing+1)); }
  done
  for f in "$REPO_ROOT"/studio/e2e/*.spec.ts; do
    b="e2e/$(basename "$f")"
    grep -q "^browser|[^|]*|${b}|" "$MANIFEST" || { echo "UNREGISTERED (browser): $b"; missing=$((missing+1)); }
  done
  while IFS='|' read -r layer _groups file _title; do
    case "$layer" in
      api)     [ -f "$REPO_ROOT/scripts/e2e/$file" ] || { echo "MISSING FILE (api):     $file"; missing=$((missing+1)); } ;;
      browser) [ -f "$REPO_ROOT/studio/$file" ]      || { echo "MISSING FILE (browser): $file"; missing=$((missing+1)); } ;;
    esac
  done < <(grep -v '^[[:space:]]*#' "$MANIFEST" | grep -v '^[[:space:]]*$')
  if [ "$missing" -eq 0 ]; then
    echo "Manifest audit clean — every suite/spec on disk is registered, and every registered file exists."
    exit 0
  fi
  echo ""
  echo "$missing problem(s). Add the missing lines to scripts/test-manifest.txt."
  exit 1
fi

# ── --list ──────────────────────────────────────────────────────────────────
if [ "$MODE" = "list" ]; then
  [ -n "$FILTER_GROUPS" ] && echo "Filter: groups=[$FILTER_GROUPS] layer=[$LAYER]" || echo "Filter: layer=[$LAYER] (all groups, excluding debug)"
  echo ""
  for l in api browser; do
    [ "$LAYER" = "all" ] || [ "$LAYER" = "$l" ] || continue
    rows="$(select_rows "$l")"
    n=$([ -z "$rows" ] && echo 0 || echo "$rows" | wc -l | tr -d ' ')
    echo "── ${l} layer (${n}) ──"
    [ -z "$rows" ] && { echo "  (none)"; echo ""; continue; }
    while IFS='|' read -r _layer groups file title; do
      printf "  %-52s %-28s %s\n" "$file" "[$groups]" "$title"
    done <<< "$rows"
    echo ""
  done
  exit 0
fi

# ── run ─────────────────────────────────────────────────────────────────────
API_PASS=0; API_FAIL=0; API_FAILED=()
BROWSER_STATUS="skipped"

run_api_layer() {
  local rows; rows="$(select_rows api)"
  if [ -z "$rows" ]; then echo "No API suites match the filter."; return 0; fi
  local n; n=$(echo "$rows" | wc -l | tr -d ' ')
  echo ""
  echo "═══════════════════════════════════════════════════════"
  echo "  API layer — ${n} suite(s)   namespace=${NAMESPACE}"
  echo "═══════════════════════════════════════════════════════"
  while IFS='|' read -r _layer _groups file title; do
    local path="${REPO_ROOT}/scripts/e2e/${file}"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ${title}  (${file})"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if [ ! -f "$path" ]; then
      echo "  SKIP: $file not found"
      continue
    fi
    if NAMESPACE="$NAMESPACE" bash "$path" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}; then
      API_PASS=$((API_PASS + 1))
    else
      API_FAIL=$((API_FAIL + 1)); API_FAILED+=("$title ($file)")
    fi
  done <<< "$rows"
}

run_browser_layer() {
  local rows; rows="$(select_rows browser)"
  if [ -z "$rows" ]; then echo "No browser specs match the filter."; return 0; fi
  local specs=()
  while IFS='|' read -r _layer _groups file _title; do specs+=("$file"); done <<< "$rows"
  echo ""
  echo "═══════════════════════════════════════════════════════"
  echo "  Browser layer — ${#specs[@]} spec(s)"
  echo "═══════════════════════════════════════════════════════"
  # studio-e2e.sh owns port-forward/gateway selection and passes args to Playwright.
  if bash "${REPO_ROOT}/scripts/studio-e2e.sh" "${specs[@]}"; then
    BROWSER_STATUS="pass"
  else
    BROWSER_STATUS="FAIL"
  fi
}

[ "$LAYER" = "api" ] || [ "$LAYER" = "all" ] && run_api_layer
[ "$LAYER" = "browser" ] || [ "$LAYER" = "all" ] && run_browser_layer

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Summary   layer=${LAYER}   groups=${FILTER_GROUPS:-<all>}"
echo "═══════════════════════════════════════════════════════"
if [ "$LAYER" = "api" ] || [ "$LAYER" = "all" ]; then
  echo "  API layer:     ${API_PASS} passed, ${API_FAIL} failed"
  for s in ${API_FAILED[@]+"${API_FAILED[@]}"}; do echo "    FAILED: $s"; done
fi
if [ "$LAYER" = "browser" ] || [ "$LAYER" = "all" ]; then
  echo "  Browser layer: ${BROWSER_STATUS}"
fi
echo ""

[ "$API_FAIL" -eq 0 ] && [ "$BROWSER_STATUS" != "FAIL" ] && exit 0
exit 1

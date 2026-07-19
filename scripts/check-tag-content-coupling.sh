#!/usr/bin/env bash
#
# check-tag-content-coupling.sh — a tag is a CLAIM ABOUT CONTENT. Prove the claim.
#
# WHY THIS EXISTS (earned, not theoretical): docs/bugs/e3-never-ran-tag-not-bumped.md.
# E-3's code never executed for an ENTIRE SLICE. The source changed; the tag did not.
# Both tag files then agreed *on a stale tag*, and the cluster faithfully matched it —
# so EVERY check stayed green while the feature was simply absent. The eval Jobs ran
# E-2-era code, fell through to the reactive path, and the "recorded ⇒ not delivered"
# guarantee was gone. Nothing was red. That is the failure this gate makes impossible.
#
# TWO UPGRADES over the E-3 original (scripts/smoke-test-cp1-e3-constitution.sh), both
# load-bearing:
#
#   1. COVERAGE — every taggable service (9), not four of nine. The E-3 gate checked
#      registry-api / eval-runner / studio / sdk only; deploy-cpe2e.sh pins NINE tags.
#
#   2. UN-EVADABLE — the E-3 gate audits a SINGLE commit (AUDIT_REF, default HEAD), so
#      splitting the source change and the tag bump across two commits evades it
#      entirely. This gate asks a question no commit boundary can dodge:
#
#          "Is the tag's last bump OLDER than the service dir's last source change?"
#
#      That is a pure git-history computation. Commit the source in one commit and the
#      bump in a later one and the gate still PASSES (correct: the bump came after the
#      content). Bump the tag and THEN change the source and it FAILS (correct: the tag
#      now lies). Order is what matters, not co-location in one commit.
#
# WORKING-TREE SEMANTICS (why this is stricter than E-3's, on purpose): deploy-cpe2e.sh
# builds from the WORKING TREE, not from HEAD. So uncommitted source under a service dir
# IS what gets baked into the image. The E-3 gate downgraded uncommitted work to a
# warning ("not shippable yet"); as a PRE-BUILD hook that would be exactly backwards —
# uncommitted source with no tag bump is precisely the image-content-diverges-from-tag
# bug, live, at the moment of deploy. So: an uncommitted change counts as "now".
#
# THE COMPARISON, per service:
#     src_ts = last change to the service's source  (uncommitted ⇒ NOW)
#     tag_ts = last bump of its TAG VAR in deploy-cpe2e.sh  (uncommitted ⇒ NOW)
#     FAIL iff src_ts > tag_ts
# Both uncommitted ⇒ equal ⇒ PASS (source and bump landed together — the good case).
# Same commit ⇒ identical timestamps ⇒ PASS.
#
# AUTHORITATIVE CHART PIN, per service (NOT "all pins agree" — that would false-fail on
# a CORRECT tree and be disabled within a day):
#   - event-gateway is pinned TOP-LEVEL (values.yaml) at 0.1.3 while its sub-chart says
#     0.1.2, and they disagree ON PURPOSE: `helm dependency update` fails and
#     deploy-cpe2e.sh swallows the error, so a committed stale .tgz shadows the sub-chart
#     and a sub-chart edit silently no-ops. The top-level pin wins regardless. See
#     charts/agentshield/values.yaml:115-136.
#   - python-executor and scheduler have NO top-level pin at all; their sub-chart values
#     ARE authoritative.
#   - values.yaml has DUPLICATE top-level keys (safety-orchestrator appears twice). YAML
#     last-wins, so the pin is resolved by PARSING the YAML, never by grepping a line —
#     a grep would read the shadowed block and "verify" a value helm never uses.
#
# Usage:
#   bash scripts/check-tag-content-coupling.sh            # working tree (the deploy hook)
#   bash scripts/check-tag-content-coupling.sh --at <ref> # audit history as of <ref>
#
# The --at form is how this gate is PROVEN: `--at 9f6603a` (the real E-3 offender) must
# flag eval-runner and studio. A gate nobody has watched FAIL is not a gate.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

DEPLOY_SH="scripts/deploy-cpe2e.sh"
VALUES="charts/agentshield/values.yaml"

AT_REF=""
if [ "${1:-}" = "--at" ]; then
  AT_REF="${2:-HEAD}"
fi

PASS=0
FAIL=0

ok()  { echo "PASS  $1"; [ -n "${2:-}" ] && echo "        $2"; PASS=$((PASS+1)); return 0; }
bad() { echo "FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; FAIL=$((FAIL+1)); return 0; }

echo "=== tag ⇄ content coupling ==="
if [ -n "$AT_REF" ]; then
  echo "  mode: HISTORY AUDIT as of $AT_REF ($(git log -1 --format=%h%x20%s "$AT_REF" 2>/dev/null | cut -c1-60))"
else
  echo "  mode: WORKING TREE (what deploy-cpe2e.sh is about to build)"
fi
echo ""

# ---------------------------------------------------------------------------
# The service table. Each row:
#   <label>|<tag var>|<source dirs, space-separated>|<chart pin spec>
#
# Chart pin spec is one of:
#   yaml:<dotted.path.in.values.yaml>            (a bare tag string)
#   yamlimg:<dotted.path>                        (a full image ref — tag is after the ':')
#   subchart:<chart dir name>                    (no top-level pin; sub-chart is authoritative)
# Multiple pins are separated by ','  — ALL of them must agree with the tag var.
#
# NOTE the two non-obvious ones:
#   - declarative-runner's image is built from the SDK (docker build -f
#     services/declarative-runner/Dockerfile . — the repo root is the context), so BOTH
#     sdk/agentshield_sdk/ and services/declarative-runner/ are its source. A stale
#     runner made every E-1 trajectory score 0.
#   - eval-runner has THREE pins, and the one that actually LAUNCHES the Job is
#     registry-api.env.EVAL_RUNNER_IMAGE. Miss it and the Job runs the OLD runner while
#     every other check stays green — the exact E-3 failure.
# ---------------------------------------------------------------------------
SERVICES=(
  "registry-api|REGISTRY_API_TAG|services/registry-api|yaml:registry-api.image.tag"
  "studio|STUDIO_TAG|studio|yaml:studio.image.tag"
  "deploy-controller|DEPLOY_CONTROLLER_TAG|services/deploy-controller|yaml:deploy-controller.image.tag"
  "declarative-runner|DECLARATIVE_RUNNER_TAG|sdk/agentshield_sdk services/declarative-runner|yaml:deploy-controller.declarativeRunnerTag"
  "eval-runner|EVAL_RUNNER_TAG|services/eval-runner|yamlimg:registry-api.evalRunnerImage,yamlimg:registry-api.env.EVAL_RUNNER_IMAGE"
  "event-gateway|EVENT_GATEWAY_TAG|services/event-gateway|yaml:event-gateway.image.tag"
  "safety-orchestrator|SAFETY_ORCHESTRATOR_TAG|services/safety-orchestrator|yaml:safety-orchestrator.image.tag"
  "python-executor|PYTHON_EXECUTOR_TAG|services/python-executor|subchart:python-executor"
  "scheduler|SCHEDULER_TAG|services/scheduler|subchart:scheduler"
)

# Dirs under services/ that intentionally have NO image tag. EXPLICIT, never a silent
# lookup miss: a service that quietly falls out of the table is how coverage rots.
NO_TAG_DIRS=("echo-agent" "minio-cp1" "nemo-guardrails")

# Paths inside a service dir that do not change image behaviour. Kept deliberately tiny:
# the Dockerfile is `COPY . .`, so almost everything in the dir IS image content.
# tests/ IS in the image (and CP1c runs pytest off the real image), so tests/ counts.
# Only docs are excluded.
EXCLUDE_GLOBS=(":(exclude)*.md")

NOW=$(date +%s)

# ---------------------------------------------------------------------------
# 1. COVERAGE: every services/* dir is either in the table or explicitly tag-free,
#    and every tag var in deploy-cpe2e.sh is in the table. Drift in BOTH directions.
# ---------------------------------------------------------------------------
for d in services/*/; do
  name=$(basename "$d")
  in_table=0
  for row in "${SERVICES[@]}"; do
    case "$row" in *"|services/$name|"*|*"|services/$name "*|*" services/$name|"*) in_table=1 ;; esac
  done
  [ "$in_table" -eq 1 ] && continue
  skip=0
  for s in "${NO_TAG_DIRS[@]}"; do [ "$s" = "$name" ] && skip=1; done
  if [ "$skip" -eq 1 ]; then
    ok "coverage: services/$name is explicitly tag-free (no image built)" ""
  else
    bad "coverage: services/$name is in NEITHER the service table NOR the tag-free list" \
        "a new service silently outside this gate is how coverage rots — add it to SERVICES[] or NO_TAG_DIRS[] in $(basename "$0")"
  fi
done

for tv in $(grep -oE '^[A-Z_]+_TAG=' "$DEPLOY_SH" | tr -d '='); do
  found=0
  for row in "${SERVICES[@]}"; do
    IFS='|' read -r _l tag _d _p <<< "$row"
    [ "$tag" = "$tv" ] && found=1
  done
  if [ "$found" -eq 1 ]; then
    ok "coverage: $tv is covered by the coupling gate" ""
  else
    bad "coverage: $tv is pinned in $DEPLOY_SH but NOT covered by this gate" \
        "add it to SERVICES[] — an uncovered pin is an unguarded claim"
  fi
done

echo ""

# ---------------------------------------------------------------------------
# 2. The chart pin resolver. Parses the YAML (last-wins on duplicate keys, exactly as
#    helm resolves it) instead of grepping a line — see the header note.
# ---------------------------------------------------------------------------
resolve_pin() {
  # $1 = pin spec (yaml:path | yamlimg:path | subchart:name)
  local spec="$1" kind="${1%%:*}" arg="${1#*:}"
  case "$kind" in
    yaml|yamlimg)
      python3 - "$VALUES" "$arg" "$kind" <<'PY' 2>/dev/null
import sys, yaml
vals, path, kind = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = yaml.safe_load(open(vals))
except Exception as e:
    print("<UNPARSEABLE>"); sys.exit(0)
cur = d
for k in path.split("."):
    if not isinstance(cur, dict) or k not in cur:
        print("<MISSING>"); sys.exit(0)
    cur = cur[k]
if kind == "yamlimg":
    s = str(cur)
    print(s.rsplit(":", 1)[1] if ":" in s else "<NO-TAG-IN-IMAGE-REF>")
else:
    print(str(cur))
PY
      ;;
    subchart)
      python3 - "charts/agentshield/charts/$arg/values.yaml" <<'PY' 2>/dev/null
import sys, yaml
try:
    d = yaml.safe_load(open(sys.argv[1]))
except Exception:
    print("<MISSING>"); sys.exit(0)
print(str(((d or {}).get("image") or {}).get("tag", "<MISSING>")))
PY
      ;;
    *) echo "<BAD-SPEC>" ;;
  esac
}

# ---------------------------------------------------------------------------
# 3. Per-service: (a) the pins agree with the tag var, (b) the tag is not older than
#    the content it claims to describe.
# ---------------------------------------------------------------------------
for row in "${SERVICES[@]}"; do
  IFS='|' read -r label tagvar dirs pins <<< "$row"

  if [ -n "$AT_REF" ]; then
    want=$(git show "$AT_REF:$DEPLOY_SH" 2>/dev/null | grep -m1 "^${tagvar}=" | cut -d'"' -f2)
  else
    want=$(grep -m1 "^${tagvar}=" "$DEPLOY_SH" | cut -d'"' -f2)
  fi
  if [ -z "$want" ]; then
    bad "$label: $tagvar not found in $DEPLOY_SH" "the gate cannot verify a pin it cannot read"
    continue
  fi

  # (a) pin agreement — skipped under --at (historical chart shape is not the point)
  if [ -z "$AT_REF" ]; then
    IFS=',' read -ra PIN_LIST <<< "$pins"
    for p in "${PIN_LIST[@]}"; do
      got=$(resolve_pin "$p")
      if [ "$got" = "$want" ]; then
        ok "$label: pin '$p' agrees with $tagvar" "$want"
      else
        bad "$label: pin '$p' says '$got' but $tagvar says '$want'" \
            "the deploy bakes tags from $VALUES (no --set), so bumping only $DEPLOY_SH leaves the chart on the OLD image while every other check stays green"
      fi
    done
  fi

  # (b) THE CLASS FIX: is the tag's last bump older than the content's last change?
  #
  # src_ts: newest commit touching any of the service's source dirs (docs excluded).
  #         Uncommitted changes there count as NOW — deploy-cpe2e.sh builds the working
  #         tree, so uncommitted source IS the image content.
  # tag_ts: newest commit whose diff to deploy-cpe2e.sh added/removed a line matching
  #         `^<TAGVAR>=`. This MUST be `-G` (regex over diff lines), NOT `-S`: the
  #         pickaxe `-S` counts OCCURRENCES of a string, and a bump changes the tag's
  #         VALUE while leaving the count at 1 — so `-S` never registers a bump at all
  #         and every service reads as "tag older than source". This gate was written
  #         with `-S` first and it false-failed registry-api on the very commit that
  #         DID bump it (9f6603a). An uncommitted bump counts as NOW.
  src_ts=0
  for d in $dirs; do
    if [ -n "$AT_REF" ]; then
      t=$(git log -1 --format=%ct "$AT_REF" -- "$d" "${EXCLUDE_GLOBS[@]}" 2>/dev/null)
    else
      t=$(git log -1 --format=%ct -- "$d" "${EXCLUDE_GLOBS[@]}" 2>/dev/null)
      if ! git diff --quiet -- "$d" "${EXCLUDE_GLOBS[@]}" 2>/dev/null \
         || ! git diff --cached --quiet -- "$d" "${EXCLUDE_GLOBS[@]}" 2>/dev/null; then
        t=$NOW
      fi
    fi
    [ -n "$t" ] && [ "$t" -gt "$src_ts" ] 2>/dev/null && src_ts=$t
  done

  if [ -n "$AT_REF" ]; then
    tag_ts=$(git log -1 --format=%ct -G"^${tagvar}=" "$AT_REF" -- "$DEPLOY_SH" 2>/dev/null)
  else
    tag_ts=$(git log -1 --format=%ct -G"^${tagvar}=" -- "$DEPLOY_SH" 2>/dev/null)
    if git diff -- "$DEPLOY_SH" 2>/dev/null | grep -qE "^[+-]${tagvar}=" \
       || git diff --cached -- "$DEPLOY_SH" 2>/dev/null | grep -qE "^[+-]${tagvar}="; then
      tag_ts=$NOW
    fi
  fi
  [ -z "$tag_ts" ] && tag_ts=0

  if [ "$src_ts" -eq 0 ]; then
    ok "$label: no source history under $dirs (nothing to couple)" ""
  elif [ "$src_ts" -gt "$tag_ts" ]; then
    src_when=$(date -r "$src_ts" '+%Y-%m-%d %H:%M' 2>/dev/null)
    tag_when=$([ "$tag_ts" -eq 0 ] && echo "NEVER" || date -r "$tag_ts" '+%Y-%m-%d %H:%M' 2>/dev/null)
    bad "$label: source changed AFTER the last $tagvar bump — the tag LIES about the image" \
        "source last changed: $src_when | $tagvar last bumped: $tag_when (currently '$want').
        REMEDY: bump $tagvar in BOTH files:
          1. $DEPLOY_SH  (line $(grep -n "^${tagvar}=" "$DEPLOY_SH" | cut -d: -f1))
          2. $VALUES     (pins: $pins)
        Without the bump the image is never rebuilt: the cluster keeps running the OLD
        code while every check stays GREEN (both files agree on the stale tag, and the
        cluster faithfully matches it). See docs/bugs/e3-never-ran-tag-not-bumped.md."
  else
    src_when=$([ "$src_ts" -eq "$NOW" ] && echo "uncommitted" || date -r "$src_ts" '+%Y-%m-%d' 2>/dev/null)
    tag_when=$([ "$tag_ts" -eq "$NOW" ] && echo "uncommitted" || date -r "$tag_ts" '+%Y-%m-%d' 2>/dev/null)
    ok "$label: $tagvar ($want) bumped no earlier than its last source change" \
       "source: $src_when ≤ tag bump: $tag_when"
  fi
done

echo ""
echo "=== tag⇄content coupling: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ tag⇄content coupling FAILED — a tag is a claim about content, and $FAIL claim(s) are false."
  exit 1
fi
if [ "$PASS" -eq 0 ]; then
  echo "❌ tag⇄content coupling INCONCLUSIVE (no checks ran)"
  exit 1
fi
echo "✅ tag⇄content coupling PASSED ($PASS checks)"

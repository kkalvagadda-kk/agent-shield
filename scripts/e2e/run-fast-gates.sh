#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run-fast-gates.sh — the CLUSTER-FREE tier of the regression gate (Eval v2 E-6).
#
# WHY THIS TIER EXISTS
# --------------------
# This repo has no CI, and it structurally cannot have the useful kind: every real
# suite needs a live cluster, deployed pods, and real LLM keys — none of which a
# hosted runner has. So E-6 does NOT invent a CI habit nobody will run. It splits
# the gates by what they actually need:
#
#   FAST  (this script)   — pure source + git-history reasoning. No cluster.
#   SLOW  (run-all.sh)    — the real-pod suites. A human, on a real cluster.
#
# and it attaches the FAST tier to the ONE command that is already unavoidable:
# `scripts/deploy-cpe2e.sh` runs on every service change (the deploy-script-only
# rule), so `--source-only` runs there PRE-BUILD. That makes a stale tag, a
# drifted filter engine, and an unguarded suite *undeployable* rather than merely
# discouraged — the same enforcement `check-filter-engine-parity.sh` already has.
#
# MODES
#   --source-only   seconds, no cluster, no docker, no node. The deploy hook.
#   (default)       the above + pytest (containerised) + typecheck + Vitest. ~2 min.
#
# EACH GATE BELOW EXISTS BECAUSE THE FAILURE ALREADY HAPPENED HERE:
#   1. tag⇄content   — E-3's code never ran for an ENTIRE SLICE because a tag was
#                      never bumped; both tag files agreed on a STALE tag and the
#                      cluster faithfully matched it, so every check stayed green
#                      while the feature was absent. (docs/bugs/e3-never-ran-tag-not-bumped.md)
#   2. suite guards  — suite-74 reported "PASS=5 FAIL=0 ✅" on a half-run that
#                      silently dropped 6 of 11 cases; and run-all.sh's run_suite()
#                      RETURNS 0 FOR A MISSING FILE, so deleting a suite makes the
#                      runner GREENER.
#   3. filter parity — registry-api's filter_engine.py missed the gateway's ReDoS
#                      hardening for months. E-4 scores the filter through that copy,
#                      so the eval graded a decision production never makes.
#   4. E-0 orphans   — `eval_runs.pass_threshold` / `dimension_weights` shipped with
#                      NO writer and NO reader; the column was NULL in every row ever
#                      written, and the publish threshold was re-declared FOUR times
#                      across THREE services (all defaulting to 0.7, so they agreed
#                      and nothing ever errored).
#   5. silent skips  — a bare `pytest` on the host (py3.9) SILENTLY SKIPS 59 of 133
#                      tests and EXITS 0, because the models need py3.10+. A fast
#                      tier that silently skips is the exact bug class this closes,
#                      so pytest runs in a throwaway container off the REAL image.
#
# Usage:
#   bash scripts/e2e/run-fast-gates.sh --source-only
#   bash scripts/e2e/run-fast-gates.sh
# ---------------------------------------------------------------------------
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
ROOT="$PWD"

SOURCE_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --source-only) SOURCE_ONLY=1 ;;
    -h|--help) sed -n '2,50p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg (expected --source-only)"; exit 2 ;;
  esac
done

PASS=0
FAIL=0
FAILED_GATES=()

hdr()  { echo ""; echo "── $1"; }
ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; FAIL=$((FAIL+1)); FAILED_GATES+=("$1"); }

# Run a sub-gate script, surfacing its output only on failure (the source tier is
# run on EVERY deploy — it must be quiet when green or it trains people to ignore it).
run_gate() {
  local name="$1" script="$2"; shift 2
  local out rc
  out="$(bash "$script" "$@" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "$name"
  else
    bad "$name" "(exit $rc)"
    echo "$out" | sed 's/^/        │ /'
  fi
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$SOURCE_ONLY" -eq 1 ]; then
  echo "  FAST GATES — source tier (no cluster, no build)"
else
  echo "  FAST GATES — full tier (source + pytest + studio)"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ---------------------------------------------------------------------------
# SOURCE TIER — seconds. Everything here is grep + git log only.
# ---------------------------------------------------------------------------

hdr "1. tag ⇄ content coupling (a tag is a claim about content)"
run_gate "tag⇄content coupling" "$ROOT/scripts/check-tag-content-coupling.sh"

hdr "2. suite guards (no half-run may read green)"
run_gate "suite guards + registration census" "$ROOT/scripts/check-suite-guards.sh"

hdr "3. filter-engine parity (the door E-4 scores == the door production runs)"
# REUSED, not duplicated: this gate already exists and already runs inside
# deploy-cpe2e.sh pre-build. The fast tier calls the same script.
run_gate "filter-engine parity" "$ROOT/scripts/check-filter-engine-parity.sh"

hdr "4. E-6 orphan sweep (every producer has a reader; one threshold, not four)"

# --- 4a. The E-0 columns have BOTH a writer and a reader -------------------
# `eval_runs.pass_threshold` + `dimension_weights` shipped in E-0 (migration 0059)
# with neither. The column was NULL in every row ever written, which made every
# `if run.pass_threshold is not None` downstream a DEAD branch that could not run.
EVR="services/registry-api/routers/eval_runner.py"
SCH="services/registry-api/schemas.py"
RUNNER="services/eval-runner/main.py"

if grep -q "pass_threshold=(" "$EVR" || grep -qE "pass_threshold=(body|EVAL_PASS)" "$EVR"; then
  ok "pass_threshold has a WRITER (create_eval_run sets the column)"
else
  bad "pass_threshold has NO WRITER" \
      "E-0 shipped the column with no writer ⇒ NULL in every row ever written, and every
        downstream 'if run.pass_threshold is not None' is a dead branch.
        REMEDY: set it on the EvalRun(...) construction in create_eval_run ($EVR)."
fi

if grep -q "run.pass_threshold" "$EVR"; then
  ok "pass_threshold has a READER (the eval_passed gate)"
else
  bad "pass_threshold has NO READER — the gate still uses the global constant" \
      "A per-run threshold nothing reads is decorative. REMEDY: effective_pass_threshold(run)."
fi

if grep -q "dimension_weights=body.dimension_weights" "$EVR"; then
  ok "dimension_weights has a WRITER"
else
  bad "dimension_weights has NO WRITER (create_eval_run never persists it)"
fi

if grep -q "_RUN_DIMENSION_WEIGHTS" "$RUNNER"; then
  ok "dimension_weights has a READER (the runner threads it to the score door)"
else
  bad "dimension_weights column has NO READER" \
      "The DOOR reads body.dimension_weights, but nothing ever puts the COLUMN in the body.
        REMEDY: the runner resolves the run's weights once and passes them ($RUNNER)."
fi

if grep -qE "pass_threshold: Optional\[float\]" "$SCH"; then
  ok "EvalRunCreate accepts a per-run pass_threshold"
else
  bad "EvalRunCreate does not accept pass_threshold — no caller can ever set one"
fi

# --- 4b. ONE threshold, not four (the D2 invariant) ------------------------
# The publish threshold existed FOUR times across THREE services, each defaulting
# to 0.7 — so they agreed and nothing errored. Wiring a per-run threshold to the
# gate ALONE makes the product LIE: a 0.85 run at threshold 0.9 renders "passed"
# in the UI and marks every item passed, while the gate refuses to publish.
n_api=$(grep -c "EVAL_PASS_THRESHOLD" "$EVR")
if [ "$n_api" -eq 3 ]; then
  ok "registry-api declares the threshold once (def + write-default + NULL fallback)"
else
  bad "registry-api has $n_api EVAL_PASS_THRESHOLD sites (expected 3)" \
      "$(grep -n "EVAL_PASS_THRESHOLD" "$EVR" | sed 's/^/          /')
        Expected exactly: the definition, the single write-time default, and the single
        legacy-NULL fallback. A 4th site is a re-declaration — that is how four copies
        of one number came to exist across three services."
fi

n_run=$(grep -c "_JUDGE_PASS_THRESHOLD" "$RUNNER")
if [ "$n_run" -eq 2 ]; then
  ok "eval-runner declares the threshold once (def + the single NULL fallback)"
else
  bad "eval-runner has $n_run _JUDGE_PASS_THRESHOLD sites (expected 2)" \
      "$(grep -n "_JUDGE_PASS_THRESHOLD" "$RUNNER" | sed 's/^/          /')
        Every per-item verdict must read _RUN_PASS_THRESHOLD (resolved from the run),
        not the env global. An extra reader is a fifth copy of the threshold."
fi

# --- 4c. The door keeps ONE weights source (D1) ----------------------------
# Letting the door ALSO resolve run_id→column would give one value two sources and
# a precedence rule — the priority-fallthrough that let MODE=webhook fall through to
# the reactive tail and deliver real side effects under a plausible PASS.
PG="services/registry-api/routers/playground.py"
n_second=$(grep -cE "run\.dimension_weights|run\[.dimension_weights" "$PG")
if [ "$n_second" -eq 0 ]; then
  ok "the score door has ONE weights source (no run_id→column second path)"
else
  bad "the score door grew a SECOND weights source ($n_second sites)" \
      "$(grep -nE "run\.dimension_weights|run\[.dimension_weights" "$PG" | sed 's/^/          /')
        Two sources for one value need a precedence rule — the priority-fallthrough
        anti-pattern. The RUNNER supplies the weights; the door reads body only."
fi

if [ "$SOURCE_ONLY" -eq 1 ]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  SOURCE TIER: PASS=$PASS FAIL=$FAIL"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  if [ "$FAIL" -ne 0 ]; then
    echo "  ❌ FAILED GATES:"
    for g in "${FAILED_GATES[@]}"; do echo "     - $g"; done
    exit 1
  fi
  [ "$PASS" -eq 0 ] && echo "  ❌ INCONCLUSIVE (no gate ran)" && exit 1
  echo "  ✅ source gates green"
  exit 0
fi

# ---------------------------------------------------------------------------
# FULL TIER — pytest (containerised) + studio typecheck/Vitest. ~2 min.
# ---------------------------------------------------------------------------

hdr "5. pytest — IN A CONTAINER off the real registry-api image"

# WHY A CONTAINER AND NOT `python3 -m pytest`:
#   The host python3 here is 3.9. models.py uses SQLAlchemy `Mapped[str | None]`
#   annotations, which need >=3.10, so those test modules carry a version skipif.
#   A bare host pytest therefore reports "74 passed, 59 skipped" AND EXITS 0 —
#   green, while 44% of the suite never ran. A fast tier that silently skips is
#   precisely the bug class this phase exists to close, so we run the tests on the
#   SAME interpreter the service runs (3.12), out of the REAL image.
# The image supplies the INTERPRETER + the installed deps; the code under test is the
# working tree, mounted read-only at /src. So the exact tag is NOT load-bearing here —
# pinning it would make this gate fail in the perfectly normal "tag bumped, not yet
# built" state, and a gate that red-lights routine work gets bypassed. Prefer the
# pinned tag when it exists, otherwise use the newest registry-api image present, and
# SAY which one ran. This is not a silent degradation: either way the tests execute the
# working tree on python 3.12. Falling back to the HOST would be the degradation, and
# that is refused outright.
RA_TAG="$(grep -E '^REGISTRY_API_TAG=' scripts/deploy-cpe2e.sh | head -1 | cut -d'"' -f2)"
IMAGE="registry.internal/agentshield/registry-api:${RA_TAG}"
IMG_NOTE="pinned tag"

if command -v docker >/dev/null 2>&1 && ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  FALLBACK="$(docker images --format '{{.Repository}}:{{.Tag}}' registry.internal/agentshield/registry-api 2>/dev/null | head -1)"
  if [ -n "$FALLBACK" ]; then
    IMAGE="$FALLBACK"
    IMG_NOTE="newest local image — ${RA_TAG} is not built yet; the interpreter is what matters, the code under test is the mounted working tree"
  fi
fi

if ! command -v docker >/dev/null 2>&1; then
  bad "pytest (containerised): docker not available" \
      "Refusing to fall back to the host interpreter: it is py3.9 and would SILENTLY
        SKIP 59 of 133 tests while exiting 0. A skipped gate that reads green is worse
        than no gate. Install/start docker, or run the source tier: --source-only"
elif ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  bad "pytest (containerised): no registry-api image present locally" \
      "Build one first: bash scripts/deploy-cpe2e.sh (the image is the INTERPRETER this
        gate depends on — python 3.12 with the service's deps). NOT falling back to the
        host py3.9, which would silently skip 59 of 133 tests and exit 0."
else
  echo "   image: $IMAGE  (python 3.12 — the interpreter the service actually runs)"
  echo "   using: $IMG_NOTE"
  PYOUT="$(docker run --rm -u root \
      -v "$ROOT/services/registry-api:/src:ro" -w /src \
      --entrypoint sh "$IMAGE" -c \
      "pip install -q pytest pytest-asyncio >/dev/null 2>&1; \
       python -m pytest tests -q -p no:cacheprovider 2>&1" 2>&1)"
  PYRC=$?
  SUMMARY="$(echo "$PYOUT" | grep -E "passed|failed|error" | tail -1)"

  # A SKIP in the container is a real signal (the interpreter is correct here, so a
  # skip means a genuinely unmet precondition) — surface it, never swallow it.
  N_SKIP="$(echo "$SUMMARY" | grep -oE '[0-9]+ skipped' | grep -oE '[0-9]+' || true)"
  if [ "$PYRC" -eq 0 ]; then
    ok "pytest in-container — $SUMMARY"
    if [ -n "${N_SKIP:-}" ] && [ "$N_SKIP" -gt 0 ]; then
      echo "        ⚠ $N_SKIP test(s) SKIPPED even on py3.12 — that is a real unmet"
      echo "          precondition, not the host-interpreter artefact. Investigate:"
      echo "$PYOUT" | grep -iE "^SKIPPED|skipped" | head -5 | sed 's/^/            /'
    fi
  else
    bad "pytest in-container — $SUMMARY"
    echo "$PYOUT" | tail -25 | sed 's/^/        │ /'
  fi
fi

hdr "6. studio — typecheck + Vitest"
if [ ! -d "$ROOT/studio/node_modules" ]; then
  bad "studio: node_modules absent" "run: cd studio && npm ci"
else
  TCOUT="$(cd "$ROOT/studio" && npm run typecheck 2>&1)"
  if [ $? -eq 0 ]; then ok "studio typecheck"; else
    bad "studio typecheck"; echo "$TCOUT" | tail -15 | sed 's/^/        │ /'
  fi

  VTOUT="$(cd "$ROOT/studio" && npm run test 2>&1)"
  if [ $? -eq 0 ]; then
    ok "studio Vitest — $(echo "$VTOUT" | grep -E "Tests +[0-9]+" | tail -1 | xargs)"
  else
    bad "studio Vitest"; echo "$VTOUT" | tail -20 | sed 's/^/        │ /'
  fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  FAST GATES (full): PASS=$PASS FAIL=$FAIL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$FAIL" -ne 0 ]; then
  echo "  ❌ FAILED GATES:"
  for g in "${FAILED_GATES[@]}"; do echo "     - $g"; done
  exit 1
fi
[ "$PASS" -eq 0 ] && echo "  ❌ INCONCLUSIVE (no gate ran)" && exit 1
echo "  ✅ all fast gates green"
echo ""
echo "  NOTE: the fast tier cannot see the live-path bugs (a real run, a real judge,"
echo "  a real DB read-back). Those are scripts/e2e/run-all.sh — incl. suite-80."
exit 0

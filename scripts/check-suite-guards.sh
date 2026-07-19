#!/usr/bin/env bash
#
# check-suite-guards.sh — a suite that stops early must NEVER read green.
#
# WHY THIS EXISTS (earned, not theoretical):
#
#   1. suite-74 once reported "PASS=5 FAIL=0 ✅" on a half-run that silently dropped 6
#      of 11 cases. FAIL=0 is only a pass if every case actually RAN. An exception, an
#      early return, or a truncated result file otherwise yields "0 failures" on a
#      half-run gate.
#
#   2. Worse, and ABOVE every per-suite census: run-all.sh's run_suite() (:38-41)
#      RETURNS 0 WHEN THE SCRIPT FILE IS MISSING — "Don't count missing future suites as
#      failures". So DELETING OR RENAMING ANY SUITE MAKES THE RUNNER GREENER, NOT REDDER.
#      Every per-suite T-SNN-COMPLETE census is defeated by a suite that never runs at
#      all. That is the hole this gate closes from the outside.
#
# TWO ASSERTIONS:
#   (a) REGISTRATION CENSUS — every registered suite exists; every suite on disk is
#       registered. Drift in BOTH directions. Protects all ~76 suites regardless of
#       their internal pattern.
#   (b) GUARD META-GATE — every DRIVER-PATTERN suite carries a crash-loud T-SNN-999 and
#       an ID-based REQUIRED_IDS census.
#
# WHY NOT ALL 76 FOR (b): the guards are defined over the python-driver + result-file
# pattern (an `except Exception` wrapping a driver process, an ID census over a results
# file). Only the driver suites HAVE a driver process to crash-wrap and a results file to
# census. The rest are plain bash+curl. Asserting the guards over all 76 would be a gate
# satisfiable only by rewriting 60+ suites — i.e. a gate nobody will run, which is worse
# than none because it reads as protection. The bash-only suites are REPORTED BY NAME
# here (never silently excluded) and recorded in the Gap Ledger.
#
# The driver-suite list is DETECTED, never hardcoded — a hardcoded list silently goes
# stale the moment someone adds a suite.
#
# Usage: bash scripts/check-suite-guards.sh

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

RUN_ALL="scripts/e2e/run-all.sh"
E2E_DIR="scripts/e2e"

PASS=0
FAIL=0
ok()  { echo "PASS  $1"; [ -n "${2:-}" ] && echo "        $2"; PASS=$((PASS+1)); return 0; }
bad() { echo "FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; FAIL=$((FAIL+1)); return 0; }

echo "=== suite guards: no half-run may read green ==="
echo ""

# ---------------------------------------------------------------------------
# (a) REGISTRATION CENSUS — the R7 hole, closed from outside run_suite().
#
# NOTE: this deliberately does NOT "fix" run_suite() to fail on a missing file.
# Suites are being registered concurrently by other workstreams; changing the runner's
# failure semantics mid-flight would break their landings for reasons unrelated to their
# work. This census fails JUST AS LOUDLY and is inert to landing order.
# ---------------------------------------------------------------------------
echo "-- registration census (a deleted/renamed suite makes run-all.sh GREENER) --"
REGISTERED=$(grep -oE '"suite-[^"]+\.sh"' "$RUN_ALL" | tr -d '"' | sort -u)

miss=""
for s in $REGISTERED; do
  [ -f "$E2E_DIR/$s" ] || miss="$miss $s"
done
if [ -n "$miss" ]; then
  bad "every registered suite exists on disk" \
      "REGISTERED BUT MISSING:$miss
        run_suite() returns 0 for a missing file, so each of these is SILENTLY SKIPPED and
        run-all.sh still reports GREEN. Either restore the file or remove its run_suite line."
else
  ok "every registered suite exists on disk" "$(echo "$REGISTERED" | wc -w | tr -d ' ') registered"
fi

unreg=""
for f in "$E2E_DIR"/suite-*.sh; do
  b=$(basename "$f")
  grep -q "\"$b\"" "$RUN_ALL" || unreg="$unreg $b"
done
if [ -n "$unreg" ]; then
  bad "every suite on disk is registered in run-all.sh" \
      "ON DISK BUT NEVER RUN:$unreg — an unregistered suite is dead code that proves nothing."
else
  ok "every suite on disk is registered in run-all.sh" "$(ls "$E2E_DIR"/suite-*.sh | wc -l | tr -d ' ') on disk"
fi

echo ""

# ---------------------------------------------------------------------------
# (b) GUARD META-GATE over the DETECTED driver-pattern suites.
# ---------------------------------------------------------------------------
echo "-- guard meta-gate (driver-pattern suites, detected not hardcoded) --"
DRIVER_SUITES=""
BASH_ONLY=""
for f in "$E2E_DIR"/suite-*.sh; do
  if grep -qE '^[[:space:]]*DRIVER=' "$f"; then
    DRIVER_SUITES="$DRIVER_SUITES $f"
  else
    BASH_ONLY="$BASH_ONLY $(basename "$f")"
  fi
done

n_driver=$(echo "$DRIVER_SUITES" | wc -w | tr -d ' ')
echo "   detected $n_driver driver-pattern suites"
echo ""

for f in $DRIVER_SUITES; do
  b=$(basename "$f")
  # suite-72-... -> 72 -> T-S72-
  num=$(echo "$b" | sed -E 's/^suite-([0-9]+)-.*/\1/')
  pfx="T-S${num}"

  # CODE ONLY — comment lines are stripped before every guard grep below.
  #
  # WHY (found by perturbing this gate, which is the only way to learn it): every
  # well-written suite DOCUMENTS its own case list in its header, so `T-SNN-999`
  # and `REQUIRED_IDS` both appear in prose near the top of the file. Grepping the
  # whole file therefore passed on a suite carrying NO GUARD AT ALL — a scratch
  # suite whose only content was `# T-S99-999 — ...` and the word REQUIRED_IDS in a
  # comment was reported "crash-loud T-S99-999 present". The gate built to stop
  # "a content-grep proves PRESENCE, not CORRECTNESS" was itself satisfiable by a
  # COMMENT: documenting a guard scored the same as implementing one, and the
  # false-negative pointed the reassuring way.
  CODE="$(grep -v '^[[:space:]]*#' "$f")"

  # 1. crash-loud wrapper — must be RECORDED IN CODE, not merely described.
  if printf '%s' "$CODE" | grep -q "${pfx}-999"; then
    ok "$b: crash-loud ${pfx}-999 present" ""
  else
    bad "$b: NO crash-loud ${pfx}-999 guard" \
        "a driver that dies mid-run writes only the cases recorded BEFORE the crash; the bash
        summary then reports PASS>0 FAIL==0 and the suite reads GREEN on a half-run.
        REMEDY: wrap the driver body in `except Exception` recording '${pfx}-999 driver ran
        every case without crashing' as a FAIL with traceback.format_exc()[-400:] (see suite-77)."
  fi

  # 2. ID-based census — an ASSIGNMENT in code, not the word in a header comment.
  if printf '%s' "$CODE" | grep -q "REQUIRED_IDS="; then
    ok "$b: ID-based REQUIRED_IDS census present" ""
  else
    bad "$b: NO REQUIRED_IDS census" \
        "without an ID census, a suite that stops early cannot be told from one that passed.
        REMEDY: add REQUIRED_IDS + a grep loop emitting '${pfx}-COMPLETE' (see suite-77)."
  fi

  # 3. the census must be ID-based, NOT a hardcoded count. A count drifted immediately in
  #    suite-74 and cannot say WHICH case vanished.
  #    `[ "$PASS" -eq 0 ]` is the INCONCLUSIVE guard (correct, keep it) — only a count
  #    gate against a NON-ZERO expected total is the anti-pattern.
  if grep -qE '\[ *"?\$(P_)?PASS"? -eq [1-9]' "$f"; then
    bad "$b: a hardcoded PASS COUNT gate stands in for the ID census" \
        "$(grep -nE '\[ *"?\$(P_)?PASS"? -eq [1-9]' "$f" | head -2)
        A count drifted immediately in suite-74 and cannot name the case that vanished.
        REMEDY: assert REQUIRED_IDS by ID instead."
  else
    ok "$b: no hardcoded count gate standing in for the ID census" ""
  fi
done

echo ""
echo "-- bash-only suites (outside the guard pattern's reach — REPORTED, not hidden) --"
echo "   $(echo "$BASH_ONLY" | wc -w | tr -d ' ') suites are plain bash+curl with no driver process to"
echo "   crash-wrap and no result file to census. Retrofitting them = rewriting them onto"
echo "   the driver pattern (a slice of its own). Tracked in the Gap Ledger."
echo "   $BASH_ONLY" | fold -s -w 100 | sed 's/^/     /'

echo ""
echo "=== suite guards: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ suite guards FAILED — $FAIL guard(s) missing. A half-run that reads green is how 6 of 11 cases vanish silently."
  exit 1
fi
if [ "$PASS" -eq 0 ]; then
  echo "❌ suite guards INCONCLUSIVE (no checks ran)"
  exit 1
fi
echo "✅ suite guards PASSED ($PASS checks)"

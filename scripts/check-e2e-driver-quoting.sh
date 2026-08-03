#!/usr/bin/env bash
# A backtick inside an e2e driver body is a COMMAND, not punctuation.
#
# The bash suites embed Python in one of two shapes, and BOTH are expanded by the shell
# before the interpreter ever sees them:
#
#   run_test "label" "        <- double-quoted string
#   ...python...
#   "
#
#   kubectl exec ... python3 - <<PY    <- UNQUOTED heredoc
#   ...python...
#   PY
#
# The expansion is the point: every suite interpolates ${AGENT_NAME}, ${E2E_TOKEN} and
# friends this way. The cost is that backticks are command substitution too, so prose
# like "returns `decided`" runs `decided` as a program. Observed twice in one day:
#
#   scripts/e2e/suite-8-playground.sh: line 565: decided: command not found
#   scripts/e2e/suite-37-workflow-hitl-opa.sh: line 61: from: command not found
#
# Both were in COMMENTS explaining a fix, so the suite still ran — it just executed
# junk first and then failed somewhere unrelated, which is the expensive part: the
# error names a line in the middle of a driver and says nothing about quoting.
#
# Guard: no backtick inside a driver body. Backticks in ordinary top-level shell
# comments are untouched — bash never expands those, and they read fine.
#
# Fix when this fires: use 'single quotes' in the prose, or drop the quoting marks.
# An escaped \` is fine and is not reported.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAIL=0
CHECKED=0

for f in "$ROOT"/scripts/e2e/*.sh; do
  CHECKED=$((CHECKED + 1))
  # Track whether we are inside a driver body, and report the offending line.
  out=$(awk '
    # --- unquoted heredoc: <<WORD  (a QUOTED <<"WORD" / <<\WORD is NOT expanded) ---
    !inbody && /<<[A-Za-z_][A-Za-z0-9_]*/ && !/<<["'"'"'\\]/ {
      match($0, /<<[A-Za-z_][A-Za-z0-9_]*/)
      term = substr($0, RSTART + 2, RLENGTH - 2)
      inbody = 1; kind = "heredoc " term; next
    }
    inbody && kind ~ /^heredoc / {
      t = kind; sub(/^heredoc /, "", t)
      if ($0 == t) { inbody = 0; next }
    }
    # --- run_test "label" "  ... opens a double-quoted driver body ---
    !inbody && /run_test .*" *"$/ { inbody = 1; kind = "run_test"; next }
    inbody && kind == "run_test" && /^" *$/ { inbody = 0; next }

    # An ESCAPED backtick is not command substitution -- bash treats \` as a literal in
    # both a double-quoted string and an unquoted heredoc. Strip those before looking,
    # or this flags correctly-escaped prose (suite-16 line 125) as a defect.
    inbody { probe = $0; gsub(/\\`/, "", probe)
             if (index(probe, "`")) printf "    line %d: %s\n", NR, $0 }
  ' "$f")
  if [ -n "$out" ]; then
    echo "FAIL  $(basename "$f") — backtick inside a driver body (shell will run it):"
    echo "$out"
    FAIL=$((FAIL + 1))
  fi
done

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "✅ e2e driver quoting PASSED — $CHECKED suite(s), no backticks in driver bodies"
else
  echo "❌ e2e driver quoting FAILED — $FAIL suite(s) would execute prose as a command"
  exit 1
fi

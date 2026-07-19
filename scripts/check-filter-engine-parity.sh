#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# filter-engine parity gate
#
# `filter_engine.py` exists TWICE and the copies must stay byte-identical:
#   services/event-gateway/filter_engine.py   — production: the real webhook hop
#   services/registry-api/filter_engine.py    — POST /playground/test-event, the door
#                                               Eval v2 E-4 scores the filter through
# Both services build from their own directory as the Docker build context, so neither
# can import a shared module without changing the build context for both. Until that
# lands, this gate keeps them in lockstep — enforcement, not discipline.
#
# WHY (it already happened, silently, for months): the gateway's copy was hardened
# against ReDoS (T-7: bound the regex input length, fail safe on over-length/invalid
# patterns) and the fix was NEVER back-ported. registry-api ran an unbounded regex on a
# caller-supplied payload on the shared control plane. And because `test-event` is the
# door an E-4 webhook eval scores the filter through, the eval would have graded a
# decision production never makes — a fake, institutionalised.
#
# This runs inside scripts/deploy-cpe2e.sh BEFORE either image is built, so divergent
# engines are not merely detectable — they are UNDEPLOYABLE. Fails loudly with a diff.
#
# Run standalone:  bash scripts/check-filter-engine-parity.sh
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
A="$ROOT/services/event-gateway/filter_engine.py"
B="$ROOT/services/registry-api/filter_engine.py"

for f in "$A" "$B"; do
  if [ ! -f "$f" ]; then
    echo "❌ filter-engine parity: missing $f"
    echo "   Both copies must exist. If you intentionally removed one, remove this gate"
    echo "   in the same change and say why."
    exit 1
  fi
done

if diff -q "$A" "$B" >/dev/null 2>&1; then
  echo "✅ filter-engine parity: event-gateway and registry-api copies are byte-identical"
  exit 0
fi

echo "❌ filter-engine parity FAILED — the two copies have DRIFTED."
echo ""
echo "   services/event-gateway/filter_engine.py  (production webhook hop)"
echo "   services/registry-api/filter_engine.py   (test-event; what E-4 scores)"
echo ""
echo "   A webhook eval scores the filter through registry-api's copy while production"
echo "   runs the gateway's. If they differ, the eval grades a decision production never"
echo "   makes — and a security fix applied to one silently misses the other (exactly how"
echo "   the ReDoS bound went un-back-ported)."
echo ""
echo "   Fix: make the change in BOTH files (they must be byte-identical), then re-run."
echo ""
echo "   --- diff (event-gateway -> registry-api) ---"
diff "$A" "$B" | sed 's/^/   /'
exit 1

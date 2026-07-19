#!/usr/bin/env bash
set -euo pipefail
echo "=== Checkpoint 3a: POC-2 eval transcript wired ==="
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EVAL="$ROOT/studio/src/pages/EvalResultsPage.tsx"
grep -q "scope: *[\"']workflow_run[\"']" "$EVAL" || { echo "FAIL: listMemory scope=workflow_run not found"; exit 1; }
grep -q "listMemory" "$EVAL" || { echo "FAIL: listMemory not called"; exit 1; }
# guard: renders only when run_id AND memberName truthy
grep -Eq "r\.run_id *&& *memberName|memberName *&& *r\.run_id" "$EVAL" || { echo "FAIL: run_id/memberName guard missing"; exit 1; }
grep -q "AttributedBubble" "$EVAL" || { echo "FAIL: AttributedBubble not rendered"; exit 1; }
echo "eval transcript: listMemory(scope=workflow_run) + guard + AttributedBubble present"
cd "$ROOT/studio"; npm run typecheck
echo "PASS"

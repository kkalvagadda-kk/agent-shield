#!/usr/bin/env bash
set -euo pipefail
echo "=== Checkpoint 3b: POC-2 share-context toggle wiring ==="
WB="$(cd "$(dirname "$0")/../.." && pwd)/studio/src/pages/WorkflowBuilderPage.tsx"
ME=$(grep -c "memory_enabled" "$WB" || true)
echo "memory_enabled occurrences in WorkflowBuilderPage: $ME"
[ "$ME" -ge 3 ] || { echo "FAIL: expected memory_enabled in mount-load + both save calls (>=3)"; exit 1; }
grep -q "workflow.memory_enabled" "$WB" || { echo "FAIL: toggle not loaded from workflow.memory_enabled on mount"; exit 1; }
grep -q "Share context between agents" "$WB" || { echo "FAIL: toggle label missing"; exit 1; }
# no invented fields
if grep -Eq "per_session|per_run|share_rationale" "$WB"; then echo "FAIL: invented scope/rationale field present"; exit 1; fi
echo "toggle: memory_enabled loaded on mount + sent in both saves; no invented fields"
echo "PASS"

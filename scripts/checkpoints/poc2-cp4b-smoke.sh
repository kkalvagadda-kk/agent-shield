#!/usr/bin/env bash
set -euo pipefail
# CP4b — live smoke: single-agent SSE carries author; suite-75 (incl. T-S75-007);
# workflow transcript has >=2 distinct agent_name. Run AFTER CP4a.
echo "=== Checkpoint 4b: POC-2 live smoke ==="
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/test-cluster-kube-config.yaml}"
echo "--- re-run suite-75 (expect T-S75-007 pass, no regression) ---"
bash scripts/e2e/suite-75-context-storage.sh | tee /tmp/poc2-suite75.log
grep -q 'PASS: T-S75-007' /tmp/poc2-suite75.log || { echo "FAIL: T-S75-007 did not pass"; exit 1; }
grep -Eq 'FAIL: T-S75-00[0-9]' /tmp/poc2-suite75.log && { echo "FAIL: a suite-75 test regressed"; exit 1; }
echo "suite-75: T-S75-007 pass, no FAIL lines"
echo "PASS"

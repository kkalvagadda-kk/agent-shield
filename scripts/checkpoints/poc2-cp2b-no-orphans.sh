#!/usr/bin/env bash
set -euo pipefail
echo "=== Checkpoint 2b: POC-2 no-orphan symbols (DoD #3) ==="
cd "$(cd "$(dirname "$0")/../.." && pwd)/studio/src"
# AttributedBubble: defined once, must have >=1 external importer
imp=$(grep -rl "from ['\"].*chat/AttributedBubble['\"]" . | wc -l | tr -d ' ')
echo "AttributedBubble importers: $imp"; [ "$imp" -ge 3 ] || { echo "FAIL: AttributedBubble under-used ($imp)"; exit 1; }
# agentColor consumed (by AttributedBubble)
grep -rq "from ['\"].*lib/agentColor['\"]" . || { echo "FAIL: agentColor orphaned"; exit 1; }
# routeToken/openAuthorBubble called outside their own module
for sym in routeToken openAuthorBubble; do
  callers=$(grep -rl "$sym" --include=*.tsx . | wc -l | tr -d ' ')
  echo "$sym callers (tsx): $callers"; [ "$callers" -ge 2 ] || { echo "FAIL: $sym has too few callers ($callers)"; exit 1; }
done
echo "PASS"

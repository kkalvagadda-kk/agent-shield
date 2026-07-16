#!/usr/bin/env bash
set -euo pipefail
echo "=== Checkpoint 2a: POC-2 chat surfaces wired (combined state) ==="
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/studio"
echo "--- typecheck ---"; npm run typecheck
echo "--- full vitest ---"; npm run test
echo "--- AttributedBubble imported in all three surfaces ---"
for f in src/pages/AgentChatPage.tsx src/components/playground/ChatPane.tsx src/pages/CatalogChatPage.tsx; do
  grep -q 'AttributedBubble' "$f" || { echo "FAIL: AttributedBubble not in $f"; exit 1; }
done
echo "--- CatalogChatPage renders run-tree children per-member ---"
grep -q 'children' src/pages/CatalogChatPage.tsx && grep -q 'agent_name' src/pages/CatalogChatPage.tsx \
  || { echo "FAIL: CatalogChatPage does not read run-tree children/agent_name"; exit 1; }
echo "PASS"

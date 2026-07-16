#!/usr/bin/env bash
set -euo pipefail
echo "=== Checkpoint 1a: POC-2 frontend foundation (libs + typecheck) ==="
cd "$(dirname "$0")/../../studio"
echo "--- focused vitest (agentColor, chatStream, AttributedBubble) ---"
npm run test -- agentColor chatStream AttributedBubble --run
echo "--- typecheck ---"
npm run typecheck
echo "PASS"

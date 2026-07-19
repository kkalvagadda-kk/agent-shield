#!/usr/bin/env bash
set -euo pipefail
echo "=== Checkpoint 1b: POC-2 backend SSE author frames ==="
cd "$(dirname "$0")/../.."
python3 -c "import ast; ast.parse(open('services/registry-api/routers/chat.py').read()); print('chat.py parses')"
AUTHOR_CT=$(grep -c '"author"' services/registry-api/routers/chat.py || true)
echo "chat.py '\"author\"' occurrences: $AUTHOR_CT"
[ "$AUTHOR_CT" -ge 2 ] || { echo "FAIL: expected >=2 author frame occurrences"; exit 1; }
grep -q 'agent_start' services/registry-api/routers/chat.py || { echo "FAIL: agent_start frame missing"; exit 1; }
grep -q 'author=name' services/registry-api/routers/chat.py || { echo "FAIL: author=name not passed at call sites"; exit 1; }
grep -q 'T-S75-007' scripts/e2e/suite-75-context-storage.sh || { echo "FAIL: T-S75-007 not registered"; exit 1; }
bash -n scripts/e2e/suite-75-context-storage.sh && echo "suite-75 syntax ok"
for f in scripts/deploy-cpe2e.sh scripts/deploy-eks.sh charts/agentshield/values.yaml; do
  grep -q '0.2.189' "$f" || { echo "FAIL: 0.2.189 missing in $f"; exit 1; }
done
echo "registry-api 0.2.189 in all three deploy files"
echo "PASS"

#!/usr/bin/env bash
# scripts/smoke-test-cp2-ws2-behaviour.sh
#
# WS-2 Checkpoint 2 — BEHAVIOUR smoke (CP2c). This IS the no-fakes behaviour gate:
# it simply runs the REAL daemon-identity acceptance suite (T-S70-001..005). Kept as a
# thin, named CP2 entry point so the checkpoint reads uniformly (deploy → infra → behaviour),
# mirroring CP1c which wrapped its behaviour proof.
#
#   suite-70: real daemon agent + workflow, real dispatch→park→decide→resume,
#             real committed run_by / principal_display / 403 / resume assertions.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== WS-2 CP2c: behaviour smoke (runs suite-70-daemon-identity.sh) ==="
echo ""
bash "$REPO_ROOT/scripts/e2e/suite-70-daemon-identity.sh"

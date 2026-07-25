#!/usr/bin/env bash
# AgentShield E2E Master Runner — API layer (bash suites).
#
# This is now a thin wrapper over scripts/run-tests.sh. The suite registry that
# used to live inline here moved to scripts/test-manifest.txt, which is the single
# source of truth for BOTH layers (bash suites + Playwright specs) and carries the
# functional group each test belongs to. Two copies of the registry would drift the
# moment someone added a suite to one and not the other.
#
# Behaviour is unchanged: no args runs every API suite, in manifest order, and
# aggregates suite-level pass/fail. Extra args (e.g. --auto-pf) still pass through
# to the individual suites.
#
# Usage:
#   bash scripts/e2e/run-all.sh
#   NAMESPACE=my-ns bash scripts/e2e/run-all.sh
#   bash scripts/e2e/run-all.sh --auto-pf
#
# To run a SUBSET instead of all ~89 suites — which is what you want after a
# scoped change — use the group selector directly:
#   bash scripts/run-tests.sh --groups                  # what groups exist
#   bash scripts/run-tests.sh --layer api --group tools # just the tools suites
#
# Browser (Playwright) tests are a SEPARATE gate and are NOT part of this run:
#   bash scripts/studio-e2e.sh                          # all specs
#   bash scripts/run-tests.sh --layer browser --group tools
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "${REPO_ROOT}/scripts/run-tests.sh" --layer api "$@"

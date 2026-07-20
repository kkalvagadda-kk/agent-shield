#!/usr/bin/env bash
# studio-journeys-e2e.sh — ONE-command browser suite for the delivered UX journeys.
#
# Runs the Playwright specs that prove the recent work end-to-end in a real browser
# (real Keycloak login, network-call + persistence assertions), in a single run:
#
#   • Knowledge Base config    — knowledge_search is NOT a listed tool on ANY of the
#                                three agent-editing surfaces (Create, Settings, Edit
#                                modal), the KB picker attaches it, and grounded answers.
#   • Workflow run ledger       — the deployment Conversations + Memory tabs list via the
#                                workflow endpoints, and opening a past session REPLAYS it.
#   • HITL approvals            — the production console queue + the workflow inline panel.
#
# It reuses scripts/studio-e2e.sh for the port-forward / gateway + login plumbing, so
# there is nothing else to set up. First-time only: cd studio && npx playwright install chromium.
#
# Usage:
#   bash scripts/studio-journeys-e2e.sh
#   STUDIO_E2E_GATEWAY_URL=... bash scripts/studio-journeys-e2e.sh   # override target
#
# Exit: 0 when no spec FAILS (annotate-skips on warm-fixture/capacity are OK); 1 on any
# real failure. Playwright's own summary is authoritative — studio-e2e.sh can mask the
# child exit code, so we grep the combined output for a failure line.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SPECS=(
  e2e/agent-knowledge-config.spec.ts        # KB special-config + Edit Agent modal (no knowledge_search leak)
  e2e/knowledge.spec.ts                     # KB sources → grounded answers
  e2e/workflow-conversations.spec.ts        # workflow ledger — Conversations tab
  e2e/workflow-memory.spec.ts               # workflow ledger — Memory tab + past-session replay
  e2e/approvals-inbox.spec.ts               # HITL — production console queue (ApprovalCard + decide)
  e2e/hitl-production-chat.spec.ts          # HITL — PRODUCTION chat: waiting-banner → console approve → auto-resume
)

# NOT in the curated set (fixture-dependent LIVE run, not deterministic): the workflow
# INLINE approval journey drives a real LLM run and needs the `flow-conditional`
# (6fc9ea22-…) + `wf-payout` fixtures deployed to park. Run it directly when those exist:
#   bash scripts/studio-e2e.sh e2e/workflow-inline-approval-live.spec.ts
# The re-park/2nd-gate behavior itself is covered deterministically by Vitest
# (WorkflowChatPage/AgentChatPage/ApprovalsInboxPage/EvalResultsPage) + suite-79 004b/004c.

echo "=== Studio journey E2E — ${#SPECS[@]} specs ==="
printf '  • %s\n' "${SPECS[@]}"
echo ""

LOG="$(mktemp -t studio-journeys.XXXXXX)"
cleanup() { rm -f "$LOG"; }
trap cleanup EXIT

bash "$REPO_ROOT/scripts/studio-e2e.sh" "${SPECS[@]}" 2>&1 | tee "$LOG"

echo ""
echo "=== Journey suite result ==="
if grep -qE "[1-9][0-9]* failed" "$LOG"; then
  grep -E "[0-9]+ passed|[0-9]+ failed|[0-9]+ skipped|✘" "$LOG" | tail -5
  echo "RESULT: FAIL — at least one journey spec failed (see output above)."
  exit 1
fi
grep -E "[0-9]+ passed|[0-9]+ skipped" "$LOG" | tail -3
echo "RESULT: PASS — all journey specs green (warm-fixture/capacity skips are expected)."
exit 0

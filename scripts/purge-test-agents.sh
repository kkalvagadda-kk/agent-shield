#!/usr/bin/env bash
# purge-test-agents.sh — Remove e2e/test-created agents (and their child rows +
# orphaned runs/events) from the platform, KEEPING the seeded demo agents and
# any agent created by a real user.
#
# KEEP set (never deleted):
#   - the 5 seeded demo agents: research-assistant, calculator-bot,
#     slack-notifier, echo-agent, order-agent
#   - any agent whose created_by is a REAL user (i.e. NOT one of the test
#     identities: system / user-alice / smoke-user)
#
# DELETE set: everything else (all e2e/test-created agents).
#
# Runs a dry-run by default (prints counts + sample). Pass --yes to execute.
#
# Usage:
#   bash scripts/purge-test-agents.sh            # dry-run
#   bash scripts/purge-test-agents.sh --yes      # execute
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PG_PASSWORD="${PG_PASSWORD:-DevPass2024}"
EXECUTE="${1:-}"

PG_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=postgresql \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${PG_POD:-}" ] && { echo "FATAL: postgres pod not found"; exit 1; }

psql_run() { kubectl exec -i -n "$NAMESPACE" "$PG_POD" -c postgresql -- \
  bash -c "PGPASSWORD='${PG_PASSWORD}' psql -U postgres -d agentshield -v ON_ERROR_STOP=1"; }

# The selection predicate — kept identical between dry-run and execute.
PREDICATE="name NOT IN ('research-assistant','calculator-bot','slack-notifier','echo-agent','order-agent')
  AND coalesce(created_by,'system') IN ('system','user-alice','smoke-user')"

echo "=== Test-agent purge (${NAMESPACE}) ==="
echo "--- Dry run: what WOULD be deleted ---"
psql_run <<SQL
SELECT count(*) AS agents_to_delete FROM agents WHERE ${PREDICATE};
SELECT name FROM agents WHERE ${PREDICATE} ORDER BY name LIMIT 15;
SELECT '... (showing first 15)' WHERE (SELECT count(*) FROM agents WHERE ${PREDICATE}) > 15;
SELECT count(*) AS agents_kept FROM agents WHERE NOT (${PREDICATE});
SQL

if [ "$EXECUTE" != "--yes" ]; then
  echo ""
  echo "Dry-run only. Re-run with --yes to execute the purge."
  exit 0
fi

echo ""
echo "--- Executing purge (transactional) ---"
psql_run <<SQL
BEGIN;
CREATE TEMP TABLE _del AS SELECT id, name FROM agents WHERE ${PREDICATE};
DELETE FROM approvals       WHERE agent_id  IN (SELECT id   FROM _del);
DELETE FROM agent_runs      WHERE agent_name IN (SELECT name FROM _del);
DELETE FROM agent_events    WHERE agent_name IN (SELECT name FROM _del);
DELETE FROM playground_runs WHERE agent_name IN (SELECT name FROM _del);
DELETE FROM agents          WHERE id        IN (SELECT id   FROM _del);
SELECT count(*) AS agents_remaining FROM agents;
COMMIT;
SQL
echo "==> Purge complete."

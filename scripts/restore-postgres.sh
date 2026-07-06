#!/usr/bin/env bash
# restore-postgres.sh — Restore an AgentShield Postgres dump produced by
# scripts/backup-postgres.sh into the running cluster.
#
# Use after a fresh deploy on a wiped/recreated cluster to bring back users
# (Keycloak), agents, runs, etc. Restores ALL databases (pg_dumpall output).
#
# Usage:
#   bash scripts/restore-postgres.sh                       # newest dump in ./backups
#   bash scripts/restore-postgres.sh path/to/dump.sql.gz   # a specific dump
#
# WARNING: the dumps use --clean --if-exists, so restoring OVERWRITES current
# data in the target databases. You will be asked to confirm.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backups}"
PG_PASSWORD="${PG_PASSWORD:-DevPass2024}"

DUMP="${1:-}"
if [ -z "$DUMP" ]; then
  DUMP=$(ls -1t "${BACKUP_DIR}"/agentshield-pg-*.sql.gz 2>/dev/null | head -1 || true)
fi
[ -z "${DUMP:-}" ] && { echo "FATAL: no dump specified and none found in ${BACKUP_DIR}"; exit 1; }
[ ! -f "$DUMP" ] && { echo "FATAL: dump not found: ${DUMP}"; exit 1; }

PG_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=postgresql \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${PG_POD:-}" ] && { echo "FATAL: postgres pod not found in ${NAMESPACE}"; exit 1; }

echo "==> About to restore ${DUMP}"
echo "    into ${PG_POD} (${NAMESPACE}) — this OVERWRITES current DB contents."
read -r -p "Type 'restore' to proceed: " CONFIRM
[ "$CONFIRM" = "restore" ] || { echo "Aborted."; exit 1; }

echo "==> Restoring..."
gunzip -c "$DUMP" | kubectl exec -i -n "$NAMESPACE" "$PG_POD" -c postgresql -- \
  bash -c "PGPASSWORD='${PG_PASSWORD}' psql -U postgres -v ON_ERROR_STOP=0"

echo "==> Restore complete. Restart dependent pods so they reconnect cleanly:"
echo "    kubectl rollout restart deploy/agentshield-registry-api deploy/agentshield-keycloak -n ${NAMESPACE}"

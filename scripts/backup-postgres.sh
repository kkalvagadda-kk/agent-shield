#!/usr/bin/env bash
# backup-postgres.sh — Disaster-recovery backup of the AgentShield Postgres.
#
# Runs pg_dumpall inside the postgres pod and streams a gzipped SQL dump to a
# directory ON YOUR MAC (outside the cluster). This is the ONLY backup that
# survives a full "Reset Kubernetes Cluster" / Docker Desktop VM wipe — the
# in-cluster PVC (even with Retain) lives inside the DD node and is lost if the
# cluster itself is recreated.
#
# Captures ALL databases (keycloak users, agentshield registry, langfuse, ...).
#
# Usage:
#   bash scripts/backup-postgres.sh                 # → ./backups/agentshield-pg-<ts>.sql.gz
#   BACKUP_DIR=~/agentshield-backups bash scripts/backup-postgres.sh
#
# Schedule it (macOS launchd example) to run automatically — see
# docs/runbooks/postgres-backup.md.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backups}"
PG_PASSWORD="${PG_PASSWORD:-DevPass2024}"
RETENTION="${RETENTION:-14}"   # keep the newest N dumps

mkdir -p "$BACKUP_DIR"
# No Date.now in-shell issues here — this is a plain host script.
TS=$(date +%Y%m%d-%H%M%S)
OUT="${BACKUP_DIR}/agentshield-pg-${TS}.sql.gz"

PG_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=postgresql \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${PG_POD:-}" ] && { echo "FATAL: postgres pod not found in ${NAMESPACE}"; exit 1; }

echo "==> Dumping all databases from ${PG_POD} → ${OUT}"
kubectl exec -n "$NAMESPACE" "$PG_POD" -c postgresql -- \
  bash -c "PGPASSWORD='${PG_PASSWORD}' pg_dumpall -U postgres --clean --if-exists" \
  | gzip > "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
if [ ! -s "$OUT" ]; then echo "FATAL: dump is empty — backup failed"; rm -f "$OUT"; exit 1; fi
echo "    Wrote ${OUT} (${SIZE})"

# Prune old dumps beyond retention count.
COUNT=$(ls -1t "${BACKUP_DIR}"/agentshield-pg-*.sql.gz 2>/dev/null | wc -l | tr -d ' ')
if [ "${COUNT}" -gt "${RETENTION}" ]; then
  ls -1t "${BACKUP_DIR}"/agentshield-pg-*.sql.gz | tail -n +$((RETENTION + 1)) | xargs rm -f
  echo "    Pruned to newest ${RETENTION} dumps."
fi
echo "==> Backup complete."

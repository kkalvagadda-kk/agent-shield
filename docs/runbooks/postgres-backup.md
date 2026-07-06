# Postgres Durability & Backup (local Docker Desktop)

AgentShield stores everything durable in one Postgres StatefulSet — **including
Keycloak users** (`keycloak` DB), the agent registry, runs, and Langfuse. This
runbook explains how that data survives restarts and how to back it up.

## What protects the data

| Layer | Protects against | Mechanism |
|-------|------------------|-----------|
| PVC (`data-agentshield-postgresql-0`) | Pod restarts | `PGDATA` lives on the PVC, not emptyDir |
| StatefulSet `persistentVolumeClaimRetentionPolicy: Retain` | `helm uninstall`, scaling | PVC not auto-deleted |
| **PV `reclaimPolicy: Retain`** | PVC/namespace deletion | Volume survives even if the PVC object is deleted. Enforced on every deploy by the `*-pv-retain` post-install hook. |
| **pg_dump backups (off-cluster)** | Full cluster reset / DD VM wipe | `scripts/backup-postgres.sh` → gzipped dump on your Mac |

The first three keep data across pod/component restarts and accidental object
deletion. **Only the off-cluster backup survives a full "Reset Kubernetes
Cluster" / Docker Desktop factory reset**, because the local-path volume lives
inside the DD node and is destroyed when the cluster is recreated.

## Docker Desktop guidance

- Do **not** use Docker Desktop → Settings → Kubernetes → **Reset Kubernetes
  Cluster**. That recreates the cluster and wipes all PV data.
- A normal laptop restart / Docker Desktop restart preserves the cluster and
  volumes. If you must recreate the cluster, restore from a backup afterward.

## Back up now

```bash
bash scripts/backup-postgres.sh                 # → ./backups/agentshield-pg-<ts>.sql.gz
BACKUP_DIR=~/agentshield-backups bash scripts/backup-postgres.sh
```

## Restore after a wipe

```bash
bash scripts/deploy-cpe2e.sh                     # bring the platform back up
bash scripts/restore-postgres.sh                 # restore newest dump (asks to confirm)
kubectl rollout restart deploy/agentshield-registry-api deploy/agentshield-keycloak -n agentshield-platform
```

## Automate backups (macOS launchd)

Create `~/Library/LaunchAgents/com.agentshield.pgbackup.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.agentshield.pgbackup</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/kkalyan/repo/agent-platform/scripts/backup-postgres.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>BACKUP_DIR</key><string>/Users/kkalyan/agentshield-backups</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>19</integer><key>Minute</key><integer>3</integer></dict>
  <key>StandardErrorPath</key><string>/tmp/agentshield-pgbackup.log</string>
  <key>StandardOutPath</key><string>/tmp/agentshield-pgbackup.log</string>
</dict></plist>
```

Then: `launchctl load ~/Library/LaunchAgents/com.agentshield.pgbackup.plist`
(runs daily at 19:03; adjust as needed).

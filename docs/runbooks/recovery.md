# Disaster Recovery — Full Cluster Rebuild + Data Restore

**When to use:** the local dev cluster was wiped or lost — after a Docker Desktop restart, a Kubernetes reset, or a corrupted build. Symptoms: `kubectl get ns` shows no `agentshield-platform`, or all your agents/deployments are gone.

**Hard-won context (why this happens):** this is a **KIND cluster** (Kubernetes-in-Docker — nodes `desktop-control-plane` / `desktop-worker`, 172.18.0.x) running *inside* Docker Desktop, with **`rancher.io/local-path` storage, reclaim policy `Delete`**. All PVC data (Postgres, ClickHouse, MinIO) lives *inside the node containers*. **A Docker Desktop restart can recreate those containers empty → total data loss.** Do not assume PVCs persist. The only real safety net is an **off-cluster Postgres backup** (below).

---

## 0. BEFORE any risky action (backup) — do this first, always

```bash
cd <repo>                         # or the worktree you're in
bash scripts/backup-postgres.sh   # pg_dumpall of ALL dbs → ./backups/agentshield-pg-<ts>.sql.gz
# copy OUTSIDE the repo so it survives a repo/worktree wipe too:
mkdir -p ~/agentshield-backups && cp -v backups/agentshield-pg-*.sql.gz ~/agentshield-backups/
```
Verify the dump is real (macOS gotcha: **`zcat` ≠ gunzip on macOS — use `gunzip -c`**):
```bash
gunzip -c ~/agentshield-backups/agentshield-pg-<ts>.sql.gz | grep -c "^COPY public.agents "   # expect >=1
gunzip -c ~/agentshield-backups/agentshield-pg-<ts>.sql.gz | wc -l                            # expect thousands of lines
```
The backup uses `kubectl` (not `docker`), so it works even when the Docker build daemon is wedged.

**What the Postgres dump covers (fully recoverable):** all 5 DBs — `agentshield` (agents, deployments, versions, tools, **encrypted credentials**, approvals), `keycloak` (users/login), `langfuse` (project + API keys), `langgraph` (HITL checkpoints), `appsmith`.
**What it does NOT cover (lost, but non-critical / reproducible):** Langfuse trace history (ClickHouse), MinIO media. Neither affects your agents/deployments/login.

---

## 1. What is NOT recreated by `deploy-cpe2e.sh` (the out-of-chart steps)

`deploy-cpe2e.sh` rebuilds images, namespaces, **all K8s secrets** (incl. the **fixed encryption key**, so DB-stored encrypted credentials decrypt after restore), helm-deploys everything, and seeds defaults. But these are **host-side / manual** and must be done separately:

| Step | Command | Needed when |
|---|---|---|
| **KIND cluster itself** | *not scripted in repo* — recreate however you first made it (`kind create cluster …` / Docker Desktop Kubernetes toggle) | Only if the cluster is fully gone (nodes missing). A restart usually brings the empty nodes back. |
| **Envoy Gateway controller** | `bash scripts/setup-envoy-gateway.sh` | `envoy-gateway-system` namespace is empty |
| **`gateway-tls` secret** (self-signed cert) | see step 4 | Missing → the HTTPS/443 listener never programs → `gateway-proxy.sh` fails |
| **`:8443` port-forward** | `bash scripts/gateway-proxy.sh` | Every restart (it's a host process; dies with the cluster) |

---

## 2. Full recovery sequence (the exact steps that worked)

Run from the repo (or worktree). Ensure **Docker Desktop is healthy first** (`docker version` responds).

### 2a. Prep helm deps
```bash
cd <repo>
ls charts/agentshield/charts/*.tgz >/dev/null 2>&1 || echo "MISSING subchart .tgz — run: helm dependency update charts/agentshield"
# if you have local template edits to a subchart (e.g. deploy-controller), re-package so they land in the .tgz:
helm package charts/agentshield/charts/deploy-controller -d charts/agentshield/charts/
```

### 2b. Envoy Gateway controller
```bash
bash scripts/setup-envoy-gateway.sh    # idempotent; skips if already installed
```

### 2c. Full platform deploy (builds all images + deploys + seeds)
```bash
bash scripts/deploy-cpe2e.sh           # long — builds images, creates secrets/namespaces, helm deploy, seed
```
Wait for `AgentShield CPE2E Deploy — COMPLETE` and pods Running.

### 2d. Restore your data — ⚠️ SCALE DOWN CONSUMERS FIRST (critical gotcha)
The restore does `DROP DATABASE` / `CREATE DATABASE`. If **any pod is connected** to the DB, the DROP fails, the restore silently falls through, `COPY` hits unique-key conflicts against the seeded rows, and **your data is NOT loaded** (you'll be left with the 5 seed agents, not your real set). So scale the DB consumers to 0 first:
```bash
NS=agentshield-platform
# record current replicas, then scale down every DB consumer:
for d in agentshield-registry-api agentshield-deploy-controller agentshield-scheduler \
         agentshield-event-gateway agentshield-keycloak agentshield-langfuse-web agentshield-langfuse-worker; do
  kubectl get deploy $d -n $NS -o jsonpath="$d={.spec.replicas}{'\n'}"
  kubectl scale deploy/$d --replicas=0 -n $NS
done
# wait until those pods are all gone:
kubectl get pods -n $NS | grep -E "registry-api|deploy-controller|scheduler|event-gateway|keycloak|langfuse"

# (belt-and-suspenders) kill any lingering connections:
PGPW=$(kubectl get secret postgres-passwords -n $NS -o jsonpath='{.data.keycloak}' | base64 -d)
kubectl exec -n $NS agentshield-postgresql-0 -c postgresql -- bash -c \
  "PGPASSWORD='$PGPW' psql -U postgres -tAc \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('agentshield','keycloak','langfuse','langgraph') AND pid <> pg_backend_pid()\""

# now restore (the script prompts 'Type restore to proceed' — pipe it in):
echo restore | bash scripts/restore-postgres.sh ~/agentshield-backups/agentshield-pg-<ts>.sql.gz
# expect to see CREATE DATABASE lines + COPY <N> row counts (NOT skipped)

# scale consumers back to their ORIGINAL replica counts (from the record above):
kubectl scale deploy/agentshield-registry-api --replicas=2 -n $NS
kubectl scale deploy/agentshield-scheduler --replicas=2 -n $NS
kubectl scale deploy/agentshield-event-gateway --replicas=2 -n $NS
for d in agentshield-deploy-controller agentshield-keycloak agentshield-langfuse-web agentshield-langfuse-worker; do
  kubectl scale deploy/$d --replicas=1 -n $NS
done
kubectl rollout status deploy/agentshield-registry-api -n $NS
```

### 2e. `gateway-tls` secret (HTTPS listener) — self-signed cert
The cert files usually survive at `certs/gateway-{cert,key}.pem`. Reuse them; regenerate only if missing:
```bash
cd <repo>; NS=agentshield-platform
if [ ! -f certs/gateway-cert.pem ]; then
  mkdir -p certs
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout certs/gateway-key.pem -out certs/gateway-cert.pem \
    -subj "/CN=agentshield.127.0.0.1.nip.io" \
    -addext "subjectAltName=DNS:agentshield.127.0.0.1.nip.io,DNS:langfuse.127.0.0.1.nip.io"
fi
kubectl create secret tls gateway-tls --cert=certs/gateway-cert.pem --key=certs/gateway-key.pem \
  -n $NS --dry-run=client -o yaml | kubectl apply -f -
# confirm the envoy svc now exposes 443:
kubectl get svc -n envoy-gateway-system | grep agentshield-gateway   # should show 80:... AND 443:...
```

### 2f. Port-forward (UI access on :8443)
```bash
bash scripts/gateway-proxy.sh &        # foreground normally; & or nohup to background it
```

### 2g. Verify recovery
```bash
API=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n agentshield-platform "$API" -c registry-api -- python3 -c \
"import asyncio; from db import AsyncSessionLocal; from sqlalchemy import select,func; from models import Agent,Deployment
async def m():
 async with AsyncSessionLocal() as db:
  print('agents', (await db.execute(select(func.count()).select_from(Agent))).scalar(),
        'deployments', (await db.execute(select(func.count()).select_from(Deployment))).scalar())
asyncio.run(m())"
# UI: open https://agentshield.127.0.0.1.nip.io:8443/ (accept the self-signed cert warning)
```
Deployed agent pods are re-provisioned by the deploy-controller from the restored `deployments` table — they come back on their own.

---

## 3. Gotchas that silently bite (learned the hard way)

- **Restore silently no-ops if any pod is connected to the DB.** Always scale consumers to 0 first (§2d). Symptom: restore says "complete" but you still have only ~5 seed agents.
- **`gateway-tls` missing → no HTTPS listener → `:8443` port-forward fails** with "Service … does not have a service port 443". Create the secret (§2e).
- **macOS `zcat` is for `.Z`, not gzip.** Verify dumps with `gunzip -c`.
- **Never kill a `docker build` mid-flight** — it can wedge the Docker daemon (build hangs, CLI hangs) while the *cluster keeps running*. If wedged: don't restart Docker unless you must (it takes the cluster with it); try cancelling the build in the Docker Desktop GUI (Builds tab) first. Low CPU % in the DD resource bar = the build is hung, not grinding.
- **Images are shared with Docker's store** (no `kind load`; `imagePullPolicy: IfNotPresent`), so they survive a Docker restart as long as Docker's image store does — usually no rebuild needed for unchanged services.

## 4. Key locations
- Backups: `~/agentshield-backups/` (off-repo) **and** `<repo>/backups/`
- Self-signed gateway cert: `<repo>/certs/gateway-{cert,key}.pem`
- Encryption key (fixed, so encrypted DB creds decrypt after restore): hardcoded in `scripts/deploy-cpe2e.sh` (`ENCRYPTION_KEY=...`), applied as secret `agentshield-encryption`

## 5. Open gap to close
The **KIND cluster creation is not scripted in the repo**. If the cluster is fully destroyed, recreation is manual. TODO: add `kind-config.yaml` + `scripts/create-cluster.sh` capturing the 2-node setup so full-cluster loss becomes a scripted recovery.

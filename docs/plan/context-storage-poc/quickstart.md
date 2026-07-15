# Quickstart — Build, Deploy, Verify (POC-0/1)

Repo root: `/Users/kkalyan/repo/agent-platform`. Assumes a running dev cluster (the `agentshield-platform` namespace) and `kubectl` context set.

## 0. Pre-flight

```bash
# Confirm the Alembic head is still 0063 (migration 0064 chains from it).
ls services/registry-api/alembic/versions/ | sort | tail -3
# If a higher number than 0063 exists, renumber 0064 + re-chain down_revision (merge-notes decision 1).
```

## 1. Syntax + mapper checks (after edits, before deploy)

```bash
# Python syntax
python3 -c "import ast; ast.parse(open('services/registry-api/memory.py').read())"
python3 -c "import ast; ast.parse(open('services/registry-api/conversation_store.py').read())"
python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0064_agent_memory_shared_thread.py').read())"
python3 -c "import ast; ast.parse(open('services/declarative-runner/main.py').read())"

# ORM mappers configure (agent_memory columns land cleanly)
cd services/registry-api && python3 -c "import models, routers.memory, sqlalchemy.orm as o; o.configure_mappers(); print('mappers ok')" && cd -
```

## 2. Image tags (bump BOTH files, same commit)

`scripts/deploy-cpe2e.sh` (~L222-228) and `charts/agentshield/values.yaml`:

| Service | Var (deploy-cpe2e.sh) | values.yaml key | 0.x → new |
|---|---|---|---|
| registry-api | `REGISTRY_API_TAG` | `registry-api.image.tag` (~L590) | 0.2.184 → 0.2.185 |
| declarative-runner | `DECLARATIVE_RUNNER_TAG` | `deploy-controller.declarativeRunnerTag` (~L661) | 0.1.46 → 0.1.47 |
| deploy-controller | `DEPLOY_CONTROLLER_TAG` | `deploy-controller.image.tag` (~L652) | 0.1.36 → 0.1.37 |

## 3. Build + deploy (the change is not done until it's deployed)

```bash
bash scripts/deploy-cpe2e.sh          # builds+pushes the 3 images and helm-upgrades
```

Migration 0064 runs via the registry-api alembic init-container on rollout.

## 4. Confirm the checkpointer went persistent (the POC-0 linchpin)

```bash
# deploy-controller must have the direct URL to inject
kubectl exec -n agentshield-platform deploy/agentshield-deploy-controller -- printenv DIRECT_DATABASE_URL | head -c 20; echo

# A freshly deployed agent pod must carry the injected env + use AsyncPostgresSaver
POD=$(kubectl get pods -n agents-platform -l app.kubernetes.io/name=<agent> -o name | head -1)
kubectl exec -n agents-platform "$POD" -- printenv DIRECT_DATABASE_URL AGENTSHIELD_DEPLOYMENT_ID
kubectl logs -n agents-platform "$POD" | grep -i "checkpointer\|AsyncPostgresSaver"   # expect: AsyncPostgresSaver ready
```
If the log shows `MemorySaver` with the URL set → T005 injection didn't land. If the pod crash-loops with "checkpointer init failed" → the fail-loud path fired; fix the URL/pool (research.md §5), do NOT re-add a silent MemorySaver fallback.

## 5. Run the e2e suite

```bash
bash scripts/e2e/suite-75-context-storage.sh          # standalone
# or as part of the full run
bash scripts/e2e/run-all.sh 75                         # if run-all supports arg-filtering; else full run
```
Expected: T-S75-001..005 all PASS (chat threads across turns; survives pod restart; foreign-session 403; workflow B reads A; durable member resumes).

## 6. Env vars introduced/used this slice

| Env | Where set | Purpose |
|---|---|---|
| `DIRECT_DATABASE_URL` | deploy-controller (secret) → injected into agent pods (T005) | persistent `AsyncPostgresSaver` checkpointer |
| `AGENTSHIELD_DEPLOYMENT_ID` | agent pod (manifest_builder, T005) | scope memory reads/writes by deployment |
| `CONVERSATION_STORE` | registry-api (optional; default `postgres`) | selects the `ConversationStore` adapter (choke point) |

## 7. Manual UI check (optional, no new UI in this slice)

There is no new Studio surface in POC-0/1 (attribution UI is POC-2). To eyeball threading: open the existing deployed-agent chat, send "my name is Ada", then "what's my name?" in the same session — the second reply should recall "Ada". Reload the page (same session_id) and re-ask — recall must survive (proves the transcript is backend-persisted, not client state).

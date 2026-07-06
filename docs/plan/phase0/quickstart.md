# Phase 0 — Quickstart (build, deploy, verify)

How to build the changed images, deploy them, and run the e2e gate for the pre-publish evaluation-loop slice.

## Prerequisites
- A running AgentShield CPE2E cluster (namespace `agentshield-platform`) — bring one up with `bash scripts/deploy-cpe2e.sh` if needed.
- `kubectl` context pointing at that cluster; `docker`, `helm`, Node (for `npx tsc`), Python 3.11.
- Repo root: `/Users/kkalyan/repo/agent-platform`.

## Image tags for this slice
Set in `scripts/deploy-cpe2e.sh` (never reuse a tag):
- `REGISTRY_API_TAG` 0.2.34 → **0.2.35** (A1) → **0.2.36** (B1)
- `EVAL_RUNNER_TAG` 0.1.0 → **0.1.1** (A2)
- `STUDIO_TAG` 0.1.31 → **0.1.32** (B2)

---

## 1. Per-file verification (run after each edit, before building)

Python (any modified backend / eval-runner file):
```bash
cd /Users/kkalyan/repo/agent-platform
for f in \
  services/registry-api/routers/playground.py \
  services/registry-api/routers/deployments.py \
  services/registry-api/routers/agents.py \
  services/registry-api/models.py \
  services/registry-api/schemas.py \
  services/registry-api/alembic/versions/0015_deployments_env_add_sandbox.py \
  services/eval-runner/main.py ; do
  python3 -c "import ast; ast.parse(open('$f').read())" && echo "ok $f"
done
```

TypeScript (Studio):
```bash
cd /Users/kkalyan/repo/agent-platform/studio && npx tsc --noEmit
grep -n "eval_passed" src/pages/DeployAgentPage.tsx   # expect NO matches
```

Make e2e scripts executable:
```bash
cd /Users/kkalyan/repo/agent-platform
chmod +x scripts/e2e/suite-8-playground.sh scripts/e2e/suite-9-eval.sh \
         scripts/e2e/suite-17-eval-gate.sh scripts/e2e/suite-6-asset-lifecycle.sh \
         scripts/e2e/suite-14-consumer-chat.sh scripts/e2e/suite-15-artifact-isolation.sh \
         scripts/e2e/run-all.sh
```

---

## 2. Build + deploy

### Option A — full deploy (simplest; also runs the `0015` migration via the init container)
```bash
cd /Users/kkalyan/repo/agent-platform
bash scripts/deploy-cpe2e.sh
```

### Option B — targeted rebuild of only the changed images (faster)
```bash
cd /Users/kkalyan/repo/agent-platform
NS=agentshield-platform

# registry-api (Slice A + B) — carries migration 0015 (runs in the init container)
docker build -t registry.internal/agentshield/registry-api:0.2.36 services/registry-api/
kubectl set image deploy/agentshield-registry-api \
  registry-api=registry.internal/agentshield/registry-api:0.2.36 -n $NS
kubectl rollout status deploy/agentshield-registry-api -n $NS --timeout=5m

# eval-runner (Slice A) — Job image; picked up on the next eval-run.
# k8s.py reads EVAL_RUNNER_IMAGE; ensure it resolves to :0.1.1 (env/Helm) or rebuild+load the tag.
docker build -t registry.internal/agentshield/eval-runner:0.1.1 services/eval-runner/

# studio (Slice B)
docker build -t registry.internal/agentshield/studio:0.1.32 studio/
kubectl set image deploy/agentshield-studio \
  studio=registry.internal/agentshield/studio:0.1.32 -n $NS
kubectl rollout status deploy/agentshield-studio -n $NS --timeout=3m
```
> For a local/kind cluster, load images into the cluster (e.g. `kind load docker-image …`) if they are not pushed to a registry the nodes can pull from. The eval-runner Job image must be pullable by the cluster for the real end-to-end tests (T-S9-013/014) to run; otherwise those degrade to MANUAL.

Confirm the migration applied:
```bash
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- \
  python3 -c "import asyncio,db; from sqlalchemy import text; \
asyncio.run((lambda: db.AsyncSessionLocal().__aenter__())())" 2>/dev/null || true
# Or check the constraint directly on Postgres:
kubectl exec -n agentshield-platform statefulset/agentshield-postgresql -- \
  psql -U postgres -d agentshield -c \
  "select conname, pg_get_constraintdef(oid) from pg_constraint where conname='ck_deployments_env';"
# Expect: CHECK (environment IN ('production','staging','canary','sandbox'))
```

---

## 3. Run the e2e suites

Individual suites (during development):
```bash
cd /Users/kkalyan/repo/agent-platform
NAMESPACE=agentshield-platform bash scripts/e2e/suite-8-playground.sh   # Slice A: bypass, 403, GET run judge fields
NAMESPACE=agentshield-platform bash scripts/e2e/suite-9-eval.sh         # Slice A: batch eval end-to-end + resilience
NAMESPACE=agentshield-platform bash scripts/e2e/suite-17-eval-gate.sh   # Slice B: deploy/publish gate matrix
NAMESPACE=agentshield-platform bash scripts/e2e/suite-6-asset-lifecycle.sh
NAMESPACE=agentshield-platform bash scripts/e2e/suite-14-consumer-chat.sh
NAMESPACE=agentshield-platform bash scripts/e2e/suite-15-artifact-isolation.sh
```

Completion gate (Task Z — MUST be green):
```bash
NAMESPACE=agentshield-platform bash scripts/e2e/run-all.sh
# Expect: "STATUS: ALL PASS", exit 0
```

---

## 4. What each Phase 0 test proves (map to the developer loop)
- **Deploy to sandbox, ungated:** T-S17-001 / T-S17-007 — create a version without `eval_passed`, deploy `environment=sandbox` → 201.
- **Evaluate:** T-S8-022/024, T-S9-011/012/013/014 — eval-runner (service identity) runs agents it doesn't own, batch eval completes `running→completed`, one bad item doesn't crash the Job, judge fields are readable.
- **Publish, gated:** T-S17-003/004/005/006 — publish 422 until the latest version is `eval_passed=true` (and adversarial where risky), then 202.
- **Production still protected:** T-S17-002, T-S6-LG-001/002 — production deploy stays eval/adversarial-gated.

## 5. Manual checks (environment-dependent)
- **Studio label:** open Studio → Agents → *(agent)* → Deploy; Step 2 reads **"Deploy to Sandbox"** with the "Ungated test deploy…" subtext; deploying an agent whose version has `eval_passed=false` succeeds; the history row shows `environment=sandbox`.
- **Haiku judge value (T-S9-015):** with a real agent deployment and an Anthropic key configured for the team, re-run suite-9's batch eval and confirm a result has `reasoning=='llm-judge (haiku)'` and its `judge_score` matches the source run's Haiku score.

## Rollback
Redeploy the previous tags (`registry-api:0.2.34`, `eval-runner:0.1.0`, `studio:0.1.31`) and `alembic downgrade -1` (drops `sandbox` from the CHECK). Note: downgrade fails if any deployment rows still have `environment='sandbox'` — delete or repoint them first.

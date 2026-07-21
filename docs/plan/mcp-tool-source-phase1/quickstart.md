# Quickstart — MCP as a Tool Source, Phase 1

> **All build/deploy steps here are DEFERRED for the planning run** (the plan produces artifacts only). They are recorded so a later implementer can run them. Do not `helm`/`kubectl`/`docker` while only producing the plan.

## Prerequisites

- Local k8s already running the AgentShield platform (Docker Desktop k8s; `charts/agentshield` via `scripts/deploy-cpe2e.sh`). Baseline stack up (`bash scripts/e2e/suite-1-health.sh` green).
- `kubectl` at that cluster, namespace `agentshield-platform`.
- Python 3.12 (matches every service's Dockerfile) + `pip` for local iteration on `services/mcp-proxy`.
- Node/npm for Studio.
- `docker` for the new `mcp-proxy` image and the rebuilt `registry-api`/`declarative-runner`/`studio`/`safety-orchestrator`/`deploy-controller` images.

## One-time setup for this feature

1. **Pin the `mcp` SDK.** research.md B1 recommends `mcp>=1.2,<2.0`; verify the latest stable minor at build:
   ```bash
   pip index versions mcp
   ```
   Pin `services/mcp-proxy/requirements.txt` to what that reports (do not jump a major).

2. **Confirm the migration head before creating `0072`.** The head moved since an earlier draft (there is **no `0069`**; the chain is `0071 → 0070 → 0068`). Confirm `0071` is still head:
   ```bash
   ls services/registry-api/alembic/versions/ | sort | tail -4
   kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- alembic current   # should show 0071
   ```
   If a `0072`+ already landed on a shared branch, take the next free number and update `down_revision`.

3. **Confirm the current image tags before bumping** (they advanced since the earlier draft — captured baseline: `REGISTRY_API_TAG=0.2.224`, `STUDIO_TAG=0.1.160`, `DECLARATIVE_RUNNER_TAG=0.1.59`, `SAFETY_ORCHESTRATOR_TAG=0.1.3`, `DEPLOY_CONTROLLER_TAG=0.1.40`, `PYTHON_EXECUTOR_TAG=0.1.0`; new `MCP_PROXY_TAG=0.1.0`):
   ```bash
   grep -E '^(REGISTRY_API_TAG|STUDIO_TAG|DECLARATIVE_RUNNER_TAG|SAFETY_ORCHESTRATOR_TAG|DEPLOY_CONTROLLER_TAG|PYTHON_EXECUTOR_TAG)=' scripts/deploy-cpe2e.sh
   ```
   Never reuse a claimed tag; mirror each bump in `charts/agentshield/values.yaml` (and, for `mcp-proxy`, in both the parent `mcp-proxy.image.tag` and the sub-chart `charts/agentshield/charts/mcp-proxy/values.yaml`).

4. **Confirm the e2e suite number.** `suite-81/82/83` are taken; this plan uses `suite-84`. If 84 is claimed by build time, take the next free number and rename the `T-S84-*` IDs.
   ```bash
   ls scripts/e2e/ | grep -oE 'suite-[0-9]+' | sed 's/suite-//' | sort -n | tail -3
   ```

## Running MCP Proxy locally (fast iteration on Tasks 5/7/8)

`services/mcp-proxy` has **no DB dependency** — its wire-protocol and session-cache logic is testable standalone. Only credential resolution (a K8s Secret read) and AuthN (TokenReview) need a cluster; both can be stubbed for local unit iteration.

```bash
cd services/mcp-proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export REGISTRY_API_URL=http://localhost:8000       # for the cross-team authz callback
export MCP_SECRETS_NAMESPACE=agentshield-mcp
export MCP_PROXY_AUDIENCE=agentshield-mcp-proxy
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

To exercise `/internal/discover` against a **real** local MCP server without a cluster, run the fixture and (locally) stub `credentials.read_server_secret` to return a `ServerConnection` pointing at it:

```bash
# separate terminal — the same fixture suite-84 uses (Task 15)
python3 scripts/e2e/fixtures/stub_mcp_server.py --port 9999
```

For the full authenticated path (TokenReview + real per-server Secret), port-forward registry-api and run against the cluster:
```bash
kubectl port-forward -n agentshield-platform svc/agentshield-registry-api 8000:8000
```

## Building and deploying (DEFERRED)

```bash
bash scripts/deploy-cpe2e.sh
```
Builds every image whose tag it declares (including the new `mcp-proxy` once Task 5 adds its `docker build services/mcp-proxy/` line) and runs `helm upgrade --install` with tags baked into `charts/agentshield/values.yaml` (no `--set`) — a tag bump not mirrored there is a no-op deploy. The new `agentshield-mcp` namespace and the proxy's RBAC come from the sub-chart. Watch the proxy:
```bash
kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=mcp-proxy -w
kubectl logs  -n agentshield-platform -l app.kubernetes.io/name=mcp-proxy -f
```
Confirm the proxy's least-privilege RBAC:
```bash
kubectl auth can-i --as=system:serviceaccount:agentshield-platform:agentshield-mcp-proxy get secrets -n agentshield-mcp          # yes
kubectl auth can-i --as=system:serviceaccount:agentshield-platform:agentshield-mcp-proxy get secrets -n agentshield-platform     # NO
kubectl auth can-i --as=system:serviceaccount:agentshield-platform:agentshield-mcp-proxy create tokenreviews.authentication.k8s.io  # yes
```

## Running the new backend e2e suite (Task 15, DEFERRED)

```bash
bash scripts/e2e/suite-84-mcp-tools.sh          # standalone until Task 15 registers it
bash scripts/e2e/run-all.sh                     # full run incl. 84
```

## Running the regression sweep (Task 17, DEFERRED)

The blast-radius suites Task 11's `governed_tool` change touches:
```bash
bash scripts/e2e/suite-3-safety.sh
bash scripts/e2e/suite-4-hitl.sh
bash scripts/e2e/suite-18-opa-governance.sh
bash scripts/e2e/suite-74-eval-v2-side-effects.sh
bash scripts/e2e/suite-81-deploy-tool-autograt.sh
```
All five must stay green **after** Task 11, not just `suite-84`. `opa test` runs locally (not deferred):
```bash
opa test services/registry-api/opa_policy/ -v     # Task 9 — new allow_deanonymize cases
```

## Running Studio tests

```bash
cd studio && npm run test        # Vitest — Tasks 12/13/14 new/changed *.test.tsx
cd studio && npm run typecheck   # mandatory after any frontend change
bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts   # Playwright (Task 16, DEFERRED — against deployed Studio)
```
First-time Playwright: `cd studio && npx playwright install chromium`. The spec authenticates as `platform-admin` via real Keycloak (`e2e/global-setup.ts`, unchanged); if a REST fixture 401s, re-verify the current platform-admin `sub` with `GET /api/v1/me` (the realm has been re-seeded before — a known gotcha, not new to this feature).

# Quickstart — MCP as a Tool Source, Phase 1

## Prerequisites

- Local k8s cluster already running the AgentShield platform (Docker Desktop k8s, per this repo's usual dev setup — `charts/agentshield` deployed via `scripts/deploy-cpe2e.sh`). This plan does not stand up the platform from scratch; it assumes the baseline stack (`registry-api`, `deploy-controller`, Postgres, Keycloak, OPA sidecar/bundle server) is already up and healthy (`bash scripts/e2e/suite-1-health.sh` green).
- `kubectl` pointed at that cluster, namespace `agentshield-platform`.
- Python 3.12 (matches every other service's `Dockerfile` base image) with `pip` for local unit iteration on `services/mcp-proxy` before containerizing.
- Node/npm for Studio (`studio/package.json` — same toolchain the rest of Studio already uses).
- `docker` for building the new `mcp-proxy` image and rebuilt `registry-api`/`declarative-runner`/`studio`/`safety-orchestrator` images.

## One-time setup for this feature

1. **Verify the `mcp` Python SDK version to pin.** Research.md B1 recommends `mcp>=1.2,<2.0` but the exact latest stable minor should be checked at implementation time:
   ```bash
   pip index versions mcp
   ```
   Pin `services/mcp-proxy/requirements.txt` to whatever that command reports as latest-stable at the time, not blindly to the number in this doc.

2. **Confirm the current baseline image tags before bumping anything** (other work may have landed on `phase-1`/main since this plan was written — captured baseline: `REGISTRY_API_TAG="0.2.210"`, `STUDIO_TAG="0.1.158"`, `DECLARATIVE_RUNNER_TAG="0.1.59"`, `SAFETY_ORCHESTRATOR_TAG="0.1.3"`):
   ```bash
   grep -E '^(REGISTRY_API_TAG|STUDIO_TAG|DECLARATIVE_RUNNER_TAG|SAFETY_ORCHESTRATOR_TAG)=' scripts/deploy-cpe2e.sh
   ```
   Never reuse a tag another change already claimed — always bump from whatever this command reports right before you build.

3. **Confirm migration `0068` is still the head** before creating `0069` (someone else may have added `0069` already on a shared branch):
   ```bash
   ls services/registry-api/alembic/versions/ | sort | tail -3
   ```

## Running MCP Proxy locally (outside k8s, for fast iteration on Tasks 3/6/7)

`services/mcp-proxy` has no hard runtime dependency on being *in* the cluster for its own unit-level logic (the wire-protocol and session-cache code is testable standalone) — only credential resolution needs a running `registry-api` + K8s API to be meaningful.

```bash
cd services/mcp-proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export REGISTRY_API_URL=http://localhost:8000   # port-forward registry-api first, see below
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

To exercise `/internal/discover` against a **real** local MCP server without needing registry-api or K8s at all (fast inner-loop for `mcp_client.py`/`session_cache.py`), start the fixture stub server from the same terminal (or a second one) and register a server row directly via the DB, or simpler — hit the proxy with a `server_id` that doesn't exist yet and confirm it fails cleanly (`502`) before wiring the real DB round-trip; then port-forward registry-api and re-test the full path:

```bash
# separate terminal — the same fixture the e2e suite uses (Task 12)
python3 scripts/e2e/fixtures/stub_mcp_server.py --port 9999

# separate terminal
kubectl port-forward -n agentshield-platform svc/agentshield-registry-api 8000:8000
```

## Building and deploying the new/changed images

Once code changes for a task land, rebuild only the images that task touches (per-task tag bumps — see plan.md's task list for the exact tag each task bumps to) and redeploy:

```bash
bash scripts/deploy-cpe2e.sh
```

This script builds every image whose tag it declares (including the new `mcp-proxy` once Task 3 adds its `docker build` line) and runs `helm upgrade --install` with no `--set` flags — every override lives in `charts/agentshield/values.yaml`, so a tag bump that isn't also mirrored there is a no-op deploy (CLAUDE.md's explicit warning, and this repo has been bitten by exactly this before per the `event-gateway` comment in `values.yaml`).

Watch the new pod come up:
```bash
kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=mcp-proxy -w
kubectl logs -n agentshield-platform -l app.kubernetes.io/name=mcp-proxy -f
```

## Running the new backend e2e suite (Task 12)

```bash
bash scripts/e2e/suite-82-mcp-tools.sh
```
Standalone (not yet wired into `run-all.sh` until Task 12 adds it there — until then, run it directly). Once registered:
```bash
bash scripts/e2e/run-all.sh   # runs every suite including 82
```

## Running the regression sweep (Task 14)

The blast-radius suites this plan's `governed_tool` change touches (native/http/python tool-call governance, HITL, safety scanning, the deploy-time auto-grant this design's precondition depends on):
```bash
bash scripts/e2e/suite-3-safety.sh
bash scripts/e2e/suite-4-hitl.sh
bash scripts/e2e/suite-18-opa-governance.sh
bash scripts/e2e/suite-74-eval-v2-side-effects.sh
bash scripts/e2e/suite-81-deploy-tool-autograt.sh
```
All five must stay green **after** Task 8 lands, not just the new `suite-82`. See plan.md Task 14 for what "impacted" means here and why this list, specifically.

## Running Studio tests

Component tests (Vitest — Tasks 9/10/11's new/changed `*.test.tsx` files):
```bash
cd studio && npm run test
```

Type-check (mandatory after any frontend change, per this repo's CLAUDE.md):
```bash
cd studio && npm run typecheck
```

Browser e2e (Playwright — Task 13's new `mcp-servers.spec.ts`), against the deployed Studio:
```bash
bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts
```
First-time Playwright browser install, if not already done on this machine:
```bash
cd studio && npx playwright install chromium
```
The spec authenticates as `platform-admin` through the real Keycloak login (`e2e/global-setup.ts` — no changes needed there); any REST fixture setup inside the spec that needs a caller identity uses the current platform-admin Keycloak `sub` (`047fad5f-f38c-430a-bfba-6e4d9009314b` as of this writing — re-verify with `GET /api/v1/me` against the live realm if the spec 401s, since the realm has been re-seeded before and invalidated an older hardcoded sub; this is a known, previously-hit gotcha, not new to this feature).

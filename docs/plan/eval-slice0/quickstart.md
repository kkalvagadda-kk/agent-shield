# Eval Slice 0 — Quickstart

Everything an implementer needs to run, test, and ship this slice. Commands are copy-pasteable from the
repo root of the **`mcp-tool-source` worktree**:
`/Users/kkalyan/repo/agent-platform/.claude/worktrees/mcp-tool-source`.

---

## Prerequisites

| Tool | Version / note |
|---|---|
| Node | as pinned by `studio/package.json`; deps already installed (`studio/node_modules` is a symlink to the primary worktree) |
| Python | 3.12 — matches the registry-api image |
| kubectl | context `test-cluster-964-10086`, namespace `agentshield-platform` |
| AWS CLI | profile `kkalyan-aws-key`, ECR in `us-west-2` |
| Playwright | first run only: `cd studio && npx playwright install chromium` |
| VPN | **required** — the EKS NLB is internal, no public IP |

```bash
export KUBECONFIG=~/.kube/test-cluster-kube-config.yaml
export AWS_PROFILE=kkalyan-aws-key
export STUDIO_E2E_GATEWAY_URL="https://k8s-envoygat-envoyage-6676b8bb93-7541836717beafbe.elb.us-west-2.amazonaws.com"
```

---

## Fast inner loop (no cluster)

```bash
cd studio
npm run typecheck                                   # tsc --noEmit — must be clean
npx vitest run src/lib/evalVerdict.test.ts          # T-1/T-2
npx vitest run src/pages/AdminPublishRequestsPage.test.tsx src/pages/DatasetsPage.test.tsx
npm run test                                        # full suite before any commit
```

Python syntax + mapper check after backend edits:

```bash
cd services/registry-api
python3 -c "import ast; ast.parse(open('routers/admin.py').read()); ast.parse(open('schemas.py').read())"
```

---

## Cluster loop

Suites `kubectl exec` into a **Running** registry-api pod. Always keep the phase filter — this cluster
carries thousands of Evicted pods and `.items[0]` without it picks a dead one
(`docs/bugs/e2e-suites-that-could-never-run.md`).

```bash
kubectl get pods -n agentshield-platform \
  -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}'
```

### Run the tests

```bash
bash scripts/e2e/suite-89-publish-queue-verdict.sh     # the new suite
bash scripts/run-tests.sh --layer api --group eval     # API layer, eval group
bash scripts/run-tests.sh --layer browser --group eval # Playwright (needs the gateway URL)
bash scripts/run-tests.sh --audit                      # every suite/spec registered
```

---

## Build & deploy

Bump **all three** tag sites or `suite-79 T-S79-002` (served tag == chart == pod) goes red:

| File | Change |
|---|---|
| `scripts/deploy-cpe2e.sh` | `REGISTRY_API_TAG` 0.2.233 → **0.2.234**, `STUDIO_TAG` 0.1.166 → **0.1.167** |
| `charts/agentshield/values.yaml` | mirror both (registry-api ~L503, studio ~L1123) |
| `studio/src/lib/build.ts` | `STUDIO_BUILD = "0.1.167"` |

```bash
KUBECONFIG=~/.kube/test-cluster-kube-config.yaml AWS_PROFILE=kkalyan-aws-key \
  bash scripts/deploy-eks.sh
```

Images build **locally on Docker Desktop** then push to ECR; EKS only runs them. A build failure is a
local Docker problem, not a wrong-cluster problem.

Verify the rollout actually landed:

```bash
kubectl get pods -n agentshield-platform \
  -l app.kubernetes.io/name=studio --field-selector=status.phase=Running \
  -o 'custom-columns=NAME:.metadata.name,IMAGE:.spec.containers[0].image' --no-headers
```

---

## Known environment quirks

- **registry-api has no `startupProbe`.** Two uvicorn workers under a 500m CPU limit can exceed the
  liveness `initialDelaySeconds=15` on cold start, so a replica may CrashLoop once or twice mid-rollout
  before settling. Wait for rollout before running suites; a `Connection refused` right after deploy is
  this, not your change.
- **`Chart.lock` drifts on every deploy** (`Chart.yaml` pins `18.x.x`/`27.x.x`). Revert it unless you
  intend a subchart upgrade: `git checkout charts/agentshield/Chart.lock`.
- **Playwright `hasText` is a substring match.** Use `has: page.getByText(x, { exact: true })` when a
  short fixture name could match a longer string.
- **`studio/e2e/*.spec.ts` is NOT typechecked** — `studio/tsconfig.json` includes only `src`. A typo
  passes `npm run typecheck` and fails at runtime. Run the spec.

---

## Definition of Done (before reporting complete)

- [ ] `T-S89-001` and `T-S89-005/006` were **observed failing** against unfixed code (DoD rule 7)
- [ ] `npm run typecheck` clean, `npm run test` green
- [ ] `--layer api --group eval` green, incl. the rewritten `T-S80-000b`
- [ ] `--layer browser --group eval` green
- [ ] `--audit` clean
- [ ] Both `docs/bugs/` postmortems written **in the same commit** as their fix
- [ ] All three tag sites bumped and the served tag verified on the live pod
- [ ] Gap ledger updated; Decision 33 **option B left open**

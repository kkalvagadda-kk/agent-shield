# AgentShield

AgentShield is a self-hosted Kubernetes platform that standardizes safety scanning, policy enforcement, human approval gates, observability, and deployment for AI agents — with zero SaaS dependencies. Teams bring their own agents (via the Python SDK or the no-code visual builder in Studio) and the platform wraps every tool call with OPA-based policy evaluation, configurable HITL approval gates, and Langfuse tracing.

**Core capabilities:**
- OPA policy enforcement (per-agent, per-tool, per-team) + HITL approval gates
- Input/output safety scanning via Safety Orchestrator (NeMo Guardrails)
- Execution modes: reactive (one-shot), durable (checkpointed), scheduled (cron), event-driven (webhook)
- Composite workflows — multiple agents wired into a run tree, orchestrated by the platform
- Agent memory (PostgreSQL + pgvector embeddings)
- Observability via Langfuse (traces, scores, datasets, evals)
- K8s-native agent deployment — the Deploy Controller reconciles agent pods; machine identity via K8s ServiceAccounts

---

## Architecture / Components

| Component | Directory | What it does |
|-----------|-----------|--------------|
| **registry-api** | `services/registry-api/` | FastAPI backend — agents, tools, teams, deployments, runs, evals, events, OPA bundle generation |
| **deploy-controller** | `services/deploy-controller/` | K8s operator that reconciles agent Deployment objects |
| **declarative-runner** | `services/declarative-runner/` | Generic runner that interprets workflow JSON for no-code agents |
| **scheduler** | `services/scheduler/` | Fires scheduled agents on cron (HA, Redis-backed) |
| **event-gateway** | `services/event-gateway/` | Public webhook ingress — token auth, rate limiting, replay, dispatch |
| **safety-orchestrator** | `services/safety-orchestrator/` | NeMo Guardrails-based input/output scan; per-scanner Langfuse spans |
| **python-executor** | `services/python-executor/` | Sandboxed Python tool runner (sidecar) |
| **eval-runner** | `services/eval-runner/` | Batch eval K8s Job runner (LLM-as-Judge, Haiku) |
| **echo-agent** | `services/echo-agent/` | Reference agent for integration testing |
| **Studio** | `studio/` | React + Vite frontend — agent catalog, visual builder, Playground, settings |
| **SDK** | `sdk/agentshield_sdk/` | Python SDK for building governed agents with LangGraph |

For architecture depth see [`docs/spec.md`](docs/spec.md) and the decision log in [`docs/decisions.md`](docs/decisions.md) (22 decisions covering auth, execution, lifecycle, and more).

---

## Prerequisites

| Tool | Version / notes |
|------|----------------|
| Docker Desktop | Kubernetes enabled (agentshield targets the local kind-based cluster Docker Desktop ships) |
| `kubectl` | Bundled with Docker Desktop or install separately |
| `helm` | v3 |
| Node.js | 20 (the Studio Dockerfile uses `node:20-alpine`; use `nvm` and pin to 20 locally) |
| Python | ≥ 3.12 (SDK `requires-python = ">=3.12"`) |
| `docker` CLI | For building images locally |

---

## Deploy locally

The canonical deploy path is a single script that builds all images into the local Docker daemon, creates namespace and secrets, and runs `helm upgrade --install`:

```bash
bash scripts/deploy-cpe2e.sh
```

What it does (8 steps):
1. Builds all service images tagged into `registry.internal/agentshield/` (Docker Desktop shares the daemon, so `imagePullPolicy: IfNotPresent` resolves them without a registry)
2. Applies namespaces (`agentshield-platform`, `agents-platform`, `agentshield-playground`) and RBAC
3. Creates all required secrets (Postgres, Redis, MinIO, Keycloak, encryption key)
4. Applies OPA bundle server config and sidecar configmaps
5. Runs `helm upgrade --install agentshield charts/agentshield --namespace agentshield-platform --create-namespace --reset-values`
6. Waits for rollouts (Postgres, Redis, registry-api, studio, scheduler, Langfuse)
7. Creates Langfuse MinIO bucket and seeds default teams (`platform`, `operations`)
8. Seeds default resources: 6 tools, 2 skills, 3 agent graphs, 5 agents

**Dev credentials** are hardcoded at the top of `scripts/deploy-cpe2e.sh`. Change them before any non-local use. See the [Dev Credentials](#dev-credentials) section below for the full list.

**Alternative (no image builds):** If images are already built and `charts/agentshield/values.yaml` has the correct tags, a plain helm command is enough:

```bash
helm upgrade --install agentshield charts/agentshield \
  --namespace agentshield-platform \
  --create-namespace \
  --reset-values
```

**Image tag tracking:** Image tag vars (`REGISTRY_API_TAG`, `STUDIO_TAG`, etc.) live in `scripts/deploy-cpe2e.sh`. Keep them in sync with the tags baked into `charts/agentshield/values.yaml`. Never reuse an existing tag — Kubernetes caches by tag.

---

## Access Studio

### Via Envoy Gateway (recommended)

All services are routed through a single Envoy Gateway ingress with path-based routing. One port-forward gives you everything:

```bash
# Install Envoy Gateway controller (one-time)
bash scripts/setup-envoy-gateway.sh

# Deploy the chart (creates Gateway + HTTPRoutes)
bash scripts/deploy-cpe2e.sh

# Start the gateway proxy (local dev only — forwards localhost:8443 → Gateway HTTPS)
bash scripts/gateway-proxy.sh
```

Open [https://agentshield.127.0.0.1.nip.io:8443](https://agentshield.127.0.0.1.nip.io:8443) — accept the self-signed cert warning once.

All services accessible via the Gateway:

| URL | Service |
|-----|---------|
| `https://agentshield.127.0.0.1.nip.io:8443/` | Studio |
| `https://agentshield.127.0.0.1.nip.io:8443/api/` | Registry API |
| `https://agentshield.127.0.0.1.nip.io:8443/realms/` | Keycloak |
| `https://langfuse.127.0.0.1.nip.io:8443/` | Langfuse (subdomain) |
| `https://agentshield.127.0.0.1.nip.io:8443/minio/` | MinIO Console |
| `https://agentshield.127.0.0.1.nip.io:8443/webhooks/` | Event Gateway |

Langfuse uses a subdomain (not a path prefix) because Next.js requires `basePath` baked at build time — assets and internal API routes break behind a prefix without it.

Log in as `platform-admin` / `PlatformAdmin2024` (see [Dev Credentials](#dev-credentials)).

### Via individual port-forwards (fallback)

If you don't need the unified Gateway:

```bash
kubectl port-forward -n agentshield-platform svc/agentshield-studio 8080:80
kubectl port-forward -n agentshield-platform svc/agentshield-registry-api 8000:8000
kubectl port-forward -n agentshield-platform svc/agentshield-langfuse-web 4000:3000
```

---

## Deploying on EKS

On EKS the Gateway gets a real LoadBalancer address — no port-forward needed. Override these values:

```yaml
# values-eks.yaml
global:
  publicUrl: "https://agentshield.yourcompany.com"
  langfuseUrl: "https://langfuse.yourcompany.com"

envoy-gateway:
  gateway:
    hostname: "agentshield.yourcompany.com"
    langfuseHostname: "langfuse.yourcompany.com"
    tls:
      secretName: gateway-tls  # populated by cert-manager (wildcard or multi-SAN)

# Langfuse subchart is packaged — can't template from global, must set explicitly
langfuse:
  langfuse:
    nextauth:
      url: "https://langfuse.yourcompany.com"
```

```bash
helm upgrade --install agentshield charts/agentshield \
  -n agentshield-platform --create-namespace \
  -f charts/agentshield/values-eks.yaml
```

**What the global values drive:**

| Service | Env var / config | Source |
|---------|-----------------|--------|
| Keycloak | `KC_HOSTNAME` | `global.publicUrl` (template) |
| Registry API | `LANGFUSE_PUBLIC_URL` | `global.langfuseUrl` (template) |
| Langfuse | `NEXTAUTH_URL` | Must be set manually (packaged subchart) |
| Gateway | main hostname | `envoy-gateway.gateway.hostname` |
| Gateway | langfuse listener | `envoy-gateway.gateway.langfuseHostname` |

**What changes from local dev:**

| Concern | Local (kind/Docker Desktop) | EKS |
|---------|----------------------------|-----|
| TLS cert | Self-signed (`certs/`) | cert-manager + Let's Encrypt (or ACM) |
| DNS | nip.io (auto-resolves to 127.0.0.1) | Route53 CNAME → NLB address |
| Port | `:8443` via port-forward | Standard `:443` (no port in URL) |
| `global.publicUrl` | `https://agentshield.127.0.0.1.nip.io:8443` | `https://agentshield.yourcompany.com` |
| `global.langfuseUrl` | `https://langfuse.127.0.0.1.nip.io:8443` | `https://langfuse.yourcompany.com` |
| `gateway-proxy.sh` | Required | Not needed |

**EKS prerequisites:**

1. Envoy Gateway controller installed: `bash scripts/setup-envoy-gateway.sh`
2. cert-manager with a ClusterIssuer for Let's Encrypt (or create `gateway-tls` secret manually — must cover both `agentshield.yourcompany.com` and `langfuse.yourcompany.com`)
3. DNS records pointing both hostnames at the NLB address (`kubectl get gateway -n agentshield-platform -o jsonpath='{.items[0].status.addresses[0].value}'`)
4. Optional: External-DNS controller to auto-create Route53 records from Gateway hostnames

**Keycloak notes:**
- `KC_HOSTNAME` is derived from `global.publicUrl` — one override handles both Gateway and Keycloak
- `KC_HOSTNAME_STRICT=true` and `KC_PROXY_HEADERS=xforwarded` remain set (Envoy terminates TLS, Keycloak sees HTTP internally)
- No port suffix needed when using standard 443

**Langfuse notes:**
- Langfuse runs on a **subdomain** (`langfuse.127.0.0.1.nip.io` / `langfuse.yourcompany.com`) — not a path prefix. Next.js requires `basePath` baked at build time; without it, assets and internal API routes break behind a prefix.
- Trace links in Studio point to `<langfuseUrl>/project/<pid>/traces/<id>`
- `NEXTAUTH_URL` must be set manually in `langfuse.langfuse.nextauth.url` (packaged subchart can't template from global)
- TLS cert must cover both hostnames (wildcard `*.yourcompany.com` or multi-SAN)

---

## Dev Credentials

All credentials below are dev defaults baked into `scripts/deploy-cpe2e.sh`. Change them before any non-local deployment.

### Platform login (Studio)

| User | Username | Password |
|------|----------|----------|
| Platform Admin | `platform-admin` | `PlatformAdmin2024` |
| Agent Reviewer | `agent-reviewer` | `Reviewer2024` |

### Keycloak Admin Console

| URL | Username | Password |
|-----|----------|----------|
| `http://localhost:8080/admin` (port-forward Keycloak) | `admin` | `AdminPass2024` |

### Langfuse

| URL | Email | Password |
|-----|-------|----------|
| `http://localhost:4000` | `admin@agentshield.local` | `AdminPass2024` |

API keys: `pk-lf-agentshield-dev-local-0001` / `sk-lf-agentshield-dev-local-0001`

### Infrastructure

| Service | Username | Password |
|---------|----------|----------|
| PostgreSQL | `postgres` | `DevPass2024` |
| Redis | — | `RedisPass2024` |
| MinIO | `agentshield-admin` | `MinioPass2024` |

---

## Testing

Three independent gates. Run them in order after a deploy.

### 1. Backend API e2e (bash + curl, against the live cluster)

```bash
bash scripts/e2e/run-all.sh
```

29 suites covering: platform health, agent lifecycle, safety scanning, HITL flows, asset lifecycle, machine identity, playground, eval runner, multi-agent handoffs, resilience, quarantine, observability, consumer chat, artifact isolation, agent creation, eval gate, OPA governance, execution shapes, durable/scheduled/event-driven modes, production runs, memory, scheduler, alerting, event gateway, composite workflows.

Accepts `--auto-pf` to auto-setup port-forwards for suites that need them. Target namespace defaults to `agentshield-platform`; override with `NAMESPACE=<ns>`.

### 2. Studio component tests (Vitest + React Testing Library)

```bash
cd studio
npm install
npm run test          # run once
npm run test:cov      # with coverage report
npm run test:watch    # interactive watch mode
```

### 3. Studio browser E2E (Playwright, real Keycloak login)

Install Chromium once:

```bash
npx playwright install chromium
```

Then run against the deployed cluster:

```bash
bash scripts/studio-e2e.sh                        # all specs
bash scripts/studio-e2e.sh e2e/workflows.spec.ts  # one spec file
```

The script port-forwards Studio to `:8080`, waits for the SPA to be reachable, then invokes `npx playwright test`. This is a separate gate from `run-all.sh` — it exercises real browser login flows (agents, workflows, playground, smoke tests).

### Type and syntax checks

```bash
# TypeScript
cd studio && npm run typecheck

# Python (per-file syntax check)
python3 -c "import ast; ast.parse(open('services/registry-api/main.py').read())"
```

### 4. Manual UI walkthrough (hands-on, click-through)

For end-to-end verification from the Studio UI — every execution mode (reactive,
durable, scheduled, event-driven), memory, composite workflows, and the event-gateway
security checks — follow the step-by-step plan with pass/fail criteria:

- **[`docs/testing/manual-ui-e2e-test-plan.md`](docs/testing/manual-ui-e2e-test-plan.md)**

It maps each test to its design doc and calls out the current UI gaps (e.g. triggers
are created via API, not a button yet) so you don't chase false bugs.

---

## Data durability and backups

Postgres stores everything persistent: agent registry, runs, Keycloak users, Langfuse data.

**What keeps data across restarts:**
- `PGDATA` lives on a PVC (`data-agentshield-postgresql-0`), not emptyDir — pod restarts are safe
- StatefulSet `persistentVolumeClaimRetentionPolicy: Retain` — PVC survives `helm uninstall`
- PV `reclaimPolicy: Retain` — volume survives even if the PVC object is deleted (enforced by a post-install hook on every deploy)

**What does NOT survive a cluster wipe:** Docker Desktop's "Reset Kubernetes Cluster" destroys the node VM and all local-path PV data with it. Do not use that option unless you've backed up first.

**Backup and restore:**

```bash
bash scripts/backup-postgres.sh   # → ./backups/agentshield-pg-<timestamp>.sql.gz (on your Mac)
bash scripts/restore-postgres.sh  # restore from a dump
bash scripts/purge-test-agents.sh # remove test artifacts from a dev cluster
```

See [`docs/runbooks/postgres-backup.md`](docs/runbooks/postgres-backup.md) for scheduling (macOS launchd) and full recovery procedures.

---

## Contributing / making changes

`CLAUDE.md` at the repo root is the source of truth for the post-implementation checklist. Summary:

**E2E tests** — every new API endpoint or behavior change needs a corresponding suite in `scripts/e2e/`. Follow the `suite-NN-<name>.sh` pattern (kubectl exec into the registry-api pod, Python/httpx assertions). Register new suites in `scripts/e2e/run-all.sh`. Test case IDs use `T-SNN-00X — <what it proves>` format.

**Image tags** — every service rebuild requires:
1. Increment patch version in `scripts/deploy-cpe2e.sh` (e.g. `0.2.59` → `0.2.60`)
2. Update the comment header with what changed
3. Keep `charts/agentshield/values.yaml` image tags in sync

Affected tag vars: `REGISTRY_API_TAG`, `STUDIO_TAG`, `DEPLOY_CONTROLLER_TAG`, `DECLARATIVE_RUNNER_TAG`, `PYTHON_EXECUTOR_TAG`, `SAFETY_ORCHESTRATOR_TAG`, `EVAL_RUNNER_TAG`, `SCHEDULER_TAG`, `EVENT_GATEWAY_TAG`.

**Alembic migrations** — numbered sequentially in `services/registry-api/alembic/versions/`. Latest is `0028`. Next migration must be `0029`.

**Design changes** — update `docs/spec.md` and `docs/decisions.md` when architecture or data model changes.

**Playground UX changes** — update `docs/experience/playground.md` when modifying `PlaygroundPage.tsx`, `ChatPane.tsx`, `HitlPanel.tsx`, `TracePanel.tsx`, `playgroundApi.ts`, or `services/registry-api/routers/playground.py`.

---

## Repository layout

```
charts/agentshield/          Helm chart (values.yaml has all image tags + component toggles)
docs/
  spec.md                    Architecture specification (v1.2.0)
  decisions.md               22 architecture decision records (D1–D22)
  design/                    Detailed design specs (auth model, playground, execution modes, OPA)
  experience/                End-user UX flow descriptions
  plan/                      Implementation plans and task lists
  runbooks/                  Operational runbooks (postgres-backup, incident response)
examples/                    Reference agent implementations (order-agent)
infra/                       Kubernetes manifests (namespaces, RBAC, OPA bundle server)
policies/                    OPA Rego policies
scripts/
  deploy-cpe2e.sh            Primary build + deploy script
  e2e/                       29 bash+curl API test suites
  backup-postgres.sh         Off-cluster Postgres dump
  restore-postgres.sh        Restore from dump
  purge-test-agents.sh       Clean up test artifacts
  studio-e2e.sh              Playwright browser E2E runner
sdk/agentshield_sdk/         Python SDK (LangGraph-based; requires Python >=3.12)
services/
  registry-api/              FastAPI backend (primary service)
  declarative-runner/        No-code agent runner
  deploy-controller/         K8s operator for agent pods
  python-executor/           Sandboxed Python tool sidecar
  scheduler/                 Cron-based agent scheduler (HA)
  event-gateway/             Webhook ingress (Phase 9)
  safety-orchestrator/       NeMo Guardrails safety scanning
  eval-runner/               Batch eval K8s Job runner
  echo-agent/                Reference/test agent
studio/                      React + Vite frontend (Node 20, TypeScript)
```

---

## Key docs

- [`docs/spec.md`](docs/spec.md) — architecture specification, user stories, acceptance criteria
- [`docs/decisions.md`](docs/decisions.md) — 22 architecture decision records
- [`docs/design/`](docs/design/) — authorization model, playground spec, execution modes, OPA contract
- [`docs/plan/`](docs/plan/) — implementation plans, data models, phased roadmaps
- [`docs/runbooks/postgres-backup.md`](docs/runbooks/postgres-backup.md) — durability, backup, restore
- [`docs/experience/playground.md`](docs/experience/playground.md) — Playground UX flow reference
- [`docs/testing/manual-ui-e2e-test-plan.md`](docs/testing/manual-ui-e2e-test-plan.md) — hands-on click-through UI test plan (all execution modes, memory, workflows, event-gateway)
- [`CLAUDE.md`](CLAUDE.md) — post-implementation checklist (e2e, image tags, migrations, docs)

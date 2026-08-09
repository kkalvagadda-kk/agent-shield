# AgentShield Platform — Claude Code Instructions

## Definition of Done (READ FIRST — this is the bar)

A backend that works is **not** a feature that works. Several past changes were reported "done" because the API + bash e2e were green, while the actual UI flow was broken (edges drawn but never persisted, `createTrigger` wired to no button, `serializeCompositeWorkflow` written but never called). To stop that recurring, a change is **done** only when ALL of the following hold — not when it compiles, typechecks, or the API test passes:

1. **A real user journey is proven, not just an endpoint.** For any UX-facing change there MUST be a Playwright spec (`studio/e2e/*.spec.ts`) — or, if truly infeasible, a step in `docs/testing/manual-ui-e2e-test-plan.md` — that drives the actual flow: click the control, trigger the network call, and assert the result in the UI. Bash suites `kubectl exec` into the pod and test the API only; they **cannot** catch a broken screen. Test the layer that can actually fail.
2. **Save → reload → assert survived.** Every create/edit surface MUST have a test that persists, **reloads from the backend**, and confirms the data is still there. Most past rework was an unclosed persistence round-trip (state lived in the store but never reached the DB). This guard is mandatory and non-negotiable for anything that writes data.
3. **No orphan code.** Every new exported function, API-client method, SSE event, or DB column MUST have a live caller/reader in the same change. Build utility + not wiring it up = NOT done. Before reporting done, grep each new symbol for a caller (e.g. `grep -rn "createTrigger" studio/src`). An orphan is debt disguised as progress.
4. **Vertical slices, not horizontal layers.** Wire one thin path end-to-end (UI control → API → DB → read back in UI) and prove it before starting the next capability. Do NOT build all models, then all endpoints, then run out of runway at the screen.
5. **Honest gap ledger.** Anything stubbed, deferred, or knowingly incomplete MUST be recorded in a visible "Known gaps" list (the header of `docs/testing/manual-ui-e2e-test-plan.md` is the canonical place), tagged **deferred (intentional)** vs **not-yet-wired (debt)**. Never let an unfinished piece read as shipped. Silence is how debt becomes a surprise.
6. **Reason from the running product, not the design doc.** Design docs describe intent and go stale. Before describing or extending a feature, read the actual code/UI. When a design doc states a feature is built, verify it in the code before relying on it; update the doc's status when reality differs.

When you report a task done, state explicitly which of these you satisfied (which Playwright/manual step proves the journey, what the reload test asserts, that no new symbol is orphaned). If you cannot satisfy one, say so and put it in the gap ledger — do not silently skip it.

## Post-Implementation Checklist (MANDATORY)

After implementing any code change, you MUST complete these steps before reporting the task as done:

### 1. E2E Tests

This project uses bash+curl e2e test suites in `scripts/e2e/`. Every new API endpoint or behavior change must have a corresponding e2e test:

- **Pattern**: Create or extend a `scripts/e2e/suite-NN-<name>.sh` file following the existing pattern (kubectl exec into the registry-api pod, run Python/httpx assertions)
- **Minimum coverage**: Test the happy path and at least one error/edge case
- **Register** the new suite in **`scripts/test-manifest.txt`** (single source of truth for both layers, with functional groups). `run-all.sh` is a thin wrapper over `scripts/run-tests.sh` and has no registry of its own. Verify with `bash scripts/run-tests.sh --audit`. Run targeted regressions with `bash scripts/run-tests.sh --group <group>` (`--groups` lists them).
- **Naming**: `T-SNN-00X — <what it proves>` format for test case IDs

### 2. Image Version Bumps

Every time you modify a service that builds into a Docker image, you MUST:

1. Increment the patch version in `scripts/deploy-cpe2e.sh` (e.g. `0.2.30` → `0.2.31`)
2. Update the comment header in `deploy-cpe2e.sh` with a brief description of what changed
3. Never reuse an existing tag — Kubernetes caches images by tag

Affected services and their tag variables:
- `services/registry-api/` → `REGISTRY_API_TAG`
- `studio/` → `STUDIO_TAG`
- `services/deploy-controller/` → `DEPLOY_CONTROLLER_TAG`
- `services/declarative-runner/` → `DECLARATIVE_RUNNER_TAG`
- `services/python-executor/` → `PYTHON_EXECUTOR_TAG`
- `services/safety-orchestrator/` → `SAFETY_ORCHESTRATOR_TAG`
- `services/eval-runner/` → `EVAL_RUNNER_TAG`
- `services/scheduler/` → `SCHEDULER_TAG`
- `services/event-gateway/` → `EVENT_GATEWAY_TAG`

**A tag lives in THREE files, not two. Update all three in the same commit:**

1. `scripts/deploy-cpe2e.sh` — the LOCAL (kind/docker-desktop) build. Builds `registry.internal/...`.
2. `scripts/deploy-eks.sh` — the CLOUD build. Builds and pushes to ECR. **This is the script the EKS test cluster uses** (`~/.kube/test-cluster-kube-config.yaml`); `deploy-cpe2e.sh` cannot deploy there, because EC2 nodes cannot see local images.
3. `charts/agentshield/values.yaml` — the DEPLOY. `helm upgrade` bakes tags in from values (no `--set`), so a stale value here means the chart points at the old tag. (registry-api ~L750, studio ~L820, `deploy-controller.declarativeRunnerTag` ~L579.)

**This rule used to name only 1 and 3, and that omission shipped a real failure (2026-08-04):** the R0 bump updated `deploy-cpe2e.sh` + `values.yaml`, `deploy-eks.sh` stayed a patch behind, and `scripts/check-tag-content-coupling.sh` blocked the deploy with `DRIFT: REGISTRY_API_TAG(local=0.2.259 eks=0.2.258)`. The same omission left `deploy-eks.sh` still calling `seed-platform-admin-role.sh` after `deploy-cpe2e.sh` had stopped — which silently overwrote the bootstrap's row provenance. **Whenever you change something in `deploy-cpe2e.sh`, grep `deploy-eks.sh` for the same thing.** They are siblings and they drift.

Run `bash scripts/check-tag-content-coupling.sh` before deploying — it is cluster-free, takes ~5s, and catches exactly this.

Run `bash scripts/check-e2e-auth-hygiene.sh` too, whenever a change adds or tightens auth on a router — **and after any scripted multi-file edit under `scripts/e2e/`**. Cluster-free (~1s). Its rules are derived from the tree and from `lib/e2e-auth.sh`, never from a hand-written list. They cover two families:

- **A suite silently stops authenticating** — a duplicate `headers=` kwarg (SyntaxError inside the in-pod driver); two `Authorization` keys in one dict (NOT an error — the last wins, so the call goes out as the wrong identity); a mutation or gated read with no credential; a header dict that authenticates only the cleanup; `${E2E_TOKEN}` referenced without sourcing `lib/e2e-auth.sh`.
- **A scripted edit breaks runtime order** — a split line continuation (something inserted between the halves of one command); a value read above the call that provides it; a lib helper called with an argument that is not assigned until further down.

`bash -n` catches **none** of these: ordering is not syntax, and a broken header dict lives inside a Python string. Do not read "parses clean" as "the edit is sound" — that gap is where every one of these shipped from. Three RBAC phases in a row shipped a router change that turned suites red; R2's sweep list was written by hand and missed 28 suites, several of which stayed red for a whole phase. Derive the list from the tree.

### 3. Experience Docs

`docs/experience/` contains end-user-facing descriptions of each major UX flow. When you change playground UX or APIs — new SSE events, new panels, new endpoints, changed error states, changed routing logic — you **MUST** update `docs/experience/playground.md` to reflect the change before reporting the task done.

Covered files that trigger an update requirement:
- `studio/src/pages/PlaygroundPage.tsx`
- `studio/src/pages/DatasetsPage.tsx`
- `studio/src/pages/EvalResultsPage.tsx`
- `studio/src/components/playground/ChatPane.tsx`
- `studio/src/components/playground/HitlPanel.tsx`
- `studio/src/components/playground/TracePanel.tsx`
- `studio/src/components/playground/VersionSelector.tsx`
- `studio/src/api/playgroundApi.ts`
- `services/registry-api/routers/playground.py`
- `services/registry-api/routers/eval_runner.py`
- `services/registry-api/judge.py`
- `services/registry-api/k8s.py` (eval Job creation)
- `sdk/agentshield_sdk/streaming.py` (SSE event format changes)

### 4. Frontend Tests (Studio)

The bash e2e suites test the **backend API only**. Studio (React) has its own two test layers, and **you MUST keep them in sync as you change/improve the frontend** — a UI change is not done until its tests are updated and green:

- **Component tests — Vitest + React Testing Library** (`studio/src/**/*.test.tsx`, colocated next to each component; APIs mocked via `vi.mock('../api/registryApi')`; render via `renderWithProviders` from `src/test/utils.tsx`):
  - When you add/change a component or its states (loading/empty/error/edge), add or update its `*.test.tsx`.
  - Run: `cd studio && npm run test` (must pass). Coverage: `npm run test:cov`.
- **Browser E2E — Playwright** (`studio/e2e/*.spec.ts`, real Keycloak login via `e2e/global-setup.ts`, runs against the deployed Studio):
  - When you add/change a user flow (new page, route, builder step, run panel), add or update the matching `e2e/*.spec.ts`.
  - Run: `bash scripts/studio-e2e.sh` (port-forwards Studio + runs Playwright; must pass). This is a **separate gate** — it is NOT part of `scripts/e2e/run-all.sh`. First-time setup: `cd studio && npx playwright install chromium`.
  - Assert UI wiring + persistence + network calls (`page.waitForResponse`), not agent execution (few agent pods are deployed, so runs may not complete — same boundary the bash suites accept).

Both suites must stay green; do not delete/skip a test to make a change pass — update the test to reflect the intended new behavior.

### 5. Verification

- **TypeScript**: After any frontend change, run `cd studio && npm run typecheck` (`tsc --noEmit`) and fix all errors
- **Python**: After any Python change, verify syntax with `python3 -c "import ast; ast.parse(open('file').read())"`. For ORM/schema changes, also confirm mappers configure (import the routers + `sqlalchemy.orm.configure_mappers()`).
- **Backend E2E**: Ensure the new bash suite is registered in `run-all.sh` and executable
- **Frontend tests**: `cd studio && npm run test` (Vitest) green, and `bash scripts/studio-e2e.sh` (Playwright) green for UI-affecting changes
- **Definition of Done gate** (see the section at the top): confirm (a) a Playwright/manual step proves the real user journey, (b) a save→reload→assert test exists for any new write surface, (c) no new exported symbol / API-client method / DB column is orphaned (grep for a caller), and (d) any incomplete piece is in the gap ledger
- **Migrations**: Alembic migrations in `services/registry-api/alembic/versions/` are numbered sequentially (latest is `0028`); make them idempotent (`IF [NOT] EXISTS`/guarded) and preserve data on renames

## Project Structure

```
services/registry-api/   — FastAPI backend (agents, tools, teams, workflows, deployments)
services/declarative-runner/ — Generic agent runner that interprets workflow JSON
services/deploy-controller/  — K8s operator that reconciles agent deployments
services/python-executor/    — Sandboxed Python code execution sidecar
studio/                      — React frontend (Vite + TailwindCSS + React Query)
sdk/agentshield_sdk/         — Python SDK for building governed agents
scripts/deploy-cpe2e.sh      — Build + deploy script with image tags
scripts/e2e/                 — End-to-end test suites (bash + curl)
charts/agentshield/          — Helm chart
docs/spec.md                 — Architecture specification
docs/decisions.md            — Architecture decision records
```

## Key Patterns

- **Auth**: Keycloak OIDC → JWT with `sub` claim. Backend uses `require_user` / `get_optional_user` from `auth_middleware.py`.
- **Team resolution**: `user_team_assignments` table maps JWT `sub` → team. Use `GET /api/v1/me` for current user's team.
- **Tool governance**: All tools are platform-managed (HTTP or Python type). SDK resolves tool names from registry at startup. Governance (OPA + HITL) wraps every tool call.
- **Agent types**: `sdk` (custom container) vs `declarative` (platform-managed runner + workflow JSON). This is an infra routing flag, not user-facing.

# AgentShield Platform — Claude Code Instructions

## Definition of Done (READ FIRST — this is the bar)

A backend that works is **not** a feature that works. Several past changes were reported "done" because the API + bash e2e were green, while the actual UI flow was broken (edges drawn but never persisted, `createTrigger` wired to no button, `serializeCompositeWorkflow` written but never called). To stop that recurring, a change is **done** only when ALL of the following hold — not when it compiles, typechecks, or the API test passes:

1. **A real user journey is proven, not just an endpoint.** For any UX-facing change there MUST be a Playwright spec (`studio/e2e/*.spec.ts`) — or, if truly infeasible, a step in `docs/testing/manual-ui-e2e-test-plan.md` — that drives the actual flow: click the control, trigger the network call, and assert the result in the UI. Bash suites `kubectl exec` into the pod and test the API only; they **cannot** catch a broken screen. Test the layer that can actually fail.
2. **Save → reload → assert survived.** Every create/edit surface MUST have a test that persists, **reloads from the backend**, and confirms the data is still there. Most past rework was an unclosed persistence round-trip (state lived in the store but never reached the DB). This guard is mandatory and non-negotiable for anything that writes data.
3. **No orphan code.** Every new exported function, API-client method, SSE event, or DB column MUST have a live caller/reader in the same change. Build utility + not wiring it up = NOT done. Before reporting done, grep each new symbol for a caller (e.g. `grep -rn "createTrigger" studio/src`). An orphan is debt disguised as progress.
4. **Vertical slices, not horizontal layers.** Wire one thin path end-to-end (UI control → API → DB → read back in UI) and prove it before starting the next capability. Do NOT build all models, then all endpoints, then run out of runway at the screen.
5. **Honest gap ledger.** Anything stubbed, deferred, or knowingly incomplete MUST be recorded in a visible "Known gaps" list (the header of `docs/testing/manual-ui-e2e-test-plan.md` is the canonical place), tagged **deferred (intentional)** vs **not-yet-wired (debt)**. Never let an unfinished piece read as shipped. Silence is how debt becomes a surprise.
6. **Reason from the running product, not the design doc.** Design docs describe intent and go stale. Before describing or extending a feature, read the actual code/UI. When a design doc states a feature is built, verify it in the code before relying on it; update the doc's status when reality differs.
7. **Bug fixes reproduce first (regression-test-first).** When the user reports a bug, review the existing tests for that functional area and confirm they can actually re-create it. If they can't, that is a test gap — **add or fix a test that FAILS against the buggy code (reproducing the issue) BEFORE writing the fix**, then make it pass and **self-verify by driving the real flow** before declaring done. A suite that stayed green through a real bug is itself defective — correct it too. Also, before touching code: **trace the root cause** (don't patch the symptom), **check whether your own recent changes caused it**, and **never weaken/remove an existing control to silence a symptom** — if the fix changes behavior, prompt the user first with your analysis + functional/UX impact. (Test the layer that can actually fail: a bug in the reactive `/chat/stream` backend can't be caught by a Vitest that hand-feeds the SSE frame — it needs a backend/e2e test that drives the real path.)

8. **Document every bug + debugging session (MANDATORY).** This repo keeps a written record of real defects and investigations — you MUST add to it, not just fix and move on:
   - **`docs/bugs/<kebab-slug>.md`** — a per-bug postmortem. Required sections: a one-line title, a **Found/Fixed** line (date + the exact image tag/commit that fixed it), **Symptom**, **Root cause** (the design flaw, not the surface error), and **Fix** (what changed + why it's the class-fix). Write this for any non-trivial bug you fix.
   - **`docs/debugging/NNN-<slug>.md`** — a numbered investigation log when the diagnosis was non-obvious (multi-layer, symptom named the wrong layer, several dead ends). Capture the **expected chain**, the **investigation steps with the exact `kubectl`/SQL/`curl` commands**, the **evidence that concluded each step**, the **root cause**, and the **fix**. Use the next free number.
   - When a debugging session teaches a reusable investigation pattern, also fold it into **`docs/debugging/troubleshooting-playbook.md`** (symptom → commands → evidence → root cause → fix).
   - Write the doc as part of the SAME change that fixes the bug (reference it in the commit). A fix with no bug/debugging doc is not done. Cross-link the failing regression test you added (rule 7) so the doc, the test, and the fix travel together.

When you report a task done, state explicitly which of these you satisfied (which Playwright/manual step proves the journey, what the reload test asserts, that no new symbol is orphaned, and — for a bug — the `docs/bugs`/`docs/debugging` entry you wrote). If you cannot satisfy one, say so and put it in the gap ledger — do not silently skip it.

## Post-Implementation Checklist (MANDATORY)

After implementing any code change, you MUST complete these steps before reporting the task as done:

### 1. E2E Tests

This project uses bash+curl e2e test suites in `scripts/e2e/`. Every new API endpoint or behavior change must have a corresponding e2e test:

- **Pattern**: Create or extend a `scripts/e2e/suite-NN-<name>.sh` file following the existing pattern (kubectl exec into the registry-api pod, run Python/httpx assertions)
- **Minimum coverage**: Test the happy path and at least one error/edge case
- **Register** new suites in `scripts/e2e/run-all.sh`
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

**Also mirror the same tag in `charts/agentshield/values.yaml`** — the deploy uses `helm upgrade` with tags baked into values.yaml (no `--set`), so bumping only `deploy-cpe2e.sh` leaves the chart pointing at the old tag. Update BOTH in the same commit. (registry-api ~L503, studio ~L820, `deploy-controller.declarativeRunnerTag` ~L579.)

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
- **Regression sweep (MANDATORY on every feature/change)**: run ALL tests that could be **impacted** by the change, not just the new one. First map the blast radius (what endpoints / SSE streams / reducers / checkpoints / DB tables / auth paths does this touch or share?), then run the impacted `scripts/e2e/suite-*.sh` (e.g. a chat-stream change → also the HITL/approval/resume/memory suites), the affected Vitest, and the affected `studio/e2e/*.spec.ts`. A green new test but a broken neighbor is a shipped regression. If a shared surface has no test that would catch a break, add one; if you can't run an impacted suite (capacity/fixture/runtime), say so and record it as a gap — never let "I ran the new test" read as "fully verified".
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

## Pushing to GitHub (read before `git push`)

Remote: `origin` = `git@github.com:kkalvagadda-kk/agent-shield.git` (**SSH**, not HTTPS). Default branch: `main`.

**Why a push may fail with `Permission denied (publickey)` even though it "worked before":** commands run through the assistant's Bash tool (and the `!` prefix) execute in a shell whose `ssh-agent` starts **empty** — it does NOT share the key you loaded in your own terminal. `gh` CLI is not installed here either. So the agent has no identity to offer GitHub and the push is denied.

**Fix — load the key from the macOS keychain into this shell's agent (non-interactive, no passphrase prompt):**
```bash
ssh-add --apple-use-keychain ~/.ssh/id_ed25519    # the GitHub key (comment: kkalvagadda@gmail.com)
ssh -T git@github.com                             # expect: "Hi kkalvagadda-kk! ..."
```
`id_ed25519` is the GitHub-registered key; `id_rsa` is an older machine key (fallback only). There is no `github.com` Host block in `~/.ssh/config`, so the default key applies.

**Then push.** Note the assistant's auto-mode classifier may BLOCK `git push` as an outward action even after the key loads — if so, the user runs it themselves (`! git push origin main`, or from their own terminal).

**Branch → main flow used here:** feature work lands on a branch (e.g. `webhook-improvements`) in a git **worktree**; `main` is checked out in the primary worktree (`/Users/kkalyan/repo/agent-platform`). Merge is a clean fast-forward when `main` is a strict ancestor:
```bash
cd /Users/kkalyan/repo/agent-platform      # the worktree where main is checked out
git merge --ff-only <feature-branch>        # NOT from the feature worktree (that's a no-op)
git push origin main
```
Running `git merge --ff-only <branch>` from inside the feature branch's own worktree is a no-op ("Already up to date") — always fast-forward `main` from the worktree that has `main` checked out.

**Verify (read-only, never blocked):**
```bash
git fetch origin && git log --oneline origin/main..main | wc -l   # 0 == fully pushed
```

# POC-2 Quickstart — build, test, deploy gates

All commands from the repo root: `/Users/kkalyan/repo/agent-platform/.claude/worktrees/ux-preview-context-storage`.

## 1. Frontend gates (studio)
```bash
cd studio

# TypeScript — must be clean (CLAUDE.md Verification #5)
npm run typecheck

# Vitest component + unit tests (new: agentColor, chatStream, AttributedBubble)
npm run test
npm run test -- agentColor chatStream AttributedBubble   # focused
npm run test:cov                                         # optional coverage
```

## 2. Browser E2E (Playwright — separate gate, real Keycloak login)
```bash
# first time only:
cd studio && npx playwright install chromium

# run against the deployed studio (port-forwards + runs Playwright):
bash scripts/studio-e2e.sh

# focused:
cd studio && npx playwright test e2e/context-attribution.spec.ts
```
Asserts UI wiring + persistence + network calls (`waitForResponse`), NOT agent execution completion (few agent pods; same boundary the bash suites accept). Target the https gateway — Secure Keycloak cookies break over http port-forward.

## 3. Backend E2E (suite-75, incl. new T-S75-007)
```bash
bash scripts/e2e/suite-75-context-storage.sh
# or the whole gate:
bash scripts/e2e/run-all.sh
```
Suites `kubectl exec` into the registry-api pod and run httpx assertions. T-S75-007 asserts a single-agent `/chat` stream's token frames carry `author`; T-S75-004 (existing) asserts the `scope=workflow_run` transcript returns per-author rows.

## 4. Python sanity (after chat.py edits)
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/routers/chat.py').read())"
```

## 5. Image bumps (BOTH services, ALL THREE files + changelog)
Before deploying, confirm the tags moved (registry-api in T1, studio in T9):
```bash
grep -n 'REGISTRY_API_TAG=\|STUDIO_TAG=' scripts/deploy-cpe2e.sh scripts/deploy-eks.sh
grep -n 'tag:' charts/agentshield/values.yaml | sed -n '1p;40p'   # registry ~L596, studio ~L915
```
Expected after POC-2: `REGISTRY_API_TAG="0.2.189"`, `STUDIO_TAG="0.1.141"` in both scripts, and the matching `tag:` values in `charts/agentshield/values.yaml`. Update the changelog comment header in each deploy script.

## 6. Deploy (sanctioned Helm path only)
```bash
KUBECONFIG=~/.kube/test-cluster-kube-config.yaml SKIP_BUILD=1 bash scripts/deploy-eks.sh
```
`SKIP_BUILD=1` reuses images already pushed to ECR by the build step. NEVER `kubectl set image/env` — that is drift (roadmap §2). If you changed source, let the deploy pipeline build+push the bumped tags first, then this applies the chart.

## 7. Orphan check (DoD #3 — every new symbol has a live caller)
```bash
grep -rn "AttributedBubble" studio/src        # imported by AgentChat/CatalogChat/ChatPane/EvalResults
grep -rn "agentColor" studio/src              # used by AttributedBubble
grep -rn "routeToken\|openAuthorBubble" studio/src   # used by AgentChat + CatalogChat
grep -n '"author"' services/registry-api/routers/chat.py   # agent_start + token frames
grep -n "memory_enabled" studio/src/pages/WorkflowBuilderPage.tsx   # both save calls
```

## 8. Docs to update before "done"
- `docs/experience/playground.md` — new `author`/`agent_start` frames, attributed bubbles, eval transcript, share-context toggle.
- `docs/testing/manual-ui-e2e-test-plan.md` — gap-ledger entries (plan §9).

## Definition-of-Done recap (state these explicitly when reporting)
- **Real journey**: `studio/e2e/context-attribution.spec.ts` drives a multi-agent workflow → attributed bubbles; drives the toggle.
- **Save→reload→assert**: the share-context toggle (Playwright) + suite-75 T-S75-004 (transcript) + T-S75-007 (author frames).
- **No orphans**: §7 greps.
- **Gap ledger**: per-session/per-run + share-rationale + reload-seeding recorded as deferred.

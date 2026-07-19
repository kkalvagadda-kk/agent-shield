# POC-5 — Quickstart & Checkpoints

Executable, ordered gates. **Deploy is LOCAL docker-desktop** via `scripts/deploy-cpe2e.sh`
+ Helm (tags baked into `charts/agentshield/values.yaml`, no `--set`). Run these from the
worktree root: `/Users/kalyankalvagadda/code/agent-shield`.

Baseline `registry-api:0.2.195` (**backend already shipped — no bump**), `studio:0.1.146`
(deploy-cpe2e.sh) / `0.1.145` (values.yaml lags), `declarative-runner:0.1.57`, Alembic `0067`.
Target `studio:0.1.147` (registry-api + runner unchanged, **no migration**).

> **Host note — no node/npm.** `node`/`npm` are NOT installed on this host, so
> `npm run typecheck` / `npm run test` cannot run locally. TypeScript is validated by the
> **studio Docker build** (`tsc && vite build`, run by `npm run build` inside
> `studio/Dockerfile`) during `deploy-cpe2e.sh` — a type error **fails the build**. Vitest is
> authored but executed in CI/build. The local frontend gate is therefore: static checks
> (grep for orphans/anchors, no dead imports) + a clean studio Docker build at CP-C.

---

## Pre-flight (no cluster)

```bash
cd services/registry-api
# Backend is already shipped; this just confirms it still parses + mappers configure.
python3 -c "import ast; [ast.parse(open(f).read()) for f in ('memory.py','conversation_store.py','schemas.py','routers/memory.py','routers/me.py')]; print('py-syntax ok')"
python3 -c "import routers.memory, routers.me, schemas; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('mappers ok')"

cd ..
# No-orphan grep (DoD 3) — each NEW symbol MUST have a live caller:
grep -rn "listConversations\|listMyConversations" studio/src          # client (T2) + sidebar (T3)
grep -rn "interface ConversationSummary" studio/src/api/registryApi.ts # TS type (T2)
grep -rn "ConversationSidebar"       studio/src                       # 3 mounts (T4/T5/T6/T7)
grep -rn "filterConversationsByEnv"  studio/src                       # sidebar + page filter
grep -rn "ConversationsTab"          studio/src                       # deployment overview (T7)
grep -rn "\"conversations\""         studio/src/pages/DeploymentOverviewPage.tsx  # 4th tab (T7)
grep -rn "PREVIEW_ITEMS"             studio/src                       # EXPECT: no hits (retired, T8)
grep -rn "\bHome\b\|MessagesSquare"  studio/src/components/Sidebar.tsx # EXPECT: no hits (dead imports removed, T8)
# Backend symbols already have live callers (shipped) — sanity only:
grep -rn "list_conversations"        services/registry-api            # memory + store + 2 routers

# suite-78 is a valid script and registered after suite-77:
bash -n scripts/e2e/suite-78-conversations.sh && test -x scripts/e2e/suite-78-conversations.sh
grep -n "suite-78-conversations" scripts/e2e/run-all.sh
```

---

## ✅ CHECKPOINT CP-A — suite-78 green against the live 0.2.195 pod

Backend is **already deployed** (registry-api:0.2.195, no bump). **Gate: block frontend UI
work until this passes** — it proves the contract the UI depends on.

```bash
# 1. Confirm the running pod is on 0.2.195 (the shipped backend). If it predates it, deploy
#    first (deploy-cpe2e.sh builds registry-api at the unchanged 0.2.195 tag).
kubectl -n agentshield-platform get deploy/agentshield-registry-api \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="registry-api")].image}'; echo
#    (expect …/registry-api:0.2.195 — if not:)
# bash scripts/deploy-cpe2e.sh && kubectl -n agentshield-platform rollout status deploy/agentshield-registry-api --timeout=180s

# 2. Smoke: both endpoints answer through the real router (in-pod, real auth headers)
POD=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n agentshield-platform "$POD" -c registry-api -- python3 - <<'PY'
import httpx
B="http://localhost:8000/api/v1"; H={"X-User-Sub":"cp-a-smoke","X-User-Team":"platform"}
with httpx.Client(timeout=20.0) as c:
    r = c.get(f"{B}/me/conversations", headers=H)
    print("me/conversations", r.status_code, "list" if isinstance(r.json(), list) else r.text[:120])
    assert r.status_code == 200 and isinstance(r.json(), list)
print("CP-A smoke OK")
PY

# 3. Full e2e suite for POC-5
bash scripts/e2e/suite-78-conversations.sh          # all RESULT ... PASS, exit 0
```

**Exit criteria**: pod on `0.2.195`, both endpoints `200`, suite-78 all-PASS.

---

## ✅ CHECKPOINT CP-B — Frontend static gate (no cluster, no npm)

Since node/npm is absent, the only local frontend gate is static. The type/Vitest gates run
inside the studio Docker build at CP-C.

```bash
# All new symbols wired (re-run the no-orphan grep block above) — every grep with a caller,
# PREVIEW_ITEMS / dead Sidebar imports gone. New/edited files present:
for f in \
  studio/src/api/registryApi.ts \
  studio/src/components/conversations/ConversationSidebar.tsx \
  studio/src/pages/ConversationsPage.tsx \
  studio/src/components/agent-detail/ConversationsTab.tsx ; do
  test -f "$f" && echo "ok  $f" || echo "MISSING $f"
done
# POC-4 citation wiring preserved in the edited chat pages:
grep -n "attachCitations\|parseKnowledgeCitations\|citations" studio/src/pages/AgentChatPage.tsx
```

**Exit criteria**: all new files present, no orphan/dead-import grep hits, POC-4 citation
wiring still referenced in `AgentChatPage.tsx`.

---

## ✅ CHECKPOINT CP-C — Studio built (type gate) + deployed + Playwright green

Ships `studio:0.1.147`. The Docker build **is** the TypeScript gate (`tsc && vite build`).

```bash
# 1. Confirm the studio bump (T11) — both files, no residual 0.1.146/0.1.145 for studio
grep -R "0.1.147" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml   # 2 hits

# 2. Build + deploy studio (docker-desktop). A TS error fails `npm run build` here.
bash scripts/deploy-cpe2e.sh
kubectl -n agentshield-platform rollout status deploy/agentshield-studio --timeout=180s

# 3. Browser E2E — real Keycloak login, https gateway (Secure KC cookies need https)
#    First-time only: (cd studio && npx playwright install chromium)  # needs node — run where node exists
bash scripts/studio-e2e.sh              # runs all specs incl. the two new POC-5 specs
```

Targeted run while iterating (on a machine with node):

```bash
cd studio
npx playwright test e2e/conversations-sidebar.spec.ts e2e/deployment-conversations.spec.ts
```

**Exit criteria (DoD 1+2)**: studio Docker build clean (no type errors), rollout healthy, and
for each surface — reload → conversation listed → click → transcript rehydrates (a `/memory`
response is awaited) → follow-up reuses the thread's `session_id` (recalls turn-1 where an
agent pod is live).

---

## Manual UI walk-through (if Playwright can't reach a live agent pod)

1. `Conversations` (real nav, non-DEMO) → `/conversations`: past conversations across agents;
   All/Sandbox/Production pills filter in place; click a row → transcript preview; **Continue**
   → seeded sandbox chat.
2. `AgentChatPage` (`/agents/:name/chat`): open the **History** dock → select a prior thread →
   the transcript swaps in and the reply box continues it; **New conversation** clears + re-keys.
   Citation chips still render on a live knowledge answer (POC-4 preserved).
3. `CatalogChatPage` (`/catalog/:artifactId/chat`): same History dock, production deployment;
   workflow attribution + rich bubbles still work.
4. `DeploymentOverviewPage` (`/agents/:name/d/:depId`): the tab bar now shows **Overview /
   Runs / Memory / Conversations**; Conversations lists this deployment's threads → click →
   deployment chat opens seeded (`?session=`) → follow-up continues. Memory tab unchanged.

Record any step that can't run (no live pod) in
`docs/testing/manual-ui-e2e-test-plan.md` with the reason.

---

## Rollback

Tags are immutable; to revert the studio surface, redeploy the previous tag:

```bash
# set STUDIO_TAG=0.1.146 in scripts/deploy-cpe2e.sh and the studio tag in
# charts/agentshield/values.yaml, then:
bash scripts/deploy-cpe2e.sh
```

registry-api needs no rollback (unchanged at `0.2.195`). No DB rollback needed — POC-5 added
no migration.

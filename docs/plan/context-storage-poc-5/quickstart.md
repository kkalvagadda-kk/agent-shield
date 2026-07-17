# POC-5 — Quickstart & Checkpoints

Executable, ordered gates. Deploy is a **user-gated, shared-cluster** step (the dev cluster
is KIND-backed and shared across sessions — a full deploy from a stale branch downgrades
untouched services, so reconcile untouched tags before a full deploy). Run these from the
worktree root: `/Users/kkalyan/repo/agent-platform/.claude/worktrees/ux-preview-context-storage`.

Baseline `registry-api:0.2.191`, `studio:0.1.144`, `declarative-runner:0.1.56`, Alembic `0065`.
Targets `registry-api:0.2.193`, `studio:0.1.146` (runner unchanged, **no migration**).

---

## Pre-flight (no cluster)

```bash
cd services/registry-api
# Syntax + mapper config (POC-5 adds a schema + a query, no ORM change — must still configure)
python3 -c "import ast; [ast.parse(open(f).read()) for f in ('memory.py','conversation_store.py','schemas.py','routers/memory.py','routers/me.py')]; print('py-syntax ok')"
python3 -c "import routers.memory, routers.me, schemas; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('mappers ok')"

cd ../../studio
npm run typecheck                       # tsc --noEmit — zero errors
npm run test                            # Vitest — ConversationSidebar / ConversationsPage / *ChatPage / DeploymentOverview green

# No-orphan grep (DoD 3) — each MUST have a live caller:
cd ..
grep -rn "list_conversations"        services/registry-api            # memory + store + 2 routers
grep -rn "ConversationSummary"       services/registry-api            # schema + 2 routers
grep -rn "listConversations\|listMyConversations" studio/src          # client + sidebar
grep -rn "ConversationSidebar"       studio/src                       # 3 mounts
grep -rn "ConversationsTab"          studio/src                       # deployment overview
grep -rn "\"conversations\""         studio/src/pages/DeploymentOverviewPage.tsx
grep -rn "PREVIEW_ITEMS"             studio/src                       # EXPECT: no hits (retired)
```

---

## ✅ CHECKPOINT CP-A — Backend deployed + suite-78 green

Ships `registry-api:0.2.193`. **Gate: block Slice B (frontend) until this passes.**

```bash
# 1. Confirm the registry-api bump landed in all three files (T15)
grep -R "0.2.193" scripts/deploy-cpe2e.sh scripts/deploy-eks.sh charts/agentshield/values.yaml

# 2. Build + deploy (KIND). Reconcile untouched tags to live FIRST (see MEMORY: kind deploy).
bash scripts/deploy-cpe2e.sh          # builds+pushes registry-api:0.2.193, helm upgrade

# 3. Wait for rollout
kubectl -n agentshield-platform rollout status deploy/agentshield-registry-api --timeout=180s

# 4. Smoke: the two endpoints answer through the real router (in-pod, real auth headers)
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

# 5. Full e2e suite for POC-5
bash scripts/e2e/suite-78-conversations.sh          # all RESULT ... PASS, exit 0
```

**Exit criteria**: rollout healthy, both endpoints `200`, suite-78 all-PASS.

---

## ✅ CHECKPOINT CP-B — Frontend build gate (no cluster)

```bash
cd studio
npm run typecheck                       # zero errors
npm run test                            # Vitest green (all colocated *.test.tsx)
npm run test:cov -- ConversationSidebar # sanity: the shared component is covered
```

**Exit criteria**: typecheck clean, Vitest green, no skipped/deleted tests.

---

## ✅ CHECKPOINT CP-C — Studio deployed + Playwright green

Ships `studio:0.1.146`.

```bash
# 1. Confirm the studio bump (T15)
grep -R "0.1.146" scripts/deploy-cpe2e.sh scripts/deploy-eks.sh charts/agentshield/values.yaml

# 2. Build + deploy studio (KIND)
bash scripts/deploy-cpe2e.sh
kubectl -n agentshield-platform rollout status deploy/agentshield-studio --timeout=180s

# 3. Browser E2E — real Keycloak login, https gateway (Secure KC cookies need https)
#    First-time only: (cd studio && npx playwright install chromium)
bash scripts/studio-e2e.sh              # runs all specs incl. the two new POC-5 specs
```

Targeted run while iterating:

```bash
cd studio
npx playwright test e2e/conversations-sidebar.spec.ts e2e/deployment-conversations.spec.ts
```

**Exit criteria (DoD 1+2)**: for each surface — reload → conversation listed → click →
transcript rehydrates (a `/memory` response is awaited) → follow-up reuses the thread's
`session_id` (recalls turn-1 where an agent pod is live).

---

## Manual UI walk-through (if Playwright can't reach a live agent pod)

1. `Conversations` (real nav, non-DEMO) → `/conversations`: past conversations across agents;
   All/Sandbox/Production pills filter in place; click a row → transcript preview; **Continue**
   → seeded sandbox chat.
2. `AgentChatPage` (`/agents/:name/chat`): open the **History** dock → select a prior thread →
   the transcript swaps in and the reply box continues it; **New conversation** clears + re-keys.
3. `CatalogChatPage` (`/catalog/:artifactId/chat`): same History dock, production deployment.
4. `DeploymentOverviewPage` (`/agents/:name/d/:depId`): the tab bar now shows **Overview /
   Runs / Memory / Conversations**; Conversations lists this deployment's threads → click →
   deployment chat opens seeded (`?session=`) → follow-up continues. Memory tab unchanged.

Record any step that can't run (no live pod) in
`docs/testing/manual-ui-e2e-test-plan.md` with the reason.

---

## Rollback

Tags are immutable; to revert, redeploy the previous tags:

```bash
# set REGISTRY_API_TAG=0.2.191 / STUDIO_TAG=0.1.144 in the three files, then:
bash scripts/deploy-cpe2e.sh
```

No DB rollback needed — POC-5 added no migration.

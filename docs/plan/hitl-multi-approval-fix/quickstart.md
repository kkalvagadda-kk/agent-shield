# Quickstart — HITL multi-approval fix (build / deploy / test)

Copy-pasteable. Local docker-desktop Kubernetes (`agentshield-platform` namespace). Run from the
repo root `/Users/kalyankalvagadda/code/agent-shield`. `node` is on `/opt/homebrew/bin`
(v26.5.0) so Vitest/Playwright run locally.

---

## 0. Prerequisites (once)

```bash
kubectl config current-context            # expect docker-desktop
kubectl get pods -n agentshield-platform  # registry-api / studio / declarative-runner Running
cd studio && npx playwright install chromium && cd ..   # first time only (for Playwright)
```

The suite-79 fixture `research-summarize` (reactive, sequential: `researcher-agent`
[web_search, risk=high] + `summarization-agent`) must be deployed and the `researcher-agent`
pod must carry the **serper** credential. Set `WF_ID` if your workflow id differs from the
suite default.

---

## 1. Syntax / typecheck gates (fast, no deploy)

```bash
# Python (registry-api core fix)
python3 -c "import ast; ast.parse(open('services/registry-api/routers/approvals.py').read())"
cd services/registry-api && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers()" && cd ../..

# Python (declarative-runner, only if Task 4 done)
python3 -c "import ast; ast.parse(open('services/declarative-runner/workflow_executor.py').read())"

# Frontend
cd studio && npm run typecheck && cd ..

# bash suite syntax
bash -n scripts/e2e/suite-79-workflow-hitl.sh
bash -n scripts/e2e/suite-45-hitl-e2e.sh
```

---

## 2. Build + deploy (local docker-desktop)

Bump tags first (Task 7): registry-api `0.2.205→0.2.206`, studio `0.1.154→0.1.155` in BOTH
`scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml` (declarative-runner
`0.1.58→0.1.59` only if Task 4).

```bash
# registry-api (core fix)
docker build -t registry.internal/agentshield/registry-api:0.2.206 services/registry-api/

# studio (build runs `tsc && vite build` = the TS gate)
docker build -t registry.internal/agentshield/studio:0.1.155 studio/

# declarative-runner (ONLY if Task 4 changed resume())
# docker build -t registry.internal/agentshield/declarative-runner:0.1.59 services/declarative-runner/

# Roll out (tags are baked into values.yaml — no --set)
helm upgrade --install agentshield charts/agentshield \
  -n agentshield-platform --reset-values --force-conflicts --timeout 20m

kubectl rollout status deploy/registry-api -n agentshield-platform
kubectl rollout status deploy/studio       -n agentshield-platform
```

> Quick single-service reload alternative (if the chart is otherwise current):
> `kubectl set image deploy/registry-api registry-api=registry.internal/agentshield/registry-api:0.2.206 -n agentshield-platform`
> — but the canonical path is the `helm upgrade` above so `values.yaml` stays the source of truth.

---

## 3. Backend e2e (the reproduce-first gate)

```bash
# Reproduce BEFORE the fix (expect T-S79-004b FAIL against 0.2.205):
bash scripts/e2e/suite-79-workflow-hitl.sh

# After deploying 0.2.206 (expect all PASS, or 004a loud SKIP):
bash scripts/e2e/suite-79-workflow-hitl.sh
bash scripts/e2e/suite-45-hitl-e2e.sh

# Override the fixture if needed:
WF_ID=<your-workflow-uuid> bash scripts/e2e/suite-79-workflow-hitl.sh
```

In-pod Keycloak token recipe (used by the suites; the CURRENT platform-admin sub is
`047fad5f-f38c-430a-bfba-6e4d9009314b` — the old `75c7c8b3…` now 401s):
```python
httpx.post('http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token',
    data={'grant_type':'password','client_id':'agentshield-studio',
          'username':'platform-admin','password':'PlatformAdmin2024'}, timeout=10)
```

---

## 4. Frontend tests

```bash
cd studio
npm run test -- WorkflowChatPage          # new Vitest (deterministic re-park re-render)
npm run test                              # full Vitest sweep (incl. WorkflowBuilderPage)
npm run typecheck
cd ..

# Playwright (best-effort live double-park) OR the manual step in the manual test plan:
bash scripts/studio-e2e.sh e2e/workflow-chat-double-approval.spec.ts
bash scripts/studio-e2e.sh e2e/workflow-inline-approval-live.spec.ts   # regression: builder inline HITL
```

---

## 5. Live diagnosis commands (for docs/debugging/012 — the exact evidence trail)

```bash
POD=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')

# Approvals on a thread (the smoking gun: one approved + one pending after a "completed" run)
kubectl exec -n agentshield-platform "$POD" -c registry-api -- \
  psql "$DATABASE_URL" -c \
  "SELECT id,status,tool_name,tool_args->>'query' AS q,created_at
     FROM approvals WHERE thread_id='<THREAD_ID>' ORDER BY created_at;"

# The child member run + parent workflow run status
kubectl exec -n agentshield-platform "$POD" -c registry-api -- \
  psql "$DATABASE_URL" -c \
  "SELECT id,agent_name,status,parent_run_id,left(output,80) AS output
     FROM agent_runs WHERE thread_id='<THREAD_ID>' OR id='<PARENT_RUN_ID>';"

# run_steps for the member (ONE web_search step == the second call never landed)
kubectl exec -n agentshield-platform "$POD" -c registry-api -- \
  psql "$DATABASE_URL" -c \
  "SELECT step_number,name,output->>'kind' FROM run_steps WHERE run_id='<CHILD_ID>' ORDER BY step_number;"

# registry logs around the resume (look for 'resume posted ... status=200' then a wrong 'completed')
kubectl logs -n agentshield-platform "$POD" -c registry-api --since=15m | grep -iE "resume|re-park|advanc|approval"

# member pod logs (the pod DID re-park: on_interrupt + a new approval id)
MPOD=$(kubectl get pods -n agentshield-platform -l app=researcher-agent -o jsonpath='{.items[0].metadata.name}')
kubectl logs -n agentshield-platform "$MPOD" --since=15m | grep -iE "interrupt|approval|web_search"
```

---

## 6. Self-verify the fix (drive the real flow — do not trust a green test alone)

1. Studio → open the `research-summarize` workflow → **Open Chat**.
2. Send: *"Search the web for the current weather in Austin, Texas, and separately search the
   web for the current weather in Seattle, Washington. Report both."*
3. Approve the first inline approval → the **inline panel must re-appear** for the second search
   → approve it.
4. Confirm the transcript shows a **grounded** answer (real weather, not the echoed prompt) and
   the run is `completed`.
5. Confirm zero orphaned approvals:
   ```bash
   kubectl exec -n agentshield-platform "$POD" -c registry-api -- \
     psql "$DATABASE_URL" -c \
     "SELECT count(*) FROM approvals WHERE thread_id='<THREAD_ID>' AND status='pending';"  # expect 0
   ```
6. If gemma refuses to double-search, the deterministic backstop (`suite-79` T-S79-004b) is the
   binding proof — record the SKIP in the run notes, not a pass.
</content>

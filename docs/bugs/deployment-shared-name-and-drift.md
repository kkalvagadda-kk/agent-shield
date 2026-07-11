# Bug: "Agent pod is unreachable" — deployment lifecycle collision + drift

## Symptom

User chats with a deployed agent (`serper-agent-4`) and gets **"Agent pod is
unreachable. It may still be starting."** The deployment record shows
`status=running` in the DB, but there is **no K8s Deployment, Service, or pod**
for it. The chat resolves to the running record and streams to a Service with
zero endpoints → `httpx.ConnectError` → the "unreachable" message.

This is NOT the OPA cold-start and NOT the HITL UI (both fixed separately). It is
a deployment-lifecycle bug.

## Root Causes

### RC-1: Shared K8s name across deployment records (the collision)

**Where:** `services/deploy-controller/manifest_builder.py` — `k8s_name = f"{agent_name}-{environment}"`; used by `build_service` and `build_deployment`.

**Problem:** Every sandbox deployment record for an agent (e.g. `serper-agent-4-f87d`, `-e932`, `-4dcc`) maps to the **same** K8s object name `serper-agent-4-sandbox`. When the lifecycle handler terminates an *old* record (`_handle_lifecycle_transitions`, `main.py:95-106` → `k8s.delete_deployment` + `k8s.delete_service`), it deletes the shared Deployment **and Service** that a *newer, still-running* record depends on.

**Evidence:** k8s events showed a rollout (`serper-agent-4-sandbox-b75b59fbc`) then the whole Deployment + Service vanished; DB had `4dcc` (running, 19:45) alongside `f87d/e932/fcde` (terminated), all with `k8s_deployment_name=serper-agent-4-sandbox`.

**Fix (proposed, not yet done):** give each deployment record a **unique** K8s name (append the short id, e.g. `serper-agent-4-sandbox-4dcc`) so terminate cleanup can never touch another record's resources. Store it on `deployments.k8s_deployment_name` (already a column) and use it everywhere (Service selector, HTTPRoute backendRef, chat proxy target).

### RC-2: No drift healing (the silent divergence)

**Where:** `services/deploy-controller/main.py:127` — the reconcile loop only fetches `status=pending`.

**Problem:** A record that is `running` in the DB but whose pod/Deployment was deleted is **never** re-reconciled. DB and cluster diverge silently; nothing detects or repairs it.

**Fix (proposed, not yet done):** add a drift pass — for each `running` record, verify its K8s Deployment exists with ≥1 ready replica; if missing, either rebuild it or flip the record out of `running`. DB `status=running` must imply a live pod.

### RC-3: OPA 5-min cold start × 60s reconcile timeout (fixed)

**Where:** `bundle_generator.py` gated agents on `status='running'`; `reconciler.py:15` `_POLL_TIMEOUT_SECONDS=60`.

**Problem:** A new pod couldn't pass OPA-gated readiness within 60s because the bundle didn't include the agent until it was already `running` (chicken-and-egg), so reconcile could mark it `failed`.

**Fix (done, registry-api 0.2.132):** `bundle_generator` now includes `status IN ('deploying','running')`, so a new agent's identity is in the bundle before its pod is ready. OPA poll intervals lowered 30/60→5/15s. Measured cold start: **~5 min → ~22s**. See `docs/debugging/003-opa-bundle-5min-cold-start.md`.

## Recovery performed

Set the drifted `running` record back to `pending` (`PATCH /api/v1/deployments/{id}` `{"status":"pending"}`); the reconciler rebuilt Service + Deployment + pod (2/2 Ready in ~22s). Verified the user's exact chat end-to-end (agent responds → `web_search` → `approval_requested`, no "unreachable") via API and Playwright (`e2e/hitl-deployment-chat.spec.ts`).

## Image Tags

- registry-api 0.2.132 (RC-3 fix); studio 0.1.108 (HITL UI); migration 0051.

## Lessons

1. **The recurring "X is broken" messages were one lifecycle disease with many faces** — fixing the symptom each error pointed at (nginx buffering, UI handler, readiness probe) never touched the deployment machinery underneath. The OPA readiness probe even converted "auth issue" into "unreachable."
2. **`status=running` in the DB must be an invariant backed by a liveness check**, not a fire-and-forget write. Without drift reconciliation, the DB lies.
3. **Shared/derived names across independent records are a collision class** — unique-per-record names make illegal cross-deletes unrepresentable (cf. CLAUDE.md "make illegal states unrepresentable").
4. **Test the churn/cold-start path, not warm hand-picked pods.**

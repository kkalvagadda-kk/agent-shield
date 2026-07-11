# 006 — Production consumer chat: "no running production deployment" (FK table mismatch)

**Date:** 2026-07-10
**Surface:** Catalog production chat (`CatalogChatPage`) → `POST /agents/{name}/chat` (`context="production"`)
**Symptom (user words):** "Error: failed to start chat. Check that this agent has a
running production deployment — when running serper-agent-4 on prod. Looks similar to
what we observed in sandbox."

## TL;DR

The production pod **was running and healthy**. The chat start still failed because
`start_chat` inserted a `production_deployments` id into `PlaygroundRun.deployment_id`,
a column whose **foreign key points at the *sandbox* `deployments` table**. The INSERT
hit a `ForeignKeyViolation` → 500 → the frontend's generic catch printed "no running
production deployment." A healthy-pod symptom that reads as "not deployed" = **look past
the error text to the actual failing statement.**

## Why the message was misleading

`CatalogChatPage` wraps the whole start-chat call in one `try/catch`; **any** throw —
404, 503, or a 500 from an FK violation — renders the same string:
`"failed to start chat. Check that this agent has a running production deployment."`
So the message described one hypothesis (no deployment) while the real cause was a
different one (DB insert rejected). First rule here: **don't trust a generic
client-side error to name the server-side cause. Reproduce and read the 5xx.**

## The architecture that made this a trap

Sandbox and production deployments live in **two different tables**:
- **Sandbox** → `deployments` (the `Deployment` model; `deploy-controller` reconciler).
- **Production** → `production_deployments` (the `ProductionDeployment` model, keyed off
  the *published-artifact* model; `production_reconciler`; controller PATCHes
  `/catalog/internal/production-deployments/{id}/status`).

`chat.py::_running_production_deployment` resolves the chain Agent → PublishedArtifact
(by name) → ProductionDeployment(status='running') and **synthesizes a `Deployment`
object** (`id = prod_dep.id`) so the downstream proxy code can be shared. That synthetic
object carries a **`production_deployments` id in a field typed as a `Deployment`.**
When `start_chat` then did `PlaygroundRun(deployment_id=deployment.id, ...)`, it wrote a
production id into a column FK-constrained to `deployments` → violation.

Sandbox chat never tripped it because there the id **is** a real `deployments` row.

## Root cause

- **Where:** `services/registry-api/routers/chat.py::start_chat`; column
  `PlaygroundRun.deployment_id` in `models.py` (added by migration `0051`).
- **Problem:** one `deployment_id` column with a hard FK to `deployments` cannot
  represent a production run — an **illegal state that the schema still let the code
  attempt**, blowing up at INSERT time. Introduced when `deployment_id` was added for
  HITL provenance (migration 0051) without accounting for the production table split.
- **Design smell:** the sibling `AgentRun` model already solved this correctly — it has
  **two** columns, `sandbox_deployment_id` (FK `deployments`) and
  `production_deployment_id` (FK `production_deployments`), and `start_chat` was already
  setting them right (`...if is_production else None`). Only `PlaygroundRun` had the
  single-column flaw. The fix was to make `PlaygroundRun` match its sibling.

## Fix (make the illegal state unrepresentable, not a runtime guard)

1. **Migration `0054`** — add `playground_runs.production_deployment_id uuid NULL
   REFERENCES production_deployments(id) ON DELETE SET NULL` (idempotent guard).
2. **`models.py`** — add `PlaygroundRun.production_deployment_id`; document that
   `deployment_id` is sandbox-only and the two must never be crossed.
3. **`chat.py::start_chat`** — write the column whose FK the id actually satisfies:
   `deployment_id = dep.id if not is_production else None`;
   `production_deployment_id = dep.id if is_production else None`. (Mirrors the AgentRun
   lines right below it.)
4. **`approvals.py::_load_provenance`** — `LEFT JOIN production_deployments` and coalesce
   so the HITL console shows `environment='production'` + the namespace for production
   approvals. `_derive_context` already returned `'production'` straight from
   `run.context`, so it needed no change.

Rejected bandaid: dropping the FK to let `deployment_id` hold either table's id. That
re-introduces the ambiguity ("which table is this id in?") the two-column design removes,
and silently breaks the `Deployment` join in provenance/context resolution.

## Investigation trail (commands)

```
# 1. Error source — it's a CLIENT-side generic catch, not the server's words
grep -rn "running production deployment" studio/src services/registry-api
# → CatalogChatPage.tsx (frontend fallback) + chat.py resolver comment

# 2. Is the pod actually down?  (No — it's up)
kubectl get deploy,pods -A | grep serper        # serper-agent-4-production 2/2 Running

# 3. Does the resolver's chain resolve?  (Yes — every link intact)
#    Agent → PublishedArtifact(name) → ProductionDeployment(status='running')
psql: production_deployments f9f8872d  artifact_id matches  status=running   ✅

# 4. So the resolve succeeds but the INSERT fails — check the write column's FK
grep -n "deployment_id" services/registry-api/models.py
# → PlaygroundRun.deployment_id  ForeignKey("deployments.id")   ← prod id ≠ deployments row
```

## Verification

- **suite-14 T-S14-008** (new): a `production_deployments` id is accepted by
  `production_deployment_id` and **rejected** by `deployment_id` (FK enforced). Skips if
  no production deployment exists.
- Live: `start_chat context=production` against serper-agent-4's running production
  deployment returns a `run_id` (no 500), and the `PlaygroundRun` row has
  `production_deployment_id` set / `deployment_id` null.
- `ast.parse` on the 3 Python files; mapper configure post-deploy.

## Generalized principles

- **A generic client catch hides the server cause.** When a UI error names a hypothesis,
  reproduce the request and read the actual status/body before believing it.
- **"Healthy pod but 'not deployed'" ⇒ suspect a lookup/persistence mismatch, not the
  workload.** The failure is between the row and the pod, not in the pod.
- **Two backing tables for one concept need two FK columns, not one polymorphic id.**
  Copy the sibling model that already got it right (`AgentRun`) instead of loosening a
  constraint. Make illegal states unrepresentable at the schema, don't guard at runtime.

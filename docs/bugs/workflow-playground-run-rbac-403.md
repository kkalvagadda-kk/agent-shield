# Workflow Playground chat "failed to start workflow run" — registry-api SA lacks RBAC in `agentshield-playground`

**Found:** 2026-07-28, Claude-in-Chrome journey Leg 19 (workflow conversation saves + rehydrates), driving `WorkflowChatPage` against the deployed cluster.
**Fixed:** NOT fixed — recorded as a gap. Blocks the entire workflow-Playground-chat surface on this cluster.
**Deployed context:** registry-api `0.2.242`, EKS `agentshield-platform`.

## Symptom

Open a deployed workflow's chat (`/workflows/{id}/d/{depId}/chat`, e.g. `poc-research-answer`), send a
turn. The user bubble renders and a per-member (`poc-researcher`) bubble opens, then the assistant
bubble shows **"Error: failed to start workflow run."** After reload the transcript is **empty** — but
that is because the run never started, so no run/transcript rows were ever written (not a persistence
regression).

## Root cause

`WorkflowChatPage` streams `POST /workflows/{id}/runs/stream`. To run a workflow in the Playground,
registry-api provisions an **ephemeral playground-runner** in the **`agentshield-playground`**
namespace, which needs a per-run ServiceAccount. registry-api's own ServiceAccount is not authorized to
manage ServiceAccounts in that namespace:

```
serviceaccounts "playground-runner-75c7c8b3-...-sa" is forbidden:
User "system:serviceaccount:agentshield-platform:agentshield-registry-api"
cannot get resource "serviceaccounts" in API group "" in the namespace "agentshield-playground"
(reason: Forbidden, code: 403)
```

So the ephemeral runner is never created → the stream POST returns non-OK → the frontend shows the
generic "failed to start workflow run." This is the **same class** as the earlier deploy-controller
machine-identity SA gap: a service is missing a Role/RoleBinding in a namespace it must provision into.

## Impact

The workflow-Playground-chat path is **entirely non-functional** on this cluster — any user chatting
with a deployed workflow hits this 403. (Single-agent Playground chat is unaffected; it does not create
a playground-runner SA in `agentshield-playground` the same way.) Leg 19's persistence+rehydration
assertion therefore cannot be exercised live. The persistence *code* path itself was previously verified
by reading (`composite_workflows.py` stamps `user_id`+`session_id` on the parent `AgentRun`; `memory.py`
rehydrates off those) — see the Phase-1 plan's F-A note — so this is an infra/RBAC blocker, not a
persistence-logic bug.

## Recommended class-fix

Add a `Role` (get/list/create/delete on `serviceaccounts`, and whatever the playground-runner also
needs — `pods`, `secrets`, `configmaps`, `jobs`) in namespace `agentshield-playground`, bound via
`RoleBinding` to `system:serviceaccount:agentshield-platform:agentshield-registry-api`, in the Helm
chart (`charts/agentshield/templates/`). Mirror exactly the permission set the playground-runner
provisioning code requires — grant no more than that. Then re-run Leg 19 to prove
save→reload→rehydrate on a workflow chat.

## Cross-links

- Frontend origin of the message: `studio/src/pages/WorkflowChatPage.tsx:243` (and `CatalogChatPage.tsx:434`).
- Related machine-identity SA gap: `docs/bugs/` deploy-controller SA / OPA bundle notes.
- Gap ledger: `docs/testing/manual-ui-e2e-test-plan.md`.

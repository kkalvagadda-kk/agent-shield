# 008 — Production OPA governance is non-functional: agents never registered as identities

**Date:** 2026-07-10
**Surface:** Production agent chat (serper-agent-4) — any governed tool call
**Symptom (user words):** "authentication issue with the search tool" in production —
"same series of issues we saw on sandbox." User asked to stop patching symptoms and fix
the class.

## TL;DR

Production agent pods are **structurally excluded from OPA tool-call governance**. Their
ServiceAccount is created but **never registered as a machine identity**, and even if it
were, `agent_identities.deployment_id` FKs the sandbox `deployments` table while
`bundle_generator` INNER-JOINs `deployments` — so a production SA subject can neither be
stored nor bundled. OPA is fail-closed, so every production tool call returns
`deny_reason="agent_unauthenticated"`. The LLM narrates that as "authentication issue with
the search tool." **HITL and tool governance simply do not run in production.**

This is the **third** bug from the same root cause (`deployments` vs `production_deployments`
two-table split): doc 006 (`PlaygroundRun.deployment_id` FK), doc 007 (tool-credential
envFrom only in sandbox path), now the identity/OPA path.

## Correcting doc 007's framing

Doc 007 assumed the production Serper call reached Serper.dev and 401'd on a missing
credential. That's almost certainly **not** what the user hit in production: the pod runs
real OPA (`AGENTSHIELD_OPA_URL` set, sidecar reachable → not DEV_MODE), so a tool call is
**OPA-denied before it ever reaches Serper**. The missing tool credential (doc 007) is a
real but *secondary* gap — it only surfaces once the identity fix lets OPA allow the call.
Order of production blockers: (1) OPA identity, then (2) tool credential.

## How the two walls each fully exclude production

1. **Reconciler never registers the identity.** Sandbox `reconciler.py` does
   `ensure_service_account` → `POST /api/v1/agents/{name}/identities`. Production
   `production_reconciler.py` did only `ensure_service_account` — no `/identities` POST in
   the file. So no `agent_identities` row for the production SA subject.
2. **Schema can't store it anyway.** `AgentIdentity.deployment_id` = `FK deployments.id`
   (`models.py`). A production deployment id belongs to `production_deployments` → FK
   violation. (Same class as doc 006's `PlaygroundRun`.)
3. **Bundle query can't read it anyway.** `bundle_generator.generate_bundle_data`
   INNER-JOINs `agent_identities → deployments → agent_versions`, gated on
   `deployments.status`. `production_deployments` is never referenced → production subjects
   never enter `data.agents`.
4. **OPA is fail-closed.** `opa_policy/agentshield.rego`: `default allow := false`;
   `agent_unauthenticated` when `data.agents[input.sa_subject]` is absent. SDK
   `opa_client.py` in PROD_MODE returns `allow=False`. Hard deny on every tool call.

## Fix — the class fix, mirroring the established two-table pattern

1. **Schema:** `agent_identities.production_deployment_id` → FK `production_deployments`
   (migration 0055). Two mutually-exclusive FK columns, exactly like
   `PlaygroundRun`/`AgentRun`. Never a polymorphic id.
2. **Endpoint/schema:** `AgentIdentityCreate`/`AgentIdentityResponse` +
   `create_agent_identity` accept & persist `production_deployment_id`.
3. **Shared helper:** new `deploy-controller/identity.py::register_agent_identity`, called
   by BOTH reconcilers — sandbox passes `deployment_id`, production passes
   `production_deployment_id` (both `reconcile_production` and `reconcile_workflow_production`).
   Mirrors the `tool_secrets.py` extraction. `ensure_service_account` returns the SA
   subject (single source of truth) — captured, not hand-built.
4. **Bundle:** `bundle_generator` UNIONs a production leg
   (`agent_identities → production_deployments → published_versions.config_snapshot->'tools'`,
   gated on `production_deployments.status`). Bundle is pull-generated live (no trigger).

Also fixed the `0.1.32` `NameError` (missing `tool_secrets` import in production_reconciler,
introduced during the reactive doc-007 attempt).

New reference doc: `docs/design/sandbox-production-parity-architecture.md` — the anti-drift
rules so this class stops recurring (shared per-pod helpers; two-column FK pattern).

## Recovery
The existing serper-agent-4 production deployment (and any other running production
deployment) predates the fix → has a pod but no identity row. Flip
`production_deployments.status='pending'` to force re-reconcile; the reconciler now
registers the identity + copies the tool secret + rebuilds the pod. This is a strict
improvement — those deployments are already fail-closed today.

## Verification
- `GET /api/v1/bundle/data.json` → `data.agents` contains
  `system:serviceaccount:production-serper-agent-4:agent-serper-agent-4-sa` with a
  non-empty `tools` array.
- A governed production tool call → OPA `allow` (not `agent_unauthenticated`).
- A high-risk tool → OPA `require_approval` + HITL pause in production.
- Serper credential present via `envFrom`; a real search returns results.
- suite-7 (machine identity) + suite-18 (OPA governance) extended with a production arm.

## Generalized principles
- **"Same class of bug in production" = look for a sandbox-only code path or a
  `deployments`-only assumption.** Grep the production reconciler for every step the
  sandbox reconciler does; grep every deployment-referencing column/query for
  `production_deployments`.
- **Governance that "works in sandbox" can be entirely absent in production** without any
  crash — it just silently fails closed (or, in DEV_MODE, silently fails open, which is
  worse: ungoverned high-risk tools). Prove governance in production, don't assume parity.
- **Third occurrence of a pattern → write the reference doc and the shared helper**, don't
  fix the third instance in isolation.

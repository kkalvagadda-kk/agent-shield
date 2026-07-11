# 007 — Production tools 401: "authentication issue with the search tool"

> **Correction (see doc 008):** this doc assumed the production Serper call reached
> Serper.dev and 401'd on the missing credential. In production the pod runs real OPA
> (not DEV_MODE), so a governed tool call is **OPA-denied (`agent_unauthenticated`) before
> it ever reaches Serper** — because production agents were never registered as machine
> identities (doc 008). The missing tool credential below is a **real but secondary** gap
> that only surfaces once the identity fix lets OPA allow the call. Order of production
> blockers: (1) OPA identity → (2) tool credential. The credential fix here is still
> correct and required.


**Date:** 2026-07-10
**Surface:** Production agent chat (serper-agent-4) — Serper web_search tool
**Symptom (user words):** "I need to search for the current weather forecast for Austin
tomorrow… I wasn't able to retrieve the weather data due to an **authentication issue
with the search tool**." Same class we saw earlier on sandbox.

## TL;DR

The production pod shipped **without the tool-credential secret**. The sandbox
reconciler resolves each tool's `auth_config_id` → K8s Secret, copies it into the pod
namespace, and exposes it via `envFrom`. The **production reconciler is a separate code
path and never did any of that** — so the Serper API key wasn't in the production pod's
env, and Serper.dev returned 401. The agent's LLM paraphrased that as "authentication
issue with the search tool."

The deeper defect: **sandbox and production keep divergent copies of pod-provisioning
logic**, and the tool-credential step lived in only one. Fixing just the symptom would
leave the next step to drift the same way.

## Evidence

```
# Production pod: LLM secrets present, but NO tool credential
kubectl get pod -n production-serper-agent-4-f9f8872d ... -o json
  ENVFROM: []                         ← no tool credential
  ENV: [... ANTHROPIC_API_KEY, AWS_* ...]   ← LLM secret injection works

# Sandbox pod (works): the Serper credential is injected
kubectl get pod -n agents-platform serper-agent-4-sandbox-... -o json
  ENVFROM: ['auth-config-eff822b7-...']   ← tool credential present
  (that secret's keys: ['serper_api_key'])
```

## Root cause

- **Where:** `services/deploy-controller/production_reconciler.py::reconcile_production`.
- **Problem:** it called `build_deployment(...)` **without `tool_secret_refs`** and never
  resolved/copied tool auth-config secrets. The sandbox path
  (`reconciler.py`) had a ~35-line inline block that did: fetch
  `/api/v1/agents/{name}/tools` → per-tool `auth_config_id` →
  `/api/v1/auth-configs/{id}/secret-ref` → `copy_secret(platform_ns → pod_ns)` → pass
  `tool_secret_refs` to `build_deployment` (which emits `envFrom`). Production had none
  of it. Two independent reconcilers that must provision identical pods diverged.

## Fix (kill the divergence, don't patch one branch)

Extract the resolve+copy into **`tool_secrets.resolve_and_copy_tool_secrets(agent_name,
namespace, k8s, settings)`** and call it from **both** reconcilers:
- `reconciler.py` — replaced its inline block with the shared call (behavior identical).
- `production_reconciler.py::reconcile_production` — added the call + passes
  `tool_secret_refs=` to `build_deployment`.

Now the two paths physically share the step, so a future change to credential handling
can't silently apply to only one environment. `copy_secret` sources from the platform
namespace (where the API auto-creates `auth-config-{uuid}` secrets) into the pod
namespace; the container reads them via `envFrom`.

Recovering the already-running deployment: the production reconciler only processes
`pending` rows, so an existing `running` deployment won't self-heal. Flip its
`production_deployments.status` back to `pending` → the controller re-reconciles →
copies the secret + rolls the pod with `envFrom`.

## Out of scope (documented gap)

**Workflow-production member tool credentials.** `reconcile_workflow_production` builds
one orchestrator pod that runs member agents' tools, but `resolve_and_copy_tool_secrets`
resolves via `/agents/{name}/tools` — a *workflow* name isn't an agent, so it returns
nothing. Workflow **sandbox** has the same limitation (both go through the agent-tools
endpoint), so this is a pre-existing platform gap, not a production divergence. Needs a
member-aware resolver (iterate workflow members → their tools → auth_configs). Tracked in
the gap ledger + `docs/design/todo/tool-credential-management.md`.

## Verification

- New production pod for serper-agent-4 has `envFrom: [auth-config-...]` and the
  `serper_api_key` env var present (matching the sandbox pod).
- Tool call succeeds (or at minimum no longer 401s on auth).
- deploy-controller **0.1.32**.

## Generalized principles

- **Two reconcilers for "the same pod, different environment" WILL drift.** Any per-pod
  provisioning step (secrets, env, mounts, sidecars) must live in a shared helper both
  call — not copy-pasted. When you find a step in one path, grep the sibling path for it.
- **"Authentication issue with the tool" from an agent = a real upstream 4xx.** The LLM
  narrates tool failures in plain English; translate it back to the HTTP layer and check
  the credential/identity before touching prompts or the model.
- **Compare the broken pod to a working one field-by-field** (`env`, `envFrom`,
  `volumeMounts`, `volumes`). The missing line is the bug.

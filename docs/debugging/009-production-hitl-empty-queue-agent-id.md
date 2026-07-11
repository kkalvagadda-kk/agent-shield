# 009 — Production HITL: approval shows in chat but never reaches the queue

**Date:** 2026-07-11
**Surface:** Production agent chat → high-risk tool → HITL; Production HITL Queue
**Symptom (user words):** "I do not see the request for approval in the HITL queue, but I
see the necessary information on the chat screen saying I need approval to access the
tool." Queue shows "No approvals in this queue — 0 total."

## TL;DR

The governance fix (doc 008) worked — production now **gates** the tool and the chat shows
the approval prompt. But the approval **record was never created**: production pods ship
with **`AGENTSHIELD_AGENT_ID` empty**, so the SDK's `POST /api/v1/approvals/` sends
`agent_id=""` → **422 `uuid_parsing`**. The SDK logs the failure and "proceeds with the
interrupt" anyway, so the chat pauses (prompt shown) but there is no DB row → the queue is
correctly empty and the pause can never be actioned (the chat hangs).

Same parity class as 006/007/008: the **production reconciler synthesizes an agent dict
that's missing a field the sandbox path gets for free** — here, the agent's `id`.

## The chain

1. `ApprovalCreate.agent_id: uuid.UUID` (required) — `schemas.py`.
2. SDK `hitl.require_approval` posts `"agent_id": config.AGENT_ID` (from
   `AGENTSHIELD_AGENT_ID`).
3. `manifest_builder.py:143` sets that env from `agent.get("id", "")`.
4. **Production `production_reconciler._build_agent_dict` never includes `id`** (it
   synthesizes `{name, team, agent_type, agent_class, execution_shape}` from
   `config_snapshot`). Sandbox's `reconciler.py` builds the dict from the live `agents`
   row, which has `id`. → production env is `""`.
5. `""` → Pydantic UUID validation → `422 {"type":"uuid_parsing","loc":["body","agent_id"],
   "msg":"Input should be a valid UUID, invalid length: expected 32, found 0"}`.
6. SDK: `Could not create approval record… 422 … proceeding with interrupt` → no row →
   empty queue + hung chat.

## Why it surfaced only now

Before doc 008, production OPA hard-denied every tool (`agent_unauthenticated`), so
`require_approval` was never reached. Once identity registration made OPA return
`require_approval`, the approval-creation path ran in production **for the first time** and
immediately hit the empty `agent_id`.

## Diagnosis trail
```
# approval rows for the production runs → ZERO (queue genuinely empty)
SELECT ... FROM approvals WHERE thread_id LIKE '<prod run id>%'   → 0

# the pod's own log
kubectl logs <prod pod> | grep approval
  → "Could not create approval record … 422 Unprocessable Entity … proceeding with interrupt"

# reproduce the 422 to find the field (test each candidate)
POST /approvals {agent_id:'', risk_level:'high', …}
  → 422 uuid_parsing loc=["body","agent_id"]      ← the field

# confirm the empty env inside the pod
kubectl exec <prod pod> -- python -c "from agentshield_sdk import config; print(repr(config.AGENT_ID))"
  → ''        # sandbox pod: a real UUID
```

## Fix

- `catalog.py::list_pending_production_deployments` (the internal endpoint the controller
  polls): add `"source_agent_id": str(artifact.source_id)` to each `dep_info`
  (`artifact.source_id` IS `agents.id` — the endpoint already joins on it for LLM
  resolution).
- `production_reconciler._build_agent_dict`: set `"id": dep_info.get("source_agent_id")`
  so `manifest_builder` populates `AGENTSHIELD_AGENT_ID`.

Applies to both `reconcile_production` and `reconcile_workflow_production` (shared
`_build_agent_dict`). Recovery: re-reconcile running production deployments to rebuild the
pod env; the SDK then posts a valid `agent_id` and the approval row is created.

## Secondary issue (flagged, not yet fixed)

The SDK **swallows** the approval-creation failure and interrupts anyway
(`hitl.require_approval` `except: … proceeding with interrupt`). Consequences: (a) it made
this hard to diagnose (only the status, not the response body, was logged), and (b) it
leaves the chat paused on an approval that can never be actioned. Options: fail-closed
(deny the tool if the record can't be created) and/or log `resp.text`. Needs an SDK/runner
rebuild + a product decision on fail-closed vs proceed.

## Generalized principles
- **When a production-only path synthesizes a domain object, diff its fields against the
  sandbox object field-by-field.** The missing field is usually the bug (id, here; earlier:
  tool_secret_refs, deployment FK column, identity). `manifest_builder` trusts
  `agent["id"]`; production silently passed `""`.
- **Silent-swallow of a governance write is a latent outage.** A HITL approval that can't
  be persisted must not read as "waiting for approval" — surface it or fail closed.
- **A required `uuid.UUID` field + an empty-string env = 422 with a precise `loc`.** Read
  the validation `loc`, then trace that env var back to who sets it.

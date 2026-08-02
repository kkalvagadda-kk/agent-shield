# WS-2 Contract — OPA `user_identity_ok` daemon rule

> **SHIPPED, and folded into the consolidated docs (2026-08-02).** This rule is live at
> `opa_policy/agentshield.rego:101-108`. It is documented as "Gate 6" in
> [`opa-authorization-contract.md`](../../../../design/opa-authorization-contract.md) §11, and its
> three open deltas (registry-side `agent_class`, playground/sandbox exemption, `require_approval`
> gating) are tracked in
> [`identity-propagation-architecture.md`](../../../../design/identity-propagation-architecture.md)
> §4.6. Kept as the WS-2 implementation record.

Extends `services/registry-api/opa_policy/agentshield.rego` (package `agentshield`; decision surface
`{allow, require_approval, reason, deny_reason}`, `default allow := false`, `agent :=
data.agents[input.sa_subject]`). WS-2 adds an **identity floor** to `allow` without touching the existing
risk-based `require_approval` (`:103`).

## New input fields (registry-api populates these in the OPA input)

| Field | Meaning | Source |
|---|---|---|
| `input.agent_class` | `"user_delegated"` \| `"daemon"` | the run's executable class (WS-0) |
| `input.user_id` | the live user's `sub`, **empty string** for a trigger-run with no caller | `resolve_principal` |
| `input.trigger_type` | `"manual"`/`"api"`/`"schedule"`/`"webhook"`/`"workflow"` | the run's trigger |

## Rule

```rego
# ─── identity floor (WS-2) ───────────────────────────────────────────────────
# daemon: no live user required (trigger-run acts as the service identity).
# user_delegated: a live user MUST be present — a missing principal is a DENY,
#                 never a silent downgrade to the service identity (fail-closed).
default user_identity_ok := false

user_identity_ok if {
	input.agent_class == "daemon"
}

user_identity_ok if {
	input.agent_class == "user_delegated"
	input.user_id != ""
}

# ─── wire into the final allow (add the floor as an extra conjunct) ───────────
# existing allow rule (:95) gains:  user_identity_ok
```

## Denial reason

```rego
deny_reason := "missing_user_identity" if {
	input.agent_class == "user_delegated"
	input.user_id == ""
}
```

## Truth table (asserted in `agentshield_test.rego`)

| agent_class | user_id | trigger | `user_identity_ok` | net |
|---|---|---|---|---|
| daemon | "" | schedule | ✅ | allow (risk gates unchanged) |
| daemon | "alice" | manual (chat) | ✅ | allow — a present user is honored (R3 floor, not a cap) |
| user_delegated | "alice" | manual | ✅ | allow |
| user_delegated | "" | schedule | ❌ | **deny** `missing_user_identity` (fail-closed) |

## Non-goals (unchanged by WS-2)

- `require_approval` risk logic (`:103,:142`) — untouched; the identity floor is orthogonal to the risk gate.
- No cryptographic RCT/actor_chain verification in rego — the signed-token check is the separate
  identity-propagation initiative. WS-2's rule reads plain `input.agent_class`/`input.user_id` stamped by
  the trusted `resolve_principal` helper inside registry-api.

# AgentShield unified authorization policy (Phase 9.1 completion).
#
# This is the SINGLE, static Rego policy shared by every agent's OPA sidecar.
# All per-request variation comes from `data` (data.agents / data.grants) produced
# by services/registry-api/bundle_generator.py — never from per-agent Rego.
#
# Wire contract (see docs/design/opa-authorization-contract.md):
#   Request:  POST /v1/data/agentshield  with {"input": {...}}
#   Response: data.agentshield = {allow, require_approval, reason, deny_reason}
#
# `import rego.v1` keeps this valid on both the deployed sidecar (OPA 0.69.0-static)
# and modern OPA (>=1.0), which is what `opa test` runs locally.
package agentshield

import rego.v1

# ─── Defaults (fail-closed) ──────────────────────────────────────────────────
default allow := false
default require_approval := false
default reason := "default_deny"
default deny_reason := ""
default user_identity_ok := false

# ─── Agent entry lookup ──────────────────────────────────────────────────────
# Undefined when the SA subject is not registered in the bundle.
agent := data.agents[input.sa_subject]

# ─── Gate 1: identity present ────────────────────────────────────────────────
# The calling pod's SA subject must be a key in data.agents.
identity_present if {
	data.agents[input.sa_subject]
}

# ─── Gate 2: identity match ──────────────────────────────────────────────────
# The bundle's registered expected_sa_subject must equal the presented subject.
# Prevents an agent from claiming a different agent's SA subject.
identity_matches if {
	agent.expected_sa_subject == input.sa_subject
}

# ─── Risk ranking ────────────────────────────────────────────────────────────
risk_rank := {"low": 1, "medium": 2, "high": 3, "critical": 4}

# Tool entries may be objects {"name","risk"} or bare strings. A bare string, or
# an object with a missing/unknown risk, is treated as "critical" (fail-closed).
_name_of(entry) := entry.name if is_object(entry)

_name_of(entry) := entry if is_string(entry)

_risk_of(entry) := entry.risk if {
	is_object(entry)
	risk_rank[entry.risk]
}

_risk_of(entry) := "critical" if {
	is_object(entry)
	not risk_rank[entry.risk]
}

_risk_of(entry) := "critical" if is_string(entry)

# ─── Gate 3: tool membership over the effective tool set ──────────────────────
# effective set = agent's own tools ∪ grants for the agent's own team.
# We collect the risk *rank* of every effective entry whose name matches the
# requested tool; a non-empty set means the tool is in scope.
_matching_ranks contains risk_rank[_risk_of(t)] if {
	some t in agent.tools
	_name_of(t) == input.tool_name
}

_matching_ranks contains risk_rank[_risk_of(t)] if {
	some t in data.grants[agent.team]
	_name_of(t) == input.tool_name
}

tool_in_set if count(_matching_ranks) > 0

# ─── Gate 4: risk → action ───────────────────────────────────────────────────
# Resolve the matched tool's risk as the MOST SEVERE among matching entries
# (fail-closed if a tool appears both as own and granted with differing risk).
max_rank := max(_matching_ranks)

resolved_risk := r if {
	some r
	risk_rank[r] == max_rank
}

# low / medium / high are allowed to execute; critical / unknown are denied.
risk_allows if resolved_risk == "low"

risk_allows if resolved_risk == "medium"

risk_allows if resolved_risk == "high"

# ─── Identity floor (WS-2) ───────────────────────────────────────────────────
# daemon: no live user required (the trigger-run acts as the service identity).
# user_delegated: a live user MUST be present — a missing principal is a DENY,
#                 never a silent downgrade to the service identity (fail-closed).
# Orthogonal to the risk-based require_approval gate below.
user_identity_ok if {
	input.agent_class == "daemon"
}

user_identity_ok if {
	input.agent_class == "user_delegated"
	input.user_id != ""
}

# ─── Final decision ──────────────────────────────────────────────────────────
allow if {
	identity_present
	identity_matches
	tool_in_set
	risk_allows
	user_identity_ok
}

# High risk is allowed but must pass through HITL.
require_approval if {
	identity_present
	identity_matches
	tool_in_set
	resolved_risk == "high"
}

# ─── deny_reason (mutually exclusive; only meaningful when allow=false) ───────
deny_reason := "agent_unauthenticated" if not identity_present

deny_reason := "identity_mismatch" if {
	identity_present
	not identity_matches
}

deny_reason := "tool_not_granted" if {
	identity_present
	identity_matches
	not tool_in_set
}

deny_reason := "tool_risk_denied" if {
	identity_present
	identity_matches
	tool_in_set
	not risk_allows
}

# Identity floor (WS-2): a user_delegated run with no live principal is a
# fail-closed deny — the missing user is never downgraded to the service identity.
# Guarded with the upstream gates so this stays MUTUALLY EXCLUSIVE with the
# deny_reasons above (agent_unauthenticated / identity_mismatch / tool_not_granted
# / tool_risk_denied); without the guards two bodies could match one request and
# OPA would raise an eval_conflict. Net result is identical to the contract's
# truth-table row 4 (all gates pass except the identity floor → this reason).
deny_reason := "missing_user_identity" if {
	identity_present
	identity_matches
	tool_in_set
	risk_allows
	input.agent_class == "user_delegated"
	input.user_id == ""
}

# ─── reason (short human string; one value per decision) ─────────────────────
reason := "allow_low_risk" if {
	allow
	resolved_risk == "low"
}

reason := "allow_medium_risk" if {
	allow
	resolved_risk == "medium"
}

reason := "require_approval_high_risk" if {
	allow
	resolved_risk == "high"
}

reason := "deny_agent_unauthenticated" if not identity_present

reason := "deny_identity_mismatch" if {
	identity_present
	not identity_matches
}

reason := "deny_tool_not_granted" if {
	identity_present
	identity_matches
	not tool_in_set
}

reason := "deny_tool_risk" if {
	identity_present
	identity_matches
	tool_in_set
	not risk_allows
}

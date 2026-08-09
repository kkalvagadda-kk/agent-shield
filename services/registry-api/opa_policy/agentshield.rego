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
default allow_deanonymize := false

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

# ─── agent_class: from the BUNDLE, never from input (D-1) ────────────────────
# `input.agent_class` is composed by the SDK inside the agent pod, so a compromised pod
# could claim "daemon" and skip the identity floor below — the branch that requires no
# human at all. The bundle keys `agent_class` off `sa_subject`, which Gates 1 and 2 have
# already verified against the pod's ServiceAccount, so it is not the pod's to assert.
#
# Undefined when the agent is unknown, which fails every gate that reads it. That is the
# correct answer for a pod we cannot identify.
agent_class := agent.agent_class

# ─── Decision 45: whose authority is this call made under? ────────────────────
# The caller's teams, as a LIST. Plural because `user_team_assignments.user_sub` is a
# primary key TODAY — one team per user — and that will stop being true; a rule written
# against a scalar would have to be rewritten rather than re-fed.
caller_teams := input.user_teams

# Tools the CALLER's teams may use: granted to them, or owned by them.
# `tool_access.team_may_use_tool` is `owner_team is None or owner_team == team`, so the
# own-team half is usable WITHOUT a grant — intersecting on `grants` alone would deny a
# user their own team's tools.
_caller_usable contains name if {
	some ct in caller_teams
	some t in data.grants[ct]
	name := _name_of(t)
}

_caller_usable contains name if {
	some ct in caller_teams
	some t in data.team_tools[ct]
	name := _name_of(t)
}

# ─── Gate 3: tool membership over the effective tool set ──────────────────────
# Decision 45 — the effective set depends on WHO the run is acting for:
#
#   daemon          agent.tools ∪ grants[agent.team]
#                   No human is involved; the agent delegates its OWN capability. This is
#                   the long-standing rule and is unchanged.
#
#   user_delegated  agent.tools ∩ tools the CALLER's teams may use
#                   "Having grant to agent does not get users in a team grants to all the
#                   tools the agents can use." A tool bound to the agent is NOT enough —
#                   the human's own authority has to reach it too.
#
# Note the asymmetry is deliberate: the daemon branch UNIONs (the agent's authority is the
# ceiling) and the delegated branch INTERSECTS (the narrower of agent and human wins).
_matching_ranks contains risk_rank[_risk_of(t)] if {
	agent_class == "daemon"
	some t in agent.tools
	_name_of(t) == input.tool_name
}

_matching_ranks contains risk_rank[_risk_of(t)] if {
	agent_class == "daemon"
	some t in data.grants[agent.team]
	_name_of(t) == input.tool_name
}

_matching_ranks contains risk_rank[_risk_of(t)] if {
	agent_class == "user_delegated"
	some t in agent.tools
	_name_of(t) == input.tool_name
	_caller_usable[input.tool_name]
}

_matching_ranks contains risk_rank[_risk_of(t)] if {
	agent_class == "user_delegated"
	some t in data.grants[agent.team]
	_name_of(t) == input.tool_name
	_caller_usable[input.tool_name]
}

tool_in_set if count(_matching_ranks) > 0

# In the AGENT's reach — its own bound tools plus what its team is granted. This is the
# agent-side capability, and it is exactly the old (pre-Decision-45) effective set.
#
# The intersection applies to THIS, not to `agent.tools` alone. A first cut used only the
# bound tools and broke `test_tool_via_team_grant_allows`: a tool granted to the agent's
# team but not individually bound was denied even to a caller who could reach it. The
# agent's capability has always included its team's grants; Decision 45 narrows by the
# HUMAN's authority, it does not also silently narrow the agent's.
_in_agent_reach if {
	some t in agent.tools
	_name_of(t) == input.tool_name
}

_in_agent_reach if {
	some t in data.grants[agent.team]
	_name_of(t) == input.tool_name
}

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
	agent_class == "daemon"
}

user_identity_ok if {
	agent_class == "user_delegated"
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

# ─── Decision 27: de-anonymization gate ──────────────────────────────────────
# A tool entry (own or granted) may carry pii_deanonymize_allowed. When the call
# is ALLOWED and the matched tool is flagged, OPA permits governed_tool to pass
# DE-ANONYMIZED arguments to the tool. Fail-closed: a bare string, a missing
# flag, or a non-boolean value yields false (no de-anonymization).
_deanon_of(entry) := entry.pii_deanonymize_allowed if {
	is_object(entry)
	is_boolean(entry.pii_deanonymize_allowed)
}

_matching_deanon contains true if {
	some t in agent.tools
	_name_of(t) == input.tool_name
	_deanon_of(t) == true
}

_matching_deanon contains true if {
	some t in data.grants[agent.team]
	_name_of(t) == input.tool_name
	_deanon_of(t) == true
}

allow_deanonymize if {
	allow
	count(_matching_deanon) > 0
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
	not _in_agent_reach
}

# Decision 45: the tool IS bound to the agent, but this human's own authority does not
# reach it. Distinct from `tool_not_granted` on purpose — that one means "the agent cannot
# do this", which is fixed by binding the tool; this one means "you cannot ask it to",
# which is fixed by granting the caller's team. Same 403 with two different remedies is
# how an operator ends up applying the wrong one.
deny_reason := "tool_not_granted_to_user" if {
	identity_present
	identity_matches
	not tool_in_set
	_in_agent_reach
	# The identity floor OUTRANKS this. With no user at all, the intersection is empty for
	# the trivial reason that there is nobody to intersect with — and answering
	# "tool_not_granted_to_user" then sends the operator to grant a team, when the actual
	# problem is that the run carries no principal. Two wrongs, and this reason names the
	# consequence rather than the cause. Caught by suite-70 T-S70-002b against a REAL
	# deployed agent, which is the only place the two can be told apart.
	user_identity_ok
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
	# `_in_agent_reach`, NOT `tool_in_set`. Decision 45 made `tool_in_set` depend on the
	# caller's grants, so with no principal it is ALWAYS false — which silently made this
	# rule unreachable and let `tool_not_granted_to_user` answer instead. The question here
	# is "could the AGENT do this", asked so the reason names the missing principal rather
	# than a grant nobody could have held.
	_in_agent_reach
	# `risk_allows` is NOT a precondition here, and cannot be: it derives from
	# max(_matching_ranks), which the Decision 45 intersection empties whenever there is no
	# caller — so requiring it made this rule unreachable for a second reason. Risk is a
	# different axis from identity, and `tool_risk_denied` stays mutually exclusive with
	# this because it requires `tool_in_set`, which is false in exactly this case.
	agent_class == "user_delegated"
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

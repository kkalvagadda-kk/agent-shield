# Unit tests for the unified AgentShield authorization policy.
# Run: opa test services/registry-api/opa_policy/
package agentshield

import rego.v1

# A registered agent with a mix of own-tool risks. The bundle keys on SA subject.
subject := "system:serviceaccount:agents-platform:agent-refunds-sa"

base_agents := {"system:serviceaccount:agents-platform:agent-refunds-sa": {
	"tools": [
		{"name": "lookup_order", "risk": "low"},
		{"name": "audit_log", "risk": "medium"},
		{"name": "issue_refund", "risk": "high"},
		{"name": "delete_account", "risk": "critical"},
		{"name": "mystery_tool", "risk": "banana"},
		"legacy_bare_tool",
	],
	"team": "platform",
	"agent_class": "user_delegated",
	"expected_sa_subject": "system:serviceaccount:agents-platform:agent-refunds-sa",
	"sa_namespace": "agents-platform",
}}

# Team grants: a tool the agent does NOT own but its team is granted.
base_grants := {"platform": [{"name": "send_email", "risk": "high"}, {"name": "read_kb", "risk": "low"}]}

input_for(tool) := {
	"sa_subject": subject,
	"tool_name": tool,
	"args": {},
	"agent_class": "user_delegated",
	"playground": false,
	"sandbox": false,
	"user_id": "",
	"user_team": "",
}

# ─── Gate 4: risk → action ───────────────────────────────────────────────────
test_low_risk_allows if {
	allow with input as input_for("lookup_order")
		with data.agents as base_agents
		with data.grants as base_grants
	not require_approval with input as input_for("lookup_order")
		with data.agents as base_agents
		with data.grants as base_grants
	reason == "allow_low_risk" with input as input_for("lookup_order")
		with data.agents as base_agents
		with data.grants as base_grants
}

test_medium_risk_allows_no_approval if {
	allow with input as input_for("audit_log")
		with data.agents as base_agents
		with data.grants as base_grants
	not require_approval with input as input_for("audit_log")
		with data.agents as base_agents
		with data.grants as base_grants
	reason == "allow_medium_risk" with input as input_for("audit_log")
		with data.agents as base_agents
		with data.grants as base_grants
}

test_high_risk_requires_approval if {
	allow with input as input_for("issue_refund")
		with data.agents as base_agents
		with data.grants as base_grants
	require_approval with input as input_for("issue_refund")
		with data.agents as base_agents
		with data.grants as base_grants
	reason == "require_approval_high_risk" with input as input_for("issue_refund")
		with data.agents as base_agents
		with data.grants as base_grants
}

test_critical_risk_denies if {
	not allow with input as input_for("delete_account")
		with data.agents as base_agents
		with data.grants as base_grants
	deny_reason == "tool_risk_denied" with input as input_for("delete_account")
		with data.agents as base_agents
		with data.grants as base_grants
}

test_unknown_risk_denies if {
	not allow with input as input_for("mystery_tool")
		with data.agents as base_agents
		with data.grants as base_grants
	deny_reason == "tool_risk_denied" with input as input_for("mystery_tool")
		with data.agents as base_agents
		with data.grants as base_grants
}

# ─── Bare-string tool entry → treated as critical → deny ─────────────────────
test_bare_string_tool_treated_as_critical if {
	not allow with input as input_for("legacy_bare_tool")
		with data.agents as base_agents
		with data.grants as base_grants
	deny_reason == "tool_risk_denied" with input as input_for("legacy_bare_tool")
		with data.agents as base_agents
		with data.grants as base_grants
}

# ─── Gate 3: membership ──────────────────────────────────────────────────────
test_tool_not_granted_denies if {
	not allow with input as input_for("format_hard_drive")
		with data.agents as base_agents
		with data.grants as base_grants
	deny_reason == "tool_not_granted" with input as input_for("format_hard_drive")
		with data.agents as base_agents
		with data.grants as base_grants
}

test_tool_via_team_grant_allows if {
	# read_kb is not in the agent's own tools; it comes from the team grant.
	allow with input as input_for("read_kb")
		with data.agents as base_agents
		with data.grants as base_grants
	reason == "allow_low_risk" with input as input_for("read_kb")
		with data.agents as base_agents
		with data.grants as base_grants
}

test_high_risk_team_grant_requires_approval if {
	allow with input as input_for("send_email")
		with data.agents as base_agents
		with data.grants as base_grants
	require_approval with input as input_for("send_email")
		with data.agents as base_agents
		with data.grants as base_grants
}

# ─── Gate 1: identity present ────────────────────────────────────────────────
test_unknown_subject_denies if {
	i := {
		"sa_subject": "system:serviceaccount:agents-platform:ghost-sa",
		"tool_name": "lookup_order",
		"args": {},
		"agent_class": "user_delegated",
		"playground": false,
		"sandbox": false,
		"user_id": "",
		"user_team": "",
	}
	not allow with input as i with data.agents as base_agents with data.grants as base_grants
	deny_reason == "agent_unauthenticated" with input as i
		with data.agents as base_agents
		with data.grants as base_grants
}

# ─── Gate 2: identity match ──────────────────────────────────────────────────
test_identity_mismatch_denies if {
	mismatched := {"system:serviceaccount:agents-platform:agent-refunds-sa": {
		"tools": [{"name": "lookup_order", "risk": "low"}],
		"team": "platform",
		"agent_class": "user_delegated",
		# expected subject deliberately differs from the presented subject.
		"expected_sa_subject": "system:serviceaccount:agents-platform:someone-else-sa",
		"sa_namespace": "agents-platform",
	}}
	not allow with input as input_for("lookup_order")
		with data.agents as mismatched
		with data.grants as base_grants
	deny_reason == "identity_mismatch" with input as input_for("lookup_order")
		with data.agents as mismatched
		with data.grants as base_grants
}

# ─── Class A (daemon) vs Class B (user_delegated) both decided by risk ───────
test_daemon_class_low_risk_allows if {
	i := {
		"sa_subject": subject,
		"tool_name": "lookup_order",
		"args": {},
		"agent_class": "daemon",
		"playground": false,
		"sandbox": false,
		"user_id": "",
		"user_team": "",
	}
	allow with input as i with data.agents as base_agents with data.grants as base_grants
}

test_daemon_class_high_risk_requires_approval if {
	i := {
		"sa_subject": subject,
		"tool_name": "issue_refund",
		"args": {},
		"agent_class": "daemon",
		"playground": false,
		"sandbox": false,
		"user_id": "",
		"user_team": "",
	}
	require_approval with input as i with data.agents as base_agents with data.grants as base_grants
}

# ─── Empty bundle → deny everything (fail-closed) ────────────────────────────
test_empty_bundle_denies if {
	not allow with input as input_for("lookup_order")
		with data.agents as {}
		with data.grants as {}
	deny_reason == "agent_unauthenticated" with input as input_for("lookup_order")
		with data.agents as {}
		with data.grants as {}
}

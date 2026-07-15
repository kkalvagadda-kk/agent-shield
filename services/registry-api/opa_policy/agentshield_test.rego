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

# user_delegated fixture — carries a live principal ("alice") so these tests
# exercise the risk gate under the WS-2 identity floor (user_identity_ok). The
# floor's empty-principal deny is covered separately by the WS-2 truth-table tests.
input_for(tool) := {
	"sa_subject": subject,
	"tool_name": tool,
	"args": {},
	"agent_class": "user_delegated",
	"playground": false,
	"sandbox": false,
	"user_id": "alice",
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

# ─── WS-2 identity floor (user_identity_ok) truth table ──────────────────────
# The floor is orthogonal to the risk gate: it only asserts a *live user is
# present* for user_delegated runs; daemon runs act as their own identity.

# Row 1 — daemon + empty user + schedule → floor holds → allow (risk unchanged).
test_daemon_empty_user_identity_ok if {
	i := {
		"sa_subject": subject,
		"tool_name": "lookup_order",
		"args": {},
		"agent_class": "daemon",
		"playground": false,
		"sandbox": false,
		"user_id": "",
		"user_team": "",
		"trigger_type": "schedule",
	}
	user_identity_ok with input as i with data.agents as base_agents with data.grants as base_grants
	allow with input as i with data.agents as base_agents with data.grants as base_grants
}

# Row 2 — daemon + "alice" + manual → floor holds (present user honored, not capped).
test_daemon_present_user_identity_ok if {
	i := {
		"sa_subject": subject,
		"tool_name": "lookup_order",
		"args": {},
		"agent_class": "daemon",
		"playground": false,
		"sandbox": false,
		"user_id": "alice",
		"user_team": "",
		"trigger_type": "manual",
	}
	user_identity_ok with input as i with data.agents as base_agents with data.grants as base_grants
	allow with input as i with data.agents as base_agents with data.grants as base_grants
}

# Row 3 — user_delegated + "alice" + manual → floor holds → allow.
test_user_delegated_present_user_identity_ok if {
	i := {
		"sa_subject": subject,
		"tool_name": "lookup_order",
		"args": {},
		"agent_class": "user_delegated",
		"playground": false,
		"sandbox": false,
		"user_id": "alice",
		"user_team": "",
		"trigger_type": "manual",
	}
	user_identity_ok with input as i with data.agents as base_agents with data.grants as base_grants
	allow with input as i with data.agents as base_agents with data.grants as base_grants
}

# Row 4 — user_delegated + empty user + schedule → floor fails → deny (fail-closed).
test_user_delegated_missing_user_denies if {
	i := {
		"sa_subject": subject,
		"tool_name": "lookup_order",
		"args": {},
		"agent_class": "user_delegated",
		"playground": false,
		"sandbox": false,
		"user_id": "",
		"user_team": "",
		"trigger_type": "schedule",
	}
	not user_identity_ok with input as i with data.agents as base_agents with data.grants as base_grants
	not allow with input as i with data.agents as base_agents with data.grants as base_grants
	deny_reason == "missing_user_identity" with input as i
		with data.agents as base_agents
		with data.grants as base_grants
}

# Regression — the risk-based require_approval is UNCHANGED by the identity floor.
# A high-risk tool on a user_delegated run WITH a present user still gates on HITL
# exactly as before (same scenario as test_high_risk_requires_approval, but with a
# live user so the floor is satisfied and the risk gate is what we observe).
test_identity_floor_leaves_require_approval_unchanged if {
	i := {
		"sa_subject": subject,
		"tool_name": "issue_refund",
		"args": {},
		"agent_class": "user_delegated",
		"playground": false,
		"sandbox": false,
		"user_id": "alice",
		"user_team": "",
		"trigger_type": "manual",
	}
	allow with input as i with data.agents as base_agents with data.grants as base_grants
	require_approval with input as i with data.agents as base_agents with data.grants as base_grants
	reason == "require_approval_high_risk" with input as i
		with data.agents as base_agents
		with data.grants as base_grants
}

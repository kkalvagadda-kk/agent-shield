package agentshield

# ─── Defaults ───────────────────────────────────────────────────────────────
default allow = false
default deny_reason = ""

# ─── SA Identity Validation ─────────────────────────────────────────────────
# Bidirectional validation:
#   1. SA subject from the JWT must exist as a key in data.agents (forward)
#   2. expected_sa_subject in the bundle entry must match the input subject (reverse)
# This prevents a pod from presenting a different agent's SA subject in its token.
sa_valid if {
    entry := data.agents[input.sa_subject]
    # Reverse check: bundle's expected subject must match input
    entry.expected_sa_subject == input.sa_subject
}

# ─── Tool Authorization ─────────────────────────────────────────────────────
# Tool must be in the agent's registered tool snapshot.
tool_registered if {
    data.agents[input.sa_subject].tools[_] == input.tool_name
}

# ─── User Grant Check (Class B / user_delegated only) ────────────────────────
# User's team must have an active grant covering this tool.
# This is the Class B intersection rule: effective_permissions = agent_scope ∩ user_grants
user_has_grant if {
    input.user_team != ""
    data.grants[input.user_team][_] == input.tool_name
}

# ─── Playground Sandbox Grant Bypass ────────────────────────────────────────
# In sandbox mode, tool side-effects are mocked — no real data leaves the system.
# Skip the team grant check so developers can test tool logic before grants exist.
# Agent scope (tool_registered) still applies — sandbox doesn't bypass safety.
grant_bypassed if {
    input.playground == true
    input.sandbox    == true
}

# The intersection rule: grant check passes when team has grant OR sandbox bypasses it.
user_grant_satisfied if { user_has_grant }
user_grant_satisfied if { grant_bypassed }

# ─── Class A — Daemon / Autonomous ──────────────────────────────────────────
# Daemon agents run as their own machine identity. No user context allowed.
# If a user JWT is present on a daemon request it means routing error or injection.
allow if {
    input.agent_class == "daemon"
    sa_valid
    tool_registered
    not input.user_id  # daemon must not carry user identity
}

# ─── Class B — User-Delegated / OBO ─────────────────────────────────────────
# Intersection rule: agent scope ∩ user grants = effective permissions.
# BOTH conditions must hold — neither agent scope alone nor user grants alone is enough.
allow if {
    input.agent_class == "user_delegated"
    sa_valid
    tool_registered
    input.user_team != ""  # user context must be present
    user_grant_satisfied
}

# ─── Deny Reasons ───────────────────────────────────────────────────────────
deny_reason = "agent_unauthenticated" if {
    not sa_valid
}

deny_reason = "tool_not_registered" if {
    sa_valid
    not tool_registered
}

deny_reason = "user_not_granted" if {
    sa_valid
    tool_registered
    input.agent_class == "user_delegated"
    not user_grant_satisfied
}

deny_reason = "user_context_missing" if {
    sa_valid
    tool_registered
    input.agent_class == "user_delegated"
    input.user_team == ""
}

# Daemon agents must not carry user identity — presence of user_id indicates
# a routing error or an injection attempt co-opting the daemon into an OBO flow.
deny_reason = "daemon_user_context_rejected" if {
    input.agent_class == "daemon"
    sa_valid
    tool_registered
    input.user_id     # truthy: non-empty string
}

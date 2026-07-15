package agentshield

# ─── Defaults ───────────────────────────────────────────────────────────────
default allow = false
default deny_reason = ""
default user_identity_ok = false

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

# ─── Identity floor (WS-2) ───────────────────────────────────────────────────
# daemon: no live user required (the trigger-run acts as the service identity).
# user_delegated: a live user MUST be present — a missing principal is a DENY,
#                 never a silent downgrade to the service identity (fail-closed).
# Orthogonal to the risk / grant gates. Mirrors the authoritative registry-api
# policy (services/registry-api/opa_policy/agentshield.rego).
user_identity_ok if {
    input.agent_class == "daemon"
}

user_identity_ok if {
    input.agent_class == "user_delegated"
    input.user_id != ""
}

# ─── Class A — Daemon / Autonomous ──────────────────────────────────────────
# Daemon agents run as their own machine identity. No user context allowed.
# If a user JWT is present on a daemon request it means routing error or injection.
allow if {
    input.agent_class == "daemon"
    sa_valid
    tool_registered
    not input.user_id  # daemon must not carry user identity
    user_identity_ok
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
    user_identity_ok       # WS-2: a live user_id must also be present (fail-closed)
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

# ─── Identity floor (WS-2) — missing live principal on a user_delegated run ───
# Fires only when every other gate passes but the identity floor fails, so it is
# mutually exclusive with the deny_reasons above (user_context_missing keys on
# user_team == ""; user_not_granted on not user_grant_satisfied) — otherwise two
# complete-rule bodies could match one request and OPA would raise an
# eval_conflict. Mirrors registry-api's `missing_user_identity`.
deny_reason = "missing_user_identity" if {
    sa_valid
    tool_registered
    input.agent_class == "user_delegated"
    input.user_team != ""
    user_grant_satisfied
    input.user_id == ""
}

# Daemon agents must not carry user identity — presence of user_id indicates
# a routing error or an injection attempt co-opting the daemon into an OBO flow.
deny_reason = "daemon_user_context_rejected" if {
    input.agent_class == "daemon"
    sa_valid
    tool_registered
    input.user_id     # truthy: non-empty string
}

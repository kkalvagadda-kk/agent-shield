# AgentShield Authorization Model — Requirements

**Status:** Draft  
**Last updated:** 2026-06-27  
**Scope:** Control plane (authoring, publishing, deployment) and data plane (runtime) authorization for all agent types on the AgentShield platform.

---

## 1. Background and Motivation

AgentShield allows users to build agents that call tools, invoke skills, and delegate to other agents — autonomously or on behalf of a human user. As of the current implementation, authorization is enforced only at the tool-call level via OPA risk labels. There is no authorization at authoring time, no gate at publishing, no deployer identity check, and no user identity propagation to tool calls.

This document defines the complete authorization model across all lifecycle stages. It is grounded in:

- IETF Draft: OAuth 2.0 OBO extension for AI agents (`draft-oauth-ai-agents-on-behalf-of-user-01`)
- Microsoft Entra Agent ID blueprint and OBO flow model
- OWASP Top 10 for Agentic Applications 2026 (ASI03, ASI10, LLM06)
- The intersection rule (WorkOS, 2025): agent permissions ∩ user permissions = effective permissions
- Compositional authorization framework for agentic AI (arxiv 2606.03518)
- NIST NCCoE concept paper on software and AI agent identity (Feb 2026)

---

## 2. Agent Classification

Every agent registered on the platform must be classified into one of two identity classes at publish time. This classification is immutable for the lifetime of the agent version and determines which authorization flows apply.

### 2.1 Class A — Daemon / Autonomous / System Agent

**Definition:** No human is present at the keyboard during execution. Triggered by schedule (cron), event (database mutation, webhook), or pipeline.

**Examples:** Nightly reconciliation agent, GitHub issue triage bot, data pipeline monitor.

**Identity model:** The agent operates under its own dedicated machine identity. Shared service accounts are not permitted — they create ambiguous audit trails and over-provision by default. Each daemon agent is provisioned with:
- A unique client ID tied to the agent's deployment
- A non-secret credential: private key JWT or mTLS client certificate with automatic rotation
- OAuth 2.0 grant type: `client_credentials`
- Token carries only the agent's identity — no user `sub` claim

**Authorization constraint:** Because there is no user to bound scope, permissions must be explicitly and narrowly declared at blueprint registration time by an administrator. The agent cannot self-escalate. Permissions are static until an admin modifies the blueprint.

### 2.2 Class B — User-Delegated / OBO / Interactive Agent / Copilot

**Definition:** A human is actively driving the session — direct prompt, UI click, or active chat session. The agent acts on behalf of that specific user.

**Examples:** Customer support copilot, developer assistant, employee workflow agent.

**Identity model:** Every token the agent presents to a downstream tool carries two identities simultaneously:
- `sub`: the delegating user's identifier
- `act.sub`: the agent's own identifier

This follows the IETF OBO draft and RFC 8693 (Token Exchange). The token is not a forwarded user token — it is a new, scoped token issued after the agent presents both its own credential and the user's token to the authorization server. The resulting token is audience-bound to the specific tool endpoint.

**Authorization constraint:** The intersection rule governs effective permissions. At every tool call:

```
effective_permissions = agent_registered_scope ∩ user_granted_permissions
```

Neither bound alone is sufficient. An agent cannot do more than its registered scope permits, and it cannot do more than the invoking user is permitted to do.

---

## 3. Control Plane Requirements — Authoring (Private Workspace)

### REQ-AUTH-1: Private by Default

All assets (agents, tools, skills, workflows) created by a user exist in that user's private workspace. They are:
- Not discoverable via any API by other users
- Not bindable into other users' agents or workflows
- Not deployable outside the owner's own development environment

No authorization check is needed within the private workspace because isolation is structural.

### REQ-AUTH-2: Immutable Asset Ownership

The user who creates an asset is permanently recorded as its owner (`created_by`). Ownership cannot be transferred. Every published asset traces back to a single human author. This field is set by the platform at creation time using the authenticated user's identity — it cannot be supplied by the client.

### REQ-AUTH-3: Cross-Asset Binding Requires Co-Visibility

A user may only bind a tool to an agent, or include a tool in a workflow, if that tool is visible to the user at bind time. A tool is visible if:
- The user owns it (private workspace), OR
- The tool has been published to the shared repository AND the user's team has an active grant to it

Attempting to bind a private tool owned by another user, or a published tool without a team grant, must be rejected at the API layer with HTTP 403 and a message identifying the tool and the missing grant.

---

## 4. Control Plane Requirements — Publishing and Admin Grant

Publishing is the transition from private to shared. It is a request for review, not an instant availability grant.

### REQ-PUB-1: Publish Creates a Pending Review State

When a user publishes an asset, it transitions to `pending_review`. While in this state:
- The asset is not queryable by users outside the author's team
- The asset is not deployable by any user
- The asset cannot be bound as a dependency by other users' agents

Only after an administrator approves the publication does the asset become available in the shared repository.

### REQ-PUB-2: Agent Class Must Be Declared at Publish Time

An agent cannot be submitted for review without a declared class: `daemon` or `user_delegated`. The review form must require this field. An admin cannot approve an agent without a class declaration. The declared class is locked for the lifetime of the version — a new version must be created to change it.

### REQ-PUB-3: Full Dependency Declaration Required

The publishing request must include a complete, machine-readable declaration of everything the asset requires:

| Asset type | Required declaration |
|---|---|
| Tool | `risk_level`, `owner_team`, endpoint URL or Python code |
| Agent | Complete tool list with each tool's `risk_level`, agent class |
| Skill | All tools in the skill with their `risk_level` |
| Workflow | Every agent and tool in the graph, with their `risk_level` |

Partial declarations are rejected. The admin review UI must render this declaration in full so the reviewer can assess scope before approving.

### REQ-PUB-4: Risk-Tiered Approval Requirements

The required approval tier is determined by the highest risk level present anywhere in the asset's dependency graph:

| Highest risk level in asset | Required approvers |
|---|---|
| `low` only | Team lead |
| `medium` | Platform administrator |
| `high` | Platform administrator |
| `critical` | Not publishable to shared repository (see REQ-PUB-5) |

Approval is a recorded action: `{asset_id, approved_by, approved_at, tier}`. Approvals are immutable audit records.

### REQ-PUB-5: Critical-Risk Tools Cannot Enter the Shared Repository

A tool with `risk_level = critical` cannot be published to the shared repository under any circumstances. It may only be used within the `owner_team`'s private namespace. An agent that depends on a `critical`-risk tool cannot be published either — the dependency must be removed or the risk level reclassified (which requires a fresh security review) before publishing is permitted.

### REQ-PUB-6: Grants Are Explicit, Targeted, and Revocable

An admin approval grants a published asset to one or more specific teams, not globally to all users. The grant record is:

```
{
  asset_id:    <uuid>,
  asset_type:  "tool" | "agent" | "skill" | "workflow",
  grantee_team: <team_name>,
  granted_by:   <admin_user_id>,
  granted_at:   <timestamp>,
  expires_at:   <timestamp | null>
}
```

Grants may include an expiry date (time-bounded access). Revoking a grant to a team cascades: all agent versions owned by that team that depend on the revoked asset are flagged `grant_invalid` and must be re-reviewed before the next deployment.

---

## 5. Control Plane Requirements — Deployment Authorization

### REQ-DEPLOY-1: Deployer Must Belong to the Agent's Owner Team

Only a member of the team that owns the agent may initiate a deployment. The deployer's team is read from the `X-Team` header forwarded by Envoy (extracted from the Keycloak JWT `agentshield_team` claim). If the deployer's team does not match the agent's `team` field, the deploy endpoint returns HTTP 403.

Cross-team deployments (e.g., a platform-ops member deploying a fraud-analytics agent) require an explicit cross-team deploy grant, recorded separately from the asset grant.

### REQ-DEPLOY-2: All Tool Dependencies Must Be Granted to the Deploying Team

Before a deployment record is created, the platform must verify that every tool in the version's tool snapshot has an active, non-expired grant to the deploying team. A single missing grant blocks the deployment. The error response must list each ungranated tool by name.

This check happens at deploy time, not at bind time — it is the primary enforcement gate that prevents a runtime OPA denial from being the first signal of a misconfigured agent.

### REQ-DEPLOY-3: Risk-Appropriate Evaluation Must Be Passed

The existing `eval_passed` gate is retained and extended:

| Highest tool risk in version | Required eval |
|---|---|
| `low` or `medium` | Standard functional eval (`eval_passed = true`) |
| `high` | Functional eval + adversarial eval (red-team pass required) |

The `eval_passed` field alone is insufficient for `high`-risk agents. A separate `adversarial_eval_passed` flag must be introduced and checked at deploy time.

### REQ-DEPLOY-4: Dedicated Machine Identity Provisioned at First Deployment

When a deployment is created for the first time for a given agent, the platform provisions a dedicated machine identity:

- **Class A (Daemon):** `client_credentials` identity with a private key JWT credential. Rotation is automatic and managed by the platform. The credential is never exposed to the developer.
- **Class B (User-Delegated):** An OBO-capable identity that can accept user access tokens and exchange them for scoped downstream tokens bound to specific tool endpoints.

This identity is stored as `agent_identity_id` on the deployment record and is used at runtime to verify that the calling pod is the registered agent. Identity provisioning failure must block the deployment.

### REQ-DEPLOY-5: No Critical-Risk Tools in Any Deployable Version

A version whose tool snapshot contains any tool with `risk_level = critical` cannot be deployed. The deploy endpoint returns HTTP 422 and names the offending tools. The path forward is to remove the tool from the agent or have a security administrator reclassify it (triggering a re-review).

---

## 6. Data Plane Requirements — Runtime Authorization

### 6A. Agent Identity Verification (Rogue Agent Prevention)

Addresses OWASP ASI03 (Agent Identity & Authorization Abuse) and ASI10 (Rogue Agents).

#### REQ-RT-1: Agent Must Present a Signed Credential on Every Tool Call

OPA policy evaluation must not be triggered by an unsigned request. Before evaluating any policy, the agent pod must present its provisioned credential — either a short-lived JWT signed by the platform's identity provider or a Kubernetes Service Account Token bound to the pod's service account. OPA validates the credential's signature and expiry before evaluating the policy. An invalid or missing credential returns `allow = false` with `reason = "agent_unauthenticated"`.

#### REQ-RT-2: Policy Is Keyed on Agent Identity, Not Agent Name String

The OPA policy path is keyed on the agent's provisioned `agent_identity_id`, not on its string name. A rogue pod that sends the correct `agent_name` in the request body but cannot present the matching signed credential receives `allow = false` for all tools, regardless of their risk level.

#### REQ-RT-3: Tool Calls Are Restricted to the Registered Tool Set

OPA must verify that the requested tool is present in the agent's deployed version's tool snapshot. A tool that is not in the registered set receives `action = deny` regardless of its risk level. This prevents a compromised agent from calling tools it was never provisioned for.

### 6B. User Delegation Enforcement (Class B Agents Only)

Addresses OWASP LLM06 (Excessive Agency) and implements the intersection rule.

#### REQ-RT-4: User Identity Is Required for All Class B Tool Calls

A Class B agent's `/chat` endpoint must reject requests that do not carry a valid user JWT with HTTP 401. The user's `sub`, `preferred_username`, and `agentshield_team` claims must be extracted and threaded through to every subsequent tool call within that session. A Class B agent that cannot identify the invoking user must not proceed to tool execution.

#### REQ-RT-5: OPA Input Must Include User Identity for Class B Agents

For Class B agents, the OPA query payload must include:

```json
{
  "input": {
    "tool_name": "<tool>",
    "args": {},
    "agent_identity_id": "<agent_id>",
    "user_id": "<user_sub>",
    "user_team": "<user_team>"
  }
}
```

OPA policy must evaluate both:
1. Does the agent's registered scope permit this tool? (existing risk check)
2. Does the invoking user's team have an active grant to this tool?

Both conditions must be true for `allow = true`. The `reason` field in the OPA decision distinguishes the failure mode: `agent_scope_denied` vs `user_not_granted`.

#### REQ-RT-6: HITL Notifications Must Include User Identity

For Class B agents, human-in-the-loop approval notifications for high-risk tool calls must include the invoking user's identity. The approval request surfaced to reviewers must state: "User [username] is requesting that agent [agent_name] invoke [tool_name] on their behalf with args [args]." A generic "an agent wants to call a tool" notification is not sufficient.

#### REQ-RT-7: Class A Agents Must Reject Requests Carrying User Context

A Class A (Daemon) agent that receives a `/chat` or trigger request carrying a user JWT must reject it with HTTP 400 and reason `daemon_agent_no_user_context`. Daemon agents run as their own machine identity. The presence of a user token indicates either a routing error or an injection attempt. This prevents a daemon agent from being co-opted into an OBO flow it was not designed for.

### 6C. Skill and Multi-Agent Authorization

#### REQ-RT-8: Skill Access Is Authorized at the Individual Tool Level

Granting an agent access to a skill does not bypass per-tool authorization. When an agent invokes a skill, each tool call inside that skill is independently evaluated by OPA against the agent's registered scope and (for Class B) the user's grants. High-risk tools inside a skill still trigger HITL regardless of how the skill is packaged.

#### REQ-RT-9: Agent-to-Agent Delegation Is Scope-Attenuating

When agent A delegates to agent B (via handoff), agent B's effective permissions for that session are the strict intersection of agent A's registered scope and agent B's own registered scope. Agent B cannot use capabilities that agent A was not permitted to use in that session, even if agent B's own blueprint would normally allow them. Each delegation hop can only narrow permissions — never widen them.

The delegation chain must be carried in the token's `act` claim (following RFC 8693 chained delegation): each handoff appends a new `act` layer, making the full chain inspectable and auditable.

---

## 7. Audit and Observability Requirements

### REQ-AUDIT-1: Every Authorization Decision Is Recorded

Every OPA decision — allow, deny, or require_approval — must be written to the `opa_decisions` audit table with:
- `agent_identity_id` (not just agent name)
- `user_id` (for Class B agents)
- `tool_name`
- `decision` outcome
- `deny_reason` (if denied)
- `policy_version`
- `input_snapshot` (full OPA input, redacted for PII)
- `trace_id` (for cross-service correlation)

The audit log must be append-only. No record may be deleted or modified after creation.

### REQ-AUDIT-2: Grant Changes Are Audited

Every grant creation, modification, and revocation is recorded with: `{admin_id, action, asset_id, grantee_team, timestamp}`. This audit trail is separate from the OPA decision log and is stored in the control plane database.

### REQ-AUDIT-3: Agent Identity Lifecycle Is Traceable

Every provisioning, rotation, and revocation of an agent machine identity is recorded. The record links: `{deployment_id, agent_identity_id, credential_type, issued_at, rotated_at, revoked_at, revoked_by}`.

---

## 8. Out of Scope for This Document

The following are related but outside the boundary of this requirements document:

- **Authentication mechanisms** for human users (Keycloak configuration, JWKS, PKCE flows) — these are prerequisites, not requirements of the authorization model itself.
- **Content safety filtering** (input/output scanning via the safety orchestrator) — governed by the safety model, not the authorization model.
- **Rate limiting and quota enforcement** — operational concerns handled separately.
- **Billing and cost attribution** — tracked separately per deployment.

---

## 9. Open Questions

| # | Question | Owner | Status |
|---|---|---|---|
| OQ-1 | For Class A daemon agents: should the machine identity credential be managed by Kubernetes Workload Identity, a secrets manager (Vault), or the platform's own PKI? | Platform architect | Open |
| OQ-2 | For cross-team deploy grants: who has authority to create them — the grantee team lead, the asset owner team lead, or only a platform admin? | Product | Open |
| OQ-3 | For grant expiry: should the default be time-bounded (e.g., 90 days, renewable) or indefinite until revoked? | Security | Open |
| OQ-4 | For adversarial eval (`high`-risk agents): what constitutes a pass — a defined red-team checklist, an automated scan, or both? | Security | Open |
| OQ-5 | For the `critical` risk level: should it ever be publishable to the shared repository under any conditions, or is team-internal always the ceiling? | Security | Open |
| OQ-6 | For token exchange in Class B OBO flow: does AgentShield operate its own authorization server or delegate to the existing Keycloak instance? | Platform architect | Open |

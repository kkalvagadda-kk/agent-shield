# AgentShield Authorization Model — Architecture Spec

**Status**: Draft — Implementation source of truth  
**Date**: 2026-06-27  
**Author**: Karthik + Claude  
**Version**: 2.0.0  
**Requirements**: `docs/authorization-model.md`  
**Referenced by**: `docs/spec.md` §Authorization Model

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [How Option C Works — The Mental Model](#2-how-option-c-works--the-mental-model)
3. [Full System Architecture](#3-full-system-architecture)
4. [Flow: First Deploy → Machine Identity Provisioning](#4-flow-first-deploy--machine-identity-provisioning)
5. [Flow: Class A (Daemon) Tool Call](#5-flow-class-a-daemon-agent-tool-call)
6. [Flow: Class B (User-Delegated) Tool Call — Low Risk](#6-flow-class-b-user-delegated-tool-call--low-risk)
7. [Flow: Class B Tool Call — High Risk (HITL path)](#7-flow-class-b-tool-call--high-risk-hitl-path)
8. [Flow: Publish + Admin Grant Workflow](#8-flow-publish--admin-grant-workflow)
9. [Flow: Deploy Gate (All Pre-flight Checks)](#9-flow-deploy-gate-all-pre-flight-checks)
10. [Flow: Agent-to-Agent Handoff (Scope Attenuation)](#10-flow-agent-to-agent-handoff-scope-attenuation)
11. [Flow: Grant Revocation and Cascade](#11-flow-grant-revocation-and-cascade)
12. [Flow: OPA Bundle Lifecycle](#12-flow-opa-bundle-lifecycle)
13. [Data Model](#13-data-model)
14. [API Contracts](#14-api-contracts)
15. [OPA Policy Structure](#15-opa-policy-structure)
16. [Infrastructure Changes](#16-infrastructure-changes)
17. [Migration Path](#17-migration-path-current--option-c)
18. [Future: Option B — Istio Waypoint + OPA ext_authz](#18-future-option-b--istio-waypoint--opa-ext_authz)
19. [Open Questions for Reviewers](#19-open-questions-for-reviewers)

---

## 1. Problem Statement

Today AgentShield has a single layer of authorization: OPA evaluates risk labels on tool calls. Two things make it insecure:

**Problem 1 — No agent machine identity.** OPA policy today is keyed on `agent_name`, a string the SDK puts in the request body. Any pod that knows the agent name can claim to be that agent and OPA will treat it as legitimate. There is no cryptographic proof that the calling pod is the registered agent.

**Problem 2 — No lifecycle gates.** Any authenticated user can bind any tool to any agent, deploy at any time, and there is no publish review, no team grant model, and no check that a deployer actually owns the agent they're deploying.

This spec introduces:
- **Agent machine identity**: each deployed agent pod gets a unique cryptographic identity (K8s Bound Service Account Token, backed by Istio's SPIFFE infrastructure). OPA policies are keyed on this identity, not a name string.
- **Control plane lifecycle gates**: private workspace isolation, publish + admin approval, team grants, and a pre-deploy checklist.
- **User identity propagation**: for interactive (Class B) agents, the invoking user's identity flows into every OPA decision, enforcing the intersection rule.

---

## 2. How Option C Works — The Mental Model

Before diving into flows, here's the conceptual picture of the three new pieces Option C introduces.

### 2.1 K8s Bound Service Account Tokens — Agent Identity

The fundamental question is: **how does OPA know which agent pod is calling it?**

Today the SDK just sends `{"tool": "X", "agent_name": "fraud-agent"}`. Any pod that knows the agent name can send that.

Option C adds a **Projected Service Account Token** to each agent pod. This is a standard Kubernetes feature:

```
Agent Pod (agent-fraud-sa ServiceAccount)
├── /var/run/secrets/kubernetes.io/serviceaccount/token   ← default token (not used for OPA)
└── /var/run/secrets/tokens/agentshield-opa              ← projected token (NEW)
    ├── audience: "agentshield-opa"    (only OPA can validate this)
    ├── subject:  "system:serviceaccount:agentshield:agent-fraud-sa"
    ├── expiry:   1 hour
    └── bound to: this pod's UID (cannot be reused by another pod)
```

The SDK reads this file and includes the token in every OPA call. OPA validates it using Kubernetes' own OIDC discovery endpoint (no network call needed — OPA caches the JWKS). If the token is missing, expired, or belongs to the wrong ServiceAccount, OPA denies before evaluating any policy.

**Why this is stronger than a name string**: the token is cryptographically signed by the Kubernetes API server. A rogue pod can't fabricate a valid token for `agent-fraud-sa` unless it runs *as* that ServiceAccount — which Kubernetes RBAC controls.

### 2.2 Istio Ambient Mesh (ztunnel) — Network Layer mTLS

Istio Ambient Mesh is installed separately from the SA token mechanism. It does **one thing** for Option C: it encrypts and mutually authenticates all pod-to-pod network traffic using mTLS, automatically, without any sidecar injection.

```
WITHOUT Istio Ambient:
  Agent Pod ──────────────────────────── HTTP ──────────────────────→ Tool Endpoint
  (plaintext, no mutual auth)

WITH Istio Ambient (ztunnel):
  Agent Pod ── mTLS (SPIFFE cert) ──→ ztunnel ── mTLS ──→ ztunnel ── TCP ──→ Tool Endpoint
  (transparent to the pod; ztunnel on each node intercepts and wraps all traffic)
```

`ztunnel` runs as a DaemonSet — one instance on each Kubernetes node. It intercepts traffic at the Linux network layer (eBPF or iptables), invisible to the application. The agent pod's code doesn't change at all.

The mTLS certificates ztunnel uses are SPIFFE SVIDs — their Subject Alternative Name is:
```
spiffe://cluster.local/ns/agentshield/sa/agent-fraud-sa
```

This is the *same identity* as the K8s SA token, expressed as a TLS certificate. So both the application layer (SA token → OPA) and the network layer (mTLS cert → ztunnel) use the same identity. They are just two representations of the same thing.

**What ztunnel does NOT do in Option C**: it does not intercept tool calls and block them based on OPA decisions. That enforcement still happens at the application layer (SDK → OPA sidecar). ztunnel's job is purely network encryption + identity. Policy enforcement moves to the network layer only in Option B (future).

### 2.3 OPA Bundle Server — Centralized Policy Distribution

Today each agent has its own policy ConfigMap (`fraud-agent-policy`, `reconciler-policy`, etc.). These contain hard-coded rules for that specific agent. Problems: 50 agents = 50 ConfigMaps, no central view, can't audit them together, and each one must be updated separately.

The OPA Bundle Server replaces all of these with a single bundle that all OPA sidecars pull from:

```
OPA Bundle Server (nginx, 2 replicas)
└── /bundles/agentshield/
    ├── data.json       ← agent registry: SA identity → tool list + risk levels + team grants
    └── policy.rego     ← one Rego file, evaluates against data.json

Each OPA sidecar:
  - On startup: pulls bundle from http://opa-bundle-server/bundles/agentshield
  - Every 30s: polls for updates
  - If bundle server unreachable: serves cached last-known-good bundle (up to 5 min)
  - After 5 min unreachable: fails closed (allow = false on all calls)
```

When a new agent is deployed, `deploy-controller` updates `data.json` (adds the new agent's SA identity and tool list) and all running OPA sidecars pick up the change within 30 seconds.

---

## 3. Full System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  CONTROL PLANE                                                                        │
│                                                                                       │
│  ┌──────────────────────────────────────────────────────────────────────────────┐    │
│  │  Registry API (Postgres-backed)                                               │    │
│  │                                                                               │    │
│  │  Asset Lifecycle        Publish/Grant            Deploy Gate                  │    │
│  │  ┌────────────────┐    ┌──────────────────┐    ┌───────────────────────────┐ │    │
│  │  │ private        │───→│ pending_review   │───→│ Check deployer team       │ │    │
│  │  │ pending_review │    │ (admin queue)    │    │ Check all tool grants     │ │    │
│  │  │ published      │    │ approved         │    │ Check eval_passed         │ │    │
│  │  └────────────────┘    └──────────────────┘    │ Check adversarial_eval   │ │    │
│  │                         AssetGrant table        │ Check no critical tools  │ │    │
│  │                         (team × asset × expiry) └───────────────────────────┘ │    │
│  └──────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                       │
│  ┌───────────────────────────────────────┐                                           │
│  │  Deploy Controller                    │                                           │
│  │  On first deploy of agent:            │                                           │
│  │  1. Creates K8s ServiceAccount        │                                           │
│  │  2. Creates AgentIdentity DB record   │                                           │
│  │  3. Pushes bundle data.json update    │                                           │
│  │  4. Creates K8s Deployment            │                                           │
│  └───────────────────────────────────────┘                                           │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│  DATA PLANE                                                                           │
│                                                                                       │
│  Internet / Internal Clients                                                          │
│      │                                                                                │
│      ▼                                                                                │
│  ┌───────────────────────────────────────────────────────────────────────────────┐   │
│  │  Envoy Gateway (unchanged from today)                                          │   │
│  │  • TLS termination                                                             │   │
│  │  • JWT validation (Keycloak JWKS)                                              │   │
│  │  • Header injection: X-User-Id, X-Username, X-Team                            │   │
│  │  • Routes: /api/v1/* → registry-api                                            │   │
│  │            /agents/{name}/* → Safety Orchestrator → agent pods                 │   │
│  └───────────────────────────────────────────────────────────────────────────────┘   │
│      │                                                                                │
│      ▼                                                                                │
│  ┌───────────────────────────────────────────────────────────────────────────────┐   │
│  │  Safety Orchestrator (unchanged)                                               │   │
│  │  Injection scan → PII redaction → forward to agent pod                        │   │
│  └───────────────────────────────────────────────────────────────────────────────┘   │
│      │                                                                                │
│      ▼                                                                                │
│  ┌───────────────────────────────────────────────────────────────────────────────┐   │
│  │  ISTIO AMBIENT MESH — ztunnel DaemonSet (L4 mTLS, transparent to pods)        │   │
│  │  All pod-to-pod traffic in agentshield namespace is automatically:             │   │
│  │  • Encrypted (TLS)                                                             │   │
│  │  • Mutually authenticated via SPIFFE certificates issued by istiod             │   │
│  │                                                                                │   │
│  │  ┌──────────────────────────────────────────────────────────────────────┐     │   │
│  │  │  Agent Pod  (example: fraud-agent)                                   │     │   │
│  │  │  K8s ServiceAccount: agent-fraud-sa                                  │     │   │
│  │  │  SPIFFE URI (from ztunnel mTLS cert):                                │     │   │
│  │  │    spiffe://cluster.local/ns/agentshield/sa/agent-fraud-sa           │     │   │
│  │  │                                                                      │     │   │
│  │  │  ┌─────────────────────────────────┐  ┌──────────────────────────┐  │     │   │
│  │  │  │  Agent Application (SDK)        │  │  OPA Sidecar :8181       │  │     │   │
│  │  │  │                                 │  │                          │  │     │   │
│  │  │  │  1. Read projected SA token     │  │  Validates SA token      │  │     │   │
│  │  │  │     from /var/run/secrets/      │  │  (K8s OIDC / JWKS)      │  │     │   │
│  │  │  │     tokens/agentshield-opa      │  │                          │  │     │   │
│  │  │  │  2. Read X-User-Id, X-Team      │  │  Checks policy against   │  │     │   │
│  │  │  │     from request headers        │  │  bundle data             │  │     │   │
│  │  │  │  3. POST to OPA with token +   │──┤                          │  │     │   │
│  │  │  │     user context + tool name    │  │  Returns:                │  │     │   │
│  │  │  │  4. If allow → execute tool    │  │  allow / require_approval │  │     │   │
│  │  │  │  5. If require_approval → pause │  │  / deny + reason         │  │     │   │
│  │  │  └─────────────────────────────────┘  └──────────┬───────────────┘  │     │   │
│  │  │                                                   │ writes audit row  │     │   │
│  │  │  Volume mounts:                                   ↓                   │     │   │
│  │  │  • /var/run/secrets/tokens/agentshield-opa       Postgres             │     │   │
│  │  │    (projected SA token, TTL=1h, aud=agentshield-opa)                  │     │   │
│  │  └──────────────────────────────────────────────────────────────────────┘     │   │
│  │                                                                                │   │
│  │  ┌──────────────────────────────────────┐                                     │   │
│  │  │  OPA Bundle Server (nginx, 2 replicas)│                                     │   │
│  │  │  Serves /bundles/agentshield/         │                                     │   │
│  │  │  ├── data.json  (agent registry)      │← updated by deploy-controller       │   │
│  │  │  └── policy.rego (single Rego file)   │   on each agent deploy              │   │
│  │  │                                       │                                     │   │
│  │  │  OPA sidecars poll this every 30s     │                                     │   │
│  │  └──────────────────────────────────────┘                                     │   │
│  └───────────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Component roles at a glance:**

| Component | Role | What changed from today |
|-----------|------|------------------------|
| Envoy Gateway | JWT validation, routing, header injection | No change |
| Safety Orchestrator | Input/output scanning | No change |
| Agent Pod | Runs agent logic, calls tools | New: projected SA token volume; SDK reads token |
| OPA Sidecar | Tool call authorization | Changed: validates SA token; keyed on SA identity not name string |
| OPA Bundle Server | Centralized policy distribution | **NEW** — replaces per-agent ConfigMaps |
| Deploy Controller | Reconciles K8s state | Extended: creates SA, updates bundle data.json |
| Registry API | Asset CRUD + lifecycle | Extended: publish/grant/deploy-gate logic |
| Istio ztunnel | L4 mTLS between all pods | **NEW** — transparent, no code changes in pods |
| Keycloak | Human user identity | No change |
| istiod | Issues SPIFFE certs for ztunnel mTLS | **NEW** (part of Istio) |

---

## 4. Flow: First Deploy → Machine Identity Provisioning

This happens once per agent, at first deployment. Subsequent deployments of the same agent reuse the same ServiceAccount.

```
Developer                    Registry API              Deploy Controller           K8s API
    │                             │                          │                        │
    │ POST /agents/fraud/deploy   │                          │                        │
    │────────────────────────────→│                          │                        │
    │                             │                          │                        │
    │          ┌──────────────────┴──────────────────────────────────────────────┐    │
    │          │  Pre-flight checks (see Flow 9 for detail)                       │    │
    │          │  • deployer team = agent.team?                                  │    │
    │          │  • all tool grants active for deployer team?                    │    │
    │          │  • eval_passed?                                                  │    │
    │          │  • no critical-risk tools?                                       │    │
    │          └──────────────────┬──────────────────────────────────────────────┘    │
    │                             │                          │                        │
    │                             │  trigger reconcile       │                        │
    │                             │─────────────────────────→│                        │
    │                             │                          │                        │
    │                             │                          │ Does SA exist?         │
    │                             │                          │───────────────────────→│
    │                             │                          │    No                  │
    │                             │                          │←───────────────────────│
    │                             │                          │                        │
    │                             │                          │ Create ServiceAccount  │
    │                             │                          │ agent-fraud-sa         │
    │                             │                          │───────────────────────→│
    │                             │                          │    Created             │
    │                             │                          │←───────────────────────│
    │                             │                          │                        │
    │                             │                          │ Write AgentIdentity    │
    │                             │  INSERT AgentIdentity   │ row to Postgres        │
    │                             │←─────────────────────────│                        │
    │                             │  { deployment_id,        │                        │
    │                             │    k8s_sa: agent-fraud-sa│                        │
    │                             │    sa_subject:           │                        │
    │                             │    "system:serviceaccount│                        │
    │                             │    :agentshield:         │                        │
    │                             │    agent-fraud-sa" }     │                        │
    │                             │                          │                        │
    │                             │                          │ POST bundle update     │
    │                             │                          │ to Bundle Server       │
    │                             │                          │  data.json +=          │
    │                             │                          │  {                     │
    │                             │                          │   "system:serviceaccount│
    │                             │                          │   :agentshield:        │
    │                             │                          │   agent-fraud-sa": {   │
    │                             │                          │    agent_class: "daemon"│
    │                             │                          │    tools: [...],       │
    │                             │                          │    tool_risk: {...}    │
    │                             │                          │   }                    │
    │                             │                          │  }                     │
    │                             │                          │                        │
    │                             │                          │ Create K8s Deployment  │
    │                             │                          │ with:                  │
    │                             │                          │  serviceAccountName:   │
    │                             │                          │    agent-fraud-sa      │
    │                             │                          │  volumes:              │
    │                             │                          │  - projected SA token  │
    │                             │                          │    audience:           │
    │                             │                          │    agentshield-opa     │
    │                             │                          │    expirationSeconds:  │
    │                             │                          │    3600                │
    │                             │                          │───────────────────────→│
    │                             │                          │    Deployment created  │
    │                             │                          │←───────────────────────│
    │                             │                          │                        │
    │                         200 OK                         │                        │
    │←────────────────────────────│                          │                        │
    │  { deployment_id, status    │                          │                        │
    │    agent_identity_id }      │                          │                        │
```

**Meanwhile, Istio/istiod automatically:**
```
istiod ──detects new pod with SA agent-fraud-sa──→ issues SPIFFE cert:
  Subject Alternative Name:
    spiffe://cluster.local/ns/agentshield/sa/agent-fraud-sa

ztunnel (on the node where fraud-agent pod schedules) ──→ loads SPIFFE cert
  All outbound traffic from fraud-agent pod is now:
  • Encrypted via TLS
  • Presented as identity spiffe://cluster.local/.../agent-fraud-sa
  • No code change in the pod required
```

**What the OPA sidecars on all running pods do (within 30 seconds):**
```
All OPA sidecars ──poll bundle server──→ GET /bundles/agentshield
  Bundle response now includes agent-fraud-sa entry in data.json
  All sidecars update their in-memory data
  → fraud-agent's SA identity is now a recognized, authorized identity in OPA
```

---

## 5. Flow: Class A (Daemon) Agent Tool Call

Class A = scheduled / event-triggered agent. No human in the loop. No user JWT.

**Example**: `reconciler-agent` is triggered by a cron job. It calls `run_check` (risk=low).

```
Cron Job / Event Source
    │
    │ POST /agents/reconciler/trigger
    │ (no user JWT — just the trigger payload)
    │
    ▼
┌─────────────────┐
│  Envoy Gateway  │  JWT validation: no user JWT found → passes through
│                 │  (Class A endpoints don't require user JWT)
└────────┬────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Reconciler Agent Pod (SA: agent-reconciler-sa)                       │
│                                                                        │
│  Step 1: Class A guard check                                          │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Agent handler checks: is there an X-User-Id header?            │  │
│  │ No → good, this is a daemon call.                              │  │
│  │ Yes → immediately return HTTP 400                              │  │
│  │        { "error": "daemon_agent_no_user_context" }             │  │
│  │       (presence of user JWT on a daemon agent = routing error  │  │
│  │        or injection attempt)                                   │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  Step 2: Agent runs LLM reasoning, decides to call run_check          │
│                                                                        │
│  Step 3: SDK builds OPA request                                       │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  sa_token = read("/var/run/secrets/tokens/agentshield-opa")    │  │
│  │  opa_input = {                                                  │  │
│  │    "tool_name": "run_check",                                    │  │
│  │    "args": { "target": "accounts" },                           │  │
│  │    "sa_token": sa_token,    ← cryptographic proof of identity  │  │
│  │    "agent_class": "daemon"                                      │  │
│  │  }                                                              │  │
│  └────────────────────────────────────────────────────────────────┘  │
│         │                                                              │
│         │ POST /v1/data/agentshield/agent                            │
│         │ Authorization: Bearer <sa_token>                           │
│         ▼                                                              │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  OPA Sidecar (:8181)                                             │  │
│  │                                                                  │  │
│  │  Step A: Validate sa_token                                       │  │
│  │  ┌───────────────────────────────────────────────────────────┐  │  │
│  │  │ OPA verifies token signature using K8s OIDC JWKS          │  │  │
│  │  │ (cached; fetched once from                                │  │  │
│  │  │  https://kubernetes.default.svc/.well-known/openid-conf)  │  │  │
│  │  │                                                            │  │  │
│  │  │ Checks: aud == "agentshield-opa"? ✓                       │  │  │
│  │  │         exp > now?                ✓                        │  │  │
│  │  │         iss == k8s cluster?       ✓                        │  │  │
│  │  │                                                            │  │  │
│  │  │ Extracts: sub = "system:serviceaccount:                    │  │  │
│  │  │                   agentshield:agent-reconciler-sa"         │  │  │
│  │  │ → This is the agent's verified identity                    │  │  │
│  │  └───────────────────────────────────────────────────────────┘  │  │
│  │                                                                  │  │
│  │  Step B: Evaluate policy against bundle data                     │  │
│  │  ┌───────────────────────────────────────────────────────────┐  │  │
│  │  │ data.registered_agents["system:serviceaccount:...         │  │  │
│  │  │                         :agent-reconciler-sa"]            │  │  │
│  │  │   → { agent_class: "daemon",                              │  │  │
│  │  │       tools: ["run_check", "write_report"],               │  │  │
│  │  │       tool_risk: { run_check: "low", write_report: "medium"}}│  │  │
│  │  │                                                            │  │  │
│  │  │ Checks:                                                    │  │  │
│  │  │  ✓ identity is registered                                  │  │  │
│  │  │  ✓ tool "run_check" is in registered tool set             │  │  │
│  │  │  ✓ agent_class = daemon AND no user_id in input           │  │  │
│  │  │  ✓ tool risk = low → allow                                │  │  │
│  │  │                                                            │  │  │
│  │  │ Result: { "allow": true }                                  │  │  │
│  │  └───────────────────────────────────────────────────────────┘  │  │
│  │                                                                  │  │
│  │  Step C: Write audit record to Postgres                          │  │
│  │  ┌───────────────────────────────────────────────────────────┐  │  │
│  │  │ INSERT INTO opa_decisions:                                 │  │  │
│  │  │  { agent_identity_id: "system:serviceaccount:...",        │  │  │
│  │  │    user_id: null,                                          │  │  │
│  │  │    tool_name: "run_check",                                 │  │  │
│  │  │    decision: "allow",                                      │  │  │
│  │  │    policy_version: "bundle-sha-abc123",                   │  │  │
│  │  │    trace_id: "..." }                                       │  │  │
│  │  └───────────────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│         │ { allow: true }                                              │
│         ▼                                                              │
│  Step 4: SDK executes tool call                                        │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ HTTP POST to run_check endpoint                                 │  │
│  │ (ztunnel wraps this in mTLS automatically)                     │  │
│  │ Tool receives: { "target": "accounts" }                        │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

**Rogue pod attack — what happens:**
```
Rogue Pod (no ServiceAccount, or wrong SA)
  → tries: POST /v1/data/agentshield/agent
           { "tool_name": "run_check", "agent_name": "reconciler-agent" }
           (no sa_token or a forged token)

OPA Sidecar:
  Step A fails: no valid token OR token sub ≠ any registered SA
  Result: { "allow": false, "reason": "agent_unauthenticated" }
  No policy evaluation happens at all.
  Audit record written with deny_reason = "agent_unauthenticated".
```

---

## 6. Flow: Class B (User-Delegated) Tool Call — Low Risk

Class B = interactive agent. A real human is at the keyboard. The agent acts on their behalf.

**Example**: `support-copilot` (Class B) is called by alice@team-a. She asks it to look up her order. The agent calls `get_order` (risk=low).

```
Alice (team-a user)
    │
    │ POST /agents/support-copilot/chat
    │ Authorization: Bearer <alice's Keycloak JWT>
    │ Body: { "message": "What's the status of order #1234?" }
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│  Envoy Gateway                                                │
│                                                               │
│  Validates alice's JWT:                                       │
│    sub: "alice-uuid-123"                                      │
│    preferred_username: "alice"                                │
│    agentshield_team: "team-a"                                 │
│                                                               │
│  Injects headers:                                             │
│    X-User-Id: alice-uuid-123                                  │
│    X-Username: alice                                          │
│    X-Team: team-a                                             │
└────────────────────────────┬─────────────────────────────────┘
                             │ (+ original body)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Safety Orchestrator                                          │
│  Scans for injection + PII → passes through                  │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Support Copilot Pod (SA: agent-support-copilot-sa)                   │
│                                                                        │
│  Step 1: Class B identity gate                                        │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Agent handler reads X-User-Id header                           │  │
│  │ Missing? → return HTTP 401 { "error": "user_identity_required" }│  │
│  │ Present? → extract user_id="alice-uuid-123", user_team="team-a"│  │
│  │            store in session context                             │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  Step 2: LLM reasons → decides to call get_order                     │
│                                                                        │
│  Step 3: SDK builds OPA request                                       │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  sa_token = read("/var/run/secrets/tokens/agentshield-opa")    │  │
│  │  opa_input = {                                                  │  │
│  │    "tool_name":    "get_order",                                 │  │
│  │    "args":         { "order_id": "1234" },                      │  │
│  │    "sa_token":     sa_token,                                    │  │
│  │    "agent_class":  "user_delegated",                           │  │
│  │    "user_id":      "alice-uuid-123",   ← from X-User-Id header │  │
│  │    "user_team":    "team-a"            ← from X-Team header    │  │
│  │  }                                                              │  │
│  └────────────────────────────────────────────────────────────────┘  │
│         │                                                              │
│         ▼                                                              │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  OPA Sidecar                                                     │  │
│  │                                                                  │  │
│  │  Step A: Validate sa_token → verified identity =                 │  │
│  │    "system:serviceaccount:agentshield:agent-support-copilot-sa" │  │
│  │                                                                  │  │
│  │  Step B: Evaluate Intersection Rule                              │  │
│  │  ┌───────────────────────────────────────────────────────────┐  │  │
│  │  │  Check 1: Is agent's identity registered?                  │  │  │
│  │  │    data.registered_agents["...agent-support-copilot-sa"]  │  │  │
│  │  │    → yes, tools: ["get_order", "send_email", ...]         │  │  │
│  │  │                                                            │  │  │
│  │  │  Check 2: Is "get_order" in agent's registered tool set?  │  │  │
│  │  │    → yes                                                   │  │  │
│  │  │                                                            │  │  │
│  │  │  Check 3: Does user's team have a grant to "get_order"?   │  │  │
│  │  │    data.team_grants["team-a"]["get_order"]                │  │  │
│  │  │    → true  ✓                                              │  │  │
│  │  │                                                            │  │  │
│  │  │  Check 4: Risk level of "get_order"?                      │  │  │
│  │  │    → "low" → allow                                        │  │  │
│  │  │                                                            │  │  │
│  │  │  Both checks passed → allow = true                        │  │  │
│  │  └───────────────────────────────────────────────────────────┘  │  │
│  │                                                                  │  │
│  │  Audit: INSERT opa_decisions                                     │  │
│  │    { agent_identity_id: "...agent-support-copilot-sa",          │  │
│  │      user_id: "alice-uuid-123",                                  │  │
│  │      user_team: "team-a", decision: "allow", ... }              │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│         │ { allow: true }                                              │
│         ▼                                                              │
│  Step 4: SDK calls get_order tool (mTLS via ztunnel)                  │
│  Step 5: Returns result to LLM, generates response for Alice          │
└──────────────────────────────────────────────────────────────────────┘
```

**What happens if bob@team-b calls the same agent:**
```
Same copilot, same tool, but user_team = "team-b"

OPA Step B, Check 3:
  data.team_grants["team-b"]["get_order"]
  → undefined (team-b has no grant for get_order)
  → false

Result: { "allow": false, "reason": "user_not_granted" }

SDK returns to LLM: tool call denied
Agent responds to Bob: "I don't have permission to look up orders for your team."
Audit record: { user_id: "bob-uuid-456", decision: "deny", deny_reason: "user_not_granted" }
```

---

## 7. Flow: Class B Tool Call — High Risk (HITL path)

**Example**: same `support-copilot`, alice@team-a asks it to cancel order #1234. Agent decides to call `cancel_order` (risk=high). team-a has a grant for `cancel_order`.

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Support Copilot Pod                                                  │
  │                                                                        │
  │  ... (same Steps 1-3 as Flow 6, but tool_name = "cancel_order") ...  │
  │                                                                        │
  │  OPA Sidecar evaluates:                                               │
  │  ┌──────────────────────────────────────────────────────────────┐    │
  │  │  Check 1: agent identity registered?  → yes                  │    │
  │  │  Check 2: cancel_order in registered tools?  → yes           │    │
  │  │  Check 3: team-a has grant for cancel_order?  → yes          │    │
  │  │  Check 4: risk level of cancel_order?  → "high"              │    │
  │  │                                                               │    │
  │  │  high risk → require_approval (not allow, not deny)          │    │
  │  │                                                               │    │
  │  │  Result: {                                                    │    │
  │  │    "allow": false,                                            │    │
  │  │    "require_approval": true,                                  │    │
  │  │    "reason": "high_risk_tool"                                 │    │
  │  │  }                                                            │    │
  │  │                                                               │    │
  │  │  Audit: INSERT opa_decisions { decision: "require_approval" } │    │
  │  └──────────────────────────────────────────────────────────────┘    │
  │         │ { require_approval: true }                                   │
  │         ▼                                                              │
  │  SDK pauses agent execution (LangGraph interrupt)                     │
  │         │                                                              │
  │         ▼                                                              │
  │  SDK calls Registry API:                                              │
  │  POST /api/v1/approvals                                               │
  │  {                                                                     │
  │    "agent_name":    "support-copilot",                                │
  │    "tool_name":     "cancel_order",                                   │
  │    "args":          { "order_id": "1234" },                           │
  │    "session_id":    "sess-xyz",                                        │
  │    "opa_decision_id": "decision-uuid",                                │
  │    "user_id":       "alice-uuid-123",   ← class B: include user      │
  │    "user_name":     "alice",                                           │
  │    "user_team":     "team-a"                                           │
  │  }                                                                     │
  └──────────────────────────────────────────────────────────────────────┘
         │
         ▼
  Registry API:
  ┌────────────────────────────────────────────────────────────────────┐
  │  Step 1 — INSERT into approvals:                                   │
  │  { id: "appr-uuid",                                                │
  │    agent_name: "support-copilot",                                  │
  │    tool_name: "cancel_order",                                      │
  │    args: { "order_id": "1234" },  ← PII-redacted                  │
  │    session_id: "sess-xyz",        ← for reviewer de-anonymization  │
  │    status: "pending",                                               │
  │    context: "production",                                           │
  │    user_id: "alice-uuid-123",                                      │
  │    user_name: "alice",                                             │
  │    user_team: "team-a",                                            │
  │    expires_at: NOW() + 30m  }                                      │
  │                                                                     │
  │  Step 2 — Look up authorized reviewers:                            │
  │  SELECT * FROM approval_authority                                   │
  │  WHERE resource_type IN ('agent', 'tool')                          │
  │    AND resource_id IN ('support-copilot', 'cancel_order')          │
  │    AND revoked_at IS NULL                                           │
  │  → returns: [{ approver_user_id: "bob-lead" },                     │
  │               { approver_role: "platform_admin" }]                 │
  │                                                                     │
  │  Step 3 — Notify only authorized reviewers via Slack:              │
  │  (DM or channel scoped to resource — NOT broadcast to #approvals)  │
  └────────────────────────────────────────────────────────────────────┘
         │
         ▼
  Slack DM to bob-lead (and any platform_admins):
  ┌────────────────────────────────────────────────────────────────────┐
  │  🔔 Approval Required — you are authorized for cancel_order        │
  │                                                                     │
  │  User alice (team-a) is requesting that support-copilot            │
  │  invoke cancel_order on their behalf.                              │
  │                                                                     │
  │  Args: { "order_id": "1234" }  (click to de-anonymize PII)        │
  │  Risk: HIGH                                                         │
  │  Expires: 30 minutes                                               │
  │                                                                     │
  │  → Review in dashboard: /approvals/appr-uuid                      │
  └────────────────────────────────────────────────────────────────────┘
         │
         ▼
  Approval Dashboard (Studio ops tab or Appsmith):
  ┌────────────────────────────────────────────────────────────────────┐
  │  PENDING APPROVALS (showing requests you are authorized to review) │
  │                                                                     │
  │  ┌──────────────────────────────────────────────────────────────┐  │
  │  │ cancel_order  ·  support-copilot  ·  alice  ·  team-a       │  │
  │  │ Args: { "order_id": "1234" }  [De-anonymize PII]            │  │
  │  │ Risk: HIGH  ·  expires in 28 min                            │  │
  │  │                                        [Approve]  [Deny]    │  │
  │  └──────────────────────────────────────────────────────────────┘  │
  └────────────────────────────────────────────────────────────────────┘

Reviewer (bob-lead) clicks Approve:
         │
         ▼
  POST /api/v1/approvals/appr-uuid/decide
  { "decision": "approved", "reviewer_notes": "order value < $100 limit" }
         │
         ▼
  Registry API:
  • Updates: status = "approved", reviewer_id = "bob-uuid", reviewed_at = NOW()
  • NOTIFY agentshield_approvals (Postgres LISTEN/NOTIFY)
  • Audit: approval decision appended to grant_audit
         │
         ▼
  Support-Copilot Pod:
  • SDK was polling for approval status (or listening on NOTIFY)
  • Approval arrived → resumes LangGraph execution
  • SDK now calls cancel_order tool (mTLS via ztunnel)
  • Tool executes, returns result
  • Agent responds to Alice: "Order #1234 has been cancelled."
```

---

## 7a. Flow: HITL in Playground (Self-Approval)

**Context**: Alice is testing `support-copilot` in the Playground. Same agent, same `cancel_order` tool, same risk=high trigger. But this is a playground run (`context='playground'`).

```
  OPA Sidecar:
  • input.playground = true
  • input.sandbox = true (or false — approval fires regardless of sandbox flag)
  • risk = "high" → require_approval: true  (unchanged; OPA doesn't bypass approval)
         │
         ▼
  SDK pauses agent, calls Registry API:
  POST /api/v1/approvals
  {
    agent_name: "support-copilot",
    tool_name:  "cancel_order",
    args:       { "order_id": "1234" },
    context:    "playground",             ← differs from production
    user_id:    "alice-uuid-123"
  }
         │
         ▼
  Registry API:
  ┌────────────────────────────────────────────────────────────────────┐
  │  INSERT approvals: { ..., context: "playground", status: "pending" }│
  │                                                                     │
  │  context = "playground" → skip approval_authority lookup           │
  │  context = "playground" → NO Slack notification sent               │
  │  Alice (user_id) is the asset owner → implicit self-approval right │
  └────────────────────────────────────────────────────────────────────┘
         │
         ▼
  Studio Playground Tab (Alice's browser):
  ┌────────────────────────────────────────────────────────────────────┐
  │  [Playground]  ●  1 Pending Approval  [Review →]                  │
  │                                                                     │
  │  SSE stream paused — waiting for your approval.                   │
  └────────────────────────────────────────────────────────────────────┘
         │  Alice clicks [Review →]
         ▼
  Playground HITL Panel (inline, or side panel):
  ┌────────────────────────────────────────────────────────────────────┐
  │  PLAYGROUND APPROVALS — Your pending requests                      │
  │  (only shows context=playground requests owned by you)             │
  │                                                                     │
  │  ┌──────────────────────────────────────────────────────────────┐  │
  │  │ cancel_order  ·  support-copilot  ·  run #4                  │  │
  │  │ Args: { "order_id": "1234" }                                 │  │
  │  │ Risk: HIGH  (in sandbox: side effects are mocked)           │  │
  │  │                                      [Approve]  [Deny]      │  │
  │  └──────────────────────────────────────────────────────────────┘  │
  └────────────────────────────────────────────────────────────────────┘
         │  Alice clicks Approve
         ▼
  POST /api/v1/approvals/appr-uuid/decide { "decision": "approved" }
  Registry API:
  • Validates: caller = owner of the playground run (alice-uuid-123 = user_id on approval)
  • Updates status = "approved", reviewer_id = "alice-uuid-123"
  • NOTIFY agentshield_approvals
         │
         ▼
  Agent resumes, executes cancel_order (mocked if sandbox=true)
  SSE stream continues, run completes normally.
```

**Key differences from production HITL:**

| Dimension | Production | Playground |
|-----------|-----------|-----------|
| `approvals.context` | `production` | `playground` |
| Approval authority | Looked up from `approval_authority` table | Implicit: asset owner |
| Slack notification | Yes — scoped DM to authorized reviewers | No |
| Dashboard | Production ops dashboard (filtered to reviewer's scope) | Playground HITL panel (filtered to this user's playground runs) |
| Self-approval | Not permitted (reviewer ≠ requester enforced) | Always permitted (owner is the tester) |
| Sandbox tool execution | N/A (production always executes) | Tool mocked if sandbox=true |

---

## 8. Flow: Publish + Admin Grant Workflow

**Example**: Developer on team-fraud-analytics creates a tool `check_credit_score` (risk=medium) and wants to share it with team-customer-support.

```
Developer (team-fraud-analytics)         Registry API              Admin (platform-admin)
         │                                    │                           │
         │  Create tool (private workspace)   │                           │
         │  POST /api/v1/tools                │                           │
         │  { name: "check_credit_score",     │                           │
         │    risk_level: "medium",           │                           │
         │    endpoint_url: "...",            │                           │
         │    owner_team: "fraud-analytics" } │                           │
         │───────────────────────────────────→│                           │
         │  201 { id: "tool-uuid-abc",        │                           │
         │        status: "private" }         │                           │
         │←───────────────────────────────────│                           │
         │                                    │                           │
         │  ── Tool is now PRIVATE ──         │                           │
         │  Not queryable by others           │                           │
         │  Not bindable by others            │                           │
         │                                    │                           │
         │  Submit for publish review         │                           │
         │  POST /api/v1/assets/tools/        │                           │
         │    tool-uuid-abc/publish           │                           │
         │  {                                 │                           │
         │    "dependency_declaration": {     │                           │
         │      "risk_level": "medium",       │                           │
         │      "owner_team": "fraud-analytics",                          │
         │      "endpoint_url": "https://..." │                           │
         │    }                               │                           │
         │  }                                 │                           │
         │───────────────────────────────────→│                           │
         │                                    │ Creates PublishRequest:  │
         │                                    │  { status: "pending_    │
         │                                    │    review",              │
         │                                    │    highest_risk: "medium"│
         │                                    │    submitted_by: dev     │
         │                                    │    dependency_decl: {...}}│
         │                                    │ Updates tool.status:     │
         │                                    │  "pending_review"        │
         │                                    │                           │
         │  202 { publish_request_id: "pr-1" }│                           │
         │←───────────────────────────────────│                           │
         │                                    │                           │
         │                                    │  ─── Tool is PENDING ─── │
         │                                    │  Admin review queue shows│
         │                                    │  the request             │
         │                                    │                           │
         │                                    │  GET /api/v1/admin/       │
         │                                    │  publish-requests?       │
         │                                    │  status=pending_review   │
         │                                    │←──────────────────────────│
         │                                    │                           │
         │                                    │  Response: full request   │
         │                                    │  with dependency_decl     │
         │                                    │  rendered for review      │
         │                                    │──────────────────────────→│
         │                                    │                           │
         │                                    │  Admin reviews:           │
         │                                    │  risk=medium → requires  │
         │                                    │  platform admin approval  │
         │                                    │  (team lead not enough)  │
         │                                    │                           │
         │                                    │  POST /api/v1/admin/      │
         │                                    │  publish-requests/pr-1/   │
         │                                    │  approve                  │
         │                                    │  { grantee_teams:        │
         │                                    │    ["customer-support"],  │
         │                                    │    expires_at: null }     │
         │                                    │←──────────────────────────│
         │                                    │                           │
         │                                    │ Registry API:             │
         │                                    │ • PublishRequest.status   │
         │                                    │   = "approved"            │
         │                                    │ • tool.status             │
         │                                    │   = "published"           │
         │                                    │ • INSERT AssetGrant:      │
         │                                    │   { asset_id: tool-uuid,  │
         │                                    │     grantee_team:         │
         │                                    │     "customer-support",   │
         │                                    │     granted_by: admin,    │
         │                                    │     expires_at: null }    │
         │                                    │ • INSERT GrantAudit:      │
         │                                    │   { action: "created" }   │
         │                                    │                           │
         │                                    │ 200 OK                    │
         │                                    │──────────────────────────→│

Now: developer on team-customer-support CAN see and bind check_credit_score.
     developer on team-other CANNOT see it (no grant).

┌────────────────────────────────────────────────────────────────────────┐
│  Visibility check on every GET /api/v1/tools:                          │
│                                                                         │
│  WHERE (tool.created_by = requesting_user                               │
│    OR (tool.status = 'published'                                        │
│        AND EXISTS (                                                     │
│          SELECT 1 FROM asset_grants                                     │
│          WHERE asset_id = tool.id                                       │
│            AND grantee_team = requesting_user.team                      │
│            AND revoked_at IS NULL                                       │
│            AND (expires_at IS NULL OR expires_at > NOW())               │
│        )                                                                │
│       )                                                                 │
│  )                                                                      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Flow: Deploy Gate (All Pre-flight Checks)

Every deploy request runs through this gate sequentially. The first failure stops and returns an error.

```
POST /api/v1/agents/fraud-agent/deploy
{ version_id: "v3", deployer_jwt: <jwt> }

Registry API runs checks in order:

Check 1: Deployer Team Ownership
┌──────────────────────────────────────────────────────┐
│  deployer_team = JWT.agentshield_team claim          │
│  agent.team = fraud-agent.team                       │
│                                                      │
│  fraud-agent.team = "fraud-analytics"                │
│  deployer_team    = "fraud-analytics"    ✓           │
│                                                      │
│  If mismatch → HTTP 403                              │
│  { "error": "deployer_not_in_owner_team",            │
│    "agent_team": "fraud-analytics",                  │
│    "deployer_team": "product" }                      │
└──────────────────────────────────────────────────────┘
         ↓ pass
Check 2: All Tool Grants Active
┌──────────────────────────────────────────────────────┐
│  version v3 tool snapshot:                           │
│    ["check_txn", "write_alert", "send_notification"] │
│                                                      │
│  For each tool, check:                               │
│    AssetGrant exists WHERE                           │
│      asset_id = tool.id                              │
│      AND grantee_team = "fraud-analytics"            │
│      AND revoked_at IS NULL                          │
│      AND (expires_at IS NULL OR expires_at > NOW())  │
│                                                      │
│  check_txn       → grant exists ✓                   │
│  write_alert     → grant exists ✓                   │
│  send_notification → NO GRANT ✗                     │
│                                                      │
│  → HTTP 422                                          │
│  { "error": "tool_grants_missing",                   │
│    "missing_grants": ["send_notification"] }         │
│                                                      │
│  (dev must get admin to grant send_notification      │
│   to fraud-analytics before deploying v3)            │
└──────────────────────────────────────────────────────┘
         ↓ pass (after grant obtained and retry)
Check 3: Functional Eval Passed
┌──────────────────────────────────────────────────────┐
│  version.eval_passed = true?                         │
│  yes ✓                                               │
│                                                      │
│  If false → HTTP 422                                 │
│  { "error": "eval_not_passed" }                      │
└──────────────────────────────────────────────────────┘
         ↓ pass
Check 4: Adversarial Eval (if any tool risk=high)
┌──────────────────────────────────────────────────────┐
│  max risk in tool snapshot = "medium"                │
│  → skip this check (only required for high-risk)     │
│                                                      │
│  If max risk = "high" AND adversarial_eval_passed    │
│  is null or false → HTTP 422                         │
│  { "error": "adversarial_eval_required_for_high_risk"}│
└──────────────────────────────────────────────────────┘
         ↓ pass
Check 5: No Critical-Risk Tools
┌──────────────────────────────────────────────────────┐
│  Any tool with risk_level = "critical"?              │
│  No ✓                                                │
│                                                      │
│  If yes → HTTP 422                                   │
│  { "error": "critical_risk_tool_not_deployable",     │
│    "offending_tools": ["wire_transfer"] }            │
└──────────────────────────────────────────────────────┘
         ↓ all checks passed
Provision Identity + Create Deployment
(see Flow 4 for detail)
```

---

## 10. Flow: Agent-to-Agent Handoff (Scope Attenuation)

**Example**: `orchestrator-agent` (scope: `[tool-a, tool-b, tool-c]`) delegates to `writer-agent` (scope: `[tool-b, tool-c, tool-d]`). The effective scope for writer-agent in this session is `[tool-b, tool-c]` — the intersection. Writer-agent cannot use `tool-d` even though its own blueprint allows it.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  Orchestrator Agent Pod (SA: agent-orchestrator-sa)                             │
│  Registered scope: [tool-a, tool-b, tool-c]                                    │
│                                                                                  │
│  LLM decides to hand off to writer-agent                                        │
│                                                                                  │
│  SDK computes effective scope for handoff:                                       │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │  orchestrator_scope = ["tool-a", "tool-b", "tool-c"]   (from OPA data)  │   │
│  │  writer_scope       = ["tool-b", "tool-c", "tool-d"]   (from OPA data)  │   │
│  │                                                                           │   │
│  │  effective_scope = orchestrator_scope ∩ writer_scope                     │   │
│  │                  = ["tool-b", "tool-c"]                                  │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  SDK builds delegation token (JWT, signed by orchestrator's SA token):          │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │  {                                                                        │   │
│  │    "sub": "alice-uuid-123",         ← user (if Class B session)         │   │
│  │    "act": {                                                               │   │
│  │      "sub": "system:serviceaccount:agentshield:agent-writer-sa",         │   │
│  │      "act": {                                                             │   │
│  │        "sub": "system:serviceaccount:agentshield:agent-orchestrator-sa"  │   │
│  │      }                                                                    │   │
│  │    },                                                                     │   │
│  │    "effective_scope": ["tool-b", "tool-c"],  ← intersection              │   │
│  │    "session_id": "sess-xyz"                                               │   │
│  │  }                                                                        │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  SDK POSTs to writer-agent:                                                     │
│  POST /agents/writer-agent/handoff                                              │
│  X-Delegation-Token: <token above>                                              │
│  Body: { "task": "write summary" }                                              │
└────────────────────────────────────────────────────────────────────────────────┘
         │ (mTLS via ztunnel)
         ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│  Writer Agent Pod (SA: agent-writer-sa)                                         │
│  Registered scope: [tool-b, tool-c, tool-d]                                    │
│                                                                                  │
│  Receives delegation token → stores effective_scope = ["tool-b", "tool-c"]     │
│  in session context                                                              │
│                                                                                  │
│  LLM decides to call tool-d (normally in writer's scope)                       │
│                                                                                  │
│  SDK builds OPA request:                                                        │
│  {                                                                               │
│    "tool_name": "tool-d",                                                       │
│    "sa_token": <writer's own SA token>,                                         │
│    "agent_class": "user_delegated",                                             │
│    "user_id": "alice-uuid-123",                                                 │
│    "user_team": "team-a",                                                       │
│    "effective_scope": ["tool-b", "tool-c"]   ← from delegation token           │
│  }                                                                               │
│         │                                                                        │
│         ▼                                                                        │
│  OPA Sidecar:                                                                   │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │  Check: is "tool-d" in effective_scope ["tool-b", "tool-c"]?            │   │
│  │  → NO                                                                    │   │
│  │  Result: { "allow": false, "reason": "delegation_scope_exceeded" }      │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  LLM decides to call tool-b instead:                                            │
│  { "tool_name": "tool-b", "effective_scope": ["tool-b", "tool-c"] }            │
│         │                                                                        │
│         ▼                                                                        │
│  OPA: "tool-b" in effective_scope → yes → allow                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Chain is auditable:** The `act` claim records every hop:
```
opa_decisions row:
  agent_identity_id:  "...agent-writer-sa"
  delegation_chain:   ["...agent-orchestrator-sa" → "...agent-writer-sa"]
  user_id:            "alice-uuid-123"
  tool_name:          "tool-b"
  decision:           "allow"
```

---

## 11. Flow: Grant Revocation and Cascade

**Example**: Admin revokes `check_credit_score` grant from `team-fraud-analytics` (e.g., compliance reason).

```
Admin
  │
  │ DELETE /api/v1/admin/grants/grant-uuid-abc
  │
  ▼
Registry API:

Step 1: Mark grant as revoked
  UPDATE asset_grants
  SET revoked_at = NOW()
  WHERE id = 'grant-uuid-abc'

Step 2: Write audit record
  INSERT grant_audit:
  { admin_id: "admin-uuid",
    action: "revoked",
    asset_id: "tool-uuid-abc",
    grantee_team: "fraud-analytics",
    timestamp: now() }

Step 3: Find all affected AgentVersions
  SELECT av.*
  FROM agent_versions av
  JOIN agent_version_tools avt ON av.id = avt.version_id
  WHERE avt.tool_id = 'tool-uuid-abc'
    AND av.team = 'fraud-analytics'

Step 4: Flag affected versions
  UPDATE agent_versions
  SET grant_invalid = true
  WHERE id IN (affected version IDs)

  → These versions are now blocked from new deployments

Step 5: Return response
  200 {
    "revoked": true,
    "affected_versions": ["fraud-agent:v3", "fraud-agent:v4"],
    "note": "Affected versions flagged grant_invalid. Existing running deployments continue. New deployments blocked."
  }
```

**What happens to running deployments:**
```
Running fraud-agent deployment (already deployed):
  → Continues to run. OPA bundle still allows check_credit_score
    because the bundle data is keyed on SA identity, not grants.
  → IMPORTANT: Grant revocation affects the CONTROL PLANE (who can deploy)
    not the DATA PLANE (what running agents can do).
  → If immediate runtime revocation is needed, the operator must:
    kubectl scale deployment fraud-agent --replicas=0
    (this is an explicit manual action, not automatic, to avoid accidental outages)

New deployment of fraud-agent:v3 or v4:
  → Deploy gate Check 2 fails: grant for check_credit_score is revoked
  → HTTP 422 { "error": "tool_grants_missing", "missing_grants": ["check_credit_score"] }
  → Developer must either: get grant reinstated OR remove check_credit_score from the agent
```

---

## 12. Flow: OPA Bundle Lifecycle

How the OPA Bundle Server keeps all sidecars in sync.

```
                    OPA Bundle Server
                    (nginx, git-backed)
                          │
             ┌────────────┴────────────┐
             │       /bundles/         │
             │       agentshield/      │
             │       ├── data.json     │
             │       └── policy.rego   │
             └────────────────────────┘
                          ▲
                          │ deploy-controller pushes data.json update
                          │ on every agent deploy
                          │
           ┌──────────────┘
           │ What data.json looks like:
           │
           │ {
           │   "registered_agents": {
           │     "system:serviceaccount:agentshield:agent-fraud-sa": {
           │       "agent_class": "daemon",
           │       "tools": ["check_txn", "write_alert"],
           │       "tool_risk": { "check_txn": "low", "write_alert": "medium" }
           │     },
           │     "system:serviceaccount:agentshield:agent-copilot-sa": {
           │       "agent_class": "user_delegated",
           │       "tools": ["get_order", "cancel_order", "send_email"],
           │       "tool_risk": {
           │         "get_order": "low",
           │         "cancel_order": "high",
           │         "send_email": "medium"
           │       }
           │     }
           │   },
           │   "team_grants": {
           │     "team-a": { "get_order": true, "cancel_order": true, "send_email": true },
           │     "team-b": { "get_order": true }
           │   }
           │ }

All OPA Sidecars (one per agent pod):
  ┌──────────────────────────────────────────────────────────────────────┐
  │  On startup:                                                          │
  │    GET http://opa-bundle-server/bundles/agentshield                  │
  │    Load data.json + policy.rego into memory                          │
  │                                                                       │
  │  Every 30s:                                                           │
  │    GET http://opa-bundle-server/bundles/agentshield                  │
  │    with If-None-Match: <last etag>                                   │
  │    304 Not Modified → no update needed                               │
  │    200 with new bundle → reload data in memory                       │
  │                                                                       │
  │  If bundle server unreachable:                                        │
  │    Continue serving last-known-good bundle                            │
  │    Log warning every 60s                                             │
  │    After 5 min: switch to fail-closed (allow = false on all calls)  │
  │    Alert fires to PagerDuty/Slack                                    │
  └──────────────────────────────────────────────────────────────────────┘
```

**Timeline: new agent deployed, how long until all OPA sidecars know about it:**

```
T+0s   Developer clicks Deploy in Studio
T+5s   deploy-controller creates K8s ServiceAccount
T+8s   deploy-controller PATCHes data.json on bundle server
T+10s  deploy-controller creates K8s Deployment
T+40s  (worst case) All existing OPA sidecars have polled and loaded new data.json
T+60s  Agent pod starts, OPA sidecar inside it also loads bundle
T+60s  New agent's tools can be called; its OPA sidecar accepts its own SA token
```

---

## 13. Data Model

### New / Extended Entities

```
Agent (existing table, extended)
├── agent_class: ENUM('daemon', 'user_delegated')  NOT NULL
│   — set at publish time, immutable for the version lifetime
└── status: ENUM('private', 'pending_review', 'published')  DEFAULT 'private'

AgentVersion (existing table, extended)
└── adversarial_eval_passed: BOOLEAN  DEFAULT NULL
    — NULL = not required (risk < high)
    — TRUE = red-team passed (required for risk = high before deploy)
    — FALSE = failed (deploy blocked)

Deployment (existing table, extended)
└── agent_identity_id: UUID  → FK to agent_identities

agent_identities (NEW)
├── id: UUID  PRIMARY KEY
├── deployment_id: UUID  → FK deployments
├── k8s_service_account: TEXT   e.g. "agent-fraud-sa"
├── sa_subject: TEXT            e.g. "system:serviceaccount:agentshield:agent-fraud-sa"
├── issued_at: TIMESTAMPTZ  NOT NULL
├── rotated_at: TIMESTAMPTZ  DEFAULT NULL
├── revoked_at: TIMESTAMPTZ  DEFAULT NULL
└── revoked_by: TEXT  DEFAULT NULL
    — append-only: rows are never deleted

publish_requests (NEW)
├── id: UUID  PRIMARY KEY
├── asset_id: UUID  NOT NULL
├── asset_type: ENUM('tool', 'agent', 'skill', 'workflow')  NOT NULL
├── submitted_by: TEXT  NOT NULL   (user sub claim — set by API, not client)
├── submitted_at: TIMESTAMPTZ  NOT NULL  DEFAULT NOW()
├── status: ENUM('pending_review', 'approved', 'rejected')  DEFAULT 'pending_review'
├── highest_risk_level: ENUM('low', 'medium', 'high')  NOT NULL
├── dependency_declaration: JSONB  NOT NULL   (full tree, validated on submit)
├── reviewed_by: TEXT  DEFAULT NULL
├── reviewed_at: TIMESTAMPTZ  DEFAULT NULL
└── review_notes: TEXT  DEFAULT NULL

asset_grants (NEW)
├── id: UUID  PRIMARY KEY
├── asset_id: UUID  NOT NULL
├── asset_type: ENUM('tool', 'agent', 'skill', 'workflow')  NOT NULL
├── grantee_team: TEXT  NOT NULL
├── granted_by: TEXT  NOT NULL   (admin user sub)
├── granted_at: TIMESTAMPTZ  NOT NULL  DEFAULT NOW()
├── expires_at: TIMESTAMPTZ  DEFAULT NULL   (null = indefinite)
└── revoked_at: TIMESTAMPTZ  DEFAULT NULL   (null = active)
    — INDEX: (asset_id, grantee_team, revoked_at, expires_at)

grant_audit (NEW — append-only, no UPDATE or DELETE permitted via RLS)
├── id: UUID  PRIMARY KEY
├── admin_id: TEXT  NOT NULL
├── action: ENUM('created', 'revoked', 'expired')  NOT NULL
├── asset_id: UUID  NOT NULL
├── grantee_team: TEXT  NOT NULL
└── timestamp: TIMESTAMPTZ  NOT NULL  DEFAULT NOW()

approvals (existing table, extended)
├── id: UUID  PRIMARY KEY
├── agent_name: TEXT  NOT NULL
├── tool_name: TEXT  NOT NULL
├── args: JSONB  NOT NULL              (PII-redacted; real values looked up via session_id)
├── session_id: TEXT  DEFAULT NULL     (for PII de-anonymization in reviewer UI)
├── opa_decision_id: UUID  → FK opa_decisions
├── status: ENUM('pending', 'approved', 'denied', 'timed_out')  DEFAULT 'pending'
├── user_id: TEXT  DEFAULT NULL        (Class B: who invoked the agent)
├── user_name: TEXT  DEFAULT NULL
├── user_team: TEXT  DEFAULT NULL
├── reviewer_id: TEXT  DEFAULT NULL    (who actioned the approval)
├── reviewed_at: TIMESTAMPTZ  DEFAULT NULL
├── reviewer_notes: TEXT  DEFAULT NULL
├── context: TEXT  NOT NULL  DEFAULT 'production'
│   -- 'production' | 'playground'
│   -- playground approvals are self-approved; no Slack notification; only visible in
│   -- the user's Playground HITL panel, not in the production approval dashboard
├── created_at: TIMESTAMPTZ  NOT NULL  DEFAULT NOW()
└── expires_at: TIMESTAMPTZ  NOT NULL  DEFAULT NOW() + INTERVAL '30 minutes'

approval_authority (NEW)
-- Who has the right to approve HITL requests for a given resource.
-- Approval rights are scoped per-agent, per-tool, or per-skill — not globally.
-- A reviewer sees only approvals for resources they have authority over.
├── id: UUID  PRIMARY KEY
├── resource_type: ENUM('agent', 'tool', 'skill')  NOT NULL
├── resource_id: TEXT  NOT NULL        (agent_name, tool_name, or skill_name)
├── approver_user_id: TEXT  DEFAULT NULL
│   -- if set: this specific user can approve requests for this resource
├── approver_role: TEXT  DEFAULT NULL
│   -- if set: any user with this Keycloak role can approve (e.g. "team_lead", "platform_admin")
│   -- at least one of approver_user_id or approver_role must be non-null
├── granted_by: TEXT  NOT NULL         (admin user_id who created this authority record)
├── granted_at: TIMESTAMPTZ  NOT NULL  DEFAULT NOW()
└── revoked_at: TIMESTAMPTZ  DEFAULT NULL
    -- INDEX: (resource_type, resource_id, revoked_at)

opa_decisions (existing table, extended)
├── agent_identity_id: TEXT  NOT NULL  (was agent_name; now SA subject string)
├── user_id: TEXT  DEFAULT NULL         (NEW: populated for Class B)
├── user_team: TEXT  DEFAULT NULL       (NEW: populated for Class B)
├── input_snapshot: JSONB  DEFAULT NULL (NEW: full OPA input, PII-redacted)
├── delegation_chain: TEXT[]  DEFAULT NULL  (NEW: for agent-to-agent flows)
└── context: TEXT  NOT NULL  DEFAULT 'production'
    -- 'production' | 'playground'
    -- Playground runs tagged to prevent audit noise from test traffic
    -- All existing production columns retained
```

### Visibility Rule (enforced in all list queries)

```sql
-- Determines if a given user (user_sub, user_team) can see an asset
CREATE OR REPLACE FUNCTION asset_visible(
  p_asset_id UUID, p_user_sub TEXT, p_user_team TEXT
) RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    -- Case 1: user owns the asset (private workspace)
    SELECT 1 FROM assets
    WHERE id = p_asset_id AND created_by = p_user_sub

    UNION ALL

    -- Case 2: published + active grant to user's team
    SELECT 1 FROM assets a
    JOIN asset_grants g ON g.asset_id = a.id
    WHERE a.id = p_asset_id
      AND a.status = 'published'
      AND g.grantee_team = p_user_team
      AND g.revoked_at IS NULL
      AND (g.expires_at IS NULL OR g.expires_at > NOW())
  );
$$ LANGUAGE sql STABLE;
```

**Playground exception**: `asset_visible()` is not the gating function for Playground access. The Playground applies its own rule: a user can test any version they own (`created_by = user_sub`), regardless of the asset's `status` (`private`, `pending_review`, or `published`). `asset_visible()` gates production API access — binding tools into agents, deploying, and discovering shared assets. These are intentionally separate: the Playground is the pre-publish iteration loop, and requiring publication before testing would defeat its purpose.

---

## 14. API Contracts

### Approval Authority Management

```
# Who can approve HITL requests for a given resource?
GET /api/v1/admin/approval-authority?resource_type=tool&resource_id=cancel_order
  Auth: admin JWT
  Returns: [ { id, resource_type, resource_id, approver_user_id, approver_role,
               granted_by, granted_at, revoked_at } ]

POST /api/v1/admin/approval-authority
  Auth: admin JWT
  Body: {
    "resource_type": "agent" | "tool" | "skill",
    "resource_id":   "<agent_name | tool_name | skill_name>",
    "approver_user_id": "<user_sub>",    // one of these two required
    "approver_role":    "<keycloak_role>"
  }
  Success: 201 { "id": "uuid" }

DELETE /api/v1/admin/approval-authority/{id}
  Auth: admin JWT
  Success: 200 { "revoked": true }
  Side effects: approval_authority.revoked_at = NOW()

# Approval dashboard query (what a reviewer sees)
GET /api/v1/approvals?status=pending
  Auth: reviewer JWT
  Returns: only approvals where the caller has authority
    (approval_authority join for this user_id + their Keycloak roles)
    AND context = 'production'
  (Playground approvals are returned only via Playground-specific endpoint)

# Playground HITL dashboard
GET /api/v1/playground/approvals?status=pending
  Auth: user JWT
  Returns: pending approvals where context='playground' AND user_id = caller
    (only the asset owner sees their own playground pending approvals)

POST /api/v1/approvals/{id}/decide
  Auth: reviewer JWT
  Body: { "decision": "approved" | "denied", "reviewer_notes": "<optional>" }
  Validation:
    Production: caller must have a matching approval_authority record
    Playground: caller must be the user_id on the approval (self-approval only)
  Success: 200 { "decided": true }
  Side effects:
    • approvals.status → decision
    • approvals.reviewer_id, reviewed_at → set
    • NOTIFY agentshield_approvals
```

### Publish & Grant Workflow

```
POST /api/v1/assets/{type}/{id}/publish
  Auth: user JWT
  Body: { "dependency_declaration": { ... } }
  Success: 202 { "publish_request_id": "pr-uuid" }
  Errors:
    422 { "error": "critical_risk_not_publishable" }
        if any dep has risk_level = "critical"
    422 { "error": "incomplete_dependency_declaration",
          "missing_fields": [...] }
        if declaration is missing required fields

GET /api/v1/admin/publish-requests?status=pending_review
  Auth: admin JWT
  Success: 200 [ { publish_request + full dependency_declaration } ]

POST /api/v1/admin/publish-requests/{id}/approve
  Auth: admin JWT
  Body: { "grantee_teams": ["team-a"], "expires_at": null }
  Success: 200 { "approved": true, "grants_created": 1 }
  Side effects:
    • asset.status → "published"
    • INSERT asset_grants (one per grantee_team)
    • INSERT grant_audit { action: "created" }

POST /api/v1/admin/publish-requests/{id}/reject
  Auth: admin JWT
  Body: { "notes": "Risk level requires re-classification" }
  Success: 200 { "rejected": true }
  Side effects: asset.status → "private"

DELETE /api/v1/admin/grants/{grant_id}
  Auth: admin JWT
  Success: 200 { "revoked": true, "affected_versions": [...] }
  Side effects:
    • asset_grants.revoked_at = NOW()
    • INSERT grant_audit { action: "revoked" }
    • agent_versions.grant_invalid = true for all versions using this asset
```

### Asset Visibility

```
GET /api/v1/tools
GET /api/v1/skills
GET /api/v1/agents
  Auth: any user JWT
  Returns: only assets visible to requesting user
  (applies asset_visible() filter — see Data Model)

POST /api/v1/agents/{name}/tools    (bind tool to agent)
  Auth: user JWT
  Body: { "tool_id": "uuid" }
  Errors:
    403 { "error": "tool_not_visible",
          "tool_id": "...",
          "missing_grant": true }
        if tool is not in user's visible set
```

### Modified Deploy Endpoint

```
POST /api/v1/agents/{name}/deploy
  Auth: user JWT
  Body: { "version_id": "uuid" }
  Pre-flight checks (in order):
    1. JWT.team == agent.team
       → 403 { "error": "deployer_not_in_owner_team" }
    2. ∀ tool ∈ version.tools:
         active AssetGrant exists for deployer's team
       → 422 { "error": "tool_grants_missing",
                "missing_grants": ["tool-name-1", ...] }
    3. version.eval_passed == true
       → 422 { "error": "eval_not_passed" }
    4. if max(tool.risk_level) == "high":
         version.adversarial_eval_passed == true
       → 422 { "error": "adversarial_eval_required_for_high_risk" }
    5. "critical" ∉ { tool.risk_level for tool in version.tools }
       → 422 { "error": "critical_risk_tool_not_deployable",
                "offending_tools": ["..."] }
  On pass:
    → Trigger deploy-controller (see Flow 4)
    → 202 { "deployment_id": "...", "agent_identity_id": "..." }
```

---

## 15. OPA Policy Structure

### Bundle layout

```
opa-bundle-server (nginx pod):
  /bundles/agentshield/
    ├── .manifest          ← OPA bundle manifest (revision, roots)
    ├── data.json          ← agent registry + team grants (updated per deploy)
    └── policy.rego        ← single Rego file for all agents
```

### policy.rego

```rego
package agentshield.agent

import future.keywords.if
import future.keywords.in

# ─── defaults ────────────────────────────────────────────────────────────────
default allow            := false
default require_approval := false
default deny_reason      := "tool_call_denied"

# ─── identity validation ──────────────────────────────────────────────────────
# sa_subject comes from the validated SA token's "sub" claim
# OPA verifies the token before this rule is evaluated
valid_agent if {
    data.registered_agents[input.sa_subject]
}

tool_registered if {
    input.tool_name in data.registered_agents[input.sa_subject].tools
}

# ─── Class B: user grant check (intersection rule) ────────────────────────────
user_has_grant if {
    data.team_grants[input.user_team][input.tool_name] == true
}

# ─── Playground sandbox: bypass user grant check ─────────────────────────────
# Sandbox mode mocks all tool side effects — no real data leaves the system.
# We skip the team grant check so developers can test tool logic before grants
# are established. Agent scope (tool_registered) still applies.
grant_bypassed if {
    input.playground == true
    input.sandbox    == true
}

# user_grant_satisfied = actual grant OR sandbox bypass
user_grant_satisfied if { user_has_grant }
user_grant_satisfied if { grant_bypassed  }

# ─── delegation scope check ───────────────────────────────────────────────────
# If a delegation token is present, tool must be in effective_scope
within_delegation_scope if {
    not input.effective_scope   # no delegation token → no scope restriction
}

within_delegation_scope if {
    input.tool_name in input.effective_scope
}

# ─── Class A: daemon agent (no user context) ──────────────────────────────────
allow if {
    valid_agent
    input.agent_class == "daemon"
    not input.user_id       # daemon must not carry user identity
    tool_registered
    within_delegation_scope
    data.registered_agents[input.sa_subject].tool_risk[input.tool_name] == "low"
}

# medium risk daemon: allow (no approval needed, audit log is the control)
allow if {
    valid_agent
    input.agent_class == "daemon"
    not input.user_id
    tool_registered
    within_delegation_scope
    data.registered_agents[input.sa_subject].tool_risk[input.tool_name] == "medium"
}

# ─── Class B: user-delegated (intersection rule) ──────────────────────────────
allow if {
    valid_agent
    input.agent_class == "user_delegated"
    input.user_id       # user identity must be present
    tool_registered
    user_grant_satisfied  # user_has_grant OR grant_bypassed (sandbox)
    within_delegation_scope
    data.registered_agents[input.sa_subject].tool_risk[input.tool_name] == "low"
}

allow if {
    valid_agent
    input.agent_class == "user_delegated"
    input.user_id
    tool_registered
    user_grant_satisfied
    within_delegation_scope
    data.registered_agents[input.sa_subject].tool_risk[input.tool_name] == "medium"
}

# ─── High-risk: require approval (both classes) ───────────────────────────────
require_approval if {
    valid_agent
    tool_registered
    within_delegation_scope
    data.registered_agents[input.sa_subject].tool_risk[input.tool_name] == "high"
}

# ─── Deny reasons (for audit + SDK error messages) ───────────────────────────
deny_reason := "agent_unauthenticated" if { not valid_agent }
deny_reason := "tool_not_registered"   if { valid_agent; not tool_registered }
deny_reason := "user_not_granted" if {
    valid_agent; tool_registered
    input.agent_class == "user_delegated"
    not user_has_grant
    not grant_bypassed
}
deny_reason := "daemon_has_user_context" if {
    valid_agent; input.agent_class == "daemon"; input.user_id
}
deny_reason := "delegation_scope_exceeded" if {
    valid_agent; tool_registered
    not within_delegation_scope
}
```

### OPA SDK input shape (sent by `opa_client.py`)

The `playground` and `sandbox` fields are always present. In production they are both `false`. OPA uses them to apply grant-bypass and to tag the `opa_decisions` row with `context='playground'`.

```python
# Class A — production
{
    "sa_token":    "<projected SA token — OPA validates this>",
    "sa_subject":  "system:serviceaccount:agentshield:agent-reconciler-sa",
    "tool_name":   "run_check",
    "args":        { "target": "accounts" },
    "agent_class": "daemon",
    "playground":  False,
    "sandbox":     False
    # no user_id, no user_team
}

# Class A — Playground daemon mode
# (Registry API stripped the user JWT; playground SA is the machine identity)
{
    "sa_token":    "<playground-runner-alice-sa token>",
    "sa_subject":  "system:serviceaccount:agentshield-playground:playground-runner-alice-sa",
    "tool_name":   "run_check",
    "args":        { "target": "accounts" },
    "agent_class": "daemon",
    "playground":  True,
    "sandbox":     True    # or False if full-execution playground run
    # no user_id — daemon mode, user was stripped
}

# Class B — production
{
    "sa_token":        "<projected SA token>",
    "sa_subject":      "system:serviceaccount:agentshield:agent-copilot-sa",
    "tool_name":       "cancel_order",
    "args":            { "order_id": "1234" },
    "agent_class":     "user_delegated",
    "user_id":         "alice-uuid-123",
    "user_team":       "team-a",
    "playground":      False,
    "sandbox":         False
}

# Class B — Playground sandbox (grant-bypass applies)
{
    "sa_token":        "<projected SA token>",
    "sa_subject":      "system:serviceaccount:agentshield:agent-copilot-sa",
    "tool_name":       "cancel_order",
    "args":            { "order_id": "1234" },
    "agent_class":     "user_delegated",
    "user_id":         "alice-uuid-123",
    "user_team":       "team-a",
    "playground":      True,
    "sandbox":         True   # grant_bypassed fires; tool_registered still required
}

# Delegated call (agent-to-agent) — production
{
    "sa_token":        "<writer agent's SA token>",
    "sa_subject":      "system:serviceaccount:agentshield:agent-writer-sa",
    "tool_name":       "tool-b",
    "args":            { ... },
    "agent_class":     "user_delegated",
    "user_id":         "alice-uuid-123",
    "user_team":       "team-a",
    "effective_scope": ["tool-b", "tool-c"],  # from delegation token
    "playground":      False,
    "sandbox":         False
}
```

---

## 16. Infrastructure Changes

### New: Istio Ambient Mesh

Install into `agentshield` namespace. L4 only — no Waypoints, no sidecar injection.

```yaml
# Label the namespace to opt into Ambient Mesh
kubectl label namespace agentshield istio.io/dataplane-mode=ambient

# Helm install: istiod + ztunnel + istio-cni
helm install istiod istio/istiod \
  --set profile=ambient \
  --namespace istio-system

helm install ztunnel istio/ztunnel \
  --namespace istio-system

helm install istio-cni istio/cni \
  --namespace istio-system
```

What this does:
- `istiod`: CA that issues SPIFFE SVIDs for every pod based on its ServiceAccount
- `ztunnel`: DaemonSet that provides L4 mTLS for all pod-to-pod traffic in labeled namespaces
- `istio-cni`: configures iptables rules so traffic is redirected through ztunnel

What this does NOT do:
- Does not inject sidecars into agent pods
- Does not intercept or inspect tool call content (L4 only = TCP level)
- Does not call OPA (that remains the SDK's job in Option C)
- Does not affect Envoy Gateway (operates at L7 ingress, separate from ambient mesh)

### New: OPA Bundle Server

```yaml
# Deployed as part of agentshield chart
apiVersion: apps/v1
kind: Deployment
metadata:
  name: opa-bundle-server
spec:
  replicas: 2
  template:
    spec:
      containers:
      - name: nginx
        image: nginx:1.25
        volumeMounts:
        - name: bundle-data
          mountPath: /usr/share/nginx/html/bundles
      volumes:
      - name: bundle-data
        configMap:
          name: opa-bundle-data
```

`deploy-controller` updates `opa-bundle-data` ConfigMap via K8s API on each agent deploy.

### Modified: Agent Pod (via deploy-controller)

New projected volume in every agent pod spec:

```yaml
volumes:
- name: agentshield-opa-token
  projected:
    sources:
    - serviceAccountToken:
        audience: agentshield-opa
        expirationSeconds: 3600
        path: agentshield-opa

volumeMounts:
- name: agentshield-opa-token
  mountPath: /var/run/secrets/tokens
  readOnly: true
```

### Modified: OPA Sidecar Config

```yaml
# OPA sidecar now configured with:
# 1. Bundle server URL (replaces ConfigMap mount)
# 2. OIDC config for validating projected SA tokens
args:
- run
- --server
- --addr=0.0.0.0:8181
- --set=services.bundle_server.url=http://opa-bundle-server.agentshield.svc:8080
- --set=bundles.agentshield.service=bundle_server
- --set=bundles.agentshield.resource=/bundles/agentshield
- --set=bundles.agentshield.polling.min_delay_seconds=30
- --set=bundles.agentshield.polling.max_delay_seconds=60
# Token verification via K8s OIDC
- --set=plugins.jwt_authz.enabled=true
```

---

## 17. Migration Path (Current → Option C)

Istio Ambient (ztunnel) is installed as part of Phase 1 alongside the OPA bundle server and SA token changes — not as a standalone prior step. There is no ztunnel-only intermediate phase. The ztunnel install is low-risk (L4 only, transparent to pods) and can be validated in the same deployment window.

### Phase 1: Istio Ambient + OPA Bundle Server + SPIFFE-Keyed Policies

Goal: establish agent machine identity, centralize policy distribution, replace name-string OPA key with SA identity. This is the core enforcement upgrade.

```
1. Install Istio Ambient into istio-system namespace
   helm install istiod istio/istiod --set profile=ambient
   helm install ztunnel istio/ztunnel
   helm install istio-cni istio/cni
   kubectl label namespace agentshield istio.io/dataplane-mode=ambient

2. Verify Istio is healthy before continuing
   - ztunnel running on all nodes
   - pod-to-pod traffic encrypted (check ztunnel logs)
   - all existing agent traffic still flows (Envoy Gateway, Safety Orchestrator unchanged)
   Rollback at this point: remove namespace label + uninstall Istio, zero app changes

3. Deploy opa-bundle-server (2 replicas)

4. Migrate policy_generator.py (registry-api):
   - Generate data.json keyed on SA subject instead of per-agent Rego
   - POST updated data.json to bundle server on each deploy
   - Stop creating per-agent ConfigMaps

5. Update OPA sidecar config in manifest_builder.py:
   - Point to bundle server URL (remove ConfigMap volume mount)
   - Add K8s OIDC config for validating projected SA tokens

6. Add projected SA token volume to all agent + workflow pod specs (manifest_builder.py):
   - audience: agentshield-opa
   - expirationSeconds: 3600
   - mountPath: /var/run/secrets/tokens/agentshield-opa

7. Update opa_client.py in SDK:
   - Read SA token from /var/run/secrets/tokens/agentshield-opa
   - Include sa_token + sa_subject + agent_class in OPA input
   - For declarative-runner: include workflow_node in OPA input

8. Update declarative-runner (graph_builder.py):
   - Same SA token read + include workflow_node name

9. Rolling redeploy all agents + workflows (one team at a time)

10. Validate:
    - OPA denies calls with missing SA token (agent_unauthenticated)
    - OPA denies calls for unregistered tools (tool_not_registered)
    - Policy keyed on SA subject, not agent_name string
    - Rogue pod test: pod without correct SA → denied

11. Remove old per-agent ConfigMaps (cleanup)
```

### Phase 2: Control Plane — Publish / Grant Workflow

Goal: add authoring isolation and the publish/admin-grant lifecycle.

```
1. DB migration (additive — no destructive changes):
   - ALTER TABLE agents ADD COLUMN agent_class TEXT, ADD COLUMN status TEXT DEFAULT 'published'
   - ALTER TABLE agent_versions ADD COLUMN adversarial_eval_passed BOOLEAN
   - CREATE TABLE publish_requests
   - CREATE TABLE asset_grants (INDEX on asset_id, grantee_team, revoked_at)
   - CREATE TABLE grant_audit (RLS: no DELETE or UPDATE)
   - CREATE TABLE agent_identities (append-only)
   - ALTER TABLE opa_decisions: rename agent_name → agent_identity_id,
     ADD COLUMN user_id, user_team, input_snapshot, delegation_chain

2. Implement registry-api endpoints:
   - POST /assets/{type}/{id}/publish
   - GET  /admin/publish-requests
   - POST /admin/publish-requests/{id}/approve
   - POST /admin/publish-requests/{id}/reject
   - DELETE /admin/grants/{id}

3. Implement asset_visible() SQL function + apply to all list queries

4. Extend deploy endpoint with pre-flight gate:
   - Deployer team check
   - Tool grant validity check
   - Adversarial eval check (high-risk agents)
   - Critical tool block

5. Extend Studio:
   - Agent class selector (daemon / user_delegated) on agent creation
   - Publish submit form with dependency declaration
   - Admin review panel (publish queue + approve/reject + grant management)

6. Backfill existing agents: status='published', agent_class based on usage type
```

### Phase 3: User Identity Threading (Class B)

Goal: enforce intersection rule for interactive agents; daemon agents reject user context.

```
1. Classify all existing agents as daemon or user_delegated (one-time manual step)

2. For Class B (user_delegated) agents:
   - Add user JWT gate in /chat handler: 401 if X-User-Id missing
   - Thread user_id + user_team into every OPA call (SDK + declarative-runner)
   - Update HITL Slack notification template to include user identity
   - Add team_grants to bundle data.json (populated from asset_grants table)

3. For Class A (daemon) agents:
   - Add guard in /trigger handler: 400 if Authorization header with user JWT present
   - OPA policy already handles daemon_has_user_context deny reason

4. Agent-to-agent handoff:
   - SDK computes effective_scope = scope_A ∩ scope_B before handoff
   - Encode delegation token with act chain + effective_scope
   - Include effective_scope in OPA input for delegated calls
```

---

## 18. Future: Option B — Istio Waypoint + OPA ext_authz

This section documents the planned future architecture. It is NOT implemented in Option C. The Option C design is explicitly built to make this migration low-risk.

### What Option B Solves (That Option C Doesn't)

In Option C, the SDK calls OPA voluntarily. A compromised agent pod that has had its SDK replaced or patched can skip the OPA call entirely and call tool endpoints directly. ztunnel will encrypt the traffic but won't block it (L4 only = no policy).

Option B puts the policy enforcement in the **kernel data path** via an Istio Waypoint Proxy — a dedicated L7 proxy that ALL outbound traffic from agent pods must pass through. The waypoint calls OPA before forwarding any request. The SDK cannot bypass this.

### Option B Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Istio Ambient Mesh (ztunnel L4 + Waypoint L7)                             │
│                                                                             │
│  ┌───────────────────────────────┐                                         │
│  │  Agent Pod (no OPA sidecar)   │                                         │
│  │  SA: agent-fraud-sa           │                                         │
│  │                               │                                         │
│  │  SDK calls tool endpoint      │                                         │
│  │  (no OPA call in SDK)         │                                         │
│  └───────────────┬───────────────┘                                         │
│                  │ outbound HTTP to tool endpoint                           │
│                  │ (ztunnel intercepts at L4)                               │
│                  ▼                                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Waypoint Proxy (per-namespace or per-SA)                            │  │
│  │  Envoy-based L7 proxy — in the kernel data path, cannot be bypassed  │  │
│  │                                                                      │  │
│  │  1. Terminate mTLS from agent pod                                    │  │
│  │     → extract SPIFFE URI from peer cert SAN:                        │  │
│  │       spiffe://cluster.local/ns/agentshield/sa/agent-fraud-sa       │  │
│  │                                                                      │  │
│  │  2. Call OPA ext_authz (gRPC CheckRequest):                         │  │
│  │     {                                                                │  │
│  │       "source": {                                                    │  │
│  │         "principal": "spiffe://...agent-fraud-sa"   ← from mTLS     │  │
│  │       },                                                             │  │
│  │       "request": {                                                   │  │
│  │         "http": {                                                    │  │
│  │           "method": "POST",                                          │  │
│  │           "path": "/tools/cancel_order",                            │  │
│  │           "headers": {                                               │  │
│  │             "x-user-id": "alice-uuid-123",   ← forwarded by Envoy  │  │
│  │             "x-team": "team-a"                                       │  │
│  │           }                                                          │  │
│  │         }                                                            │  │
│  │       }                                                              │  │
│  │     }                                                                │  │
│  │                                                                      │  │
│  │  3. OPA responds: allow / deny                                       │  │
│  │                                                                      │  │
│  │  4. If allow: forward request to tool endpoint                      │  │
│  │     If deny: return 403 to agent pod                                │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                  │ (if allowed)                                              │
│                  ▼                                                          │
│  ┌───────────────────────────────┐                                         │
│  │  Tool Endpoint Pod            │                                         │
│  └───────────────────────────────┘                                         │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  OPA (centralized, 3 replicas — NO longer per-pod)                    │ │
│  │  Same bundle server as Option C. Same Rego. Same data.json.           │ │
│  │  Now exposed on gRPC port 9191 for ext_authz.                         │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
```

### Option B Migration Steps (from Option C)

When the time comes to migrate, deploy Waypoints directly. There is no intermediate ztunnel-only phase — Istio Ambient (ztunnel + istiod) is already running as part of Option C Phase 1. The only new piece is the Waypoint Proxy and the switch from per-pod OPA sidecar to centralized OPA ext_authz.

All prerequisites are already in place from Option C:
- Istio Ambient (istiod + ztunnel) running in the cluster
- SPIFFE SVIDs minted for all agent pods (via istiod + each pod's ServiceAccount)
- OPA bundle server running with data.json keyed on SA subjects
- policy.rego unchanged — no policy rewrite needed

```
Step 1: Deploy centralized OPA (3 replicas) with ext_authz gRPC enabled
  - Add to OPA Deployment args:
      --set=plugins.envoy_ext_authz_grpc.addr=:9191
      --set=plugins.envoy_ext_authz_grpc.query=data.agentshield.agent.allow
  - Scale to 3 replicas with PodDisruptionBudget (minAvailable: 2)
  - Same bundle server config as per-pod sidecars — no data.json changes

Step 2: Deploy Waypoint Proxy for agentshield namespace
  kubectl apply -f - <<EOF
  apiVersion: gateway.networking.k8s.io/v1
  kind: Gateway
  metadata:
    name: agentshield-waypoint
    namespace: agentshield
    labels:
      istio.io/waypoint-for: namespace
  spec:
    gatewayClassName: istio-waypoint
  EOF

Step 3: Configure Waypoint to call centralized OPA via ext_authz
  - Apply EnvoyFilter on the Waypoint pointing to centralized OPA at port 9191
  - Waypoint extracts SPIFFE URI from mTLS peer cert SAN automatically
  - Waypoint forwards X-User-Id, X-Team headers (already set by Envoy Gateway)

Step 4: Shadow mode validation (run both OPA sidecar + Waypoint in parallel)
  - Leave OPA sidecars running
  - Configure Waypoint ext_authz in log-only mode (allow all, log decisions)
  - Compare Waypoint OPA decisions vs sidecar OPA decisions for 24–48h
  - Confirm decisions are identical before switching enforcement on

Step 5: Switch Waypoint to enforcement mode
  - Update EnvoyFilter: ext_authz failure_mode_allow: false
  - Monitor: Waypoint should now block any call that OPA denies

Step 6: Validate bypass is impossible
  - Deploy test pod with correct SA but SDK OPA call removed
  - Attempt direct tool call (no OPA call from SDK)
  - Waypoint must block it → confirms SDK bypass is closed

Step 7: Remove OPA sidecars from all agent pods (manifest_builder.py)
  - Remove opa sidecar container from pod spec template
  - Remove projected SA token volume (Waypoint reads identity from mTLS cert, not token)
  - Rolling redeploy all agents + declarative-runner workflows

Step 8: Remove OPA call from SDK and declarative-runner
  - Delete services/sdk/agentshield_sdk/opa_client.py
  - Remove _wrap_tool_with_governance() from graph_builder.py
  - SDK now calls tool endpoints directly — Waypoint handles all enforcement
  - Remove SA_SUBJECT, AGENT_CLASS env vars from pod specs (no longer needed by SDK)

Step 9: Cleanup
  - Remove opa-bundle-server projected token volume annotations
  - Decommission per-pod OPA ConfigMap infrastructure (already removed in Option C)
  - Update Helm chart to remove OPA sidecar sub-chart
```

**Authorization during migration (Steps 1–5):** Both OPA sidecars (per-pod) and Waypoint (shadow mode) are running. The sidecars enforce; the Waypoint logs. No authorization gap.

**Cutover moment (Step 5 → 6):** Enforcement switches from sidecars to Waypoint. This is the only moment where a decision mismatch could cause a user-visible impact — shadow mode in Step 4 prevents this by confirming decisions match first.

### What does NOT change in Option B migration

| Component | Option C | Option B | Changed? |
|-----------|----------|----------|----------|
| SPIFFE identity model | K8s SA subject | Same | No |
| OPA Rego policy | Same file | Same file | No |
| OPA bundle server | Same | Same | No |
| data.json format | Same | Same | No |
| Audit tables | Same | Same | No |
| Control plane (publish/grant/deploy) | Same | Same | No |
| Envoy Gateway | Same | Same | No |
| Keycloak | Same | Same | No |

The only things that change:
- Add Waypoint Proxy deployment
- Configure ext_authz on Waypoint → centralized OPA
- Remove OPA sidecar from agent pods
- Remove OPA call from SDK

---

## 19. Open Questions for Reviewers

| # | Question | Context | Options Considered | Blocked Decision |
|---|----------|---------|-------------------|-----------------|
| OQ-1 | Who can authorize cross-team deploy grants? | REQ-DEPLOY-1 allows cross-team deploys with an explicit grant, but authority is unspecified. Least-privilege = platform admin only. Developer velocity = team leads should be able to peer-grant. | A: Platform admin only (most conservative). B: Grantee team lead self-approves. C: Either team lead can approve. | Controls who can POST `/api/v1/admin/grants` for cross-team scenarios |
| OQ-2 | Grant expiry: default indefinite or time-bounded? | Time-bounded (90d) is safer but creates renewal toil. Indefinite is simpler but grants accumulate silently over time. The schema supports both via nullable `expires_at`. | A: Default indefinite, optional expires_at. B: Default 90d, renewable before expiry. | UX default in the admin approval UI; ops burden implications |
| OQ-3 | Adversarial eval for high-risk agents — what constitutes a pass? | REQ-DEPLOY-3 requires `adversarial_eval_passed=true` but the criteria are undefined. Without a definition, this gate is theater. The `adversarial_eval_passed` column is in the schema; the deploy gate checks it. We just need the security team to define what sets it to true. | A: Manual red-team checklist reviewed by security. B: Automated scan (Garak / promptfoo adversarial). C: Both, sequentially. | Security team must define before the high-risk deploy gate is enforced for real |
| OQ-4 | Token exchange for Class B OBO — real or simulated? | Keycloak 26.0 supports RFC 8693 token exchange (experimental, can be enabled). With real token exchange, the token the agent presents to tool endpoints would be an actual audience-bound OBO token. Without it (current design), user identity is carried as claims in the request headers — no actual token exchange. Real token exchange is more standards-compliant but adds Keycloak configuration complexity and requires tools to validate the OBO token. | A: Enable Keycloak token exchange (RFC 8693, proper OBO). B: Simulate OBO via header threading (current plan). | Whether tool endpoints receive a real OBO token or just X-User-Id headers |

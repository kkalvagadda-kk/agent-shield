# OPA Authorization Contract (Phase 9.1 completion)

**Status: SHIPPED AND LIVE — verified on the cluster 2026-08-02.** This document began as a
remediation spec; the remediation landed. It is now the **wire contract of record** for the
tool-call authorization layer, plus a short delta list (§11).

> **§0 — Boundary.** Three authorization layers, independent, failing differently:
> **RBAC** ([`rbac-and-artifact-authorization.md`](rbac-and-artifact-authorization.md)) — may this
> *person* press Deploy? · **Identity**
> ([`identity-propagation-architecture.md`](identity-propagation-architecture.md)) — whose
> authority does the *run* carry? · **This doc** — may this *agent pod* call this *tool*, right
> now? This doc owns exactly one decision, made from the inputs in §3. It does **not** own who may
> start a run (§10.3), nor any credential handed *to* a tool (a fourth mechanism entirely —
> identity doc §2, "Delegated tool-call credentials").

**Live verification, 2026-08-02** — executed against agent pod `cic-journey-agent-sandbox` on
EKS `test-cluster-964-10086`:

```
GET  localhost:8181/health?bundles=true   → {}                    # all bundles activated
GET  localhost:8181/v1/data/agentshield   → {"allow":false,"deny_reason":"agent_unauthenticated",
                                              "reason":"deny_agent_unauthenticated", …}
GET  localhost:8181/v1/data               → agents: 19 entries, per-tool risk present
                                              ("cic-echo-tool": risk "high"), grants{platform}
```

Bundle loads (no Forbidden), the unified `package agentshield` is the served policy, `data.json`
carries per-tool risk, and the empty-input case correctly denies on gate 1. **The original
"why this exists" — reproduced below for history — no longer describes the system.**

> **Historical (2026-07-11 → 2026-07-28):** the Phase 9.1 "unified bundle" migration was wired
> only halfway. OPA sidecars never loaded a bundle (403), the served policy was the wrong package
> + hit a column-name bug, and no policy exposed the decision fields `opa_client` reads. Result:
> in real deployments OPA denied every tool call (fail-closed on empty result), while all e2e
> tests passed *vacuously* because dev/playground/sandbox used `mock_opa` (allow-all) and HITL was
> triggered by the SDK's static `fn.risk` — never touching the OPA sidecar.

This document is the single source of truth for both the contract and the tests. Do not diverge
from the contract below without updating this file.

---

## 1. Topology (unchanged)

- Each agent pod runs an **OPA sidecar** (`localhost:8181`) mounting the shared
  `opa-sidecar-config` ConfigMap (`infra/opa-bundle-server/configmap-opa-config.yaml`,
  namespace `agents-platform`).
- The sidecar polls the central **nginx bundle server** (`opa-bundle-server` Deployment,
  `infra/opa-bundle-server/`, namespace `agentshield-platform`).
- A `bundle-sync` sidecar in the bundle-server pod curls registry-api
  `/api/v1/bundle/...` every 30s and writes the bundle content nginx serves.
- registry-api builds the bundle content from the DB (`bundle_generator.py`,
  `routers/bundle.py`).

## 2. The three defects to fix — **ALL THREE FIXED** (verified 2026-08-02)

| # | Defect | Fix, in code today |
|---|---|---|
| 1 | 403 / bundle never loads | `GET /api/v1/bundle/bundle.tar.gz` serves a real gzipped bundle (`routers/bundle.py:87`); `bundle-sync` + `bundle-init` fetch it (`infra/opa-bundle-server/deployment.yaml:34,98`). Live probe: `health?bundles=true` → `{}` |
| 2 | `policy_rego` vs `rego_policy` column-name bug | endpoint superseded per §5; the tarball path is the live one |
| 3 | Package / contract mismatch | one static `package agentshield` at `services/registry-api/opa_policy/agentshield.rego`, exposing exactly the §4 decision surface. Live probe returns `allow`/`require_approval`/`reason`/`deny_reason` |

Original defect text retained below for the historical record.

### 2h. Historical defect detail

1. **403 / bundle never loads.** OPA config `resource: /bundles/agentshield` makes OPA
   fetch a **single gzipped bundle tarball** at that URL. nginx serves a *directory* of
   loose files (`data.json`, `policy.rego`) with autoindex off → `GET /bundles/agentshield`
   → `try_files $uri $uri/ =404` → directory → **403**. Fix: serve a real OPA bundle
   `.tar.gz` at the resource path.
2. **Column-name bug.** `routers/bundle.py` `get_bundle_policy` runs
   `SELECT policy_rego FROM agent_policies` but the column is **`rego_policy`**
   (`models.py:590`). It throws → silently serves the fallback default-deny. Fix the
   column name (and this whole endpoint is superseded — see §5).
3. **Package / contract mismatch.** `opa_client` queries `POST /v1/data/agentshield` and
   reads top-level `result.allow / require_approval / reason / deny_reason`. No policy in
   the repo exposes those at `data.agentshield` (generator emits
   `package agentshield.agent.{name}`; fallback is `package agentshield.agent`). Fix: ship
   ONE policy `package agentshield` implementing the decision logic in §4.

## 3. Wire contract (FIXED — do not change)

`opa_client.py` is the source of truth for the request/response shape.

### Request (SDK → OPA sidecar)
`POST http://localhost:8181/v1/data/agentshield`
```json
{ "input": {
    "sa_subject":  "system:serviceaccount:agents-platform:agent-<name>-sa",
    "tool_name":   "issue_refund",
    "args":        { "...": "..." },
    "agent_class": "user_delegated" | "daemon",
    "playground":  false,
    "sandbox":     false,
    "user_id":     "",   // Class B (user_delegated) invoking user sub; "" for Class A
    "user_team":   ""    // Class B invoking user team; "" for Class A
} }
```

### Response (OPA → SDK), i.e. `data.agentshield`
```json
{ "result": {
    "allow":            true,
    "require_approval": false,
    "reason":           "policy_decision",
    "deny_reason":      ""          // set only when allow=false
} }
```

## 4. Decision logic (authoritative)

Evaluated in order; first failing gate wins.

1. **Identity present.** `input.sa_subject` must be a key in `data.agents`.
   Else → `allow=false, deny_reason="agent_unauthenticated"`.
2. **Identity match.** `data.agents[input.sa_subject].expected_sa_subject == input.sa_subject`.
   Else → `allow=false, deny_reason="identity_mismatch"`.
3. **Tool membership.** `input.tool_name` must be in the agent's *effective tool set* =
   `agent.tools` ∪ `data.grants[agent.team]`.
   Else → `allow=false, deny_reason="tool_not_granted"`.
4. **Risk → action.** Resolve the matched tool's `risk`, then map:
   | risk       | action           | allow | require_approval |
   |------------|------------------|-------|------------------|
   | `low`      | allow            | true  | false            |
   | `medium`   | log              | true  | false            |
   | `high`     | require_approval | true  | **true**         |
   | `critical` | deny             | false | false            |
   | unknown/missing | deny        | false | false            |
   - `critical`/unknown → `allow=false, deny_reason="tool_risk_denied"`.
   - `medium` → allow=true (audit via decision_logs; no separate gate needed here).
5. `reason` is a short human string (e.g. `"allow_low_risk"`, `"require_approval_high_risk"`).

**`sandbox` / `playground` inputs:** carried in the input but the v1 policy does NOT branch
on them for allow/deny. (Future: sandbox may auto-approve HITL. Record as future improvement,
do not implement now.)

## 5. Bundle data shape (data.json) — REQUIRED CHANGE

`bundle_generator.generate_bundle_data` must include per-tool **risk**. Target shape:
```json
{
  "agents": {
    "<sa_subject>": {
      "tools": [ {"name": "lookup_order", "risk": "low"},
                 {"name": "issue_refund", "risk": "high"} ],
      "team": "platform",
      "agent_class": "user_delegated",
      "expected_sa_subject": "<sa_subject>",
      "sa_namespace": "agents-platform"
    }
  },
  "grants": {
    "<team>": [ {"name": "<tool>", "risk": "<risk>"} ]
  }
}
```
- Own-tool risk: from the version tool snapshot (`av.tools` — dicts already carry `risk`;
  see `policy_generator._build_risk_map`). Default missing risk to `"critical"` (fail-closed).
- Grant-tool risk: join `asset_grants` → the tools registry table to resolve each granted
  tool's risk. **Confirm the tools table risk column name** before writing the query.
- Keep it backward-tolerant: the Rego must still function if a tool entry is a bare string
  (treat as `risk="critical"`), but the generator should emit `{name,risk}`.

## 6. Bundle serving — REQUIRED CHANGE

OPA wants ONE gzipped tarball at `resource: /bundles/agentshield`. A valid OPA bundle
tarball contains `data.json` (→ `data`) and one or more `.rego` files at the archive root
(optionally a `.manifest`). Two acceptable implementations — pick the simpler:

- **(Preferred) registry-api serves the tarball.** Add `GET /api/v1/bundle/bundle.tar.gz`
  that returns a gzipped tar of `data.json` + the unified `policy.rego`
  (`Content-Type: application/gzip`). `bundle-sync`/`bundle-init` fetch that single file to
  `/data/agentshield.tar.gz`; nginx serves it at `/bundles/agentshield`; OPA polls it.
  This centralizes bundle-building in Python and removes the loose-file scheme.
- **(Alt) bundle-sync builds the tarball** with `tar czf` from the two curled files.

Whichever: nginx must serve the tarball bytes at the exact path OPA requests
(`/bundles/agentshield`), with a content type OPA accepts (`application/gzip` or
`application/octet-stream`). Update the nginx `location`, the OPA `resource` if you rename,
and the bundle-server liveness/readiness probes (currently probe `/bundles/agentshield/data.json`).

The unified `policy.rego` is a **static asset** (it's the same for all agents — decisions
come from `data.json`). It does not need to be generated per-agent. Ship it as a checked-in
file the bundle build reads. `policy_generator.py`'s per-agent `.rego` generation + the
per-agent `{name}-policy` ConfigMap path are **retired** by this change — remove or neutralize
them (the risk map it computes may still be reused to populate `data.json`).

## 7. SDK change (centralize governance)

`sdk/agentshield_sdk/graph_builder.py` line ~61:
```python
# BEFORE
needs_approval = decision.require_approval or fn.risk in ("high", "critical")
# AFTER (trust OPA)
needs_approval = decision.require_approval
```
Denial already flows from `decision.allow`. `mock_opa` (DEV_MODE) stays allow-all for local
dev. **Consequence:** any deployed agent image that must exercise real OPA needs a rebuild
with the updated SDK. Bump the affected image tags (`declarative-runner`, and the e2e test
agent image if separate) in `scripts/deploy-cpe2e.sh` per CLAUDE.md, with a comment.

## 8. Image / version bumps (fix agent owns these)

Per `CLAUDE.md`: bump the patch tag for every rebuilt image in `scripts/deploy-cpe2e.sh`
and never reuse a tag. Rebuilt by this change: `registry-api` (bundle endpoint + generator),
`declarative-runner` (SDK change), plus the OPA bundle-server manifests are raw (not tagged
images) so they redeploy via `kubectl apply`. Add a one-line header comment describing the change.

## 9. What "done" looks like (verification, run by the main thread — NOT the agents)

1. `opa test` on the unified policy passes (Rego unit tests live beside the policy).
2. After deploy: an agent pod's OPA sidecar logs show a successful bundle activation and
   **no more `Bundle load failed: ... Forbidden`**.
3. `POST localhost:8181/v1/data/agentshield` on a deployed sidecar returns the §4 decisions
   for crafted inputs (allow low, require_approval high, deny critical, deny not-granted,
   deny identity-mismatch, deny unknown-subject).
4. The OPA governance e2e suite (§ test agent) goes green.

---

## Test surface — "everything OPA is designed to offer" (test agent scopes off this)

The tests must actually reach the OPA sidecar (not `mock_opa`). Strongest approach: query a
deployed sidecar's `/v1/data/agentshield` directly with crafted inputs (deterministic, no
agent business logic needed), plus a bundle-loaded health check, plus augment the existing
governance suites. Cover at minimum:

- **Bundle health:** sidecar loads the bundle (no Forbidden); bundle server serves a valid
  `.tar.gz`; `data.json` carries risk.
- **Identity:** unknown `sa_subject` → deny `agent_unauthenticated`; mismatched
  `expected_sa_subject` → deny `identity_mismatch`.
- **Membership:** tool in own set → allowed; tool not in own set and not granted → deny
  `tool_not_granted`; tool available only via **team grant** → allowed (cross-team grant path).
- **Risk → action:** low → allow; medium → allow (logged); high → `require_approval=true`
  (HITL); critical → deny `tool_risk_denied`; unknown-risk tool → deny.
- **Class A vs Class B:** daemon vs user_delegated `agent_class` inputs both decided
  correctly (user_id/user_team empty for Class A).
- **Governance surface parity:** the SDK now honors OPA's `require_approval`/`deny` — verify
  the HITL/deny e2e paths (suites 4/5/12) still hold with the static-risk shortcut removed,
  i.e. they now pass *because of OPA*, not despite it.
- **Fail-closed:** OPA unreachable → SDK denies (`opa_unreachable`).

Register the new suite in `scripts/e2e/run-all.sh`; naming `T-S18-00X — <what it proves>`
(pick the next free suite number). Do NOT edit `scripts/deploy-cpe2e.sh` or service source —
that's the fix agent's lane.

---

## 10. Open issues observed in the field — GAP ledger (2026-07-28)

Confirmed live on the EKS test cluster during the Claude-in-Chrome lifecycle journey
(`docs/testing/claude-in-chrome-journey.md`). These are the still-open gaps this contract
targets, now with real evidence, plus one adjacent authorization bug found + fixed.

### 10.1 OPA `default_deny` still fires in real deployments — ~~STILL OPEN~~ **RESOLVED**

> **Corrected 2026-08-02.** This section is **stale**. Re-probed on the same cluster: the sidecar
> reports all bundles activated, `data.agents` holds 19 agents with per-tool risk, and
> `data.agentshield` evaluates the unified policy. `default_deny` is no longer the live behaviour;
> a bare query now returns `deny_agent_unauthenticated` — gate 1 of §4 doing its job, not an
> unloaded bundle. `cic-echo-tool` is present in the bundle at `risk: "high"`, so the specific
> tool this section reported as permanently denied would now resolve to `require_approval`. The
> §2–§6 fix shipped between 2026-07-28 and now. Evidence in the header block.
>
> The observation below was accurate when written; kept so the fix has a before-picture.

The §2 defect ("in real deployments OPA denies every tool call") was **confirmed present on
2026-07-28**. A high-risk HTTP tool (`cic-echo-tool`) bound to a deployed declarative agent, when
actually invoked (sandbox playground leg 4/12 AND production consumer leg 12b), returns:

> **Tool 'cic-echo-tool' denied by policy: default_deny**

i.e. the OPA sidecar has no bundle loaded (fail-closed) so every tool call is denied. The agent
handles it gracefully (reports the denial), so wiring/HITL/persistence assertions still passed,
but **no tool ever actually executed** in the whole journey. This is exactly the §2/§9.2
symptom: `Bundle load failed: … Forbidden`. The §2–§6 fix (serve a real `.tar.gz` bundle at
`/bundles/agentshield`, ship the unified `package agentshield` policy, carry per-tool risk in
`data.json`) is unshipped. Until it lands, `default_deny` is the live behavior for every bound
tool.

### 10.2 Production HITL decide — platform-admin 403 (adjacent, FIXED)
Not the OPA sidecar layer but the **registry-api approval-authority** layer that HITL routes to
(`routers/approvals.py::decide_approval`). A high-risk tool call in production correctly parks to
the reviewer console, but the reviewer's **Approve** returned **403 `not_authorized_to_decide`**,
so the run never resumed. Root cause:

- The production interactive branch required a **per-tool `ApprovalAuthority` grant** and never
  honored the caller's **`platform-admin` role** at all.
- `_ADMIN_ROLES` was spelled `{"platform_admin","team_lead"}` (underscore) while the real role in
  `user_team_assignments` is **`platform-admin`** (hyphen, per `rbac.ROLE_HIERARCHY`), so even the
  daemon branch's admin check never matched.
- Secondary: `_has_authority_for_tool` used `scalar_one_or_none()`, which **500s
  (`MultipleResultsFound`)** when a user holds ≥2 active grants for one tool — which the auto-grant
  pattern produces (sandbox + production deploys each grant the owner).

**Fix (shipped):** a top-level **platform-admin special case** in `decide_approval` (an admin-role
caller may decide ANY approval, any context, without a per-tool grant), corrected `_ADMIN_ROLES`
to include the hyphenated `platform-admin`, and switched the authority existence checks from
`scalar_one_or_none()` to `.limit(1)/.first()`. See `docs/bugs/production-hitl-decide-403-authority.md`.

### 10.3 Run INITIATION is not an OPA concern — stated so nobody looks for it here (2026-08-02)

This contract governs **tool calls inside a run**: given that a run is happening, may this agent
call this tool. It says nothing about **who may start a run in the first place**, and it should
not — the decision needs a Keycloak identity and a team lookup, neither of which reaches the OPA
sidecar (see §3's input shape: `sa_subject`, `tool_name`, `agent_class`, `user_id`, `user_team` —
no notion of a trigger, a schedule, or a caller asking to fire one).

Recording it because the boundary is easy to misread. The schedules workstream found that
`POST /api/v1/internal/runs/start` has **no authentication at all** — an unauthenticated POST
reaches the handler (422 on body shape) while the read endpoint beside it correctly 401s. That is
a real hole, but fixing it here would be wrong: OPA would be asked a question it has no inputs
for, and a `default_deny` on run initiation would stop the scheduler itself.

It belongs to `identity-propagation-architecture.md` — Drop point 7 and 7a, §4.2's manual-fire
row, and Phase 3/3a, which own both the service-identity fix and the authenticated, team-scoped
route a Studio "Run now" control would need. Evidence and the reason the control was NOT built:
`docs/bugs/internal-run-door-has-no-authentication.md`.

**One thing that IS this contract's business,** once those runs are properly attributed: §4's
decision logic has no gate on *who authorized a scheduled run*. A daemon fires on `sa_subject` +
granted scopes with `user_id=""` by design (§4 / identity-propagation §4.6 Gate 5). If a future
requirement says a high-risk tool call in an autonomous run needs a named human authorizer, that
is a new gate here, fed by `AgentTrigger.created_by` from identity-propagation's migration `0052`.
It is an open question in that doc (§10), not a decision this contract has taken.

### 10.4 Sandbox/playground auto-approve note
The journey confirmed §4's "future improvement" note is now real behavior on the **sandbox**
side: a high-risk tool call in the playground/sandbox parks as an **inline self-service** approval
(resumable in place), while the SAME call in **production** routes to the reviewer console
(authority-scoped, no self-approve). The OPA policy still does not branch on
`sandbox`/`playground` (§4) — this split is enforced above OPA (SDK/registry-api), consistent with
this contract.

---

## 11. What shipped BEYOND this contract (2026-08-02)

The live policy implements more than §4 describes. Documented here so §4 stops being read as the
complete rule set — `services/registry-api/opa_policy/agentshield.rego` is the code of record.

| Addition | Where | Why it matters |
|---|---|---|
| **Gate 6 — `user_identity_ok` identity floor** (WS-2) | `:22,101-108`, AND-ed into `allow` at `:116` | A **sixth gate** §4 never listed, with a fifth `deny_reason`, `missing_user_identity` (`:182`). A `user_delegated` agent with `input.user_id == ""` is denied. This is the gate that caused a silent, total tool outage before identity propagation existed — see `docs/bugs/opa-user-identity-floor-denies-tools-missing-x-user-sub.md` |
| **Risk resolution is MAX, not first-match** | `risk_rank` `:43`, `_matching_ranks` `:67-72`, `max_rank` `:82` | §4.4 says "resolve the matched tool's risk", which is ambiguous when a tool appears in both the agent's own set and a team grant at **different** risk levels. The implementation takes the **highest** — the fail-closed reading. Contract text should be read as such |
| **Decision 27 de-anonymize gate** | `allow_deanonymize` `:23,149`, `_matching_deanon` `:137-147`, per-tool `pii_deanonymize_allowed` | A second decision output beside allow/require_approval, fail-closed on a bare string or missing flag |
| **22 Rego unit tests + a CI gate** | `opa_policy/agentshield_test.rego`; `scripts/smoke-test-cp1-ws2-infra.sh` T-CP1B-003 | §9.1's "`opa test` passes" is satisfied and enforced |
| **`suite-18-opa-governance.sh` covers the full §Test-surface** | T-S18-001…012 | bundle health, tarball validity, per-tool risk in `data.json`, all four risk→action rows, both identity denials, team-grant path, daemon class. Queries the deployed sidecar directly, as prescribed |

## 12. Remaining deltas — the whole list

Small, and two of them are owned elsewhere. Nothing here needs a migration; number allocation
across the three authorization docs leaves this one with none.

| # | Delta | Owner |
|---|---|---|
| 1 | Gate 6 reads self-reported `input.agent_class`, not registry-side `agent.agent_class` — a compromised pod can relabel itself `daemon` and skip the identity floor | **identity doc §4.6 D-1**, its Phase 2 |
| 2 | `input.playground` / `input.sandbox` are carried and ignored (§4). May be correct — the sandbox/production HITL split is already enforced above OPA (§10.4) — so this is a decision, not automatically a gap | **identity doc §4.6 D-2 / §10** |
| 3 | Gate 6 gates `allow` but not `require_approval` (`:120-124`). Harmless only because the SDK returns early on `not decision.allow` (`graph_builder.py:303-308`) | **identity doc §4.6 D-3** |
| 4 | `opa_decisions` is a built table + router with **zero writers**; `Approval.opa_decision_id` (FK exists) is never populated, so no OPA decision is ever audited | **identity doc Phase 5** |
| 5 | No gate on *who authorized* an autonomous run — a daemon fires on `sa_subject` + scopes with `user_id=""` by design. If a high-risk tool call in a scheduled run should require a named human authorizer, that is a **new gate here**, fed by `AgentTrigger.created_by` | this doc, once identity `0081` lands (§10.3) |

## 13. Consolidated sources

| Source | What moved here | What stays there |
|---|---|---|
| `authorization-model-spec.md` §12 (OPA bundle lifecycle), §15 (policy structure + SDK input shape) | superseded by §1/§3/§5/§6 — the per-agent `package agentshield.agent.{name}` scheme it describes was **retired** by the unified-policy change | its §4–§7 machine-identity flows |
| `plan/execution-models-v2/ws2/contracts/opa-daemon-rule.md` | the `user_identity_ok` floor + `input.agent_class`/`user_id`/`trigger_type` inputs → §11 | the WS-2 task decomposition |
| `sandbox-production-parity-architecture.md` | the sandbox↔production governance parity requirement → §10.4 | the two deploy paths, the two-table split, the per-pod parity matrix |
| `debugging/001` (HITL not triggering), `003` (OPA bundle 5-min cold start), `008` (production OPA identity parity) | referenced as the operational record behind §2 and §10 | the investigations themselves |

**Not consolidated here, deliberately:** run-initiation auth (§10.3 → identity doc), control-plane
RBAC (→ `rbac-and-artifact-authorization.md`), and credentials handed *to* a tool
(→ `identity-propagation-architecture.md` **§4.8**).

**Where this contract sits for an MCP tool call.** An `mcp_tool` invocation passes three
independent gates; OPA is the **first**, and the only one this document owns:

1. **OPA (here)** — may this agent call this tool at all? Decided in the pod before dispatch
   (`graph_builder.py:303` deny → `:369` dispatch).
2. **MCP proxy team floor** — may this agent's team reach this server/tool? (`mcp-proxy/authz.py`,
   caller authenticated by K8s TokenReview.)
3. **The upstream MCP server** — may this end user do this thing, per the credential the proxy
   presents? (identity doc §4.8.)

A deny at gate 1 means the call never reaches the proxy. An allow at gate 1 says nothing about
gates 2 and 3 — OPA has no input describing the upstream server's own authorization.

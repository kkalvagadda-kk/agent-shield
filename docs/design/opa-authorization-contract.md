# OPA Authorization Contract (Phase 9.1 completion)

**Status:** authoritative spec for completing the unified-bundle OPA authorization layer.
**Why this exists:** the Phase 9.1 "unified bundle" migration was wired only halfway. OPA
sidecars never load a bundle (403), the served policy is the wrong package + hits a
column-name bug, and no policy exposes the decision fields `opa_client` reads. Result:
in real deployments OPA denies every tool call (fail-closed on empty result), while all
e2e tests pass *vacuously* because dev/playground/sandbox use `mock_opa` (allow-all) and
HITL is triggered by the SDK's static `fn.risk` — never touching the OPA sidecar.

This document is the single source of truth for both the fix and the tests. Do not diverge
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

## 2. The three defects to fix

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

# Future work — grant eval-runtime tools so the HITL/eval e2e suites go green under fail-closed OPA

**Status:** open — future work
**Raised:** 2026-07-19 (during the `origin/main` merge verification on `worktree-ux-preview-context-storage`)
**Related:** `docs/decisions.md` (fail-closed OPA revert), commit `fe059b6` (deploy-time tool-access auto-grant), `scripts/e2e/suite-81-deploy-tool-autograt.sh`, `docs/testing/execution-models-v2-manual-e2e.md` (post-merge verification notes)

## One-liner
Seven HITL/eval bash e2e suites are red under the (correctly) restored fail-closed OPA
governance because their **fixture agents declare no tools** — the tools are injected from
the **eval dataset at runtime** — so nothing creates the `AssetGrant` those tool calls need,
and the governed call is denied (`deny_reason 'tool_not_granted'`) before it can run or HITL-park.
This is a **test-harness / eval-runner gap, not a product regression** — real agents that
declare their tools work correctly (proven: `hitl-agent` parks on `web_search`).

## Affected suites (7)
- `scripts/e2e/suite-72-eval-v2-durable.sh` — durable eval; `get_weather`/`refund_action` never run (`tool_call=0`, `refund_action` never parks).
- `scripts/e2e/suite-45-hitl-e2e.sh` — reactive HITL cases that need a fixture tool to park.
- `scripts/e2e/suite-60-single-agent-durable-hitl.sh`
- `scripts/e2e/suite-65-production-hitl-console.sh`
- `scripts/e2e/suite-70-daemon-identity.sh` — daemon workflow member completes without parking.
- `scripts/e2e/suite-71-scheduled-e2e.sh` — scheduled workflow park + alerting precondition.
- `scripts/e2e/suite-37-workflow-hitl-opa.sh` — **separate** failure: a pre-existing test-harness
  asyncpg *"got Future attached to a different loop"* error, unrelated to grants; fix independently.

## Root cause (traced)
1. Fail-closed OPA (the fail-open bypass was reverted, as intended) denies **any** ungranted
   tool call — `resolved_risk`/authorization come from `data.grants[team]` in the OPA bundle,
   and an ungranted tool yields `deny_reason 'tool_not_granted'`, `allow=false`.
2. Tool grants (`AssetGrant`, `asset_type='tool'`) are created via the catalog **publish** flow
   or the new **deploy-time auto-grant** (`_auto_grant_tool_access`, `deployments.py` +
   `catalog.py`, commit `fe059b6`) — the latter grants an agent's **declared** tools
   (`AgentTool` bindings ∪ `version.tools`) to its team on deploy.
3. The eval/HITL **fixtures declare no tools**: `version.tools == []` and 0 `AgentTool`
   bindings. Their tools (`get_weather`, `refund_action`, …) are supplied by the **eval
   dataset at runtime**, i.e. after deploy. So the deploy-time auto-grant has nothing to grant,
   and the runtime tools stay ungranted → denied.
4. These suites only ever passed under the fail-open bypass, which let ungranted tools through.

## Options to green them (pick one when this is picked up)
- **A — eval-runner / test-harness grants the dataset's tools (recommended, smallest blast radius).**
  When an eval run (or a suite fixture) materializes runtime tools for a fixture agent, create the
  matching `AssetGrant(asset_type='tool')` for the fixture's team (reuse `_auto_grant_tool_access`
  or the admin grants API) before the run executes, and regenerate/serve the OPA bundle. Keeps
  fail-closed governance intact; high-risk tools still HITL-park.
- **B — OPA policy implicitly authorizes an agent's OWN declared tools for its own team.**
  The bundle already carries each agent's tool set (`data.agents[sa].tools`); the rego could allow
  a tool that is in the agent's own set for the agent's own team without a separate `AssetGrant`
  (the deploy gate already treats own-team tools as implicitly exempt, `deployments.py`). This is
  a cleaner, more general design but a larger change (bundle_generator + rego) and a governance
  decision — it would make the deploy-time auto-grant redundant for own-team tools. Does **not**
  help the eval fixtures on its own, because those tools aren't in the agent's declared set either
  (they're runtime-injected), so A is still needed for the eval path.
- **C — accept as a documented boundary.** Leave the suites red (as today), same boundary the bash
  suites already accept for agent-execution that can't complete on the dev cluster. Cheapest, but
  the suites stay non-green and can mask a real regression in those paths.

## Acceptance when done
Re-run the 7 suites (`scripts/e2e/run-all.sh` or individually) on a deployed cluster and confirm
the tool executes / HITL-parks (`tool_call > 0`, a real `awaiting_approval` + `approval_id`),
without weakening fail-closed governance (an unrelated, un-granted tool must still be denied).
Fix `suite-37`'s asyncpg event-loop harness bug separately.

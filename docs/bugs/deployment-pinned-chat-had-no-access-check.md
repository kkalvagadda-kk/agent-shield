# `POST /agents/{name}/deployments/{dep_id}/chat` had no access check at all

**Found:** 2026-08-07, by inspection during an authorization design review with Kalyan —
tracing whether an agent grant confers the agent's tool grants.
**Fixed:** 2026-08-07 — registry-api `0.2.266`, branch `schedule-lifecycle`.
**Gap ledger:** G-R3-2.

## Symptom

None. Nothing failed, nothing was logged, no test went red. The endpoint worked — for
everybody, which was the defect.

## Root cause

`routers/chat.py` has two entry points that start a run against an agent.

`start_chat` (`:550`, `POST /{name}/chat`) enforced access:

```python
caller_team = await _caller_team(db, user_sub)
if caller_team != agent.team:
    if not caller_team:                      raise 403   # no team assignment
    if not await _has_grant(db, agent.id, caller_team):  raise 403
```

`start_deployment_chat` (`:801`, `POST /{name}/deployments/{dep_id}/chat`) does the same
job pinned to an exact deployment. It checked: a valid JWT, the agent exists and is
active, the deployment exists and is running, and session ownership. It **resolved
`caller_team` and then never used it for a decision** — no comparison to `agent.team`,
no `_has_grant`. The variable was computed only to stamp the run.

So the authorization lived on one of two doors to the same capability.

**And Studio routes to the unguarded one.** `App.tsx:84` maps
`/agents/:name/d/:depId/chat`, which is what a fleet row links to. This was not a
theoretical path — it was the path the product used.

### Reproduced on the cluster, before the fix

A `consumer` in team `operations`; agent `trigger-demo-b` owned by team `platform`; no
grant between them. Same caller, same agent, same moment:

```
/agents/trigger-demo-b/chat                      -> 403  "Team 'operations' does not have access"
/agents/trigger-demo-b/deployments/{id}/chat     -> 200  run started
```

The first probe was inconclusive and is worth recording: the persona was in team
`platform`, the same team as the agent, so `start_chat`'s own-team fast path would have
allowed it too. A 200 there proved nothing. The gap entry said "suspected, not proven"
until a persona was moved to `operations` and the token re-minted.

### The class

Two parallel implementations of one decision; one gets the fix, the other does not. This
repo has two prior postmortems for exactly this shape — `webhook_clients.py` /
`agent_endpoints.py`, and `approvals._ADMIN_ROLES` diverging from `rbac`. Here the two
copies were 250 lines apart in the same file, which is why nobody noticed one was missing.

## Fix

`_require_agent_access(db, agent, user_sub) -> caller_team`, defined next to `_has_grant`,
called by **both** handlers.

Extraction rather than adding the missing check to the second handler. Copying it would
have restored correctness and reset the clock on the same divergence — the next change to
the rule would again have to find both sites. One function, two callers.

**Semantics are unchanged**: own-team, else an active `AssetGrant`. Whether `asset_grants`
— documented as *visibility* in §2 of the RBAC design, and used here as *authority* — is
the right source for that decision is a separate open question (**G-R3-3**, owned by R5).
This fix does not decide it; it makes both paths agree on whatever the answer turns out
to be.

## Regression test

`scripts/e2e/suite-98-rbac-role-enforcement.sh`:

| Case | Asserts |
|---|---|
| `T-S98-017` | a **cross-team** caller gets 403 from the deployment-pinned endpoint |
| `T-S98-018` | an **entitled** caller still gets 200 from it |

Both are needed. 017 alone is satisfied by a change that 403s everybody, which would break
every fleet-row chat in the product. 018 is the over-reach guard.

`T-S98-017` deliberately moves its persona to a team the target agent does not own, and
re-mints the token afterwards. Without that it would run same-team and pass through the
own-team fast path — green while asserting nothing, which is how the first probe misled.

**RED-first (DoD rule 7), properly this time:** run against the deployed `0.2.265` before
the fix shipped — `T-S98-017 FAIL (200)`, `T-S98-018 PASS`. Suite total 18/1. The earlier
R3 work had to substitute captured evidence for a red run; here the unfixed image was still
live, so the real red run was free.

## Files changed

`services/registry-api/routers/chat.py` (helper extracted; both handlers call it),
`scripts/e2e/suite-98-rbac-role-enforcement.sh` (T-S98-017/018),
`docs/design/rbac-and-artifact-authorization.md` (G-R3-2),
`docs/testing/manual-ui-e2e-test-plan.md`.
Tag bumped in `deploy-cpe2e.sh`, `deploy-eks.sh`, `values.yaml`.

## Lessons

1. **A resolved-but-unused variable is a signal.** `caller_team` was computed on line 833
   and never read for a decision. That is what an access check looks like after somebody
   removes it — or after somebody adds the parameter and forgets the body.
2. **Two entry points to one capability need one function, not two correct copies.** The
   third instance of this class in this repo. The fix is always extraction.
3. **Prove the bypass with a caller who could not have passed anyway.** The first
   reproduction was same-team and would have been allowed by the legitimate path. A 200 is
   only evidence when the control being tested is the one that would have said no.
4. **A design review found it, not a test.** Nothing was red. The suites all passed. It
   surfaced from asking "does a grant on an agent confer its tools" and reading the two
   handlers side by side.

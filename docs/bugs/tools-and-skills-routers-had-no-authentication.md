# `routers/tools.py` and `routers/skills.py` had no authentication on any route

**Found:** 2026-08-07, by a grep-derived audit of the unauthenticated-router list while
scoping Decision 46 (a tool's owner team). Not by a failing test.
**Fixed:** 2026-08-07 — registry-api `0.2.267`, branch `schedule-lifecycle`.
**Gap ledger:** G-R3-6.

## Symptom

None, again. Every suite was green. The endpoints worked — for anyone who could reach the
Service, with no credential at all, including `POST`, `PUT` and `DELETE`.

## Root cause

R1 closed 10 unauthenticated routers. `tools.py` and `skills.py` were **not on that list**,
because the list was written by hand from the RBAC design doc's §1.4 table rather than
derived from the tree. Both routers took `Depends(get_db)` and, at most,
`Depends(get_optional_user)` — which returns `None` for an anonymous caller and was used
only to stamp `created_by`.

Twelve routes, zero of them authenticated:

| Router | Routes | Included |
|---|---|---|
| `tools.py` | 7 | `POST /`, `PUT /{id}`, `DELETE /{id}`, `POST /{id}/test` |
| `skills.py` | 5 | `POST /`, `PUT /{id}`, `DELETE /{id}` |

### Why the mutations are the serious half

`Tool.risk_level` is not descriptive metadata. It is an input to two live controls:

- the HITL gate — a `high` tool routes its calls to an approval queue;
- OPA's risk→action rule in `agentshield.rego`.

So an unauthenticated `PUT /api/v1/tools/{id}` that lowers `risk_level` from `high` to
`low` relaxes governance for **every agent bound to that tool**, in every team, with no
audit trail naming anybody. `POST /{id}/test` is worse in a different direction: it invokes
the tool for real, with the platform's stored credentials, on behalf of nobody.

### The second defect the same handler carried

`create_tool` built the row with `Tool(**body.model_dump(exclude={"side_effecting"}))`.
`owner_team` came straight from the request body, and `ToolCreate.owner_team` defaults to
`None`. Studio never sends it.

`tool_access.team_may_use_tool` reads a null owner as *usable by every team*:

```python
if owner_team is None or owner_team == team:
    return True
```

So the normal Studio create path produced the most permissive state the model allows —
**65 of ~173 tool rows on the test cluster have `owner_team IS NULL`**. That is not drift
that crept in; it is what the create path was built to produce.

### The class

**A hand-written list of things to fix.** R1's router sweep, R2's blast-radius sweep, and
this are three instances in three phases. Every time the list came from a document or from
memory, it was short. The R2 sweep verified 17 suites by hand while a later grep found 41
anonymous create sites across ~28 suites. The fix for the class is not "be more careful" —
it is `scripts/check-e2e-auth-hygiene.sh`, which derives the list from the tree and fails
the change.

## Fix

**Mutations gated, reads deliberately left open.**

`POST`, `PUT`, `DELETE` and `POST /{id}/test` on both routers now carry
`dependencies=[Depends(require_user)]`.

The **reads stay exempt, and that is a decision, not an oversight**: two in-cluster machine
callers reach them with no `Authorization` header —

- `services/declarative-runner/workflow_executor.py:247` — `GET /tools/{id}`
- `services/declarative-runner/workflow_executor.py:232` — `GET /skills/{id}`
- the SDK `tool_resolver` — `GET /tools/` once at pod startup

Gating those would break every agent pod on boot. They need a **verifiable service
identity**, which `docs/design/identity-propagation-architecture.md` Phase 3 owns. Until
then the exemption is written into the handler's own comment naming the caller and the
file:line, so the next person to look does not have to rediscover why.

**`owner_team` is now derived, never supplied** (Decision 46):

```python
caller_team = await get_user_team(db, claims["sub"])
if body.owner_team and body.owner_team != caller_team:
    if await get_user_global_role(db, caller) != "platform-admin":
        raise HTTPException(403, ...)      # forgeable attribution
    owner_team = body.owner_team           # admin/seed path only
else:
    owner_team = caller_team
tool = Tool(**body.model_dump(exclude={"side_effecting", "owner_team"}))
tool.owner_team = owner_team
```

`owner_team` is excluded from the kwargs splat so the body value can never reach the row by
a path the guard does not cover. A platform-admin may still assign ownership elsewhere,
because seeding and admin-side creation legitimately do; for anyone else, honouring a body
field is the same shape as the `X-User-Sub` fallback R2 deleted from `create_agent`.

**No backfill of the 65 null rows here.** They stay usable by every team until Decision 47
step B flips the `publish_status` default — deliberately, because a backfill would strand
agents whose tool bindings cross teams today. Recorded in the gap ledger, not silently
skipped.

## Regression tests

`scripts/e2e/suite-98-rbac-role-enforcement.sh` (suite total 19 → **23**, all green against
`0.2.267`):

| Case | Asserts |
|---|---|
| `T-S98-019` | anonymous `POST /tools/` → **401** |
| `T-S98-020` | a non-admin asking for `owner_team: "operations"` → **403** |
| `T-S98-021` | a contributor's tool lands `owner_team=platform` — **derived**, not supplied (201) |
| `T-S98-022` | anonymous `GET /tools/` still **200** — the machine-caller exemption is pinned |

019 alone is satisfied by a change that 401s everything; 021 and 022 are the over-reach
guards. 022 in particular exists so that a future well-meaning "close the last exempt
routes" commit turns a test red instead of turning every agent pod into a CrashLoop.

**Gate extended in the same change.** `scripts/check-e2e-auth-hygiene.sh` now also matches
`/api/v1/tools` and `/api/v1/skills`. It immediately found **7 more uncredentialed call
sites** in suites 2, 6, 16 and 18 that the change would have turned red — found before the
deploy, cluster-free, in about a second.

## Files changed

`services/registry-api/routers/tools.py`, `services/registry-api/routers/skills.py`,
`scripts/check-e2e-auth-hygiene.sh`, `scripts/e2e/suite-98-rbac-role-enforcement.sh`,
`scripts/e2e/suite-{2,6,16,18}-*.sh` (Bearer added),
`docs/decisions.md` (46), `docs/design/rbac-and-artifact-authorization.md` (G-R3-6),
`docs/testing/manual-ui-e2e-test-plan.md`.
Tag `0.2.267` bumped in `deploy-cpe2e.sh`, `deploy-eks.sh`, `values.yaml`.

## Lessons

1. **Derive the list, don't write it.** Three phases in a row shipped an incomplete sweep
   because the list came from a doc or from memory. The gate script is the fix; the
   discipline is not.
2. **An exemption must name its caller in the code.** "Reads are open" is indistinguishable
   from "we forgot" six months later. The handler comment carries the file:line of the two
   machine callers and the phase that will close it.
3. **A default of `None` on an ownership column is a permission decision.** `owner_team`
   defaulting to null and `team_may_use_tool` treating null as universal were each
   defensible alone. Together they made the ordinary create path emit the most permissive
   row available.
4. **Found by a design question, not a test.** Same as `deployment-pinned-chat-had-no-access-check.md`
   two days earlier. Reading two handlers side by side while asking "who owns this" keeps
   outperforming the suites at finding missing authorization.

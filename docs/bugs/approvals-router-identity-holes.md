# The approvals router's identity holes — a guard that failed open, and two routes with no guard at all

**Found:** 2026-08-09, while fixing the `suite-70 T-S70-003` red that `8392a61` (identity P3)
carried deliberately.
**Fixed:** registry-api `0.2.281`, completed in `0.2.282` (the dataset router needed all five
routes on one identity source, not two — see "The half-conversion that 0.2.282 finished").
**Regression tests:** `T-S102-010` … `T-S102-014b` (`scripts/e2e/suite-102-verifiable-service-identity.sh`),
`T-S70-003` / `T-S70-004` (`scripts/e2e/suite-70-daemon-identity.sh`).
**Investigation log:** [`docs/debugging/017-suite-70-the-403-that-was-a-200.md`](../debugging/017-suite-70-the-403-that-was-a-200.md).

---

## Symptom

`8392a61`'s own commit message recorded the red and named a cause:

> `suite-70 T-S70-003` — a REAL hole this change opened. A non-reviewer's decide returns 200
> where 403 is expected: the guard reads `if caller and caller != "system" and not
> caller_is_admin`, and `caller` used to be unconditionally truthy via the `body.reviewer_id`
> fallback that is now gone.

**Both halves of that are wrong**, and the way they are wrong is the lesson. See the
debugging log for how each was killed. In short:

* `T-S70-003` returned 200 because the **test** could not express a non-reviewer, not because
  the guard skipped. Its driver's client carried `auth=BearerAuth()` — a module-level cached
  **platform-admin** token — and expressed "non-reviewer" as a per-request `X-User-Sub`
  header. `httpx` merges per-request headers over client headers but **re-applies `auth=` on
  every request**, so the decide went out as an admin, `caller_is_admin` was true, and the
  403 branch was correctly skipped. The 200 was the right answer to the question actually
  asked.
* The `if caller and …` skip was **not reachable** the way the message claimed. Measured on
  EKS against `0.2.280`:

  ```
  scheduler: azp='scheduler' sub='3449efa5-fe95-4056-9d54-cec534c60a7f'
             kind='service' caller_sub='3449efa5-…' is_authenticated=True truthy_sub=True
  ```

  Keycloak `client_credentials` tokens carry a `sub` (the service-account user), so `caller`
  was never empty in practice. The empty-`sub` path was defence-in-depth, not a live bypass.

The real defect was underneath both, and it was worse.

## Root cause

### 1. Authorization by accident — a role table asked to answer a question about *kind*

`decide_approval` collapsed a three-state `Caller` into a string and then re-derived intent
from that string:

```python
caller = identity.sub                                    # user | service | (empty)
caller_is_admin = bool(caller and caller != "system" and (await _caller_roles(caller, db)) & _ADMIN_ROLES)
if caller and caller != "system" and not caller_is_admin:
    ... 403
```

`identity.is_authenticated` is `kind != "anonymous"`, so a **verified trusted-service token
passed the 401 gate** and arrived at an authority check judged by:

```sql
SELECT role FROM user_team_assignments WHERE user_sub = :sub
```

That query cannot tell a human from a service account. The scheduler was refused **only
because nothing had granted its subject a role** — measured: 0 rows for that sub, 21 in the
table. Grant it one and the denial evaporates. `T-S102-010` inserts a `platform-admin` row
for the scheduler's service-account sub and the pre-fix decide answered:

```
FAIL T-S102-010 a verified SERVICE token cannot decide an approval even as platform-admin
     — got 200 {"id":"bb45443f-109c-40f2-9eb1-5c52c13c71dd", ...}
```

A service account approved a real pending HITL request. HITL is the control that stops an
agent taking a dangerous action; nothing in the code said the actor had to be a person, so
whether it was one depended on which rows happened to exist. Bootstrap and seed scripts write
that table.

`kind` was added by P3 precisely so readers would not infer identity from which field happens
to be truthy (`auth_middleware.py:182-184`). This function ignored it.

### 2. `caller and` — an authorization guard that fails OPEN

Independently of reachability, the shape is wrong: a guard whose subject can be falsy is a
guard that can be skipped. `caller` was truthy only by accident — it used to fall back to
`body.reviewer_id`, **a value the caller types**. P3 removed that fallback and left the two
branches that leaned on it (`:865`, `:874`). `caller != "system"` was likewise a bypass
reachable only by typing the literal; the timeout worker never used this route (it mutates the
row in-process and POSTs the pod directly), and a verified JWT `sub` cannot be `"system"`.

### 3. Three routes with no identity parameter at all

P3 closed `list_approvals` on the rule *"no identity must never be the widest identity"* and
`decide_approval` on *"the first question is who are you, never what are you asking about"* —
then left, in the same file and adjacent files:

| route | what it did with no credential |
|---|---|
| `GET /approvals/{id}` | returned the full record plus `principal_display`, `requested_by`, `requested_by_team`, `deployment_name`, `environment`; 404-vs-200 also answered whether an ID exists |
| `POST /approvals/{id}/reopen` | reset a `rejected`/`timed_out` approval to `pending`, nulled `reviewer_id` and `reviewer_notes`, granted a fresh expiry — **reject → reopen → decide was a complete route around HITL, and it erased who rejected** |
| `PATCH`/`DELETE /playground/datasets/{id}` | `if require_owner and caller and ds.owner_user_id != caller` under `get_optional_user` → `caller=None` skipped the *only* ownership check. Pre-existing, not a P3 regression. `X-User-Sub` also let a caller simply **type** the owner's sub |
| `POST /playground/approvals/{id}/decide` | decided a HITL approval; `x_user_sub` was read only as an audit label, so the record of who approved a gate was whatever the caller typed |

`reopen_approval` is the sharpest one: it is a *decision about a decision*, and it enforced
nothing, so the expensive door (`decide`) was guarded while the cheap door next to it was not.

## Fix

**One accessor answers "is there a person", once** — `Caller.require_user_sub()`
(`auth_middleware.py`): 401 for anonymous, **403 for a service**, otherwise the verified human
`sub`. A caller that is not a person never reaches a role lookup, so the role table is no
longer asked a question it cannot answer. Verified against all four kinds:

```
anonymous:         -> 401 Authentication required.
service-empty-sub: -> 403 service_identity_cannot_act_as_user
service-with-sub:  -> 403 service_identity_cannot_act_as_user
user:              -> 'u-1'
```

That is the class-fix: it makes "authenticated but not identifiable as a person"
unrepresentable at the call site, so the next handler cannot re-invent `if caller and …`.

**The guards become unconditional.** With `caller` a non-empty human sub by construction,
`caller and` and `caller != "system"` are deleted from all three sites. A guard with nothing
to skip cannot be skipped.

**The decide authority rule now exists once** — `_require_authority_to_decide(caller, approval,
db)`, shared by `decide_approval` and `reopen_approval`. Two copies of an authorization rule is
two places to forget one.

**`get_approval`, `reopen_approval`, and both dataset write paths** gain `resolve_caller` +
`require_user_sub()`, with the 401 **before** the row lookup so existence is not disclosed by
probing IDs. `X-User-Sub` is removed from the dataset write signatures — requiring a credential
was not enough while the owner comparison could be satisfied by a typed header.

**`decide_playground_approval` gains auth on `is_authenticated`, deliberately NOT
`require_user_sub()`.** A trusted service legitimately decides there: eval-runner self-approves
a gated durable step while iterating a dataset (`services/eval-runner/main.py::_self_approve`).
Refusing services would have closed the hole and broken batch eval — which is why
`T-S102-014b` asserts a verified service still reaches the handler. The two decide routes
differ in *who may act*, so they ask different questions rather than sharing one helper with a
mode flag. `approval.reviewer_id` is now the verified subject (a service stamps its service
name — a truthful "no human decided this" instead of a fabricated reviewer).

### Deliberately removed, with a capability loss recorded

`_SERVICE_IDENTITIES = {"eval-runner"}` (`playground.py:56`, call site `:715`) set
`x-agentshield-auto-approve: true` when `run.user_id == "eval-runner"` — a **HITL bypass keyed
on a database string**. P3 made `PlaygroundRun.user_id` the verified subject, which for
eval-runner is a service-account UUID, so the branch stopped matching real eval-runner runs and
still matched legacy rows holding the literal: **dead where it was needed, live where it was
not.** `8392a61`'s message claimed this set was already deleted; it was not.

Deleting it costs reactive-eval HITL auto-approve. The durable path is unaffected
(`_self_approve`). Restoring it correctly needs the run row to **remember** that a trusted
service created it, because the stream and `resume-stream` are separate requests whose caller
may be a different principal — so `Caller.kind` is not available where the decision is made.
That is a `PlaygroundRun` column set from `identity.service_name` at creation plus a migration,
and it is in the gap ledger rather than smuggled in here. `eval_mode` cannot stand in: it is
`live`/`record` on **every** playground run, interactive ones included.

## The half-conversion that 0.2.282 finished

`0.2.281` moved the dataset **write** paths to the credential and left `create`, `list` and
`get` on `(user or {}).get("sub") or x_user_sub or "dev"`. That was worse than either choice
alone: the OWNER recorded at create and the caller compared at write were drawn from **different
sources**, so a legitimate owner got 403 on their own dataset. `suite-9` showed it immediately —
`DELETE` moved `401 → 403`, and the rows explain why:

```
recent dataset owners: [('s9-fail-ds','dev'), ('s9-real-ds','dev'),
                        ('smoke-dataset-suite9','e2e-suite9-user'), ...]
```

Owners were the literal string `"dev"` and a typed header value. All five routes now take
`resolve_caller` + `require_user_sub()`. **Ownership only means something if both sides name the
same kind of thing** — that is the actual rule, and half a router obeying it is a guarantee of
403s rather than a partial improvement.

One asserted behaviour changed as a result, and it is encoded rather than hidden: `suite-89
T-S89-006` accepted `200 []` for an anonymous dataset list and now requires **401**. An empty
`200` is indistinguishable from "you own nothing", so it told an anonymous caller the endpoint
was theirs to call, and it silently widens if the filter is ever dropped again — which is the
failure that case was written for. The assertion was inverted, not deleted.

## A finding this fix did NOT change — deploy-time auto-grant vs routed reviewer scope

`T-S70-003` still read 200 after the identity fix, and the reason is not an identity bug.
`_caller_can_review` allows an explicit per-tool `ApprovalAuthority` grant as a third arm, and
deploying an agent with risky tools **auto-grants that tool to every member of the team**
(`routers/deployments.py:92-118`). Measured: `refund_action` carries 27 active grants and every
`contributor` holds one. So the suite's own deploy handed its "non-reviewer" authority over the
tool under test, and the decide was correctly allowed.

The suite now **revokes that grant before deciding and asserts the revoke happened**, so the
case tests the routed-scope rule. Whether the per-tool arm should apply to a daemon,
reviewer-routed approval at all is a design decision that would tighten a live authorization
path — recorded as **G-ID-0** for Kalyan, not changed here.

## Why the class of problem is closed, not the instance

* The question "is this a person" has exactly one implementation, and it lives on the type that
  knows the answer.
* No authorization guard in this router is conditional on the truthiness of its own subject.
* The decide authority rule has one definition, reached by both routes that can decide.
* Every route that reads or mutates an approval answers "who are you" before it answers "what
  are you asking about".
* A test exists for each — including `T-S102-014b` and `T-S102-009`, which fail if the fix
  degenerates into "refuse everything".

## Still open (gap ledger)

* `POST /api/v1/approvals/` needs no credential. Load-bearing: the SDK's `governed_tool` POSTs
  it from agent pods that hold no platform identity. Closing it is an agent-identity change.
* Reactive-eval auto-approve, above.
* `catalog.py:61` (`if x_user_team:` with `Header(default="")` — omit the header, lose the
  cross-team grant filter), `tool_access.py:46` (NULL `owner_team` usable by every team, fed by
  `tools.py:157` / `mcp_servers.py:123`), `playground_approvals.py:44` (accepts `x_user_sub`,
  never uses it), and the surviving literal-identity fallbacks (`datasets.py:108`,
  `eval_runner.py:306`, `playground.py:1894`, `composite_workflows.py:222, 992, 1072, 1271`).
* Not a bug, so nobody re-reports it: the eight `can_manage_artifact` 403s in `triggers.py` and
  `composite_workflows.py` are gated on `ENFORCE_TRIGGER_MGMT`, which is `False`
  (`rbac.py:237`). They log-and-permit today, knowingly carried by R4.

## Lessons

1. **A red suite's cause must be read from the request it actually sends, not from the diff
   that turned it red.** Both of `8392a61`'s claims here were plausible readings of the diff
   and neither survived one look at the wire.
2. **`is_authenticated` is not `is_a_person`.** Any check that then consults a per-user table
   needs the second question answered first, or the table decides it by accident.
3. **A guard conditional on its own subject fails open.** `if caller and …` is never right in
   an authorization path.
4. **When you delete a fallback, grep the branches that leaned on it.** P3 removed
   `body.reviewer_id` and left two.
5. **Guard the cheap door too.** `decide` was hardened while `reopen` — which makes a decided
   approval decidable again — had no identity at all.

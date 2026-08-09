# 017 — suite-70's 403 that was a 200: the symptom named the wrong layer twice

**Date:** 2026-08-09 · **Cluster:** EKS `test-cluster-964-10086`, ns `agentshield-platform`,
registry-api `0.2.280` → `0.2.281`
**Bug doc:** [`docs/bugs/approvals-router-identity-holes.md`](../bugs/approvals-router-identity-holes.md)

`8392a61` (identity P3) shipped with `T-S70-003` red and a written diagnosis. The diagnosis
named a product guard. The cause was in the test. Then the guard turned out to be broken
anyway — for a different reason than the one written down. Two wrong attributions in one
handoff, both arrived at by reading the diff instead of the wire.

## The expected chain

`T-S70-003` claims: *on a REAL parked daemon approval, a NON-reviewer decide is rejected 403.*

```
driver (in-pod)  --PATCH /api/v1/approvals/{id}-->  decide_approval
                                                     ├── identity = resolve_caller(credential)
                                                     ├── caller_is_admin = roles(caller) ∩ ADMIN
                                                     ├── reviewer_scope = _derive_reviewer_audit(...)
                                                     └── if caller and caller != "system" and not caller_is_admin:
                                                             _caller_can_review(...) or 403
```

The carried note said `caller` had become falsy, so the guard skipped and returned 200.

## Step 1 — read the case, not the guard

`scripts/e2e/suite-70-daemon-identity.sh`:

```python
HDR = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}      # :175, ADMIN = live platform-admin sub
c = httpx.AsyncClient(base_url=BASE, headers=HDR, timeout=90.0, auth=BearerAuth())   # :260
...
nr = await c.patch(f"/approvals/{approval_id}",
                   json={..., "reviewer_id": NONREV_SUB},
                   headers={"X-User-Sub": NONREV_SUB})      # :345-347
```

**Evidence:** `NONREV_SUB = str(uuid.uuid4())` (`:181`) — a sub no Keycloak user owns. And
`BearerAuth` (`scripts/e2e/lib/e2e_auth.py`) is a module-level cache keyed to
`KC_USER = "platform-admin"`, with **no per-persona argument**.

`httpx` merges per-request `headers` over client headers but **re-applies `auth=` on every
request** — that is the whole reason `BearerAuth` exists (a static header dies at the 300s
token life). So the PATCH carried an admin JWT. Identity P3 made `resolve_caller` read the
credential and nothing else, so `caller` was the admin's sub, `caller_is_admin` was true, and
the 403 branch was **correctly** skipped.

**Conclusion:** the 200 was the right answer. The suite could not express a non-admin caller at
all. Same class as suite-5 and the inverted `T-S93-004` — not a product regression.

**Corollary found in the same read:** `T-S70-004` ("a REVIEWER decide resumes the run") granted
`REVIEWER_SUB` the role by direct SQL and then decided with the *admin* token, so it passed via
`caller_is_admin` and had **never** exercised the reviewer-scope path it claims to prove. Green
for the wrong reason is worse than red.

## Step 2 — is the guard broken anyway? Measure, don't reason

The note's mechanism required `identity.sub` to be empty while `is_authenticated` is true. That
is structurally possible — `caller_from_claims` returns the **service** arm *before* the
non-empty-`sub` check (`auth_middleware.py:219-225`) — so the question is whether Keycloak ever
issues a service token without a `sub`.

```bash
KUBECONFIG=~/.kube/test-cluster-kube-config.yaml kubectl exec -i -n agentshield-platform "$POD" \
  -c registry-api -- env S0_SECRET="$SCHED_SECRET" python3 - <<'PY'
# client_credentials mint + _decode_token + caller_from_claims, printing sub/azp/kind
PY
```

```
scheduler: azp='scheduler' sub='3449efa5-fe95-4056-9d54-cec534c60a7f' typ='Bearer'
           kind='service' caller_sub='3449efa5-…' is_authenticated=True
           truthy_sub=True trusted='scheduler'
```

**Evidence kills the second hypothesis too:** service tokens carry a `sub`, so `caller` is
never empty and the `if caller and …` skip is **not reachable in this deployment**. It is
defence-in-depth, not a live bypass. Anyone writing "closed a live bypass" here would be
wrong.

## Step 3 — so why *is* a service token refused today? Ask the DB, not the code

```sql
SELECT * FROM user_team_assignments WHERE user_sub = '3449efa5-…';   -- []
SELECT count(*) FROM user_team_assignments;                          -- 21
SELECT role, count(*) FROM user_team_assignments GROUP BY role;       -- contributor 12, consumer 6, platform-admin 3
```

`_caller_roles` (`approvals.py:289`) is a bare `SELECT role FROM user_team_assignments WHERE
user_sub = :sub`. It cannot distinguish a human from a service account. The scheduler is
refused **only because nothing granted its subject a role.**

**This is the real defect.** The denial is incidental, not structural. So the reproducing test
had to be reshaped: a plain "scheduler token decides → 403" assertion passes on `0.2.280` and
proves nothing.

## Step 4 — build the case that actually fails

`T-S102-010`: insert a `platform-admin` row for the scheduler's service-account sub, create a
real pending approval, decide with a verified scheduler token, delete both rows in a `finally`.

Two test bugs on the way, both mine, both worth recording because they masqueraded as findings:

| observed | cause |
|---|---|
| `SETUP: could not create approval (500)` | `Approval.agent_id` is `ForeignKey("agents.id")` (`models.py:771`); a random UUID is an FK violation, not a fixture. Borrow a real agent id. |
| `JSONDecodeError: line 1 column 301` | the suite's `call()` truncates bodies to 300 chars so every case can print one safely; an `ApprovalResponse` is longer. Read `id`/`version` back from the DB by the `thread_id` the case chose. |
| `T-S102-012` got `422 {"loc":["body"]}` | `reopen_approval` takes a required `ReopenRequest`; an empty request answers 422 before any identity question, which would have let the case "pass" for a reason unrelated to auth. Send a valid body. |

Then, against unmodified `0.2.280`:

```
FAIL T-S102-010 a verified SERVICE token cannot decide an approval even as platform-admin — got 200 {"id":"bb45443f-…"}
FAIL T-S102-011 read one approval with NO credential is 401 (not 404) — got 404 {"detail":"Approval '90e3fe07-…' not found."}
FAIL T-S102-012 reopen an approval with NO credential is 401 (not 404) — got 404
FAIL T-S102-013 update a dataset with NO credential is 401 (not 404/200) — got 404 {"detail":"Dataset not found"}
FAIL T-S102-014a playground decide with NO credential is 401 — got 404 {"detail":"Approval not found"}
PASS T-S102-014b a verified service token still reaches the playground decide handler — got 404
```

A `404` on a random UUID is the tell: **the row lookup ran before any auth decision**, i.e.
there was no auth decision.

## Root cause

Three distinct things, only the first of which the handoff named — and it named it wrongly:

1. **Test fixture** — `T-S70-003`/`004` expressed identity with a header while the client
   carried `auth=`. `BearerAuth`'s module-level cache made a second persona impossible to
   express, so the suite silently tested the admin path twice.
2. **Authorization by accident** — a service token reaching a per-user role table is judged by
   whichever rows exist. `is_authenticated` answers "is there a credential"; the code needed
   "is there a person".
3. **Missing guards** — `get_approval` and `reopen_approval` had no identity parameter, and
   `reopen` (reject → `pending`, `reviewer_id` nulled) is the cheap way to the outcome `decide`
   guards.

## Fix

`Caller.require_user_sub()` — 401 anonymous, 403 service, else the verified human sub — called
by the four handlers that need a person. `caller and` and `caller != "system"` deleted. The
decide authority rule extracted to `_require_authority_to_decide`, shared with `reopen`. Dataset
write paths take the credential only. `decide_playground_approval` gains auth on
`is_authenticated` (a service legitimately decides there). `_SERVICE_IDENTITIES` deleted.
Details and the capability-loss note in the bug doc.

`BearerAuth` gained per-instance credentials with a per-instance cache, so one driver can hold
an admin client and two persona clients and all three self-refresh past 300s.

## Playbook entry earned

**Symptom:** a test asserting 403 gets 200 (or asserting 401 gets 404), right after an auth
change.
**First command — before reading the handler:** dump what the driver actually puts on the wire.
Client-level `auth=`, client-level `headers=`, and per-call overrides interact; `httpx` re-runs
`auth=` per request and it wins over a header of the same name.
**Second:** if the assertion is about a *service* or a *role*, query the rows
(`user_team_assignments`, `approval_authority`) before concluding anything. A denial can be
incidental.
**Never:** attribute a red to a mechanism you have only read in a diff. Both attributions in
`8392a61`'s `KNOWN RED` block were diff-derived and both were wrong.

# `POST /api/v1/agents/` accepted anonymous callers and believed their `X-User-Sub`

**Found:** 2026-08-06, by inspection while wiring RBAC R2 (the blast-radius pass over
`can_create_agent`'s intended call site).
**Fixed:** 2026-08-06 — registry-api `0.2.263`, branch `schedule-lifecycle`.
**Class:** the same one as `docs/bugs/studio-blank-page-unauthed-fetch-teams-summary.md` —
an identity taken from a place the caller controls.

## Symptom

No user-visible symptom. That is the point: this was never reported, because nothing
about it fails. An unauthenticated request creating an agent gets a clean `201`.

```
POST /api/v1/agents/            # no Authorization header
X-User-Sub: <any sub you like>
{"name":"anything","team":"platform"}
    -> 201 Created
```

## Root cause

`routers/agents.py::create_agent` resolved its caller like this:

```python
user: dict | None = Depends(get_optional_user),
x_user_sub: Optional[str] = Header(default=None, alias="X-User-Sub"),
...
caller = (user or {}).get("sub") or x_user_sub or "system"
```

Three fallbacks, and only the first is an authentication. `get_optional_user` returns
`None` rather than raising, so no token is required at all. `X-User-Sub` is a plain
request header — an audit stamp that in-cluster services set for provenance, never a
credential. `"system"` is a literal.

`caller` is not decorative. It becomes:

- `Agent.created_by` — the permanent attribution on the row, and
- the grantee of `grant_creator_admin(db, "agent", agent.id, caller)`, which inserts an
  **`agent-admin` grant** on the new artifact (`rbac.py`).

So an unauthenticated caller could create an agent, attribute it to anyone, and hand
`agent-admin` on it to a subject of their choosing — including themselves. `agent-admin`
is the artifact-scoped role that (per the model, §2 of the RBAC design) permits
production deploy, rollback, runtime-config edits, and further delegation. R1 did not
close this: `agents.py` was not among the ten routers §1.4 enumerated.

### The part worth keeping

**The gap ledger recorded this hole as smaller than it was.** The endpoint matrix in
`rbac-and-artifact-authorization.md` §3 marked `POST /agents/` as `❌` — "no guard" —
with the note *"`agents.py` — `get_optional_user`, audit only"*. `❌` in that table means
*authenticated but no role check*; `🔓` means *no authentication at all*. This row was
`🔓` and had been since the table was written.

The distinction is not pedantic. `❌` reads as "a logged-in user can do something they
shouldn't", which is an authorization gap you schedule. `🔓` reads as "anyone on the
network can", which is one you stop for. Reading the row instead of the code would have
kept this in the R3 queue behind lower-severity work.

## Fix

`create_agent` now takes `Depends(require_user)` and gates on `can_create_agent`
(contributor+) — its first call site since it was written:

```python
caller = claims["sub"]
if not await can_create_agent(db, caller):
    raise HTTPException(403, ...)
```

Why this is the class fix and not the instance fix:

- **The `X-User-Sub` fallback is deleted, not outranked.** Leaving it as a secondary
  source keeps a client-settable header feeding an identity field; it would simply stop
  being reachable through this one door while remaining the pattern. A header the caller
  controls is not an identity, and the only correct number of fallbacks from a verified
  `sub` is zero.
- **`caller` now has one producer** — the validated JWT. `created_by` and the auto-grant
  read the same value from the same place, so they cannot disagree.
- Checked before removing it: no in-cluster machine caller creates agents. `eval-runner`
  sends `X-User-Sub` to `/playground/eval/score` and `PATCH /playground/eval-runs/{id}`
  (`main.py:129,1667`), never here; the only other producer of this request is
  `sdk/agentshield_sdk/cli.py`, a human-run CLI.

## Regression test

`scripts/e2e/suite-98-rbac-role-enforcement.sh`:

| Case | Asserts |
|---|---|
| `T-S98-004` | a **consumer** is refused agent creation — `POST /agents/` → 403 |
| `T-S98-010` | a **contributor** may still create one — `POST /agents/` → 201 |

Both halves matter. 004 alone is satisfied by a change that denies everybody, which
would break the role whose entire purpose is creating things.

**Honest note on RED-first (DoD rule 7):** `T-S98-004` was written *before* R2 and ran
RED against `0.2.261`/`0.2.262`, so the reproduce-first gate is met for the missing
authorization. It is **not** met for the anonymous path specifically: `suite-98`'s helper
always sends a token, so no case ever exercised "no `Authorization` header at all" on
this route. `T-S98-005` covers anonymous only for `/admin/users`. Recorded as a gap
rather than claimed — the anonymous-create case is worth adding to `suite-97`'s route
walk (`T-S97-011`), which already partitions authenticated vs exempt routes and would
have flagged `POST /agents/` as exempt if it had covered `agents.py`.

## Files changed

`services/registry-api/routers/agents.py` (guard + fallback removal),
`docs/design/rbac-and-artifact-authorization.md` (§3 matrix row corrected `❌` → the real
status, with the correction noted in the row so it is not silently rewritten),
`scripts/e2e/suite-98-rbac-role-enforcement.sh` (T-S98-010).

## Lessons

1. **`get_optional_user` is not authentication.** It is a convenience for handlers that
   behave differently when a user is known. Any handler that *writes* an identity field
   needs `require_user`.
2. **A header is an audit stamp, never a credential** — this repo already has the same
   lesson in `scripts/e2e/lib/e2e-auth.sh`, whose header records fifteen suites dying
   because `X-User-Sub` alone stopped being accepted. The same confusion, from the other
   direction.
3. **A gap ledger is a summary, and summaries lose the distinction that matters.** `❌`
   vs `🔓` was one character and the difference between "schedule it" and "stop". When a
   ledger row is about to gate a decision, re-read the code it describes.
4. **An orphaned policy function is worse than a missing one.** `can_create_agent` was
   written, correct, and unused. Its existence made the endpoint *look* considered.

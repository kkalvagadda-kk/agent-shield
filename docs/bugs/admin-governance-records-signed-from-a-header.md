# Five admin handlers took the reviewer's identity from a header, defaulting to `"system"`

**Found:** 2026-08-07, while adding the publish cascade (Decision 47 step C) to
`approve_publish_request` — the handler being edited had it, and a grep showed four more.
**Fixed:** 2026-08-07 — registry-api `0.2.269`, branch `schedule-lifecycle`.

## Symptom

None. Every suite green. The values written were plausible (`"smoke-admin"`, `"system"`),
which is precisely why nobody looked at them.

## Root cause

`routers/admin.py` is gated to `platform-admin` at the router level (R2), so the **caller**
is authenticated and authorized. But five handlers took the identity they *recorded* from a
request header:

```python
x_user_sub: str = Header(default="system", alias="X-User-Sub"),
```

| Handler | Field it signed |
|---|---|
| `approve_publish_request` | `PublishRequest.reviewed_by`, every `AssetGrant.granted_by`, `GrantAudit.admin_id`, `PublishedVersion.promoted_by` |
| `reject_publish_request` | `PublishRequest.reviewed_by` |
| `create_grant` | `AssetGrant.granted_by`, `GrantAudit.admin_id` |
| `revoke_grant` | `GrantAudit.admin_id` |
| `create_approval_authority` | `ApprovalAuthority.granted_by` |

So a verified admin could attribute their own approval — and every grant it creates, and
the audit row that records it — to any string they chose, including the literal `"system"`,
which is also what an omitted header produced.

These are not audit decorations. `reviewed_by` is the name a governance record carries
forever, `granted_by` is who authorized a team's access to an asset, and `GrantAudit` exists
for no purpose other than answering "who did this". A field that the actor supplies cannot
answer that question.

### Severity, stated honestly

Lower than the anonymous-write bugs that preceded it: the router already requires
platform-admin, so this is not an escalation path for an outsider. It is an
**accountability** failure among privileged callers, and a record that cannot distinguish
two admins is worth very little in the incident it was written for.

### Why it surfaced now

The cascade made approval more consequential. Approving an agent no longer just publishes
that agent — it publishes the agent's own-team tools org-wide. A record of *who decided
that* stops being bookkeeping at the point where the decision has a blast radius.

### The class

Identical in shape to `publish_agent`'s `X-User-Sub` (R3, `0.2.264`) and `create_agent`'s
(R2, `0.2.263`), both already fixed and both documented as "delete the header, don't demote
it". This is the third and fourth appearance of the same pattern in three phases. The reason
it kept reappearing is that each fix was applied to the handler in front of whoever was
looking, and `admin.py` was never the handler in front of anyone.

## Fix

All five now take `claims: dict = Depends(require_user)` and bind
`x_user_sub = claims["sub"]`. The header parameter is **deleted**, not outranked — a
secondary identity source that any client can set is not a fallback, it is the bug. Same
reasoning recorded in `create_agent` and `publish_agent`.

**All five in one change, deliberately.** Fixing only `approve_publish_request` — the one
the cascade work touched — would have left four siblings with the identical defect and reset
the clock on rediscovering it. "Does this fix the class of problem, or just this instance?"

**No caller had to change.** Studio reaches these routes only from pages already gated by
`<RequireRole minRole="platform-admin">`, and it authenticates with a bearer token; the
header was never how it identified itself. Several e2e suites do send
`X-User-Sub: smoke-admin` alongside their Bearer — those calls now record the token's
subject instead. Checked before shipping: no suite asserts on `reviewed_by`, `granted_by`,
`admin_id` or `promoted_by` from these handlers. The `granted_by` values that *are* asserted
(`suite-84`, `suite-87`, `suite-93`) come from direct DB inserts, and `suite-42`'s reads
`artifact_role_grants`, which is written by `rbac.grant_creator_admin` — a different table
and a different code path.

## Regression test

There is no dedicated case, and that is a **gap, recorded rather than glossed**: proving
this properly needs two distinct platform-admin identities so a test can show the record
names the caller and not the header. `suite-98` provisions three personas but only one is
platform-admin, and `e2e_ensure_persona` pins the others to lower roles.

What does cover it today is negative and indirect — the eight-suite sweep over every suite
that touches `/admin/grants`, `/admin/publish-requests` and `/admin/approval-authority`
(suites 5, 14, 15, 18, 42, 89, 93, 98) stayed green, proving the switch broke no caller. A
green sweep proves the change is safe; it does not prove the attribution is right.

Deferred to R5, which already has to build a second admin identity for the approval-authority
rework. Tracked in `docs/testing/manual-ui-e2e-test-plan.md`.

## Files changed

`services/registry-api/routers/admin.py` (five handlers),
`docs/testing/manual-ui-e2e-test-plan.md`.
Tag `0.2.269` in `deploy-cpe2e.sh`, `deploy-eks.sh`, `values.yaml`.

## Lessons

1. **Authenticating the caller and recording the caller are two different things.** R2 did
   the first and everyone, including me, read it as having done both. The router gate makes
   the endpoint safe; it does nothing about what the handler writes into a column.
2. **A default of `"system"` hides the hole.** With no header the record still looked
   filled-in and machine-ish. A `None` that broke the insert would have been found years ago.
3. **When you fix a pattern, grep for it in the same commit.** Three phases fixed this same
   header one handler at a time. The grep that found these five took one command and could
   have been run during R2.

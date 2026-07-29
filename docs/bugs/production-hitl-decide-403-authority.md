# Production HITL: reviewer approve → 403, run never resumes

**Found:** 2026-07-28 (Claude-in-Chrome lifecycle journey, leg 12b — production HITL reviewer-console approval).
**Status:** OPEN — root cause narrowed to two candidates (below); not yet fixed. Journey leg 12b
logged **partial** (banner / no-self-approve / console-listing / authority-enforced all PROVEN;
the approve→resume step is blocked by this 403).

## Symptom
On the consumer (marketplace) surface, a high-risk tool call correctly parks to the reviewer
console (NOT inline — the production/sandbox split works). In `/approvals`, clicking **Approve**
fires `PATCH /api/v1/approvals/{id}` → **403 Forbidden** (`detail: not_authorized_to_decide`).
The consumer run stays parked ("Awaiting approval…") and never resumes.

## What was ruled in / out (traced live on cluster)
- The pending approval: `cic-journey-agent` / `cic-echo-tool` HIGH, `context=production`,
  thread `0d6e8784-…`.
- The consumer run's `user_id` = `75c7c8b3-…` (platform-admin — the browser user).
- `75c7c8b3` **has** an active `approval_authority` grant for `cic-echo-tool`
  (`auto:deploy:04f92574…`, `revoked_at IS NULL`).
- `decide_approval` (routers/approvals.py:755) takes the **production per-tool** branch
  (`_derive_reviewer_audit → reviewer_scope=None`, so NOT the daemon branch).
- Run offline in the pod as `75c7c8b3`: `_has_authority_for_tool("75c7c8b3","cic-echo-tool") =
  **True**` → by the deployed code the decide **should succeed**.
- Yet the live browser PATCH is **403**. So the live `x_user_sub` the gateway presents on the
  PATCH is evidently **not** `75c7c8b3` (or is a sub that lacks the per-tool grant).

## Two candidate root causes (need one more datapoint to disambiguate)
1. **Gateway identity mismatch** — Envoy injects an `x_user_sub` on `PATCH /approvals` that
   differs from the sub the consumer *run* was attributed to (`75c7c8b3`). The list endpoint
   (`GET /approvals?status=pending`) returns the item, so the live caller has *visibility* for
   `cic-echo-tool` — via a per-user grant OR via admin-role broadening.
2. **Admin role ≠ decide authority** — if the live caller sees the item via a **platform_admin
   role** (which broadens `list_approvals` visibility) but has **no per-tool grant**, the decide
   path 403s: it only accepts a per-tool per-user grant OR a per-tool `approver_role` row, NOT a
   blanket admin role. Per the product owner, "platform-admin is admin and can approve" — so this
   would be a real authorization gap (admin role should confer decide authority).

**Next datapoint:** capture the exact `x_user_sub` Envoy injects on the PATCH (add temporary
debug logging to `decide_approval`, or compare against `GET /me`). If it ≠ `75c7c8b3` → cause 1.
If it = `75c7c8b3` but still 403 → the deployed decide path differs from source (investigate).

## Secondary bug (confirmed, independent)
`_has_authority_for_tool` (routers/approvals.py:244) uses `result.scalar_one_or_none()`. A user
can legitimately hold **≥2 active grants for the same tool** (e.g. auto-granted by both a sandbox
AND a production deploy). With 2+ rows, `scalar_one_or_none()` raises `MultipleResultsFound`
(→ 500, or surfaces as the decide failing). Reproduced by adding a 2nd grant for
`75c7c8b3`/`cic-echo-tool`. **Fix:** use `.limit(1)` / `.first()` (existence check), not
`scalar_one_or_none()`. Same pattern to audit in the role-based `role_q` (line 788) which also
uses `scalar_one_or_none()` and would 500 on 2+ role rows for one tool.

## Journey impact
Leg 12b assertions PROVEN: awaiting-approval banner, **no** consumer self-approve control,
console lists who/tool/risk/args, decide is authority-gated (403 not rubber-stamp). NOT proven:
reviewer approve → consumer resume (blocked by this 403). Recorded as a gap in the journey.

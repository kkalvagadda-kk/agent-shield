# Publishing twice enqueued two requests, and the button gave no lasting sign it had worked

**Found** 2026-08-02 (Claude-in-Chrome schedule-lifecycle journey, leg 8) · **Fixed** 2026-08-02, registry-api `0.2.255` / studio `0.1.179`

> **This doc was wrong when first written and has been rewritten.** The original claimed
> Publish produced *no feedback at all*. Both publish paths do toast on success —
> `AgentDetailPage` ("Publish request submitted (id: …)") and `PlaygroundPage` ("Publish
> request submitted"). That claim rested on a single journey screenshot that most likely
> missed a transient toast, and it did not survive reading the code. The corrected
> findings are below; the earlier version overstated the UI half and mis-attributed the
> cause of the duplicates.

## Symptom

Clicking **Publish Agent** in the Playground's PROMOTE panel left the button looking
un-clicked. Its two siblings in the same panel latch — "Mark Version Passed" becomes a
green **"Version Passed"** and disables — but Publish reverted to its normal blue state
and stayed clickable. The only durable evidence the request existed was the **Pending
Review** badge on the *agent* page, a different screen.

I misread it during the journey: clicked Publish, saw an unchanged panel, checked the
database, found no production deployment, and concluded the click had done nothing. It
had.

## Root cause

Two independent defects that compound.

### 1. No guard against a second submission (the one that corrupts state)

`routers/agents.py::publish_agent` ran its checks — agent exists, no critical-risk tool,
eval gate — and then constructed `PublishRequest(...)` + `db.add(pr)` **unconditionally**.
No query for an existing `pending_review` row, and no uniqueness constraint behind it.
Two agents on the live cluster carry two pending requests each.

**What the duplicates actually were.** Not human double-clicks. Reading the rows:

```
s89-1785209863-agent  req=831ff202  version=39cdd77d  by=system      03:37:46
s89-1785209863-agent  req=c92ac022  version=None      by=75c7c8b3…   03:37:49
```

Three seconds apart, **two different callers**, one pinning a version and one not — a
test suite (`s89-*`) submitting through two paths. So the mechanism was concurrent
callers, not a frustrated operator. The missing guard is the same either way, and a
reviewer facing two rows for one artifact has the same problem regardless of who filed
them: approving one leaves its twin behind, pointing at the same asset, with nothing
marking it superseded.

### 2. The button did not latch (the one that invites it)

```tsx
disabled={publishAgentMutation.isPending}                                   // Publish
disabled={markAgentPassedMutation.isPending || markAgentPassedMutation.isSuccess}  // its sibling
```

A toast is transient by design; it is a notification, not state. When the control itself
returns to its resting appearance, the panel's own record of what happened is gone the
moment the toast fades — and the natural response to "did that work?" is to click again.

## Fix

**Server — idempotent, not 409.** `publish_agent` now looks for an existing
`pending_review` request for the asset and returns it instead of adding a second. If the
caller is asking for a *different* version than the pending request pins, the existing
request is re-pointed at it and logged, rather than refused.

Idempotent rather than 409 because there is **no withdraw endpoint** and
`ck_publish_requests_status` admits only `pending_review | approved | rejected` — so a
refusal would leave the operator with no way forward and no way to correct a stale
request. One row per asset that always reflects the latest intent is also what the
reviewer needs to see. The response shape is unchanged, so a caller cannot tell the two
paths apart, which is the point: *"publish this"* succeeded either way.

**UI.** The Publish button latches to a green, disabled **"Awaiting review"**, matching
its siblings, and its title names what still has to happen (approve in Admin ▸ Publish
Queue, then Deploy Latest from Marketplace). The toast now names where the request went —
the queue is not somewhere an operator would think to look.

## Regression test

`scripts/e2e/suite-17-eval-gate.sh` **T-S17-010** — publishes the same agent twice and
asserts both calls return the **same** `publish_request_id`. Fails against the old code,
which returned two different ids.

Asserts the id rather than counting rows: the contract is "the second call is the same
request", which is what protects the reviewer, and it holds no matter how the row count
is later indexed.

## Not fixed here

**A partial unique index** on `(asset_id) WHERE status = 'pending_review'` would make the
duplicate structurally impossible rather than guarded, which is the standard this repo
holds elsewhere. It is not added because creating it requires resolving the two existing
duplicate pairs first, and they are not losslessly mergeable — within each pair one row
pins a version and the other does not, so there is no "keep the newer" rule that provably
discards nothing. Deleting rows from a live queue is the operator's call, not a
migration's. Recorded in the gap ledger.

## Lessons

1. **A toast is a notification; the control is the state.** Transient feedback cannot
   answer "did that work?" thirty seconds later, and the button is what the operator
   looks at.
2. **Check the sibling controls.** The two buttons beside this one already latched. A
   defect that is inconsistent with its immediate neighbours is usually an omission, not
   a decision.
3. **Read the rows before naming the mechanism.** "Missing feedback caused retries" was a
   plausible story that the data did not support — the timestamps and submitters said
   concurrent callers. The fix was the same; the explanation would have been wrong in the
   permanent record.

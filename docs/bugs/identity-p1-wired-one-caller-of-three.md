# Identity P1 shipped "one kwarg covers all three durable callers" — one caller passed it

**Found:** 2026-08-08, while scoping P1.5.
**Fixed:** 2026-08-08, registry-api `0.2.277` (the same change that added the durable anchor).

## Symptom

Every **production** and **scheduled** durable run reached the agent pod with no identity.
The runner fell through to its transition branch, set `user_id = ""`, and OPA's identity
floor — live and denying since WS-2 (`agentshield.rego:22,101-108`, AND-ed into `allow` at
`:116`) — denied the first `user_delegated` tool call.

Which is exactly the outage `docs/bugs/opa-user-identity-floor-denies-tools-missing-x-user-sub.md`
describes. P1 existed to end it. For the sandbox it did. For production it never started.

Nothing failed loudly, because the fail-closed design worked as intended: no token means no
identity means a denial, and a denial names a **tool**, three layers from the cause.

## Root cause

Not a coding error. A **claim that was true about the shared function and false about its
callers**, which then got recorded as the phase's design and read as its status.

`durable_dispatch.dispatch_durable_run` grew an `rct` kwarg, and both the design doc and the
function's own docstring said:

> "**one edit covers all three durable callers** (`playground.py:356` sandbox,
> `workflow_orchestrator.py:224` workflow member, `internal.py:198` production), which is
> why threading identity through this function is the whole of the durable slice."

The kwarg is genuinely the only edit *the dispatcher* needs. But a kwarg with a `None`
default changes nothing for a caller that does not pass it, and only `playground.py` did.
`grep -n "rct" routers/internal.py workflow_orchestrator.py` returned **nothing** — for
months, in a phase marked complete.

Three things made it survive:

1. **The sentence describes the mechanism, and was read as describing the outcome.** "One
   edit covers all three callers" is a statement about how much work is *needed*, not about
   how much was *done*. Once it was in the design doc it read as done.
2. **`rct: str | None = None`.** A default that means "no identity" is correct for the
   transition window and is also indistinguishable from "this caller was forgotten". There
   is no arity error, no type error, no log line.
3. **`suite-99` deliberately asserts nothing about a live run** — correctly, it is the P0
   primitive suite. `suite-100` (Decision 45) covers grants, not propagation. So no suite
   ever asked "does a production run carry identity", and the answer went unmeasured.

The same shape produced the ledgered `G-45` note about `internal.py` and `/chat/stream` not
minting a RunContext. That note was right and was filed as a *future* phase's work, when it
was in fact P1's own scope left undone.

## Fix

- `internal.py::start_internal_run` builds the `RunContext` from the ALREADY-RESOLVED
  `Principal` — never re-derived — and passes `rct` through `_dispatch_and_complete` to
  both the durable dispatch and the reactive `/chat` POST.
- `workflow_orchestrator` inherits the parent's anchor onto the child member row and mints
  the member's token from that child anchor.
- The anchor (migration `0082`) is what makes the claim checkable rather than asserted: the
  identity a run carries is now a **column**, so a suite can read it back.

**The class fix is `T-S101-009`, not the wiring.** The wiring fixes today's three callers; a
fourth added tomorrow would repeat this exactly. That case derives every registry-side
`/resume` POST **from the tree** and requires identity wiring at each — and it immediately
found a **fifth** resume door (`approval_timeout_worker.py`) that the hand-written list of
four had missed, which would otherwise have shipped unidentified in this very change.

## Two more defects the new suite found while proving this one

Recorded here rather than as separate postmortems because both were found by
`suite-101` before either could ship, and both are the same shape as the bug above — an
identity layer that degrades quietly instead of refusing.

**1. A corrupt anchor minted a plausible identity.** `RunContext.from_claims` does
`str(claims.get("user_sub") or "")`, which stringifies anything. The anchor is a **JSONB
column**, not a signed token, so `{"user_sub": {"a": 1}}` produced a perfectly valid token
whose user is the literal text `{'a': 1}` — corruption laundered into an identity OPA would
then authorize. Type validation now happens in `_anchor_is_well_formed`, at the DB boundary
where the data stops being signed. Deliberately **not** in `run_context.py`: that module
parses an already-HMAC-verified payload, and it is vendored byte-identical into two other
services.

**2. Re-hydration could have cancelled the resume instead of degrading it.** Four of the
five resume call sites sit inside a broad `except Exception: return`. An exception escaping
`rehydrate` would not have lost identity — it would have silently skipped the resume and
hung the run. Losing identity is a denial an operator can see; losing the resume is a run
that never finishes and names nothing. `rehydrate` is now **total** by contract, stated in
its docstring and asserted by `T-S101-006`, rather than five copies of a try/except at the
call sites.

## Lessons

1. **A shared kwarg is not propagation.** Threading a parameter through a helper is the
   easy half; the callers are the work. When a design says "one edit covers N call sites",
   the completion check is `grep` for the argument at all N — not for the parameter.
2. **`= None` defaults hide unwired callers by construction.** Where the default means
   "silently degraded", nothing distinguishes deliberate from forgotten. If a required
   value cannot be made required, a test must count the call sites.
3. **A phase is done when a suite can read the outcome, not when the code exists.** P1 had
   no way to observe whether a run carried identity, because identity lived only in a
   header in flight. The anchor column is what makes P1 falsifiable — and the moment it
   existed, the gap was one query away.
4. **Derive sweeps from the tree.** The hand list had four resume doors. There are five.
   This repo has already shipped a hand-written sweep that missed 28 suites.

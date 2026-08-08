# e2e suites broken by runtime ordering, four times, from scripted edits

**Found:** 2026-08-05 → 2026-08-08 (four separate passes).
**Fixed:** 2026-08-08 — `scripts/check-e2e-auth-hygiene.sh` rule 9 generalized to the class.

## Symptom

A suite aborts at setup with `line N: API_POD: unbound variable`, or every identity
assertion in it silently compares against the empty string. `bash -n` reports the file
as valid, because it is.

Four occurrences, all from **mechanical multi-file edit passes**, never from hand-writing
a suite:

| Pass | Suites | Shape |
|---|---|---|
| R3 scripted Bearer | suite-20/23/24/25/29/40 | auth block inserted **after** first use |
| R3 follow-up | suite-70 | same |
| `${E2E_SUB}` replacement | nine suites | read above the mint |
| `${E2E_SUB}` replacement | live, during the edit | `e2e_set_token "$NS" "$API_POD"` above `API_POD=` |

## Root cause

Two shapes of one class — **runtime dataflow order in a bash file**:

- **A. use before define** — `${E2E_SUB}` read above the `e2e_set_token` that sets it.
- **B. call before its arguments** — `e2e_set_token "$NS" "$API_POD"` above `API_POD=`.

The design flaw is not carelessness, it is **where the insertion point comes from**. A
scripted pass picks its anchor by *textual landmark* ("after the `source` line", "near the
top") because landmarks are greppable. Correctness depends on *dataflow* — every read
below its write at runtime. The two agree across the uniform majority of the 105 suites
and diverge exactly in the tail: suites that resolve `API_POD` late, inside a function, or
after a fixture block. A landmark rule is a remembered exemplar, not a derivation over the
actual set — the same meta-cause as the four times a grep pattern in this repo was
narrower than its target (`/api/v1/tools` → `f"{BASE}/tools/"` → relative `'/agents/'` →
`headers=VAR`).

**Why it survived four passes is a separate fact, and the more useful one.** These are
*not* silent failures: all 105 suites run `set -euo pipefail`, so both shapes abort naming
the variable. The problem is that the signal costs a **cluster run**, while the only cheap
post-edit check is `bash -n` — a parser, structurally blind to ordering. So a 21-file edit
pass had no cheap verification of the one property it was most likely to break, and
"parses clean" got read as "the edit is sound".

## Fix

`check-e2e-auth-hygiene.sh` rule 9 previously hardcoded the names `E2E_TOKEN`, `E2E_SUB`
and `e2e_set_token`. It therefore caught shape A only and **could not have seen shape B at
all** — an instance fix wearing a class fix's clothes.

It now derives its inputs from `scripts/e2e/lib/e2e-auth.sh`:

- parses the lib (skipping its embedded Python heredocs, so `H={...}` is not mistaken for
  a variable the lib provides) and separates **source-time** assignments (top level — live
  the instant a suite sources the file) from **call-time** ones (inside a function body);
- takes the transitive closure over `e2e_*` calls, so `e2e_refresh_token` is recognized as
  a provider of `E2E_TOKEN` because it calls `e2e_set_token`;
- flags shape A (a call-time variable read above every provider, including a suite's own
  `E2E_TOKEN="$(e2e_require_token …)"` form) and shape B (a lib helper called with a `$VAR`
  assigned further down);
- skips `trap` lines — a trap body runs at EXIT, so referencing a variable assigned below
  it is correct, and flagging it is the kind of noise that gets a gate ignored.

Adding a helper to the lib extends the rule with no edit to the gate. That is the point:
every previous miss in this file's history came from a hand-written list going stale.

## Verification

Proven RED first (DoD rule 7) with throwaway probe suites, then deleted:

| Probe | Expected | Result |
|---|---|---|
| `e2e_set_token "$NS" "$API_POD"` above `API_POD=` | fire (B) | ✅ `:8 calls a lib helper with $API_POD, which is not assigned until line 10` |
| `${E2E_SUB}` read above the mint | fire (A) | ✅ `:9 reads ${E2E_SUB} … provided at line 11` |
| `${E2E_SUB}` read **below** the mint | silent | ✅ |
| `${E2E_KC_USER}` read right after `source` (source-time var) | silent | ✅ |
| `E2E_TOKEN="$(e2e_require_token …)"` as the provider | silent | ✅ |
| `${E2E_TOKEN}` read, sourced but never minted | fire | ✅ |

Real tree: **clean** across all 105 suites — matching an independent manual scan of
`API_POD=` vs `e2e_set_token` line numbers. Shape B is dormant today, not absent.

## Lessons

1. **A cheap check that answers a different question is worse than no cheap check** — it
   manufactures false confidence. `bash -n` has never once caught a defect from a scripted
   edit to these suites: not the split line continuations, not the auth-on-cleanup-only
   header dicts, not either ordering shape.
2. **When a gate misses something, the fix belongs in the gate** — not in that day's grep.
   Fifth time this lesson has been recorded in this file's own comments.
3. **Check whether the rule you just wrote covers the class or the instance.** Rule 9's
   first version was written the day shape B bit me and still could not see shape B.

Related: `docs/bugs/e2e-suites-that-could-never-run.md`,
`docs/bugs/trigger-e2e-suites-dead-since-require-user.md`,
`docs/testing/red-test-ledger.md`.

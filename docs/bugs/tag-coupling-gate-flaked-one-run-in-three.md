# The tag⇄content coupling gate flaked ~1 run in 3 — `grep -q` + `pipefail`

**Found:** 2026-08-08, while bumping to registry-api `0.2.274` / studio `0.1.187`.
**Fixed:** 2026-08-08, same change (`scripts/check-tag-content-coupling.sh`).

## Symptom

Immediately after bumping all four tag locations correctly, the gate reported:

```
FAIL  registry-api: source changed AFTER the last REGISTRY_API_TAG bump — the tag LIES about the image
=== tag⇄content coupling: PASS=42 FAIL=1 ===
```

Re-running it with **no edit in between** gave `PASS=43 FAIL=0`. Twelve consecutive runs
produced a mix of both verdicts, roughly one failure in three.

## Root cause

Not a tag problem at all. `scripts/check-tag-content-coupling.sh` runs `set -uo pipefail`
(line 63), and the working-tree freshness probe was:

```bash
if git diff -- "$DEPLOY_SH" 2>/dev/null | grep -qE "^[+-]${tagvar}=" \
   || git diff --cached -- "$DEPLOY_SH" 2>/dev/null | grep -qE "^[+-]${tagvar}="; then
  tag_ts=$NOW
fi
```

`grep -q` exits **the instant it matches**. `git diff` is still writing, gets SIGPIPE, and
exits 141. Under `pipefail` the pipeline's status is that 141 — a **failure** — even though
the pattern was found. So whether git had finished flushing before grep bailed decided the
verdict:

- git finishes first → grep exits 0 → pipeline 0 → `tag_ts=$NOW` → **PASS**
- grep matches early → git dies 141 → pipeline 141 → `tag_ts` stays at the last *committed*
  bump → `src_ts > tag_ts` → **FAIL**

The bumped tag lives only in the working tree until commit, so losing the dirty-check is
exactly equivalent to "the tag was never bumped". The gate was accusing a correct bump.

Note this is a **race the pattern makes more likely the longer the diff is**, and the tag
lines in `deploy-cpe2e.sh` / `deploy-eks.sh` are enormous — each carries a multi-hundred-word
changelog comment. The bigger the diff, the wider the window.

## Fix

Capture, then test. No pipeline status left to misread:

```bash
_tagdiff="$( { git diff -- "$DEPLOY_SH"; git diff --cached -- "$DEPLOY_SH"; } 2>/dev/null \
             | grep -E "^[+-]${tagvar}=" || true )"
if [ -n "$_tagdiff" ]; then
  tag_ts=$NOW
fi
```

This is the class fix for this call site, not a `|| true` bolted onto the old form: the
condition no longer depends on a process's exit status at all. Verified over **12
consecutive runs, 12× `PASS=43 FAIL=0`**.

The one other `| grep -q` in the file (line 428, `echo "$builders" | grep -qx "$img"`) is
safe — the producer is a shell builtin writing a small buffer, so there is no process to
SIGPIPE.

## Why this one mattered more than its size

A gate that flaps is **worse than no gate**. Its whole job is to block a deploy when a tag
lies about its image — and the repo has a postmortem
(`docs/bugs/e3-never-ran-tag-not-bumped.md`) for exactly that failure shipping while every
check stayed green. An intermittent red teaches the operator to re-run until it passes,
which is precisely the reflex that would wave a *real* drift through. It converts a control
into a coin flip and leaves the coin flip looking like a control.

The same shape as `docs/bugs/e2e-suite-runtime-ordering-from-scripted-edits.md`, found the
same day: a check that answers a different question than the one you think you asked.

## Lessons

1. **`cmd | grep -q` under `set -o pipefail` is a bug, not a style choice.** Capture the
   output and test it, or drop `-q`. Anywhere a pipeline's *status* is the predicate,
   `pipefail` and early-exiting consumers are in direct conflict.
2. **A verdict that changes without an edit is a defect in the checker,** never a reason to
   re-run. The instinct to "try it again" is the thing this class of bug feeds on.
3. **Run a gate more than once before trusting it green** — especially right after touching
   what it measures. One run proves nothing about a racy check.

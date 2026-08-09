# Six e2e suites were dead for months — bash spliced into their Python heredocs

**Found:** 2026-08-09, while running `suite-68`/`suite-94` as blast radius for identity P1.5.
**Fixed:** 2026-08-09, same change. Class fix: `check-e2e-auth-hygiene.sh` rules **8b** and **8c**.

## Symptom

`suite-68` reported:

```
Running driver detached in-pod (deploy + empty-input run can take ~2 min)…
started

=== Results ===
ERROR: no result file — driver log:
  File "/tmp/s68_driver_1786255777842.py", line 11
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
           ^^^^^^^
SyntaxError: invalid syntax
```

A **bash** line inside a **Python** file. Six suites were affected:
`suite-64`, `suite-65`, `suite-66`, `suite-68`, `suite-94`, `suite-96`.

## Root cause

These suites do not use `python3 -c '<program>'`. They write the driver to a file first:

```bash
kubectl exec -i ... -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, os, uuid, httpx
...
PY
```

Two scripted edit passes — the R3 Bearer sweep (`55c2ae4`) and the E2E_SUB sweep
(`748c2fd`) — inserted

```bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"
```

**inside** that heredoc. The insertion point was picked by a textual landmark ("above the
header dict"), and in these six suites that landmark happens to live inside the heredoc
body rather than in the shell script around it. Exactly the landmark-versus-dataflow
recurrence documented in `docs/bugs/e2e-suite-runtime-ordering-from-scripted-edits.md`,
one layer further in: not the wrong *order*, the wrong *language*.

Every one of the six already authenticated correctly. Each sources the lib, calls
`e2e_require_token` and `e2e_install_pyauth` **above** the heredoc, and builds its client
with `auth=BearerAuth()`. The inserted lines were redundant *and* fatal.

### Why it survived so long

**The failure mode does not look like a test failure.** A driver that never starts writes
no result file, so the suite reports *"no result file"* / *"driver did not finish"*. That
reads as cluster flakiness — a slow pod, a lost exec — and the natural response is to
re-run it, which fails the same way. Three of the six were also already in the red-test
ledger for unrelated fixture reasons, which absorbed the suspicion.

`bash -n` passes: the suite file **is** valid bash. The heredoc body is just a string to
the shell. Same blind spot as every other entry in this family.

## A second defect in the same lines

The heredocs are **quoted** (`<<'PY'`), so `${E2E_SUB}` and `${E2E_TOKEN}` are never
interpolated — they arrive as those literal strings. Measured across the tree, 17 such
references exist. Classified:

* **16 are decorative.** They land in `X-User-Sub`, which handlers consult only as a
  fallback when there is no token (`armed_by = (user or {}).get("sub") or x_user_sub`), and
  `BearerAuth()` always supplies one. Removed rather than fixed: the value was both wrong
  and unread.
* **1 broke assertions — `suite-71`.** It compares `trig.armed_by == ADMIN` (could never be
  true) and `rb != ADMIN` (passed for the wrong reason, since any real sub differs from the
  literal). Fixed by passing the sub through the environment: `S71_ADMIN_SUB=$E2E_SUB`, read
  with `os.environ`.

**The bug was also hiding itself from the gate.** Rule 9 checks that a suite reading
`${E2E_SUB}` calls something that sets it. These suites "passed" because the spliced
`e2e_set_token` line was present in the file — inside the heredoc, where it never ran. The
gate was satisfied by the very defect. Removing the splice turned six findings red, which
is how the second defect surfaced at all.

## Fix

1. The spliced bash removed from all six heredocs; each keeps the auth it already had.
2. `suite-71`'s sub threaded via environment.
3. The 16 decorative literals deleted.
4. **Class fix — two gate rules**, both checking the *exact* property rather than a proxy:
   * **8b** — `python3 -c '<program>'`: slice from the opening quote to the next `'`, which
     is precisely what bash hands `python3`, and `compile()` it.
   * **8c** — heredoc-written drivers: `compile()` the heredoc body.

   An earlier draft of 8b hunted for apostrophes instead and **false-positived on
   suite-99**, whose one-line driver legitimately closes mid-line. Compiling is exact;
   pattern-matching the quoting was not.

## Verified

Suites that previously produced **no result at all** now run: `suite-96` **10/0**,
`suite-94` **9/0**, `suite-68` **3/0**. Both new rules were confirmed red-first by
re-introducing the defect and watching the gate flag it, then restoring.

## Lessons

1. **"No result file" is a suite defect until proven otherwise.** It reads as
   infrastructure noise, and that is exactly why it survives. A suite that cannot fail
   cannot pass either.
2. **A scripted edit must know what language it is editing.** Every rule in this gate's
   history came from a pass that chose its insertion point textually. A heredoc body is a
   different language in the same file, and no amount of care about *ordering* catches it.
3. **A gate can be satisfied by the bug it should catch.** Rule 9 saw `e2e_set_token` in the
   file and stopped asking whether it could ever execute. Presence is not execution.
4. **Compile it, do not pattern-match it.** The exact property was cheap, had no false
   positives, and caught a strictly larger class than the regex that preceded it.

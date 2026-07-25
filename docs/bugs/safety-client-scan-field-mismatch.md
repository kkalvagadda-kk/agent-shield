# SDK safety_client speaks the wrong wire fields → every real scan fails closed

**Found/Fixed:** 2026-07-25 · fixed in sdk `0.2.2` (branch `mcp-tool-source`, commit adding P10). Regression test: `sdk/tests/test_safety_client_fields.py`.

## Symptom

On a real deployment (`AGENTSHIELD_SAFETY_URL` set), every `scan_input` /
`scan_output` call to the Safety Orchestrator failed closed — the SDK raised
`SafetyBlockedError(reason="scanner_error")` — even though the orchestrator was
healthy. Local dev (mock path) worked, so it never surfaced in unit runs. Any
de-anonymized value the orchestrator returned was silently dropped.

## Root cause

A **two-sided field-name mismatch** between the SDK client and the orchestrator's
Pydantic schemas (`services/safety-orchestrator/schemas.py`):

| Direction | Orchestrator expects/returns | SDK sent/read (bug) |
|-----------|------------------------------|---------------------|
| request | `message`, `thread_id` | `text`, `trace_id` |
| input response | `anonymized_message` | `sanitized_text` |
| output response | `deanonymized_message` | `clean_text` |

Because `message` is a **required** field on `ScanInputRequest`/`ScanOutputRequest`,
a request carrying `text` (and no `message`) is a `422` at the orchestrator →
`resp.raise_for_status()` throws → the client's `except` maps it to
`scanner_error` and **fails closed**. And even had the request succeeded, the SDK
read `sanitized_text`/`clean_text` from the response — keys the orchestrator never
sends — so it always fell back to the un-scanned original text.

This is the class of bug where two services co-evolve their wire contract but only
one side is updated, and the mismatch is invisible until a real (non-mock) call is
made. The mock path used the SDK's own dataclass field names, masking it in tests.

## Fix

`sdk/agentshield_sdk/safety_client.py` — align both sides of the wire to the
orchestrator's schema, while keeping the SDK-facing dataclass field names stable
(`ScanInputResult.sanitized_text` / `ScanOutputResult.clean_text`) so callers are
unaffected:

- request: `text → message`, `trace_id → thread_id`, `session_id` defaulted to
  `""` (server-required, non-optional).
- response reads: `sanitized_text → anonymized_message`, `clean_text →
  deanonymized_message`, each falling back to the original text.
- added `deanonymize_args(args, agent_name, session_id)` (Decision 27) — **fail-open**
  (a de-anon outage returns args unchanged, never raises; contrast the scans which
  fail closed).

**Class-fix, not a patch:** the fix is driven by a regression test
(`test_safety_client_fields.py`) that asserts the exact wire field names against a
`MockTransport` — so a future one-sided rename fails a test instead of silently
fail-closing in production. Written regression-first (it failed against the buggy
code before the fix landed) per DoD rule 7.

## Lessons

- A mock that uses your own internal field names hides a wire-contract drift. Test
  the **wire**, not the dataclass — assert the actual JSON body a real request
  sends and the actual keys a real response is read from.
- Fail-closed defaults are correct for a safety gate, but they also make a wiring
  bug look like a scanner outage. The regression test distinguishes "scanner down"
  from "client speaks the wrong dialect."

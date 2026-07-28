# HTTP tool body_template breaks when a value contains JSON-special characters

**Found/Fixed:** 2026-07-27 — fixed in SDK `0.2.8` / declarative-runner `0.1.65`.
Surfaced building the `email_notifier` tool (Resend HTTP tool): an email body is
free text and routinely contains quotes and newlines.

## Symptom

An HTTP tool whose `http_body_template` is JSON (e.g. `email_notifier`:
`{"from":"…","to":"{{to}}","subject":"{{subject}}","html":"{{body}}"}`) produced an
invalid request whenever a substituted value contained a `"`, `\` or newline. The
call then fell back to sending the malformed string as raw `content`, and the
upstream API (Resend) returned `400 Bad Request`. Plain values worked; any realistic
email body did not.

## Root cause

Both HTTP executors — the SDK's `HttpToolExecutor` and the declarative-runner's
`HttpToolNodeExecutor` — did **substitute-then-parse**:

```python
body = _substitute_vars(body_template, kwargs)  # raw string interpolation
req_kwargs["json"] = json.loads(body)           # then parse
```

`_substitute_vars` injects the raw value into the JSON *text*, so a value like
`He said "hi"` turned `{"html":"{{body}}"}` into `{"html":"He said "hi""}` — no
longer valid JSON. The design flaw is treating a **structured** payload as a flat
string template: the value crosses the JSON string boundary unescaped.

## Fix

Invert the order to **parse-then-substitute-in-leaves** via a shared
`_render_http_body(template, variables) -> (body, is_json)`:

1. `json.loads` the *template* once (placeholders live inside quoted string values,
   so the template itself is valid JSON).
2. Walk the parsed structure and substitute `{{var}}` only inside string leaves,
   with real Python values.
3. Return the built object for httpx `json=` — the value can no longer break the
   structure because it is never re-parsed.

Backward-compatible fallback: if the template is **not** valid JSON (a form body, or
a numeric placeholder outside quotes like `{"count": {{n}}}`), it drops to the legacy
substitute-then-parse path, so existing tools are unaffected.

The helper is duplicated byte-identically in both services (they are separate images
and already duplicate `_substitute_vars`). Applied at all three body-construction
sites: SDK `HttpToolExecutor.http_tool_fn`, runner `HttpToolNodeExecutor.execute` and
`.http_tool_fn` (the schema-driven `dict(kwargs)` fallback for templateless POSTs is
preserved).

## Regression test

`sdk/tests/test_http_tool_json_body_escaping.py` — asserts a body with quotes +
newline + backslash lands intact in a structured payload (`test_quotes_and_newlines_
do_not_break_json`), and explicitly demonstrates the old substitute-then-parse path
raised `JSONDecodeError` on the same value (`test_old_substitute_then_parse_would_
have_failed`). Also covers the non-JSON and numeric-placeholder fallbacks. 6/6 green.

## Deploy

SDK `0.2.7→0.2.8`; declarative-runner `0.1.64→0.1.65` (bumped in `deploy-cpe2e.sh`,
`deploy-eks.sh`, and `charts/agentshield/values.yaml` `deploy-controller.declarativeRunnerTag`).
The fix activates for `email_notifier` once the declarative-runner image is rolled;
until then, keep email bodies free of raw quotes/newlines.

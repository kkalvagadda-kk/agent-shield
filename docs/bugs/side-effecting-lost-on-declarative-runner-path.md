# `side_effecting` never reached the callable on the declarative-runner path → record mode mocked EVERY tool

**Found:** 2026-07-15 by the Eval-v2 **E-2** no-fakes gate (suite-74, T-S74-005 / T-S74-010). **Fixed:** declarative-runner `0.1.47`.

## Symptom
Under `eval_mode=record`, a **provably read-only** tool (`side_effecting=false`, an HTTP GET) was mocked and recorded instead of being delivered for real:

```
T-S74-005 FAIL  read-only pass-through … real_reflection=False mock_sentinel=True
  recorded=[{tool: s74_read, mocked_response: {id: "mock-…"}, would_have_invoked: "s74_read"}]
```

Knock-on: `T-S74-010` (fail-closed runner) could never reach its assertion — its precondition is a record-mode run that recorded **nothing**, but with every tool being mocked, something was always recorded.

## Root cause — two tool-building paths; E-2 threaded only one
`graph_builder.governed_tool`'s seam reads the classification straight off the callable:

```python
return getattr(fn, "side_effecting", None) is not False   # None ⇒ unclassifiable ⇒ mocked (fail-closed)
```

The **SDK** path stamps it correctly (`tool_resolver._build_executor` → `HttpToolExecutor(side_effecting=…)` → `http_tool_fn.side_effecting = …`).

But the **declarative-runner** — the runtime that actually serves declarative agents — does **not** use `tool_resolver`. It has its own builder, `workflow_executor._tool_dict_to_executor`, feeding `node_executors.HttpToolNodeExecutor` / `PythonToolNodeExecutor`. That path copied `name`/`description`/`risk`/`method`/`headers`/`body_template`/`auth_config_id`/`input_schema` from the registry payload — and **dropped `side_effecting`**. The node executors stamped `.risk` and `.tool_name` onto the callable but never `.side_effecting`.

So on every declarative agent, `getattr(fn, "side_effecting", None)` was `None` → fail-closed → **every** tool mocked under record, read-only ones included.

Fail-closed meant the bug was *safe* (nothing leaked), which is exactly why it survived: the seam looked right in isolation, and a unit test against the **SDK** executors passed:

```
stamped: read=False write=True unclassified=None
record → should_record: read=False write=True unclassified=True   # correct — but this is the SDK path, not the runner's
```

Only a real run through a real declarative pod exposed it.

## Fix (declarative-runner 0.1.47)
Thread the flag through the runner's own builder with the identical contract the SDK uses (same attribute, same `None` = unclassified = fail-closed default):

- `services/declarative-runner/workflow_executor.py::_tool_dict_to_executor` — carry `side_effecting` from the registry payload into both the python and http executor configs.
- `services/declarative-runner/node_executors.py` — `HttpToolNodeExecutor.__init__` / `PythonToolNodeExecutor.__init__` read `node_config.get("side_effecting")`; `as_tool_callable()` stamps `…_tool_fn.side_effecting = self.side_effecting` next to `.risk`/`.tool_name`.

## Lessons
1. **Two paths that build the same thing will drift.** `tool_resolver` (SDK) and `_tool_dict_to_executor` (runner) both turn a registry `ToolResponse` into a governed callable. A field added to one silently no-ops on the other. Any new tool attribute must be threaded through **both** — or, better, they should converge on one builder (follow-up).
2. **Fail-closed hides the gap.** Because unclassified ⇒ mocked, the miss degraded into "over-mocking" rather than an error. Safe, but wrong, and invisible without an end-to-end assertion that a read-only tool IS delivered.
3. **Testing a component in isolation can confirm a false theory.** The SDK-path unit check passed and led to a "stale pod" hypothesis; a fresh re-run reproduced the failure identically and pointed at the real fork. Reproduce before concluding.
4. **The gate earned its keep again** — a fifth real defect (after the trigger runner_url, datasets mode-drop, approval_id, trajectory collapse, and the JSONB text-coercion bugs) found only because the suite drove a real pod and asserted a real delivery.

## Files
- `services/declarative-runner/workflow_executor.py` (fix)
- `services/declarative-runner/node_executors.py` (fix)
- `sdk/agentshield_sdk/tool_resolver.py` / `tool_executor.py` (the already-correct SDK path — the contract mirrored)
- `sdk/agentshield_sdk/graph_builder.py` (`_should_record` — the reader; unchanged, was always correct)
- `scripts/e2e/suite-74-eval-v2-side-effects.sh` (the gate that found it)

# Bug: Governed-tool graph build crashes with KeyError (agent pod CrashLoop)

**Date:** 2026-07-13
**Status:** Implemented
**Severity:** High — any agent binding a python-type tool (or an arg-less HTTP tool) CrashLoops at startup; the tool is unusable.

## Symptom

A declarative agent that binds a python tool never starts — the pod CrashLoops at
lifespan setup with:

```
File ".../langchain_core/tools/base.py", create_schema_from_function
File ".../pydantic/deprecated/decorator.py", __init__
    annotation = type_hints[name]
KeyError: 'kwargs'
```

(An arg-less HTTP tool produces the same crash with `KeyError: 'params'`.) The agent's
graph is never built, so the pod is `CrashLoopBackOff` and the agent is dead on arrival.

The bug was masked for tools whose HTTP URL/body carries a `{{template}}` variable —
those build named params and happen to escape it — which is why "adding an input_schema"
*appeared* to fix one instance, hiding the real (broader) cause.

## Root Causes

### RC-1: Executor `__signature__` had parameters absent from `__annotations__`

**Where:** `sdk/agentshield_sdk/tool_executor.py` — `PythonToolExecutor.as_tool_callable`
(the `**kwargs` param) and `HttpToolExecutor.as_tool_callable` (the arg-less `params` param).
**Problem:** LangChain 1.x introspection (`create_schema_from_function` → pydantic's
deprecated `validate_arguments`) does `type_hints[name]` for **every** parameter and raises
`KeyError` if any parameter — including `**kwargs` or the `params` catch-all — has no
annotation. Python tools always emitted `(**kwargs)` with `__annotations__={'return': str}`
(→ `KeyError('kwargs')`); arg-less HTTP tools emitted `(*, params=...)` with `{}`
(→ `KeyError('params')`).
**Fix:** `__annotations__` is now **derived from the signature params**
(`_annotations_from_params`), so a param can never lack a hint. Python tools additionally
build **named, typed params from their registered `input_schema`**
(`_params_from_input_schema`, threaded through `tool_resolver._build_executor`) — the same
treatment HTTP tools get from `{{template}}` vars — falling back to an annotated `**kwargs`
only when no `input_schema` is declared.

### RC-2: `input_schema` was ignored during signature construction (latent design gap)

**Where:** `sdk/agentshield_sdk/tool_resolver.py` / `tool_executor.py`.
**Problem:** The `Tool.input_schema` (populated for most tools) was never consulted, so
even python tools with a full JSON-Schema got an opaque `**kwargs` model-facing schema
(and, per RC-1, crashed). The premise that "input_schema present → works" was a
coincidence of HTTP template-var handling, not `input_schema` being read.
**Fix:** see RC-1 — python tools now consume `input_schema`.

### RC-3: graph_state InjectedState silently dropped for python tools

**Where:** `sdk/agentshield_sdk/graph_builder.py` — `_wrap_tool_with_governance`.
**Problem:** The wrapper appended the `graph_state` keyword-only param *after* the
executor's params. For a bare `**kwargs` signature that is illegal (a keyword-only param
can't follow `**kwargs`), so `inspect.Signature.replace` raised, the `except` swallowed it,
and HITL reasoning-capture was silently lost for every python tool (old image logged
`"wrong parameter order: variadic keyword parameter before keyword-only parameter"`).
**Fix:** insert `graph_state` **before** any `VAR_KEYWORD` param.

## Image Tags

- `declarative-runner:0.1.39` (bakes the SDK fix) — `deploy-cpe2e.sh` + `values.yaml`.
- Control: `declarative-runner:0.1.38` reproduces `KeyError('kwargs')`.

## Files Changed

- `sdk/agentshield_sdk/tool_executor.py` — `_params_from_input_schema`,
  `_annotations_from_params`; python executor uses input_schema + annotated fallback;
  http executor derives annotations from params.
- `sdk/agentshield_sdk/tool_resolver.py` — pass `input_schema` to `PythonToolExecutor`.
- `sdk/agentshield_sdk/graph_builder.py` — inject `graph_state` before `**kwargs`.
- `sdk/tests/test_tool_executor_schema.py` — 16 regression cases (invariant + bind).
- `scripts/e2e/suite-54-tool-schema-build.sh` + `run-all.sh` registration.

## Verification

- In-pod repro (langchain-core 1.4.9): Cases A/C crash pre-fix; B builds.
- Shipped artifact: `docker run declarative-runner:0.1.39` → all 4 shapes bind, exit 0;
  `docker run declarative-runner:0.1.38` → `KeyError('kwargs')`.
- Unit: 16/16 green.

## Lessons

1. **The signature/annotations pair is an invariant — derive one from the other.** Two
   hand-maintained sources of truth drifted; the fix makes drift unrepresentable rather than
   patching each crash site.
2. **A "fix" that only works because of an adjacent code path is not a fix.** input_schema
   didn't fix anything — HTTP template-var handling did. Reproduce the *class*, not the instance.
3. **Silent `except Exception` around signature surgery hid RC-3 for months.** Injection
   failures that degrade governance/observability should at least be loud.

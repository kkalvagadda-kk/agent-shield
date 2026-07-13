# Change Brief

**Date**: 2026-07-13
**Jira**: (none)
**PR**: (none)
**Source**: User description + empirical reproduction inside the deployed runner pod

## What's Changing

Governed tool wrappers fail LangChain's args-schema introspection (`KeyError` during
`lc_tool()` / `create_schema_from_function`) for any platform tool whose executor
signature contains a parameter that is missing from the callable's `__annotations__`.
Fix `tool_executor.py` so the `__signature__` and `__annotations__` it emits are always
consistent — every parameter in the signature has a matching type hint.

## Why

Reproduced inside `refund-processor-sandbox` (langchain-core 1.4.9 / langgraph 1.2.9),
mimicking exactly what `graph_builder.build_graph()` does per tool
(`_wrap_tool_with_governance` → `lc_tool`):

| Case | Executor signature | `__annotations__` | Result |
|------|--------------------|-------------------|--------|
| A — python tool (`PythonToolExecutor`) | `(**kwargs: str)` | `{'return': str}` | **`KeyError: 'kwargs'`** |
| B — http tool with `{{order_id}}` template | `(*, order_id: str)` | `{'order_id': str}` | OK |
| C — http tool, no template vars | `(*, params: 'str\|None'=None)` | `{}` | **`KeyError: 'params'`** |

Root cause: langchain 1.x → pydantic's deprecated `validate_arguments` does
`type_hints[name]` for **every** parameter and raises `KeyError` when a parameter has no
annotation. `tool_executor.py` builds `__signature__` with params
(`kwargs` for python; `params` for arg-less http) that it never adds to `__annotations__`.

Two corrections to the original hypothesis:
- **`input_schema` is irrelevant.** It is never consulted when building the executor
  signature (`_build_executor` in `tool_resolver.py` does not pass it; `tool_executor.py`
  ignores it). `refund_action` builds only because it is an HTTP tool **with a named
  `{{template}}` variable** (Case B), not because it has an `input_schema`.
- **The bug is broader than "python + **kwargs".** It hits (1) every python-type tool,
  and (2) every http tool with no template variables. Only http tools with named
  template params escape it.

Secondary observation: for python tools the graph_state InjectedState injection in
`_wrap_tool_with_governance` also silently fails (a KEYWORD_ONLY param can't be appended
after `**kwargs`; the `except Exception` swallows it), so those tools would lose HITL
reasoning capture even if the schema crash were worked around.

## Scope

- `sdk/agentshield_sdk/tool_executor.py` — `PythonToolExecutor.as_tool_callable` and
  `HttpToolExecutor.as_tool_callable` (signature/annotations construction).
- Verify no regression in `sdk/agentshield_sdk/graph_builder.py` governance wrapping.
- Rebuilt into `declarative-runner` (and any SDK-consuming) image.

## Spec Reference

`docs/spec.md:805` — "Pass executor callables to `create_react_agent(llm, tools=[...])`
for that agent node." The spec requires governed executor callables to bind to the graph;
it does not describe the schema-introspection failure. Spec is correct; the code violates
it. Implementation-level fix, no spec amendment.

## Acceptance Criteria

- [x] A high-risk **python** tool with no declared params builds via `lc_tool()` with no
      `KeyError` (Case A — verified in shipped 0.1.39 image, model props `['kwargs']`).
- [x] An http tool with **no** template variables builds via `lc_tool()` (Case C — verified,
      model props `['params']`).
- [x] Existing http-with-template tools still build and still expose their named args in
      the model-facing schema (Case B — unchanged, model props `['order_id']`).
- [x] `graph_state` InjectedState is injected for python tools too (before `**kwargs`) and
      is excluded from every model-facing schema, so HITL reasoning capture is not silently
      dropped (old 0.1.38 logged "wrong parameter order" and skipped it; 0.1.39 does not).
- [x] Python tools with an `input_schema` expose typed named params (`['order_id','amount']`)
      instead of an opaque `kwargs` blob.
- [x] SDK unit test added and green: `sdk/tests/test_tool_executor_schema.py` (16 cases,
      covering the signature/annotations invariant, input_schema→named params, `**kwargs`
      fallback, graph_state-before-var-keyword, and the full langchain bind for all shapes).
- [x] e2e regression suite `scripts/e2e/suite-54-tool-schema-build.sh` (runs the SDK build
      inside a real declarative-runner pod; registered in run-all.sh).

## Verification performed

- **Root-cause repro** inside a live pod (langchain-core 1.4.9): Cases A & C crashed with
  `KeyError('kwargs')` / `KeyError('params')`; Case B built.
- **Fix on the shipped artifact**: `docker run declarative-runner:0.1.39` → all 5 shapes
  bind, exit 0. **Control**: `docker run declarative-runner:0.1.38` → `KeyError('kwargs')`.
- **Unit tests**: 16/16 green.
- Deployed: declarative-runner:0.1.39 (deploy-controller env `DECLARATIVE_RUNNER_IMAGE`
  confirmed `…:0.1.39`, so new agent deployments use the fixed image).

## Known gaps (honest ledger)

- **not-yet-run (env-blocked)**: A full "create python-tool agent → deploy → pod Reaches
  Running" journey on-cluster was blocked by environmental gates unrelated to this fix:
  (a) team `default` has no `agents-default` namespace (reconcile 404); (b) granting a tool
  or upgrading an existing shared deployment were permission-gated this session. The shipped
  0.1.39 image was instead verified directly via `docker run` (equivalent proof — same
  artifact, same langchain), plus suite-54 will exercise it in-pod once any agent runs 0.1.39.
- **pre-existing, unrelated blocker (NOT this change)**: the registry-api rollout is stuck —
  the DB is at Alembic revision **0058** but the repo/image top out at **0057** (an
  uncommitted migration `0058` was applied by a prior build). The rebuilt registry-api:0.2.155
  `alembic-migrate` init CrashLoops ("Can't locate revision identified by '0058'"). Old
  registry-api pods keep serving. This is a migration-drift landmine to reconcile separately;
  do not stamp the DB down (would drop whatever 0058 added). Flagged for the user.

## Out of Scope

- The separate live failure in `fraud-alert-triage-sandbox`
  (`ValueError: Agent.instructions must be a non-empty string`, node_executors.py:282) —
  a workflow agent node with empty instructions. Tracked separately; not a KeyError pod.
- The pre-existing 0058-migration drift blocking the registry-api rollout (see ledger above).
- OPA / HITL decision logic; only the tool-callable schema construction changes here.

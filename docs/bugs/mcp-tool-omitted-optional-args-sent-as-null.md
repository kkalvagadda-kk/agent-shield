# MCP tool calls forwarded omitted-optional args as `null`, breaking servers with typed optionals

**Found/Fixed:** 2026-07-27 — fixed in SDK `0.2.6` / declarative-runner `0.1.63`. Surfaced
building the real Tavily web-search agents.

**Two executor paths, same two bugs.** MCP tool calls go through the SDK's `McpToolExecutor`
(`sdk/agentshield_sdk/tool_executor.py`, used by SDK-container agents) OR the runner's
**separate** `McpToolNodeExecutor` (`services/declarative-runner/node_executors.py`, used by
DECLARATIVE agents — repo-explainer / web-researcher). The first fix (SDK `0.2.5`) only
touched the SDK path, so declarative agents still failed; the complete fix (below) patches
BOTH. There were also **two** distinct defects, not one — see Root cause.

## Symptom

A declarative agent bound to Tavily's MCP tools called `tavily__tavily_search` with a real
query, but the tool returned:

```
Internal error: 12 validation errors for call[tavily_search] …
max_results  Input should be a valid integer [input_value=None]
search_depth Input should be 'basic' or 'advanced' [input_value=None]
…
```

The agent then fell back to its training data ("I'm experiencing tool connectivity issues"),
so the answer was NOT grounded in live web results. The **same** DeepWiki agent worked
perfectly — `deepwiki__ask_question` returned real repo content.

## Root cause

`McpToolExecutor`'s tool callable forwarded the raw LangChain kwargs to the proxy:

```python
payload = { …, "arguments": kwargs, … }   # tool_executor.py (before)
```

LangChain builds the tool's signature from the discovered MCP `input_schema`
(`_params_from_input_schema`), giving every **optional** property `default=None`. When the LLM
omits an optional param, LangChain still passes it — as an explicit `None` — so the executor
sent `{"query": "...", "max_results": null, "search_depth": null, …}` on the wire. An MCP
server that types its optionals (Tavily: `max_results:int`, `search_depth:enum`) rejects
`null`, because in JSON-Schema/MCP an omitted optional means "use the default" — which is
**absence**, not `null`.

DeepWiki was immune only by accident: both its params (`repoName`, `question`) are
**required**, so there were no optionals to null-inject.

**Second defect — the schema's declared defaults were ignored.** Both `_params_from_input_schema`
(SDK) and `_mcp_params_from_schema` (runner) built every optional param with a blanket
`default=None`, discarding the default the discovered schema actually declares. Tavily's
`tavily_search` types its optionals *with real defaults* — `topic: "general"`,
`max_results: 5`, `search_depth: "basic"` — and **rejects `None`** for them (its own default
resolves to `None` when the arg is absent, then fails the `Literal`/`int` validation). So even
after dropping `None`, an omitted optional gave Tavily no value to work with. The correct
behavior is to carry the schema's declared default so an omitted optional sends the intended
value; only optionals with *no* declared default fall through to `None` (and get dropped).

Proven by a controlled A/B against the live MCP proxy from inside the agent pod:
- `{"query": "...", "max_results": 3}` → **HTTP 200, real results**.
- `{"query": "...", "max_results": null, "search_depth": null}` (what the SDK sent) →
  `is_error: true`, the identical validation error.

So the server, proxy, and credentials were all healthy — the defect was purely arg-marshaling.

## Fix

Two changes, applied to **both** executor paths (`tool_executor.py` + `node_executors.py`):

1. **Carry the schema's declared default** for an optional param instead of a blanket `None`:
   ```python
   schema_default = (defn or {}).get("default", None)
   inspect.Parameter(name, KEYWORD_ONLY, default=schema_default, annotation=Optional[pytype])
   ```
   An omitted optional now sends the server's intended value (`topic="general"`,
   `max_results=5`, …). Only optionals with no declared default remain `None`.

2. **Drop remaining `None`-valued kwargs** before building the proxy payload, so an optional
   with no schema default is **absent** on the wire and the upstream applies its own default:
   ```python
   mcp_arguments = {k: v for k, v in kwargs.items() if v is not None}
   payload = { …, "arguments": mcp_arguments, … }
   ```

Together this is the **class-fix** for *any* MCP tool with optional typed params, not just
Tavily. A falsy-but-not-None value the model actually set (`0`, `""`, `False`) is preserved —
only `None` (the "omitted, no default") is dropped. A required-only tool (deepwiki-shaped)
has nothing to change, so its calls are byte-identical.

## Regression test

`sdk/tests/test_mcp_tool_arg_marshaling.py` (3 cases): omitted optionals are dropped (fails
against the old `"arguments": kwargs`), explicitly-set args (incl. falsy `0`/`""`) survive,
and a required-only tool passes through unchanged. Green at SDK 0.2.5.

## Deploy

SDK 0.2.4→0.2.5; declarative-runner 0.1.61→0.1.62 (the runner bundles the SDK from source);
`declarativeRunnerTag` in `charts/agentshield/values.yaml` + `deploy-cpe2e.sh` + `deploy-eks.sh`
bumped together. After rolling the runner tag and re-deploying, `repo-explainer` +
`web-researcher` return live Tavily-grounded results (the CLEAN-args A/B above is exactly that
path).

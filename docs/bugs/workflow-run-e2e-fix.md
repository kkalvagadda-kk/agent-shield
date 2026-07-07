# Bug: Workflow Runs Failing End-to-End (2026-07-07)

## Symptom

Deploying agents from Studio worked, but running composite workflows produced no output and no visible error in the UI. Agent pods crashed or timed out silently.

## Root Causes (6 cascading issues)

### 1. Workflow dispatch URL wrong (ConnectError)

**Where:** `services/registry-api/workflow_orchestrator.py`
**Problem:** Orchestrator hardcoded `-production` in the agent service URL, but agents were deployed with environment `sandbox`.
**Fix:** Added `_resolve_agent_environment()` — queries the Deployment table for the agent's actual running environment.

### 2. `memory_context` TypeError in declarative-runner

**Where:** `services/declarative-runner/workflow_executor.py`
**Problem:** `main.py` passed `memory_context` kwarg to `run()` but the method signature didn't accept it.
**Fix:** Added `memory_context: list[dict] | None = None` parameter and wired it into the LangGraph state as history messages.

### 3. LLM provider FK column always NULL

**Where:** `services/registry-api/routers/agents.py` (create + update endpoints)
**Problem:** Studio saves `llm_provider_id` in the agent's metadata JSON, but the deploy endpoint reads the FK column (`agent.llm_provider_id`) which was never populated.
**Fix:** Set `agent.llm_provider_id = body.metadata.get("llm_provider_id")` in both `create_agent` and `update_agent`.

### 4. RBAC — 403 writing secret to agent namespace

**Where:** `charts/agentshield/charts/registry-api/templates/rbac.yaml`
**Problem:** Registry-api had a namespaced Role in `agentshield-platform` but needed to write secrets to `agents-platform` where agent pods run.
**Fix:** Upgraded to ClusterRole + ClusterRoleBinding granting secrets/configmaps/jobs verbs cluster-wide.

### 5. DeploymentResponse missing LLM fields

**Where:** `services/registry-api/schemas.py` (`DeploymentResponse`)
**Problem:** Deploy-controller fetches Deployment via API but `llm_secret_name`, `llm_env_keys`, `llm_provider_type`, `llm_provider_model` weren't serialized. Controller never got the secret reference.
**Fix:** Added the four fields to `DeploymentResponse`.

### 6. Env var name mismatch (Bedrock credentials)

**Where:** `services/deploy-controller/manifest_builder.py`
**Problem:** Secret keys stored lowercase (`aws_region`, `aws_access_key_id`) but boto3/langchain expect uppercase canonical names (`AWS_DEFAULT_REGION`, `AWS_ACCESS_KEY_ID`).
**Fix:** Added `_ENV_NAME_MAP` that maps lowercase secret keys to the canonical env var names expected by provider SDKs.

### 7. Empty credentials in DB (Studio key-name mismatch)

**Where:** `studio/src/pages/ProvidersPage.tsx`
**Problem:** Studio sent credentials with UPPERCASE keys (`AWS_ACCESS_KEY_ID`, `AWS_DEFAULT_REGION`) but the backend Pydantic schema `LLMCredentials` expects lowercase (`aws_access_key_id`, `aws_region`). Pydantic silently dropped unrecognized keys — all values stored as `None`.
**Fix:** Changed Studio to send lowercase keys matching the schema: `api_key`, `aws_access_key_id`, `aws_secret_access_key`, `aws_region`.

### 8. Invalid Bedrock model ID

**Where:** LLM provider `default_model` field in DB
**Problem:** `claude-sonnet-4-6` is not a valid Bedrock model identifier. On-demand invocation requires the `us.` cross-region inference prefix for newer models.
**Fix:** Updated provider to `us.anthropic.claude-sonnet-4-6`. Updated Studio's `MODELS` constant with correct Bedrock model IDs. Validated by calling `boto3 bedrock.list_foundation_models()` and testing with `converse()`.

### 9. Bedrock read timeout (60s default too short)

**Where:** `sdk/agentshield_sdk/llm.py`
**Problem:** Default boto3 read timeout is 60s. Complex prompts (especially the second agent receiving the full output of the first) exceed this.
**Fix:** Configured `BotoConfig(read_timeout=300, connect_timeout=10, retries={"max_attempts": 2})` on `ChatBedrockConverse`.

### 10. UI doesn't show workflow output

**Where:** `studio/src/pages/WorkflowBuilderPage.tsx`, `studio/src/api/registryApi.ts`, `services/registry-api/schemas.py`
**Problem:** (a) `AgentRunResponse` schema didn't include `input`/`output`/`error_message` fields. (b) `AgentRunItem` TypeScript interface was missing them. (c) The run tree UI rendered status badges but never displayed the actual output text. (d) Polling stopped after 30s (15×2s) — too short for Bedrock calls.
**Fix:** Added fields to backend schema + TS interface. Added output/error rendering in the run tree panel. Extended polling to 90×3s (4.5 min).

### 11. SSE double-wrapping floods trace panel with `[message]` entries

**Where:** `services/declarative-runner/main.py`, `studio/src/components/playground/ChatPane.tsx`
**Problem:** `format_sse()` in the SDK produces pre-formatted SSE frames (`event: text_delta\ndata: {...}\n\n`). The streaming endpoint wrapped these in `EventSourceResponse` from sse-starlette, which applies its own `data:` framing — result is double-wrapped frames with no named event. The registry-api SSE proxy defaults unnamed events to `event: "message"`, flooding the trace panel with hundreds of `[message]` entries.
**Fix:** (a) Replaced `EventSourceResponse` with `StreamingResponse(media_type="text/event-stream")` since frames are already pre-formatted. (b) Added frontend guard to filter `message` events from trace: `if (event && event !== "message")`.

## Image Tags (final)

| Service | Tag |
|---------|-----|
| registry-api | 0.2.70 |
| deploy-controller | 0.1.11 |
| declarative-runner | 0.1.12 |
| studio | 0.1.53 |

## Files Changed

- `services/registry-api/workflow_orchestrator.py` — `_resolve_agent_environment()`
- `services/registry-api/routers/agents.py` — set `llm_provider_id` FK on create/update
- `services/registry-api/routers/deployments.py` — write secret to agent namespace
- `services/registry-api/schemas.py` — `DeploymentResponse` LLM fields + `AgentRunResponse` output fields
- `services/deploy-controller/manifest_builder.py` — `_ENV_NAME_MAP` for env var names
- `services/declarative-runner/workflow_executor.py` — `memory_context` param
- `services/declarative-runner/main.py` — `StreamingResponse` instead of `EventSourceResponse`
- `sdk/agentshield_sdk/llm.py` — explicit `region_name` + 300s read timeout
- `studio/src/pages/ProvidersPage.tsx` — lowercase credential keys + correct Bedrock model IDs
- `studio/src/pages/WorkflowBuilderPage.tsx` — render output + extended polling
- `studio/src/api/registryApi.ts` — `AgentRunItem` output/input/error_message fields
- `studio/src/components/playground/ChatPane.tsx` — filter `message` events from trace
- `charts/agentshield/charts/registry-api/templates/rbac.yaml` — ClusterRole

### 12. TracePanel crashes on non-string tool results (blank screen)

**Where:** `studio/src/components/playground/TracePanel.tsx`, `studio/src/components/playground/ChatPane.tsx`, `sdk/agentshield_sdk/streaming.py`
**Problem:** Fix #11 unmasked a latent bug. Before the fix, SSE double-wrapping caused all events to arrive as `event: "message"` — the ChatPane filter skipped them, so TracePanel never saw `tool_call_end` events. After the fix, events arrive with correct types. TracePanel renders `ev.result.slice(0, 40)` but tool results can be dicts/lists (not strings). `.slice()` on a non-string throws `TypeError`, crashing React. No ErrorBoundary existed, so the entire app unmounted → blank white screen.
**Fix:** (a) `String()` coercion in TracePanel before `.slice()`. (b) ChatPane coerces `result`/`content`/`tool_name` to string via `String()` before passing to trace. (c) SDK `streaming.py` now `json.dumps()` dict/list results instead of passing raw objects. (d) Added `ErrorBoundary` component wrapping Routes in `App.tsx` to prevent future blank screens.

## Lessons

1. **Namespace isolation is real.** K8s secrets are namespace-scoped — always write to the namespace where the consumer pod runs.
2. **Pydantic is strict on field names.** Sending `AWS_ACCESS_KEY_ID` when the model expects `aws_access_key_id` silently drops the value. Use a `model_validator` or document the exact key format.
3. **Bedrock model IDs aren't obvious.** Newer Claude models require the `us.` cross-region inference prefix. Always validate with `list_foundation_models()` + a test `converse()` call.
4. **Poll duration must match execution time.** If the backend operation takes 2+ minutes, a 30s poll window guarantees the UI shows stale state.
5. **Response schemas must include data the UI needs.** If the DB column exists but the API response schema doesn't serialize it, the frontend can never display it.
6. **Don't double-wrap SSE.** If `format_sse()` already produces `event: X\ndata: {...}\n\n`, use raw `StreamingResponse`, not `EventSourceResponse` which adds its own framing.
7. **Never trust TypeScript `as` casts at runtime.** `payload.result as string` does zero runtime validation — the value stays whatever type the JSON parser produced. Always `String()` coerce before calling string methods.
8. **Add an ErrorBoundary.** A React app without one produces a blank white screen on any render error. The actual error is only visible in DevTools console — users see nothing.

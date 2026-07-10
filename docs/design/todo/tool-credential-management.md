# Tool Credential Management — End-to-End Design

**Status:** Implemented (all 7 phases)
**Last updated:** 2026-07-09
**Related:** Decision 15 (Composio pattern), PRD FR-092/FR-099/FR-102, GAP-bugs P1-09

## Context

Tools that call external APIs (Web Search via Serper.dev, Slack webhooks, etc.) need API keys. Today, the platform has no way for users to provide, store, or resolve these credentials. The `AuthConfig` model, DB table, and CRUD API were built in the initial schema (Decision 15, "Composio pattern"), but the runtime resolution and UI were never implemented.

**User symptom:** Agent with Web Search tool says "I don't have access to web browsing tools." Root cause has two layers:
1. Tool description not passed to LLM (fixed — declarative-runner 0.1.18)
2. Even if the LLM calls the tool, the API key `{{serper_api_key}}` is sent as a literal string → 401 from Serper.dev

**What exists vs. what's missing:**

| Layer | Status |
|-------|--------|
| `auth_configs` DB table + AuthConfig model | Built (migration 0001) |
| CRUD API `/api/v1/auth-configs/` | Built (auth_configs.py) |
| `Tool.auth_config_id` FK | Built |
| Schemas (AuthConfigCreate/Update/Response) | Built |
| Studio: Credentials page | Missing |
| Studio: Tool form credential dropdown | Missing |
| API: auto-create K8s Secret from user input | Missing |
| Deploy-controller: mount tool secrets into pod | Missing |
| Runner + SDK: resolve credentials at call time | Missing |
| Seed data: default AuthConfig for dev | Missing |
| DELETE guard (block if tools reference it) | Missing |
| `updated_at` not bumped on PUT | Bug |

---

## User Journey (target state)

**Persona:** Agent builder who wants their agent to use Web Search.

1. User has a Serper.dev API key
2. Goes to **Studio → Settings → Credentials**, clicks "Add Credential"
3. Enters name "Serper", picks type "API Key", enters the key name (`serper_api_key`) and value, saves
4. Goes to **Tools → Web Search → Edit**, picks "Serper" from the Credential dropdown, saves
5. Creates an agent with Web Search, deploys, chats — tool calls work with the real API key

**Credential rotation:** User updates the credential value in Studio. Backend updates the K8s Secret. Next agent pod restart picks up the new key. No tool or agent reconfiguration.

---

## Design

### Phase 1: API — K8s Secret auto-creation

**Problem:** The current `POST /auth-configs/` accepts a `k8s_secret_ref` string — the user must pre-create the K8s Secret via `kubectl`. That's unrealistic for most users.

**Change:** Accept a `credentials` dict (key-value pairs) in the create/update payload. The API auto-creates a K8s Secret in the `agentshield` namespace and stores the generated secret name as `k8s_secret_ref`. The raw credentials are never stored in Postgres or returned by the API.

```
POST /api/v1/auth-configs/
{
  "name": "Serper",
  "type": "api_key",
  "credentials": {"serper_api_key": "actual-key-value-here"},
  "owner_team": "platform"
}
```

Backend:
1. Creates K8s Secret `auth-config-{uuid}` in namespace `agentshield` with the provided key-value pairs
2. Stores `k8s_secret_ref = "auth-config-{uuid}"` on the AuthConfig row
3. Returns `AuthConfigResponse` (no secret ref, no credentials — write-only)

On update: if `credentials` is provided, update the existing K8s Secret's data.
On delete: delete the K8s Secret too (after guard check).

**Files:**
- `services/registry-api/routers/auth_configs.py` — add `credentials` handling + K8s Secret CRUD
- `services/registry-api/schemas.py` — add `credentials: dict[str, str] | None` to `AuthConfigCreate` and `AuthConfigUpdate`
- `services/registry-api/k8s.py` — add `create_secret()`, `update_secret()`, `delete_secret()` helpers (this file already exists with K8s client code)

### Phase 2: API hardening

Fix three bugs in the existing auth_configs router:

1. **DELETE guard:** Before deleting, check if any Tool or MCPServer references this AuthConfig. Return 409 with the list of referencing tools if so.
2. **FK existence check on tool create/update:** When `auth_config_id` is provided, verify the AuthConfig exists before committing. Return 422 if not found.
3. **`updated_at` bump:** Set `config.updated_at = func.now()` in the PUT handler.

**Files:**
- `services/registry-api/routers/auth_configs.py` — delete guard + updated_at
- `services/registry-api/routers/tools.py` — auth_config_id existence check

### Phase 3: Deploy-controller — mount tool credentials into agent pods

**Problem:** Agent pods have no access to tool credentials. The declarative runner can't resolve `{{serper_api_key}}` because the K8s Secret value isn't available in the pod.

**Design:** When the deploy-controller builds the pod manifest, it:
1. Reads the agent's tools (via `AgentTool` join → `Tool.auth_config_id` → `AuthConfig.k8s_secret_ref`)
2. For each distinct `k8s_secret_ref`, adds an `envFrom: secretRef` entry to the agent container spec
3. All secret keys become env vars in the pod (e.g., `serper_api_key=actual-key-value`)

This is a standard K8s pattern. The deploy-controller already has the ServiceAccount and RBAC to read secrets across namespaces.

**Alternative considered:** Having the runner call the registry API at tool-call time to resolve credentials. Rejected because: (a) adds latency per tool call, (b) requires the registry API to read and return raw secret values (security risk), (c) the K8s-native `envFrom` pattern is simpler and well-understood.

**Trade-off:** Credential rotation requires pod restart. Acceptable — `POST /deployments/{id}` with `action=upgrade` already handles this, and we can add a "re-deploy affected agents" button later.

**Data flow for deploy:**
- `POST /deployments/` → deploy-controller reconciler picks up the deployment
- Reconciler calls registry API: `GET /agents/{name}/tools` → gets tool list with `auth_config_id`
- For each unique `auth_config_id`, calls: `GET /auth-configs/{id}` (need to return `k8s_secret_ref` on an internal-only endpoint or field)
- `manifest_builder.build_deployment()` adds `envFrom` entries for each secret

**Files:**
- `services/deploy-controller/manifest_builder.py` — add `envFrom` secret refs to agent container
- `services/deploy-controller/reconciler.py` — fetch tool auth configs during reconciliation
- `services/registry-api/routers/auth_configs.py` — new internal endpoint or param to return `k8s_secret_ref` for service-identity callers

### Phase 4: Runtime resolution — substitute credentials in headers

**Problem:** `HttpToolNodeExecutor.as_tool_callable()` passes `self.headers` as-is to httpx. Headers with `{{var}}` placeholders (like `"X-API-KEY": "{{serper_api_key}}"`) are sent as literal strings.

**Fix:** Before making the HTTP call, substitute `{{var}}` placeholders in headers from `os.environ`. Phase 3 makes the credential values available as env vars; this phase consumes them.

```python
# In as_tool_callable() → http_tool_fn():
resolved_headers = {
    k: executor._substitute_vars(v, dict(os.environ))
    if "{{" in v else v
    for k, v in executor.headers.items()
}
```

Same fix needed in the SDK's `HttpToolExecutor.as_tool_callable()` for custom-container agents.

**Files:**
- `services/declarative-runner/node_executors.py` — substitute vars in headers from env
- `sdk/agentshield_sdk/tool_executor.py` — same pattern

### Phase 5: Studio — Credentials page

New CRUD page under Settings → Credentials. Follow the ToolsPage pattern (react-hook-form + zod).

**List view:** Table with columns: Name, Type, Linked Tools (count), Created. Actions: Edit, Delete.

**Create/Edit form:**
- Name (text input)
- Type (select: API Key, Bearer Token, OAuth2, mTLS)
- Credentials section (dynamic based on type):
  - API Key: key name + key value (password input)
  - Bearer: token value (password input)
  - OAuth2: client_id + client_secret + token_url
  - mTLS: cert + key (file upload or paste) — future, can defer
- Security note: "Stored as a Kubernetes Secret. Values are write-only — they can't be retrieved after saving."

**Delete:** Confirmation dialog. If tools reference it, show the list and block deletion (API returns 409).

**Files:**
- `studio/src/pages/CredentialsPage.tsx` — new page
- `studio/src/api/registryApi.ts` — add `createAuthConfig()`, `updateAuthConfig()`, `deleteAuthConfig()` functions; expand `AuthConfig` interface
- `studio/src/components/Sidebar.tsx` — add "Credentials" nav item under Settings (line 62)
- `studio/src/App.tsx` — add `/credentials` route

### Phase 6: Studio — Tool form credential dropdown

Add a "Credential" dropdown to the existing tool create/edit form on ToolsPage.

- Fetches auth configs via `listAuthConfigs()`
- Shows as a `<select>` with option "None" + list of auth config names
- Saves as `auth_config_id` on the tool

Also add a visual indicator on tools that have `{{var}}` in headers but no `auth_config_id` linked — a warning badge: "Needs credential."

**Files:**
- `studio/src/pages/ToolsPage.tsx` — add credential dropdown to ToolForm
- `studio/src/api/registryApi.ts` — add `auth_config_id` to `CreateToolPayload` and `RegistryTool` interfaces

### Phase 7: Seed data

Update `scripts/seed-defaults.sh` to:
1. Create a default AuthConfig "Serper (dev)" with a placeholder key (or env-sourced key)
2. Link it to the `web_search` tool via `auth_config_id`

This makes the dev/demo environment work out of the box if a real Serper key is provided via env var.

**File:**
- `scripts/seed-defaults.sh`

---

## Build Order

Phases 1-4 are the backend vertical slice. Phases 5-6 are the frontend. Phase 7 is seed data.

**Minimum viable:** Phases 1 + 3 + 4 + 7 gets Web Search working for dev (no UI, but seed data handles the config).

**Full self-service:** Add Phases 2 + 5 + 6 so users can manage credentials from Studio.

**Recommended order:** 1 → 2 → 3 → 4 → 7 → 5 → 6

Each phase can be deployed and verified independently.

---

## Image tag bumps

| Phase | Service | Tag variable |
|-------|---------|-------------|
| 1, 2 | registry-api | `REGISTRY_API_TAG` |
| 3 | deploy-controller | `DEPLOY_CONTROLLER_TAG` |
| 4 | declarative-runner | `DECLARATIVE_RUNNER_TAG` |
| 5, 6 | studio | `STUDIO_TAG` |

---

## Verification

1. **Phase 1:** `POST /auth-configs/` with `credentials` → verify K8s Secret created (`kubectl get secret auth-config-{id} -o yaml`)
2. **Phase 2:** `DELETE /auth-configs/{id}` when tools reference it → 409. Create tool with bad `auth_config_id` → 422.
3. **Phase 3:** Deploy agent with Web Search linked to AuthConfig → verify pod env vars contain credential (`kubectl exec ... -- env | grep serper`)
4. **Phase 4:** Chat with deployed agent, ask "what's the weather in Austin" → agent calls Web Search → gets real results
5. **Phase 5-6:** Create credential in Studio UI, link to tool, deploy agent, chat → end-to-end works
6. **Phase 7:** Fresh `deploy-cpe2e.sh` → Web Search works out of the box with seeded dev credential

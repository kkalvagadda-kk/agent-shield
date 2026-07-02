# Studio API Additions — registryApi.ts

**File:** `studio/src/api/registryApi.ts`
**Phase:** A (changes), B (additions)

---

## Phase A Changes

### `listAgents` — status param made conditional

```typescript
// Before
export const listAgents = async (
  limit = 100,
  offset = 0,
  status = "active"
): Promise<Paginated<Agent>> => {
  const { data } = await http.get<Paginated<Agent>>("/agents/", {
    params: { limit, offset, status },  // always sends ?status=active
  });
  return data;
};

// After (A3)
export const listAgents = async (
  limit = 100,
  offset = 0,
  status?: string           // <-- optional, no default
): Promise<Paginated<Agent>> => {
  const { data } = await http.get<Paginated<Agent>>("/agents/", {
    params: { limit, offset, ...(status !== undefined ? { status } : {}) },
  });
  return data;
};
```

**Call sites:**
- `AgentsPage`, `Sidebar` (default call) → `listAgents()` → adds `status=active` by caller
- `AdminArtifactsPage` → `listAgents(200, 0, undefined)` → no status filter, returns all
- `AdminPublishRequestsPage` enrichment → `listAgents(200, 0, "active")` — only need active names

Wait — if `status` has no default, existing callers that do `listAgents()` will no longer send `?status=active`. All callers must be audited:

| Call site | Current behavior | After change |
|-----------|-----------------|--------------|
| `AgentsPage.tsx` | `listAgents()` → active only | Must change to `listAgents(100, 0, "active")` |
| `Sidebar.tsx` (B4) | `listAgents(200, 0, "active")` | Already explicit — fine |
| `AdminArtifactsPage.tsx` (A5) | `listAgents(200, 0, undefined)` | No filter — correct |
| `AdminPublishRequestsPage.tsx` (A3) | `listAgents(200, 0, "active")` | Explicit — fine |
| `CatalogPage.tsx` | May call listAgents | Audit required |

**Action:** When implementing A3, grep for `listAgents(` and add explicit `"active"` where the default was being relied upon, except `AdminArtifactsPage` which passes `undefined`.

### `approvePublishRequest` — `grantee_teams` optional

```typescript
// Before
export const approvePublishRequest = async (
  id: string,
  body: { grantee_teams: string[]; expires_at?: string }
): Promise<{ approved: boolean; grants_created: number }> => { ... };

// After (A4)
export const approvePublishRequest = async (
  id: string,
  body: { grantee_teams?: string[]; expires_at?: string } = {}
): Promise<{ approved: boolean; grants_created: number }> => { ... };
```

---

## Phase A New Functions

### `listTools` — add `limit` + `offset` overload (A3)

The current signature is `listTools(params?: { team?: string })`. A3's enrichment call needs to paginate:

```typescript
// Add overloaded signature or change to positional params:
export const listTools = async (
  limit = 100,
  offset = 0,
  params?: { team?: string }
): Promise<Paginated<RegistryTool>> => {
  const { data } = await http.get<Paginated<RegistryTool>>('/tools/', {
    params: { limit, offset, ...params },
  });
  return data;
};
```

Existing callers that pass `{ team: "..." }` must be updated to `listTools(100, 0, { team: "..." })`.

If the signature change is too risky, use a separate `listToolsPaginated(limit, offset)` function.

### `listSkills` — same pattern (A3)

```typescript
export const listSkills = async (
  limit = 100,
  offset = 0,
  params?: { team?: string }
): Promise<Paginated<Skill>> => {
  const { data } = await http.get<Paginated<Skill>>('/skills/', {
    params: { limit, offset, ...params },
  });
  return data;
};
```

### `listAllDeployments` (A7)

```typescript
export const listAllDeployments = async (
  status?: string,
  limit = 100
): Promise<Paginated<Deployment>> => {
  const { data } = await http.get<Paginated<Deployment>>("/deployments/", {
    params: { limit, ...(status ? { status } : {}) },
  });
  return data;
};
```

Backed by existing `GET /api/v1/deployments/` in `routers/deployments.py`. The `Deployment` interface is already exported from `registryApi.ts`.

---

## Phase B New Functions

### `startAgentChat` (B3)

```typescript
export interface AgentChatStart {
  run_id: string;
  session_id: string;
  stream_url: string;
  agent_name: string;
  deployment_id: string;
}

export const startAgentChat = async (
  name: string,
  body: { message: string; session_id?: string }
): Promise<AgentChatStart> => {
  const { data } = await http.post<AgentChatStart>(`/agents/${name}/chat`, body);
  return data;
};
```

The `stream_url` in the response is a path like `/api/v1/agents/{name}/chat/{run_id}/stream`. The frontend appends `?token=<kc.token>` before opening `EventSource`.

---

## Interface Additions

### `Agent` (existing — verify these fields are present)

```typescript
export interface Agent {
  // ... existing fields ...
  publish_status: string;   // Already present
}
```

No change needed — `publish_status` is already in the `Agent` interface.

### `Deployment` (existing — verify `agent_name` is present)

```typescript
export interface Deployment {
  // ... existing fields ...
  agent_name: string | null;  // Already present
}
```

No change needed.

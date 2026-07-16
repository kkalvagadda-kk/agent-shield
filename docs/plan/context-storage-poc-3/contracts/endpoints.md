# POC-3 — API Contracts

## Registry-API HTTP endpoints (added to `routers/me.py`, prefix `/api/v1/me`)

Both are `Depends(require_user)` and **caller-scoped**: `user_id = caller.sub`. No id in the
path — a user can only ever read/write their OWN row (ownership is structural, not checked).

### `GET /api/v1/me/preferences`

Returns the caller's preferences row, or an all-null default if none exists.

Response `200` (`UserPreferences`):
```json
{
  "response_length": "concise",
  "tone": null,
  "format": "bulleted",
  "language": "auto",
  "expertise": "expert",
  "updated_at": "2026-07-16T12:00:00Z"
}
```
No row yet → all five preset fields `null`, `updated_at": null`. `401` if unauthenticated.

### `PUT /api/v1/me/preferences`

Upsert the caller's row (`INSERT … ON CONFLICT (user_id) DO UPDATE`, `updated_at = now()`).

Request body (`UserPreferencesUpdate` — every field Optional):
```json
{ "response_length": "concise", "tone": null, "format": "bulleted",
  "language": "es", "expertise": "expert" }
```
- Any value outside `contracts/enums.md` → **422** (Pydantic enum). `updated_at` is
  server-managed and ignored/absent on input.
- Omitted field vs explicit `null`: PUT is a full replace of the five preset columns — an
  omitted field is treated as `null` (cleared). The frontend always sends all five.

Response `200`: the persisted `UserPreferences` (same shape as GET, incl. `updated_at`).

## Pod dispatch payload — the `user_directive` field (registry → runner)

The composed directive rides as ONE new bounded field in the pod's `/chat/stream` JSON body,
built in `pod_stream.py::stream_pod_chat_frames`:

```json
{
  "message": "…",
  "thread_id": "…",
  "conversation_id": "…",
  "scope": "agent",
  "user_directive": "[Advisory user preferences — …] Keep answers brief and to the point. Use bullet points."
}
```

- **Field name: `user_directive`** (chosen this plan). Omitted when `None` (daemon / no prefs) —
  the runner then behaves exactly as today.
- It is a **platform-composed string only**. The runner MUST NOT parse it, read `user_profiles`,
  or compose from raw input — it appends the string to the system prompt and nothing else.
- Identity still rides in headers (`x-user-sub`, `x-agent-team`, `x-deployment-id`) — unchanged.

## Runner request schema (`declarative-runner/main.py::ChatRequest`)

```python
class ChatRequest(BaseModel):
    message: str = ""
    thread_id: str | None = None
    conversation_id: str | None = None
    scope: str = "agent"
    workflow_run_id: str | None = None
    metadata: dict | None = None
    user_directive: str | None = None   # NEW — platform-composed advisory; None = no change
```

## Frontend API client (`studio/src/api/registryApi.ts`)

```ts
export interface UserPreferences {
  response_length: string | null;
  tone: string | null;
  format: string | null;
  language: string | null;
  expertise: string | null;
  updated_at?: string | null;
}

export const getMyPreferences = async (): Promise<UserPreferences> => {
  const { data } = await http.get<UserPreferences>("/me/preferences");
  return data;
};

export const updateMyPreferences = async (
  prefs: Omit<UserPreferences, "updated_at">,
): Promise<UserPreferences> => {
  const { data } = await http.put<UserPreferences>("/me/preferences", prefs);
  return data;
};
```

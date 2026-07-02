# Phase A-B-C: Data Model

**No new database tables in Phases A, B, or C.**

All three phases reuse existing tables. This document describes which tables each phase reads/writes and the exact query patterns.

---

## Existing Tables Used

### `agents`

| Column | Type | Phase usage |
|--------|------|-------------|
| `id` | UUID PK | Grant lookup by asset_id |
| `name` | str | Route param for `/agents/{name}/chat` |
| `team` | str | Owner-team access bypass check (B1) |
| `status` | str | Filter `status='active'` in chat endpoint |
| `publish_status` | str | A3 enrichment, A5 display, A6 catalog filter |
| `description` | str | A3/A5 display |

**Phase A:** Read only (enrichment, display).
**Phase B (B1):** `SELECT` by `name` + `status='active'` to validate agent exists before chat.

---

### `publish_requests`

| Column | Type | Phase usage |
|--------|------|-------------|
| `id` | UUID PK | Approve/reject endpoint key |
| `asset_id` | UUID | Looked up via client-side name map (A3) |
| `asset_type` | str | Display (A3, A5) |
| `status` | str | Filter: `pending` / `approved` / `rejected` |
| `submitted_by` | str | Display |
| `submitted_at` | datetime | Display |

**Phase A (A3, A4):** A3 reads; A4 changes `grantee_teams` in `PublishRequestApprove` Pydantic model only (no schema column change). Router write: `UPDATE publish_requests SET status='approved' WHERE id=:id`.

---

### `asset_grants`

| Column | Type | Phase usage |
|--------|------|-------------|
| `id` | UUID PK | Grant lookup |
| `asset_id` | UUID | Join key for grant check (B1) |
| `asset_type` | str | Filter `asset_type='agent'` (B1) |
| `grantee_team` | str | Match against caller's team (B1) |
| `expires_at` | datetime? | Expiry check in B1 |
| `revoked_at` | datetime? | Null check — if not null, treat as no grant |

**Phase B (B1):** `SELECT FROM asset_grants WHERE asset_id=:agent_id AND asset_type='agent' AND grantee_team=:team AND revoked_at IS NULL LIMIT 1`. If `expires_at IS NOT NULL` and past current time, deny.

---

### `user_team_assignments`

| Column | Type | Phase usage |
|--------|------|-------------|
| `user_sub` | str | Keycloak sub claim from JWT |
| `team_name` | str | Maps to `asset_grants.grantee_team` |

**Phase B (B1):** `SELECT team_name FROM user_team_assignments WHERE user_sub=:sub`. One-row lookup to resolve caller → team. Used to check grant eligibility.

---

### `deployments`

| Column | Type | Phase usage |
|--------|------|-------------|
| `id` | UUID PK | Display |
| `agent_id` | UUID | Grant check lookup + running-deployment check (B1) |
| `agent_name` | str | Display (A7), sidebar chat links (B4) |
| `status` | str | Filter `status='running'` for A7 + B1 + sidebar |
| `deployed_at` | datetime | Display (A7) |
| `error_message` | str? | Display (A7) |

**Phase A (A7):** `GET /api/v1/deployments/?status=running` — already exists in `routers/deployments.py`.
**Phase B (B1):** `SELECT FROM deployments WHERE agent_id=:id AND status='running' ORDER BY deployed_at DESC LIMIT 1`. Returns 503 if no row found.
**Phase B (B4):** Sidebar fetches `listAllDeployments("running", 100)` to determine which granted agents show Chat vs Deploy button.

---

### `playground_runs`

| Column | Type | Phase usage |
|--------|------|-------------|
| `id` | UUID PK | `run_id` returned to client; `stream_url` key |
| `user_id` | str | Keycloak sub claim; ownership check in stream endpoint |
| `agent_name` | str | Validated against route param |
| `context` | str | **Set to `"production"`** (not `"playground"`) for chat runs |
| `sandbox` | bool | **Set to `False`** for chat runs |
| `input_message` | str | User's chat message |
| `status` | str | `"running"` on create; future: update to `"completed"` |
| `started_at` | datetime | Set on create |

**Phase B (B1):** INSERT on `POST /agents/{name}/chat`. SELECT by `id` + ownership check on `GET /agents/{name}/chat/{run_id}/stream`.

The `context='production'` value ensures these runs are excluded from the playground HITL queue (which filters `context='playground'`) and included in the production HITL queue. This preserves the HITL context separation confirmed in suite-8 T-S8-006.

---

## Schema Changes

Only one schema change across all three phases:

**`services/registry-api/schemas.py` — `PublishRequestApprove`:**
```python
# Before (Phase A3 baseline)
class PublishRequestApprove(BaseModel):
    grantee_teams: list[str]
    expires_at: Optional[datetime] = None

# After (A4)
class PublishRequestApprove(BaseModel):
    grantee_teams: list[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None
```

No Alembic migration needed — this is a Pydantic request body schema, not a DB column.

---

## Teams-Summary API (used in B2, B4)

Phase B reads `GET /api/v1/admin/teams-summary` — an existing endpoint that returns:
```json
[
  {
    "name": "platform",
    "members": [{ "user_sub": "abc123", "username": "kalyan" }],
    "grants": [
      { "asset_type": "agent", "asset_name": "customer-intelligence-agent", "asset_id": "uuid..." }
    ]
  }
]
```

`MyAgentsPage` and `Sidebar` use this to determine which agents the current user's team has been granted. This endpoint already exists — no backend changes.

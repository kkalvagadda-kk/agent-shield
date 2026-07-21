# Data Model — MCP as a Tool Source, Phase 1

Baseline observed in this worktree (verify with quickstart.md before creating anything): the alembic head is **`0071`** (`0071_backfill_webhook_clients_to_applications.py`, `down_revision = "0070"`, which chains `0070 → 0068`). There is **no `0069`** file (a former `0069` was superseded/renumbered). This plan's migration is therefore **`0072`**, `down_revision = "0071"` — not `0069` as an earlier draft assumed (research.md #11).

## Migration `0072_mcp_server_fields.py`

File: `services/registry-api/alembic/versions/0072_mcp_server_fields.py`

Idempotent (guarded `ADD COLUMN` via inspector existence checks), following the pattern already used by 0063 (`side_effecting`) and 0068 (guarded checks). No downtime: every new column is nullable-or-defaulted, so existing rows never need a backfill to satisfy a `NOT NULL` without a default.

### `mcp_servers` — 6 new columns

| Column | Type | Nullable | Default | Purpose |
|---|---|---|---|---|
| `identity_mode` | `VARCHAR(32)` | NOT NULL | `'none'` | `'on_behalf_of'` \| `'service_identity'` \| `'none'`. `'none'` for every external server (identity modes are an internal-server concept). CHECK `ck_mcp_servers_identity_mode`. Settable in Phase 1 but has **zero runtime effect** until Phase 2 (FR-MCP-21 is externally blocked). |
| `is_external` | `BOOLEAN` | NOT NULL | `false` | Drives the mandatory output-scan CALL for untrusted-source results (FR-MCP-31; the scan *action* is stubbed in Phase 1 — research.md B14) and gates which identity modes are legal (cross-field note below). |
| `transport_config` | `JSONB` | NULL | `NULL` | Transport-specific extra config. Phase 1 populates only HTTP-relevant keys (e.g. extra headers beyond the auth config, a connect-timeout override); `stdio`'s `command`/`args`/`env` shape is reserved for Phase 3 and not validated in Phase 1. |
| `health_detail` | `JSONB` | NOT NULL | `'{}'::jsonb` | `{"last_error": str \| null, "last_success_at": iso8601 \| null, "consecutive_failures": int, "schema_drift": [ {"tool_name": str, "detected_at": iso8601}, ... ]}`. Written by the discover/sync path in Phase 1 (§ below). The periodic health-check loop that keeps it fresh between syncs is Phase 2 (FR-MCP-22). Note: the MCP Proxy's `/internal/discover` returns `health_detail` as a plain **string** (the failure reason); registry-api composes that string into `last_error` and maintains the rest of this JSONB shape (contracts). |
| `list_changed_supported` | `BOOLEAN` | NOT NULL | `false` | Whether the server advertised `notifications/tools/list_changed` during `initialize`. Recorded in Phase 1 discovery for forward-compatibility; the actual subscription (FR-MCP-07) is Phase 2. |
| `scan_results` | `BOOLEAN` | NOT NULL | `true` | Per-server opt-out of the per-tool-call output-scan CALL (FR-MCP-31) — **internal servers only**. `governed_tool` ignores this and always scans when `is_external = true` (enforced in code, not by a DB constraint — plan.md Task 11 / research.md B15). |

Additional CHECK constraint:
```sql
ALTER TABLE mcp_servers ADD CONSTRAINT ck_mcp_servers_identity_mode
  CHECK (identity_mode IN ('on_behalf_of', 'service_identity', 'none'));
```
No CHECK enforces `is_external = true ⇒ identity_mode = 'none'` at the DB layer (deliberate — a future admin correction to a misclassified server shouldn't be blocked by a constraint). The `MCPServerCreate`/`MCPServerUpdate` Pydantic `model_validator`s enforce it at the API boundary instead (reject `is_external=true` with `identity_mode != 'none'`).

The pre-existing `mcp_servers` columns (`id`, `name` **unique**, `description`, `server_url`, `transport` [CHECK `streamable_http|stdio`], `auth_config_id` FK, `owner_team`, `status` [CHECK `connected|disconnected|error`], `last_synced_at`, `discovered_tool_count`, `created_at`, `updated_at`) and the two lifecycle invariants commented on the `MCPServer` model (`name` immutable; DELETE blocked while any child tool is bound) are unchanged by this migration — the invariants are enforced by the Task 6 router, not new DDL.

### `tools` — 1 new column

| Column | Type | Nullable | Default | Purpose |
|---|---|---|---|---|
| `pii_deanonymize_allowed` | `BOOLEAN` | NOT NULL | `false` | Decision 27 / FR-MCP-51's per-tool de-anonymize permission. Applies to **every** tool type, not just `mcp_tool` (research.md B4). Fail-closed default. Mirrors `side_effecting` (0063). |

Not MCP-specific — it lives on the shared `tools` table. Included here because Decision 27's gate is part of this Phase 1 slice.

### Migration skeleton

```python
"""0072 — MCP server runtime fields + Tool.pii_deanonymize_allowed (Decision 27).

Six additive MCPServer columns (identity_mode, is_external, transport_config,
health_detail, list_changed_supported, scan_results) back the MCP Proxy runtime.
One additive Tool column (pii_deanonymize_allowed) backs the generic per-tool-call
de-anonymize gate (Decision 27) — applies to every tool type, not only mcp_tool.

Idempotent: every ADD COLUMN is guarded by an inspector existence check (mirrors
0063's side_effecting pattern) so re-running against a partially-applied DB is safe.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect as sa_inspect

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    return {c["name"] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    mcp_cols = _existing_columns("mcp_servers")
    if "identity_mode" not in mcp_cols:
        op.add_column("mcp_servers", sa.Column("identity_mode", sa.String(32),
                      nullable=False, server_default="none"))
        op.create_check_constraint(
            "ck_mcp_servers_identity_mode", "mcp_servers",
            "identity_mode IN ('on_behalf_of', 'service_identity', 'none')")
    if "is_external" not in mcp_cols:
        op.add_column("mcp_servers", sa.Column("is_external", sa.Boolean(),
                      nullable=False, server_default=sa.false()))
    if "transport_config" not in mcp_cols:
        op.add_column("mcp_servers", sa.Column("transport_config",
                      postgresql.JSONB(), nullable=True))
    if "health_detail" not in mcp_cols:
        op.add_column("mcp_servers", sa.Column("health_detail", postgresql.JSONB(),
                      nullable=False, server_default=sa.text("'{}'::jsonb")))
    if "list_changed_supported" not in mcp_cols:
        op.add_column("mcp_servers", sa.Column("list_changed_supported", sa.Boolean(),
                      nullable=False, server_default=sa.false()))
    if "scan_results" not in mcp_cols:
        op.add_column("mcp_servers", sa.Column("scan_results", sa.Boolean(),
                      nullable=False, server_default=sa.true()))

    tool_cols = _existing_columns("tools")
    if "pii_deanonymize_allowed" not in tool_cols:
        op.add_column("tools", sa.Column("pii_deanonymize_allowed", sa.Boolean(),
                      nullable=False, server_default=sa.false()))


def downgrade() -> None:
    tool_cols = _existing_columns("tools")
    if "pii_deanonymize_allowed" in tool_cols:
        op.drop_column("tools", "pii_deanonymize_allowed")

    mcp_cols = _existing_columns("mcp_servers")
    for col, constraint in [
        ("scan_results", None), ("list_changed_supported", None),
        ("health_detail", None), ("transport_config", None), ("is_external", None),
        ("identity_mode", "ck_mcp_servers_identity_mode"),
    ]:
        if col in mcp_cols:
            if constraint:
                op.drop_constraint(constraint, "mcp_servers", type_="check")
            op.drop_column("mcp_servers", col)
```

---

## `Tool` row shape for `type='mcp_tool'`

No new columns beyond `pii_deanonymize_allowed` — `mcp_server_id` and `mcp_tool_name` already exist (migration `0001`). Field-by-field for a discovered MCP tool row:

| Field | Value at discovery time |
|---|---|
| `name` | `"{server_name}__{mcp_tool_name}"` (auto-namespaced — Open Question 3, resolved). Platform-unique identifier the LLM calls by. Because `name` is derived from `MCPServer.name`, the server `name` is **immutable** (model invariant; Task 6 rejects a rename). |
| `mcp_tool_name` | The raw upstream tool name from `tools/list` (e.g. `"search_issues"`). Used for the actual `tools/call` dispatch — never namespaced. |
| `mcp_server_id` | FK to the owning `MCPServer`. |
| `type` | `"mcp_tool"` |
| `input_schema` | The server's JSON Schema, verbatim from `tools/list`'s `inputSchema`. |
| `risk_level` | `"low"` (D4 default; admin can raise via `PUT /api/v1/tools/{id}`, unchanged). |
| `side_effecting` | `true` (conservative default — an MCP tool's real side effects are unknown; `mcp_tool` is not added to `infer_side_effecting`'s GET-only carve-out). |
| `owner_team` | `MCPServer.owner_team` — the **only** new work needed for team-scoping to apply automatically (architecture doc §"Team-Scoping"). |
| `publish_status` | Column default for a newly-created `Tool` (`'published'` per `models.py`) — unchanged, no MCP override. |
| `pii_deanonymize_allowed` | `false` (column default). |
| `status` | `"active"` at first discovery. |
| `auth_config_id` | `NULL` (a `Tool` row's own `auth_config_id` is unrelated to `MCPServer.auth_config_id`; the proxy resolves server-level creds itself). |
| `http_*` / `python_code` | `NULL` (not applicable to this type). |

### Re-sync (`POST /api/v1/mcp-servers/{id}/sync`) upsert semantics

For each tool the server currently reports in `tools/list`:
- **New tool** (no existing row for this `mcp_server_id` + `mcp_tool_name`) → insert as above.
- **Existing tool, unchanged `input_schema`** → no tool-row write needed (server-row timestamp bump only). If it was `inactive` (previously vanished, now back), flip `status='active'`.
- **Existing tool, changed `input_schema`** → **auto-apply** the new schema (`Tool.input_schema` overwritten immediately — Open Question 5, resolved) **and** flag it: append `{"tool_name": ..., "detected_at": ...}` to `MCPServer.health_detail['schema_drift']`. The sync does not stall for review. `POST .../sync` accepts an optional `acknowledge_schema_drift: bool` (default `false`) which, when `true`, clears the prior `schema_drift` list **before** this sync records new entries.

For each existing `Tool` row (`mcp_server_id` = this server) **not** present in the current `tools/list`:
- Set `status = 'inactive'` — **never hard-deleted** (FR-MCP-04, and the model comment: vanished tools are marked `status='inactive'`, never row-deleted). Still queryable, still bound if an agent already bound it (impact-analysis parity), but excluded from `ToolsPicker` the same way any non-`active` tool already is (existing `Tool.status` filter — no new filter logic).

> **`inactive` vs `deprecated` (resolved):** the `MCPServer`/`Tool` model comments added 2026-07-21 say vanished-upstream tools are marked **`status='inactive'`**. The design doc §8 flagged "confirm inactive vs. deprecated at build"; the authoritative model comment resolves it to **`inactive`**, and this plan uses `inactive` throughout. `active`/`inactive`/`deprecated` are all valid `ck_tools_status` values already; MCP introduces only the `active → inactive` (vanished) and `inactive → active` (reappeared) *transitions*, driven by `/sync`, not new enum values.

---

## Per-server credential Secret (path b — research.md B13)

registry-api materializes one K8s Secret per registered server so the MCP Proxy can resolve connection + credentials without a DB connection and without the master encryption key.

| Property | Value |
|---|---|
| Name | `agentshield-mcp-server-{server_id}` |
| Namespace | `agentshield-mcp` (**dedicated** — created by the mcp-proxy subchart; scopes the proxy's `get secrets` RBAC so it cannot read `agentshield-encryption`/DB creds in `agentshield-platform`) |
| Data key `connection` | JSON string: `{"server_url": str, "transport": str, "transport_config": obj\|null, "is_external": bool, "owner_team": str\|null}` |
| Data key `auth_headers` | JSON string: `{header_name: header_value, ...}` — composed by registry-api from `crypto.decrypt_json(AuthConfig.credentials_encrypted)` per `AuthConfig.type` (`bearer` → `{"Authorization": "Bearer <token>"}`; `api_key` → the creds' header name/value; `oauth2`/`mtls` → best-effort, ledgered). Empty `{}` when the server has no `auth_config_id`. |
| Writer | `services/registry-api/mcp_secrets.py::materialize_server_secret` via existing `k8s.upsert_secret` (registry-api's cluster-wide secret ClusterRole already permits cross-namespace writes — no new RBAC). |
| Written on | server register, `/sync`, and `PUT` when `auth_config_id` changes. |
| Deleted on | server DELETE (`mcp_secrets.delete_server_secret` via `k8s.delete_secret`). |
| Read by | MCP Proxy `credentials.read_server_secret` (`get` on secrets in `agentshield-mcp` only). Never decrypted by the proxy — registry-api decrypted once at materialization. |

The Secret is a *derived* artifact; the durable credential source stays the Fernet blob in `AuthConfig.credentials_encrypted` in Postgres, so a Postgres restore + a re-sync regenerates every per-server Secret.

---

## State transitions

### `MCPServer.status`

```
   (create) ──▶ disconnected  (server_default; before the first synchronous discover completes)
                     │ POST /mcp-servers (synchronous discover attempt)
           success   │   failure
        ┌────────────┴─────────────┐
        ▼                          ▼
   connected  ◀── sync ok ──   error
        │  sync fails             │ sync succeeds
        └──────▶ error ◀──────────┘
```

- `disconnected` is only the pre-first-attempt state — the row briefly exists before the synchronous discover call in the same `POST` resolves. Phase 1 has no "administratively disabled" state, so a server reaches `connected` or `error` before the create request returns (registration is not all-or-nothing).
- `connected ↔ error` transitions happen only via an explicit `/sync` (or a tool call failing) in Phase 1 — no background health loop yet (Phase 2 / FR-MCP-22). A server that dies between syncs keeps a stale `connected` — an accepted, ledgered Phase 1 gap.

### `Tool.status` (for `mcp_tool` rows; the value set is shared/unchanged)

```
 (discovered) ─▶ active ──(vanished from tools/list on /sync)──▶ inactive
                    ▲                                               │
                    └────────(reappears in a later tools/list)──────┘
                    │
                    └──(admin PUT .../tools/{id} {"status":"..."})──▶ (admin-set)
```

MCP introduces only the `active ↔ inactive` transitions (driven by `/sync`); the `active`/`inactive`/`deprecated` value set (`ck_tools_status`) is pre-existing. `type='mcp_tool'` rows are **not** independently deletable — `DELETE /api/v1/tools/{id}` on such a row is rejected `409` (Task 2), because their lifecycle is owned solely by server discovery/sync/delete.

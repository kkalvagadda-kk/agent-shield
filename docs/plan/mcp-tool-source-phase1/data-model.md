# Data Model — MCP as a Tool Source, Phase 1

Baseline observed in this worktree: latest migration is `0068_knowledge_base_rag.py` (`revision = "0068"`). This plan's migration is `0069`, `down_revision = "0068"`.

## Migration `0069_mcp_server_fields.py`

File: `services/registry-api/alembic/versions/0069_mcp_server_fields.py`

Idempotent (guarded `ADD COLUMN IF NOT EXISTS` / inspector checks), following the pattern already used by 0063 (`side_effecting`) and 0068 (guarded `CREATE EXTENSION`/table checks). No downtime: every new column is nullable-or-defaulted so existing rows never need a backfill pass to satisfy a `NOT NULL` without a default.

### `mcp_servers` — 6 new columns

| Column | Type | Nullable | Default | Purpose |
|---|---|---|---|---|
| `identity_mode` | `VARCHAR(32)` | NOT NULL | `'none'` | `'on_behalf_of'` \| `'service_identity'` \| `'none'`. `'none'` for every external server (identity modes are an internal-server concept, §4a of the requirements doc). CHECK constraint `ck_mcp_servers_identity_mode`. |
| `is_external` | `BOOLEAN` | NOT NULL | `false` | Drives the untrusted-input mandatory output-scan (FR-MCP-31) and gates which identity modes are legal (see cross-field note below). |
| `transport_config` | `JSONB` | NULL | `NULL` | Transport-specific extra config. Phase 1 only populates HTTP-relevant keys (e.g. custom headers beyond the auth config, connection timeout override); `stdio`'s `command`/`args`/`env` shape is reserved for Phase 3 and not validated in Phase 1. |
| `health_detail` | `JSONB` | NOT NULL | `'{}'::jsonb` | `{"last_error": str \| null, "last_success_at": iso8601 \| null, "consecutive_failures": int}`. Written by the discover/sync path in Phase 1 (§ below); the periodic health-check loop that keeps it fresh between syncs is Phase 2 (FR-MCP-22), not built here. |
| `list_changed_supported` | `BOOLEAN` | NOT NULL | `false` | Whether the server advertised `notifications/tools/list_changed` capability during `initialize`. Recorded in Phase 1 discovery for forward-compatibility; the actual subscription (FR-MCP-07) is Phase 2. |
| `scan_results` | `BOOLEAN` | NOT NULL | `true` | Per-server opt-out of the mandatory per-tool-call output scan (FR-MCP-31) — **internal servers only**. `governed_tool` must ignore this and always scan when `is_external = true` (enforced in code, not by a DB constraint, since a constraint can't reference "the code's own gate ordering" — see plan.md Task 8). |

Additional CHECK constraint:
```sql
ALTER TABLE mcp_servers ADD CONSTRAINT ck_mcp_servers_identity_mode
  CHECK (identity_mode IN ('on_behalf_of', 'service_identity', 'none'));
```

No CHECK enforces `is_external = true ⇒ identity_mode = 'none'` at the DB layer (deliberate — the requirements doc frames identity mode as "N/A for external" as an API/UI-level default, not a hard DB invariant, and a future admin correction to a misclassified server shouldn't be blocked by a constraint). The `MCPServerCreate`/`MCPServerUpdate` Pydantic validators enforce it at the API boundary instead (a model_validator rejecting `is_external=true` with `identity_mode != 'none'`).

### `tools` — 1 new column

| Column | Type | Nullable | Default | Purpose |
|---|---|---|---|---|
| `pii_deanonymize_allowed` | `BOOLEAN` | NOT NULL | `false` | Decision 27 / FR-MCP-51's per-tool de-anonymize permission. Applies to **every** tool type, not just `mcp_tool` (research.md B4). Fail-closed default — no tool receives real PII in its call arguments unless explicitly marked. |

This column is **not** MCP-specific — it lives on the same `tools` table every tool type already uses, exactly like `side_effecting` (migration 0063) does. It is included in this migration because Decision 27's gate is part of this Phase 1 slice, not because it is an MCP field.

### Migration skeleton

```python
"""0069 — MCP server runtime fields + Tool.pii_deanonymize_allowed (Decision 27).

Six additive MCPServer columns (identity_mode, is_external, transport_config,
health_detail, list_changed_supported, scan_results) back the MCP Proxy runtime
this migration's sibling code change adds. One additive Tool column
(pii_deanonymize_allowed) backs the generic per-tool-call de-anonymize gate
(Decision 27) — applies to every tool type, not only mcp_tool.

Idempotent: every ADD COLUMN is guarded by an inspector existence check (mirrors
0063's side_effecting backfill pattern) so re-running against a partially-applied
DB is safe.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect as sa_inspect

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    return {c["name"] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    mcp_cols = _existing_columns("mcp_servers")
    if "identity_mode" not in mcp_cols:
        op.add_column(
            "mcp_servers",
            sa.Column("identity_mode", sa.String(32), nullable=False,
                      server_default="none"),
        )
        op.create_check_constraint(
            "ck_mcp_servers_identity_mode", "mcp_servers",
            "identity_mode IN ('on_behalf_of', 'service_identity', 'none')",
        )
    if "is_external" not in mcp_cols:
        op.add_column(
            "mcp_servers",
            sa.Column("is_external", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
        )
    if "transport_config" not in mcp_cols:
        op.add_column(
            "mcp_servers",
            sa.Column("transport_config", postgresql.JSONB(), nullable=True),
        )
    if "health_detail" not in mcp_cols:
        op.add_column(
            "mcp_servers",
            sa.Column("health_detail", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
        )
    if "list_changed_supported" not in mcp_cols:
        op.add_column(
            "mcp_servers",
            sa.Column("list_changed_supported", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
        )
    if "scan_results" not in mcp_cols:
        op.add_column(
            "mcp_servers",
            sa.Column("scan_results", sa.Boolean(), nullable=False,
                      server_default=sa.true()),
        )

    tool_cols = _existing_columns("tools")
    if "pii_deanonymize_allowed" not in tool_cols:
        op.add_column(
            "tools",
            sa.Column("pii_deanonymize_allowed", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
        )


def downgrade() -> None:
    tool_cols = _existing_columns("tools")
    if "pii_deanonymize_allowed" in tool_cols:
        op.drop_column("tools", "pii_deanonymize_allowed")

    mcp_cols = _existing_columns("mcp_servers")
    for col, constraint in [
        ("scan_results", None),
        ("list_changed_supported", None),
        ("health_detail", None),
        ("transport_config", None),
        ("is_external", None),
        ("identity_mode", "ck_mcp_servers_identity_mode"),
    ]:
        if col in mcp_cols:
            if constraint:
                op.drop_constraint(constraint, "mcp_servers", type_="check")
            op.drop_column("mcp_servers", col)
```

---

## `Tool` row shape for `type='mcp_tool'`

No new columns beyond `pii_deanonymize_allowed` above — `mcp_server_id` and `mcp_tool_name` already exist (migration `0001`). Field-by-field for a discovered MCP tool row:

| Field | Value at discovery time |
|---|---|
| `name` | `"{server_name}__{mcp_tool_name}"` (auto-namespaced, Open Question 3 — resolved in the architecture doc §7). This is `Tool.name`, the platform-unique identifier the LLM calls by. |
| `mcp_tool_name` | The raw upstream tool name exactly as the MCP server's `tools/list` returned it (e.g. `"search_issues"`). Used for the actual `tools/call` dispatch — never namespaced. |
| `mcp_server_id` | FK to the owning `MCPServer`. |
| `type` | `"mcp_tool"` |
| `input_schema` | The server's JSON Schema for the tool, taken verbatim from `tools/list`'s `inputSchema` field. |
| `risk_level` | `"low"` (D4 — default; admin can raise it post-discovery through the existing `PUT /api/v1/tools/{id}` path, unchanged). |
| `side_effecting` | `true` (conservative default per AR-01 — an MCP tool's real side effects are unknown to the platform; same fail-closed posture `infer_side_effecting` already applies to anything not provably read-only). `mcp_tool` is not added to `infer_side_effecting`'s read-only-if-GET carve-out — that carve-out is HTTP-method-specific and does not apply here. |
| `owner_team` | `MCPServer.owner_team` (the *only* new work needed for team-scoping to apply automatically — architecture doc §"Team-Scoping"). |
| `publish_status` | Whatever the column default already is for a newly-created `Tool` row (`'published'` per `models.py` — unchanged, no MCP-specific override). |
| `pii_deanonymize_allowed` | `false` (column default — an admin opts a specific MCP tool in explicitly, same as any other type). |
| `status` | `"active"` at first discovery. |
| `auth_config_id` | `NULL` (a `Tool` row's own `auth_config_id` is unrelated to `MCPServer.auth_config_id` — the MCP Proxy resolves server-level credentials itself; per-tool auth on the `Tool` row is not used for `mcp_tool`). |
| `http_method`/`http_url`/`http_headers`/`http_body_template`/`http_timeout_ms`/`python_code` | `NULL` (not applicable to this type — same convention as an `http` row leaving `python_code` NULL). |

### Re-sync (`POST /api/v1/mcp-servers/{id}/sync`) upsert semantics

For each tool the server currently reports in `tools/list`:
- **New tool** (no existing `Tool` row for this `mcp_server_id` + `mcp_tool_name`) → insert as above.
- **Existing tool, unchanged `input_schema`** → update `updated_at` only (via the discovery timestamp bump on the server row); no tool-row write needed.
- **Existing tool, changed `input_schema`** → **auto-apply** the new schema (`Tool.input_schema` overwritten immediately — Open Question 5, resolved) **and** set a review flag. This plan represents "flagged for review" as a `health_detail`-adjacent marker on the **server** row rather than a new per-tool column: `MCPServer.health_detail['schema_drift'] = [{"tool_name": ..., "detected_at": ...}, ...]`, cleared by an explicit "acknowledge" action from the sync/detail endpoint (`POST /api/v1/mcp-servers/{id}/sync` accepts an optional `acknowledge_schema_drift: bool` body field, defaulting to `false`, which — if `true` on the *previous* unacknowledged drift — clears the list before recording any new drift from *this* sync). This avoids a new column purely to carry a UI badge, and keeps the "what changed and when" detail queryable from the one JSONB blob the server row already has.

For each existing `Tool` row (`mcp_server_id` = this server) **not** present in the server's current `tools/list` response:
- Set `status = 'deprecated'` (never hard-deleted — FR-MCP-04). Still queryable, still bound if an agent already bound it (impact-analysis parity with native tools), but excluded from `ToolsPicker` going forward the same way any `deprecated` tool already is (`Tool.status` filter, unchanged existing behavior — no new filter logic needed).

---

## State transitions

### `MCPServer.status`

```
                 ┌─────────────┐
   (create)  ───▶│ disconnected│  (server_default; before first discover attempt completes)
                 └──────┬──────┘
                        │ POST /mcp-servers (synchronous discover attempt)
             success    │      failure
        ┌───────────────┴────────────────┐
        ▼                                ▼
 ┌─────────────┐                  ┌───────────┐
 │  connected  │◀────sync ok──────│   error   │
 └──────┬──────┘                  └─────┬─────┘
        │ sync fails                     │ sync succeeds
        └────────────────▶ error ◀───────┘
```

- `disconnected` is only the pre-first-attempt state (the row briefly exists before the synchronous discover call in the same `POST` request resolves) — Phase 1 has no "administratively disabled" state, so a server transitions to either `connected` or `error` before the create request even returns (FR-MCP-02's "registration is not an all-or-nothing gate" — the row is created either way, per the architecture doc's Studio walkthrough).
- `connected → error` and `error → connected` both happen only via an explicit `/sync` call in Phase 1 (no background health-check loop yet — that's Phase 2/FR-MCP-22). This means a server that goes down between syncs keeps reporting stale `status='connected'` until someone syncs or a tool call against it fails — an accepted Phase 1 gap, logged in the ledger.

### `Tool.status` (for `mcp_tool` rows specifically; the column and its other values are unchanged/shared with every type)

```
 (discovered) ──▶ active ──(vanished from tools/list on a /sync)──▶ deprecated
                     │
                     └──(admin PUT .../tools/{id} {"status":"inactive"})──▶ inactive
```

`deprecated` is the only new *transition trigger* MCP introduces (driven by sync, not by an admin action) — the `active`/`inactive`/`deprecated` value set itself is unchanged (`ck_tools_status`, pre-existing). A `deprecated` MCP tool that reappears in a later `tools/list` (server re-added a tool it had removed) is treated as the "existing tool, unchanged/changed input_schema" case above and flips back to `active`.

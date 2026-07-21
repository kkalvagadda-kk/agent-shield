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

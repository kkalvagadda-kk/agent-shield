"""Backfill webhook_clients rows into applications + invoker grants (Decision 30
§5.3 / §10 step 2). Idempotent — re-running skips (team_name, name) pairs that
already exist (ON CONFLICT DO NOTHING against uq_applications_team_name), and
skips (artifact, role, grantee) grants that already exist (ON CONFLICT DO
NOTHING against uq_arg_active_grant).

Preconditions: migration 0069 has run (applications table + widened
artifact_role_grants constraints must already exist).

Does NOT touch or drop webhook_clients — it stays in place, read-only-in-
intent for one release (services/registry-api/routers/webhook_clients.py's
write endpoints return 410 once the gateway cutover ships; GET keeps working
so pre-existing rows this migration is about to consume remain independently
inspectable during the rollback window).

Revision ID: 0070
Revises: 0069
"""
from alembic import op

revision = "0070"
down_revision = "0069"


def upgrade() -> None:
    # Pass 1: one applications row per distinct (team, client_id) pair that has
    # ever been registered in webhook_clients. DISTINCT ON + ORDER BY created_at
    # ASC keeps the EARLIEST secret/created_by when the same (team, client_id)
    # was registered on more than one trigger under the SAME team (still one
    # reusable identity, per design doc §5.3) — only a different TEAM produces
    # a second row, via the (team_name, name) uniqueness itself.
    op.execute("""
    INSERT INTO applications (team_name, name, secret_encrypted, enabled, created_by, created_at)
    SELECT DISTINCT ON (team_name, client_name)
        team_name, client_name, secret_encrypted, true,
        COALESCE(created_by, 'system:backfill-0070'), created_at
    FROM (
        SELECT
            wc.client_id AS client_name,
            wc.secret_encrypted,
            wc.created_by,
            wc.created_at,
            COALESCE(a.team, w.team) AS team_name
        FROM webhook_clients wc
        JOIN agent_triggers t ON t.id = wc.trigger_id
        LEFT JOIN agents a ON a.id = t.agent_id
        LEFT JOIN workflows w ON w.id = t.workflow_id
    ) source_rows
    WHERE team_name IS NOT NULL
    ORDER BY team_name, client_name, created_at ASC
    ON CONFLICT (team_name, name) DO NOTHING
    """)

    # Pass 2: one invoker grant per webhook_clients row's trigger artifact,
    # resolving applications.id via the (team, client_id) pair Pass 1 just
    # ensured exists (whether inserted this run or a prior run — a plain JOIN
    # against the now-current applications table, not a RETURNING set from
    # Pass 1, so re-running this migration after a partial prior run is safe).
    op.execute("""
    INSERT INTO artifact_role_grants (artifact_type, artifact_id, role, grantee_type, grantee_id, granted_by)
    SELECT DISTINCT
        sr.artifact_type,
        sr.artifact_id,
        'invoker',
        'application',
        app.id::text,
        'system:backfill-0070'
    FROM (
        SELECT
            t.agent_id, t.workflow_id,
            COALESCE(a.team, w.team) AS team_name,
            wc.client_id AS client_name,
            CASE WHEN t.agent_id IS NOT NULL THEN 'agent' ELSE 'workflow' END AS artifact_type,
            COALESCE(t.agent_id, t.workflow_id) AS artifact_id
        FROM webhook_clients wc
        JOIN agent_triggers t ON t.id = wc.trigger_id
        LEFT JOIN agents a ON a.id = t.agent_id
        LEFT JOIN workflows w ON w.id = t.workflow_id
    ) sr
    JOIN applications app
        ON app.team_name = sr.team_name AND app.name = sr.client_name
    WHERE sr.team_name IS NOT NULL
    ON CONFLICT (artifact_id, role, grantee_type, grantee_id) WHERE revoked_at IS NULL
        DO NOTHING
    """)


def downgrade() -> None:
    # Remove ONLY the rows this migration could have produced (grantee_type=
    # 'application' AND granted_by='system:backfill-0070') — never touch grants
    # a human created through the new API after this migration ran, and never
    # touch applications a human created directly through POST /teams/{team}/
    # applications (created_by would not be 'system:backfill-0070' for those).
    op.execute("""
        DELETE FROM artifact_role_grants
        WHERE grantee_type = 'application' AND granted_by = 'system:backfill-0070'
    """)
    op.execute("""
        DELETE FROM applications WHERE created_by = 'system:backfill-0070'
    """)

"""ONE definition of "is this trigger's artifact live", readable by all three services.

WHY A VIEW
----------
"Is the artifact behind this trigger alive?" is asked by THREE separate images that
share no Python:

  * services/scheduler          — decides what to register on a cron
  * services/event-gateway      — decides whether a webhook resolves
  * services/registry-api       — renders it on the schedules page

Every attempt to state that predicate independently has been wrong, twice in one day:

  `w.status = 'published'`          matched 0 of 140 rows — nothing writes that value
                                    (only writer sets 'archived'). Every workflow
                                    schedule silently died and suite-95 stayed green.
  `w.publish_status = 'published'`  reachable, but TOO STRICT — a workflow reaches
                                    production by deploying its MEMBER AGENTS while its
                                    own row stays draft/private. suite-66 broke.

Both failures are the same shape as the ones this repo already carries postmortems
for: `agent_endpoints.py` exists because a pod URL was built in eight places, and
`webhook_clients.py`'s header documents two hand-maintained lookups drifting. A view
puts the rule in the one place all three services already reach — Postgres — so a
future change lands everywhere at once or not at all.

WHAT "LIVE" MEANS
-----------------
  agents     status = 'active'      (archived/deprecated/quarantined are not live)
  workflows  status <> 'archived'   (draft AND published both run)

The workflow rule matches `routers/internal.py`'s run door, which rejects only
'archived'. That alignment is deliberate: the trigger filter and the run door
disagreeing about "runnable" is precisely the drift being removed.

NOT enforced here: "a never-eval-gated draft must not fire on a cron". A real policy
question, but enforcing it in the filter while the door permits it would recreate the
two-definitions bug. Recorded as open in
docs/bugs/workflow-schedules-gated-on-a-status-nothing-sets.md.

WHY `artifact_is_live` IS A COLUMN, NOT A WHERE CLAUSE
-----------------------------------------------------
The view returns EVERY trigger, with liveness computed. Consumers filter:

    scheduler / gateway   WHERE artifact_is_live AND enabled AND trigger_type = ...
    schedules page        no filter — it must SHOW the dead ones, and say why

A view that pre-filtered to live rows would be useless to the page whose entire job
is making disarmed and un-runnable schedules visible.

COLUMNS ARE ENUMERATED, NOT `t.*`
---------------------------------
Postgres freezes `SELECT *` at view-creation time, so a later ALTER TABLE ADD COLUMN
would NOT appear here and the omission would be silent. Adding a column a consumer
needs therefore costs a migration — deliberate, and cheaper than the silent kind.

Revision ID: 0077
Revises: 0076
"""
from alembic import op

revision = "0077"
down_revision = "0076"


VIEW_SQL = """
CREATE OR REPLACE VIEW trigger_liveness AS
    SELECT
        t.id, t.agent_id, t.workflow_id, t.trigger_type, t.cron_expression,
        t.timezone, t.enabled, t.token_hash, t.auth_mode, t.filter_conditions,
        t.input_payload, t.armed_by, t.approver_role, t.alert_email,
        t.alert_on_failure, t.disabled_reason, t.disabled_at,
        t.created_at, t.updated_at,
        'agent'::text        AS artifact_kind,
        a.id                 AS artifact_id,
        a.name               AS artifact_name,
        a.team               AS artifact_team,
        a.status             AS artifact_status,
        (a.status = 'active') AS artifact_is_live
    FROM agent_triggers t
    JOIN agents a ON t.agent_id = a.id
    UNION ALL
    SELECT
        t.id, t.agent_id, t.workflow_id, t.trigger_type, t.cron_expression,
        t.timezone, t.enabled, t.token_hash, t.auth_mode, t.filter_conditions,
        t.input_payload, t.armed_by, t.approver_role, t.alert_email,
        t.alert_on_failure, t.disabled_reason, t.disabled_at,
        t.created_at, t.updated_at,
        'workflow'::text        AS artifact_kind,
        w.id                    AS artifact_id,
        w.name                  AS artifact_name,
        w.team                  AS artifact_team,
        w.status                AS artifact_status,
        (w.status <> 'archived') AS artifact_is_live
    FROM agent_triggers t
    JOIN workflows w ON t.workflow_id = w.id
"""


def upgrade() -> None:
    op.execute(VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS trigger_liveness")

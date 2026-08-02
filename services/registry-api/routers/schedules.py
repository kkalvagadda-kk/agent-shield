"""Cross-artifact schedule operations (R5) — the read side of the schedules page.

  GET /api/v1/schedules?trigger_type=schedule|webhook

WHY THIS EXISTS
---------------
Triggers were reachable only per-artifact (`/agents/{name}/triggers`,
`/workflows/{id}/triggers`), so "what is scheduled on this platform, and is it
actually going to run?" had no home. 37 triggers sat armed on deleted and archived
artifacts — including a never-published workflow firing every 15 minutes for days —
because there was nowhere to notice them.

A VIEW, NOT A SECOND WRITER
---------------------------
Read-only, deliberately. Every mutation the schedules page performs (enable, disable,
delete) routes back to the EXISTING artifact-scoped trigger routers, branching on
`artifact_kind`. Adding schedule-specific write endpoints would create a second writer
for rows that already have one — the drift this whole workstream exists to remove.

LIVENESS IS NOT RESTATED HERE
-----------------------------
`artifact_is_live` comes from the `trigger_liveness` view (migration 0077), the same
row the scheduler and event-gateway read. Writing the predicate again in this file
would make it a FOURTH definition of "runnable", and it was already wrong twice in one
day when stated independently (`w.status='published'` matched nothing;
`w.publish_status='published'` was too strict). The page's contract says `will_fire`
is "computed server-side from the SAME predicate the scheduler reads" — this is what
makes that true rather than aspirational.

Dispatchability is the second half and comes from `resolve_dispatch_target`, the same
call `/internal/runs/start` uses, for the same reason.

AUTHORIZATION (R7)
------------------
`require_user` + team scoping. A cross-artifact listing is exactly the shape
Decision 33 was written about: `list_eval_runs` and `list_datasets` filtered inside
`if caller:` with no `else`, and registry-api installs no global auth middleware, so a
missing identity meant a full-table read. Here that would leak every team's schedules
at once. Deny-by-default is applied BEFORE the endpoint exists rather than after a
leak — `platform-admin` sees all, everyone else sees their own team.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_endpoints import DispatchTargetError, resolve_dispatch_target
from auth_middleware import require_user
from db import get_db
from models import Agent
from rbac import get_user_global_role, get_user_team
from schemas import ScheduleListItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


# One row per trigger, joined to its artifact and its most recent run. The liveness
# column is taken from the view — not recomputed.
_LIST_SQL = """
SELECT
    tl.id::text                AS trigger_id,
    tl.trigger_type,
    tl.artifact_kind,
    tl.artifact_id::text       AS artifact_id,
    tl.artifact_name,
    tl.artifact_team,
    tl.artifact_status,
    tl.artifact_is_live,
    tl.cron_expression,
    tl.timezone,
    tl.input_payload,
    tl.enabled,
    -- No `armed_at`. Arm state is `enabled` (above) and nothing else — see the note
    -- on ScheduleListItem. `armed_by` IS real: it records the human whose authority a
    -- daemon run carries, stamped at create time by routers/triggers.py.
    tl.armed_by,
    tl.disabled_at             AS disarmed_at,
    tl.disabled_reason         AS disarm_reason,
    tl.alert_email,
    tl.alert_on_failure,
    r.id::text                 AS last_run_id,
    r.status                   AS last_run_status,
    r.started_at               AS last_run_at,
    r.error_message            AS last_run_error
FROM trigger_liveness tl
LEFT JOIN LATERAL (
    -- Keyed on trigger_id, the same key routers/triggers.py::list_trigger_runs uses.
    -- A schedule's runs are a property of the SCHEDULE; the two deployment FK columns
    -- are NULL on every trigger-driven run and point at different tables anyway.
    SELECT ar.id, ar.status, ar.started_at, ar.error_message
    FROM agent_runs ar
    WHERE ar.trigger_id = tl.id
    ORDER BY ar.started_at DESC
    LIMIT 1
) r ON TRUE
WHERE tl.trigger_type = :trigger_type
  AND (:all_teams OR tl.artifact_team = :team)
ORDER BY tl.artifact_name, tl.created_at
"""


def _next_fire(cron: Optional[str], tz: Optional[str]) -> Optional[datetime]:
    """Next occurrence, or None for a missing/invalid cron.

    Same croniter call as `routers/agents.py`'s scheduled health branch — a bad cron
    yields None rather than raising, so one malformed row cannot 500 the whole page.
    """
    if not cron:
        return None
    try:
        from croniter import croniter

        return croniter(cron, datetime.now(tz=timezone.utc)).get_next(datetime)
    except Exception:
        return None


@router.get(
    "",
    response_model=list[ScheduleListItem],
    summary="Every schedule (or webhook) across agents and workflows",
)
async def list_schedules(
    trigger_type: str = Query("schedule", pattern="^(schedule|webhook)$"),
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[ScheduleListItem]:
    caller = claims.get("sub") or ""
    all_teams = (await get_user_global_role(db, caller)) == "platform-admin"
    team = await get_user_team(db, caller)

    if not all_teams and not team:
        # Deny by default. An authenticated caller with no team assignment sees
        # NOTHING — never the unfiltered table. This is the `else` branch Decision 33
        # was written about; its absence elsewhere leaked every eval run on the
        # platform to an unauthenticated caller.
        logger.info("list_schedules: caller %s has no team — returning empty", caller)
        return []

    rows = (await db.execute(
        text(_LIST_SQL),
        {"trigger_type": trigger_type, "all_teams": all_teams, "team": team or ""},
    )).mappings().all()

    # Dispatchability, per row, from the SAME resolver the dispatch door uses.
    # Deliberately not reimplemented as SQL: a second definition of "can this reach a
    # pod" is the exact bug class this endpoint's `will_fire` field is supposed to
    # close. Cost is 1-2 extra queries per row; the page is scoped to hundreds of
    # schedules (brief: "hundreds, not tens of thousands"), and the agent lookups are
    # memoised below so N distinct artifacts cost N resolutions, not N triggers.
    dispatchable: dict[str, tuple[bool, Optional[str]]] = {}

    async def _dispatch_state(kind: str, artifact_id: str, name: str):
        key = f"{kind}:{artifact_id}"
        if key in dispatchable:
            return dispatchable[key]
        if kind != "agent":
            # Workflow dispatch resolves members at run time (workflow_orchestrator),
            # not through resolve_dispatch_target. Reporting a per-member verdict here
            # would be a guess, so liveness alone decides and the page says nothing it
            # cannot stand behind.
            dispatchable[key] = (True, None)
            return dispatchable[key]
        agent = (await db.execute(
            text("SELECT id, name, team FROM agents WHERE id = :i"), {"i": artifact_id}
        )).mappings().first()
        if agent is None:
            dispatchable[key] = (False, f"agent '{name}' no longer exists")
            return dispatchable[key]
        try:
            await resolve_dispatch_target(
                db, Agent(id=agent["id"], name=agent["name"], team=agent["team"]),
                environment="production",
            )
            dispatchable[key] = (True, None)
        except DispatchTargetError as exc:
            dispatchable[key] = (False, str(exc))
        return dispatchable[key]

    items: list[ScheduleListItem] = []
    for row in rows:
        can_dispatch, dispatch_why = await _dispatch_state(
            row["artifact_kind"], row["artifact_id"], row["artifact_name"]
        )
        # `why_not` answers ONE question in the operator's order of action: a disarmed
        # trigger is not going to fire whatever else is true, so that reason wins.
        if not row["enabled"]:
            will_fire, why_not = False, (
                row["disarm_reason"] or "this schedule is disabled"
            )
        elif not row["artifact_is_live"]:
            will_fire, why_not = False, (
                f"{row['artifact_kind']} '{row['artifact_name']}' is "
                f"{row['artifact_status']}"
            )
        elif not can_dispatch:
            will_fire, why_not = False, dispatch_why
        else:
            will_fire, why_not = True, None

        items.append(ScheduleListItem(
            trigger_id=row["trigger_id"],
            trigger_type=row["trigger_type"],
            artifact_kind=row["artifact_kind"],
            artifact_id=row["artifact_id"],
            artifact_name=row["artifact_name"],
            artifact_team=row["artifact_team"],
            artifact_status=row["artifact_status"],
            cron_expression=row["cron_expression"],
            timezone=row["timezone"],
            next_fire_at=_next_fire(row["cron_expression"], row["timezone"]),
            input_payload=row["input_payload"],
            enabled=row["enabled"],
            armed_by=row["armed_by"],
            disarmed_at=row["disarmed_at"],
            disarm_reason=row["disarm_reason"],
            will_fire=will_fire,
            why_not=why_not,
            last_run_id=row["last_run_id"],
            last_run_status=row["last_run_status"],
            last_run_at=row["last_run_at"],
            last_run_error=row["last_run_error"],
            alert_email=row["alert_email"],
            alert_on_failure=row["alert_on_failure"],
        ))
    return items

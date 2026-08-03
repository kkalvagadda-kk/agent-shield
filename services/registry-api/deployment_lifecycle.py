"""Deleting a version tears down the deployments that pinned it.

ONE definition, shared by agents and workflows.

There were two copies of this. The agent copy (``routers/versions.py``) detached the
referencing ``agent_runs`` rows and then deleted the deployment rows. The workflow copy
(``routers/composite_workflows.py``) only set ``status = 'terminated'`` and left the rows
in place — so the ``DELETE FROM workflow_versions`` that followed hit::

    ForeignKeyViolationError: update or delete on table "workflow_versions" violates
    foreign key constraint "workflow_deployments_version_id_fkey"

Every attempt to delete a workflow version that had ever been deployed returned 500.
Two copies of one rule, one of them wrong, and the wrong one had no test — so this
function is the rule, and the callers pass their tables in explicitly rather than the
helper sniffing which kind of thing it was handed.

Note on vocabulary: the callers return ``terminated_deployments`` and the field is kept
for API compatibility, but the rows are **removed**, not marked terminated. The count is
of deployments that were still live when the version was deleted.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute


async def detach_and_delete_deployments(
    db: AsyncSession,
    *,
    deployment_model: type,
    version_id: uuid.UUID,
    run_fk_column: InstrumentedAttribute,
) -> int:
    """Remove every deployment pinned to ``version_id``; return how many were live.

    ``run_fk_column`` is the ``AgentRun`` column pointing at this deployment table
    (``AgentRun.sandbox_deployment_id`` for agents, ``AgentRun.workflow_deployment_id``
    for workflows). It is NULLed first: that FK is ``NO ACTION``, so leaving it set
    turns this into the same 500 by another route. Passed in explicitly — a helper that
    guessed the column from the model would be one ``getattr`` away from silently
    detaching nothing.

    Callers must still ``commit``; this only stages the work so the version delete and
    the teardown land in one transaction.
    """
    deps = (
        await db.execute(
            select(deployment_model).where(deployment_model.version_id == version_id)
        )
    ).scalars().all()
    if not deps:
        return 0

    live_count = sum(1 for d in deps if d.status != "terminated")

    await db.execute(
        update(run_fk_column.parent.class_)
        .where(run_fk_column.in_([d.id for d in deps]))
        .values({run_fk_column.key: None})
    )
    for dep in deps:
        await db.delete(dep)

    return live_count

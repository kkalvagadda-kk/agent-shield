"""THE addresses of a deployed agent. One definition, imported everywhere.

WHY THIS MODULE EXISTS (TODO-8, earned):

`_team_namespace` existed TWICE and had already drifted:

    workflow_orchestrator.py:  f"agents-{(team or 'platform').lower().replace(' ', '-')}"
    routers/internal.py:       f"agents-{team.lower().replace(' ', '-')}"

Same name, same job, two behaviours: on an empty team the first yields
`agents-platform` and the second `agents-`, a DIFFERENT namespace; on `None` the
second raises `AttributeError`. Latent today only because `agents.team` is
NOT NULL and no row is empty — i.e. it is one nullable column away from a live bug,
and nothing would have flagged it.

And the pod URL was built in EIGHT places, some environment-aware and some
hardcoding `-production`. That mix produced a real, live defect: every
sandbox/playground approval resume POSTed to a `{agent}-production` Service that
does not exist, httpx raised, the caller swallowed it into a `logger.warning`, and
the approval row was marked resolved anyway — the reviewer saw "approved" while the
agent was never resumed. 68 sandbox + 133 playground approvals were exposed.

This is the repo's #1 bug class in one place: two paths compute the same thing, one
gets fixed, the other silently does not, and because the neglected side fails SAFE
nothing errors. The fix is not "correct both copies" — it is to have one.

RULES:
  * `environment` is REQUIRED everywhere. It used to default to "production", and
    every caller took the default. A wrong default is invisible at the call site; a
    missing argument is a TypeError at import. Callers resolve the real environment
    (`workflow_orchestrator._resolve_agent_environment`) and say which they mean.
    CLAUDE.md: "when sandbox and production share infrastructure they must pass
    explicit identifiers — never rely on a default."
  * Production-only doors (`/internal/runs/start`, workflow-run dispatch) pass
    `environment="production"` EXPLICITLY. That is a statement, not a default.

ADDRESS vs. ADMISSIBILITY (added after the trigger-dispatch bug):

`agent_pod_base` answers "what is the address?" and nothing else. It will happily
name a Service that was never created — which is exactly what happened at
`routers/internal.py`, the last surviving instance of the eight-places problem
above: it imported `team_namespace` from here but still hand-built
`f"...{agent_name}-production..."`. Its admission check asked a DIFFERENT question
("is ANY deployment running?", no environment filter), so a sandbox-only agent
passed the door and DNS-failed. 1,197 scheduled runs died that way.

`resolve_dispatch_target` is the fix: ONE call that answers both, deriving the
address from the very row it validated. Two questions that must agree cannot be
asked in two places. Use it for every dispatch to a deployed agent; use bare
`agent_pod_base` only where the caller has already proven the deployment exists.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


def team_namespace(team: str | None) -> str:
    """The K8s namespace an agent's pods live in for `team`.

    Tolerates None/empty because the two former copies disagreed on exactly that
    (one defaulted to 'platform', the other built the invalid `agents-`). Defaulting
    is correct here: it mirrors the deploy-controller's own naming, so an unset team
    resolves to the same namespace the pod was actually created in.
    """
    return f"agents-{(team or 'platform').lower().replace(' ', '-')}"


def agent_pod_base(agent_name: str, team: str | None, environment: str) -> str:
    """Cluster-internal base URL of an agent's pod Service.

    Mirrors `deploy-controller/manifest_builder.py`, which names the Service
    `f"{agent_name}-{environment}"` — so `environment` is what distinguishes
    `{agent}-sandbox` from `{agent}-production`, and getting it wrong resolves to a
    Service that does not exist rather than to the wrong agent (fails as DNS, which
    callers then swallow — see the module docstring).
    """
    return f"http://{agent_name}-{environment}.{team_namespace(team)}.svc.cluster.local:8080"


class DispatchTargetError(Exception):
    """No dispatchable deployment in the requested environment.

    Carries an OPERATOR-READABLE reason, because it is written straight onto the
    failed run row and read in the UI. The old failure text was
    `dispatch failed: [Errno -2] Name or service not known` — technically true and
    operationally useless: it named the symptom (DNS) instead of the cause (the
    agent was never deployed to production). Never let this message degrade back
    into a transport error.
    """


@dataclass(frozen=True)
class DispatchTarget:
    """A validated dispatch destination: the address AND the row that justifies it.

    `deployment_id` is the `deployments` row that was checked. Note it CANNOT be
    written to `agent_runs.production_deployment_id` — that column FKs to
    `production_deployments` (the published-artifact table), a different lifecycle
    from `deployments` (the table with the `environment` column, which is what the
    deploy-controller turns into a `{agent}-{environment}` Service). Two tables,
    two meanings, one confusable name. Run history keys on `agent_runs.trigger_id`
    instead — see `routers/triggers.py::list_trigger_runs`.
    """
    base_url: str
    deployment_id: uuid.UUID
    environment: str


async def resolve_dispatch_target(db, agent, *, environment: str) -> DispatchTarget:
    """Resolve where a run for `agent` goes in `environment` — or refuse, with a reason.

    THE point of this function is that admissibility and address are decided
    together, from the same row. A caller cannot check one thing and dispatch to
    another, because there is only one call and it returns both.

    `environment` is required and explicit for the same reason it is on
    `agent_pod_base`: a wrong default is invisible at the call site.

    Raises `DispatchTargetError` when the environment has no running deployment.
    The message names what IS deployed, so the operator's next action is obvious
    ("deployed to sandbox, needs production") rather than a guess.
    """
    # Imported here, not at module top: this module is imported by low-level
    # callers and must stay cheap; `models` pulls the whole ORM graph.
    from sqlalchemy import select

    from models import Deployment

    rows = (await db.execute(
        select(Deployment.id, Deployment.environment, Deployment.status)
        .where(Deployment.agent_id == agent.id)
        .order_by(Deployment.deployed_at.desc().nulls_last())
    )).all()

    for dep_id, env, status in rows:
        if env == environment and status == "running":
            return DispatchTarget(
                base_url=agent_pod_base(agent.name, agent.team, env),
                deployment_id=dep_id,
                environment=env,
            )

    # Refuse with the diagnosis, not the symptom.
    live = sorted({env for _i, env, status in rows if status == "running"})
    if live:
        detail = f"it is deployed to {', '.join(live)}"
    elif rows:
        detail = "none of its deployments are running"
    else:
        detail = "it has never been deployed"
    raise DispatchTargetError(
        f"agent '{agent.name}' has no running {environment} deployment — {detail}. "
        f"Schedule and webhook triggers dispatch to {environment}; "
        f"deploy the agent to {environment} (or publish it) before arming a trigger."
    )

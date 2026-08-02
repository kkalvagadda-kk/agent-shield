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

    `deployment_id` identifies the row that was checked, and `source_table` says
    WHICH table it came from — because there are two, with different namespaces:

      deployments (environment='production')  -> pod in `agents-{team}`
      production_deployments (the Publish flow) -> pod in its OWN namespace,
          `production-{artifact}-{id8}` (catalog.py mints it; production_reconciler
          names the Deployment `{agent}-production` inside it)

    So `deployment_id` alone is ambiguous and must never be treated as a
    `deployments` id — `docs/design/sandbox-production-parity-architecture.md` §41
    lists four prior bugs from exactly that assumption. It also cannot be written
    to `agent_runs.production_deployment_id` unless it came from the production
    table. Run history keys on `agent_runs.trigger_id` instead
    (`routers/triggers.py::list_trigger_runs`).
    """
    base_url: str
    deployment_id: uuid.UUID
    environment: str
    # "deployments" | "production_deployments" — which lifecycle justified this.
    source_table: str


async def resolve_dispatch_target(db, agent, *, environment: str) -> DispatchTarget:
    """Resolve where a run for `agent` goes in `environment` — or refuse, with a reason.

    THE point of this function is that admissibility and address are decided
    together, from the same row. A caller cannot check one thing and dispatch to
    another, because there is only one call and it returns both.

    TWO LEGS FOR PRODUCTION, AND BOTH MUST BE CHECKED
    -------------------------------------------------
    An agent can be in production by two independent routes, and they land in
    DIFFERENT NAMESPACES:

      1. `POST /agents/{name}/deploy {"environment":"production"}` writes a
         `deployments` row; the pod goes to `agents-{team}`.
      2. **Publish** (`routers/catalog.py`) writes a `production_deployments` row
         and mints its OWN namespace, `production-{artifact}-{id8}`;
         `production_reconciler` names the Deployment `{agent}-production` inside it.

    The first version of this function checked only leg 1 and always composed
    `agents-{team}`. So a PUBLISHED agent — the only production route Studio
    actually offers — was reported as "has no running production deployment",
    which was false, and the refusal message told the operator to Publish: the
    exact action that lands in the leg the resolver could not see. Self-defeating
    advice, and worse than the DNS error it replaced because it was confidently
    wrong rather than merely unhelpful.

    `docs/design/sandbox-production-parity-architecture.md` §41 states the rule —
    "any column or query that assumes 'a deployment id is a `deployments` id'
    breaks for production" — and lists four earlier bugs from the same assumption.
    `bundle_generator` already solved it with a UNION over both legs; this mirrors
    that shape rather than inventing a second approach.

    `environment` is required and explicit for the same reason it is on
    `agent_pod_base`: a wrong default is invisible at the call site.

    Raises `DispatchTargetError` when nothing serves `environment`. The message
    names what IS deployed so the next action is obvious rather than a guess.
    """
    # Imported here, not at module top: this module is imported by low-level
    # callers and must stay cheap; `models` pulls the whole ORM graph.
    from sqlalchemy import select

    from models import Deployment, ProductionDeployment, PublishedArtifact

    # ── Leg 1: `deployments` (has an `environment` column) ────────────────────
    rows = (await db.execute(
        select(Deployment.id, Deployment.environment, Deployment.status,
               Deployment.k8s_namespace)
        .where(Deployment.agent_id == agent.id)
        .order_by(Deployment.deployed_at.desc().nulls_last())
    )).all()

    for dep_id, env, status, ns in rows:
        if env == environment and status == "running":
            # Namespace from the ROW, not recomposed. `k8s_namespace` is NOT NULL,
            # but fall back to the team default rather than emit "None" into a host.
            namespace = ns or team_namespace(agent.team)
            return DispatchTarget(
                base_url=f"http://{agent.name}-{env}.{namespace}.svc.cluster.local:8080",
                deployment_id=dep_id,
                environment=env,
                source_table="deployments",
            )

    # ── Leg 2: `production_deployments` (the Publish flow) ────────────────────
    # Only meaningful for production; a published artifact has no sandbox leg here.
    if environment == "production":
        pub = (await db.execute(
            select(ProductionDeployment.id, ProductionDeployment.namespace)
            .join(PublishedArtifact, PublishedArtifact.id == ProductionDeployment.artifact_id)
            .where(
                PublishedArtifact.source_id == agent.id,
                PublishedArtifact.type == "agent",
                ProductionDeployment.status == "running",
            )
            .order_by(ProductionDeployment.deployed_at.desc().nulls_last())
            .limit(1)
        )).first()
        if pub:
            pd_id, pd_ns = pub
            # The published pod lives in the namespace catalog.py minted for it.
            # Composing `agents-{team}` here is the bug this leg exists to fix.
            namespace = pd_ns or f"production-{agent.name}"
            return DispatchTarget(
                base_url=f"http://{agent.name}-production.{namespace}.svc.cluster.local:8080",
                deployment_id=pd_id,
                environment="production",
                source_table="production_deployments",
            )

    # Refuse with the diagnosis, not the symptom.
    live = sorted({env for _i, env, status, _ns in rows if status == "running"})
    if live:
        detail = f"it is deployed to {', '.join(live)}"
    elif rows:
        detail = "none of its deployments are running"
    else:
        detail = "it has never been deployed"
    # The remedy names what the operator can actually DO, in the surface they are
    # most likely reading this from. Studio's agent page has no "deploy to
    # production" control at all — its Deploy button opens a "Deploy to sandbox"
    # modal with no environment choice, and the only route to production is
    # Publish, which is gated on a passing eval (Decision 20; the button's own
    # tooltip reads "Run an eval that passes before publishing").
    #
    # An earlier wording here led with "deploy the agent to production", which is
    # true of the API and unreachable from the UI — advice that sends the reader
    # looking for a button that does not exist is only marginally better than the
    # DNS error this message replaced. Caught by driving the real screen
    # (docs/testing/claude-in-chrome-schedule-failure-journey.md, leg 7).
    #
    # It then named "Publish" alone, which is REACHABLE but INSUFFICIENT — the
    # second and worse failure, because the operator does the named thing, watches
    # it succeed, and gets this identical message again. Publishing produces a
    # `published_artifacts` row (a CATALOG LISTING). It does not produce a
    # `production_deployments` row: `routers/admin.py::approve_publish_request`
    # never touches ProductionDeployment, and `ProductionDeployment.artifact_id`
    # FKs to `published_artifacts.id` — so the row that makes an agent reachable is
    # only created by the SEPARATE catalog deploy. Three steps, and naming step one
    # as if it were the remedy is how an operator ends up in a loop.
    # Found by driving the real screens: claude-in-chrome-schedule-lifecycle-journey
    # leg 8, and docs/bugs/publish-does-not-create-a-production-deployment.md.
    remedy = (
        "publish it, approve it in Admin → Publish Queue, then deploy it from "
        "Marketplace → the artifact → Deploy Latest (all three are required — "
        "publishing alone only creates the catalog listing)"
        if environment == "production"
        else f"deploy the agent to {environment}"
    )
    raise DispatchTargetError(
        f"agent '{agent.name}' has no running {environment} deployment — {detail}. "
        f"Schedule and webhook triggers dispatch to {environment}. {remedy}, "
        f"then re-enable the trigger."
    )

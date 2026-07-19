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
"""
from __future__ import annotations


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

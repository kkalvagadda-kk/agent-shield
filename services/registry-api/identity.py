"""Principal resolution — the ONE identity decision for a run (WS-2 T007).

Both entry paths converge here so the "who is this run acting as?" decision is
made in a single place (No-Bandaid: shared code, not duplicated per path):

  * interactive `/chat`  → `resolve_principal(agent, caller=<jwt user>, ...)`
  * trigger `/internal/runs/start` → `resolve_principal(agent, caller=None, ...)`

The decision is driven by **whether a JWT caller is present** — passed as an
explicit `caller` param — NOT by sniffing `agent.agent_class`. The class only
selects the *fallback* when no caller is present:

  * caller present (any class) → caller identity (R3 identity floor, not a cap:
    a daemon agent's `/chat` run still runs under the caller).
  * caller absent + daemon → the agent's SERVICE identity (agent_identities).
    `user_id` is empty (no live human); audit reads "service:X on behalf of Y".
  * caller absent + user_delegated + armer → the arming user's identity.
  * caller absent + user_delegated + NO armer → DENY (fail-closed). Never
    silently downgrade a user-delegated run to the service identity.

Wired in Phase 3 (T009 `routers/internal.py`, T010 `routers/chat.py`); this
module has no caller in T007 by design.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AgentIdentity


class PrincipalResolutionError(Exception):
    """Identity could not be resolved — the run MUST be denied (fail-closed).

    Raised when a user_delegated trigger-run has neither a JWT caller nor an
    arming human (`trigger.armed_by`), or when a daemon has no provisioned
    service identity. Callers turn this into a deny, never a default-to-service
    downgrade.
    """


@dataclass
class Principal:
    """The resolved actor for a run.

    Attributes:
        run_by:     Subject to stamp on `agent_runs.run_by` — the SERVICE
                    identity subject for a daemon trigger-run, otherwise the
                    caller / arming user's sub.
        user_id:    The live human user's sub for the OPA `user_identity_ok`
                    gate. EMPTY STRING for a daemon trigger-run (no live human).
        display:    Human-readable principal label for audit / approvals
                    ("service:X on behalf of Y" | the caller's display).
        is_service: True when acting as a machine/service identity (daemon
                    trigger-run) rather than a human.
        agent_class: The executable's class, carried for the OPA input.
    """

    run_by: str
    user_id: str
    display: str
    is_service: bool = False
    agent_class: str = ""


def _caller_display(caller: Mapping[str, Any]) -> str:
    """Best human label for a JWT caller (name → preferred_username → email → sub)."""
    for key in ("name", "preferred_username", "email", "sub"):
        val = caller.get(key)
        if val:
            return str(val)
    return "unknown"


def principal_display(
    *,
    caller: Optional[Mapping[str, Any]] = None,
    agent_name: Optional[str] = None,
    workflow_name: Optional[str] = None,
    armed_by: Optional[str] = None,
    is_service: bool = False,
) -> str:
    """Derive the audit/approval display string (data-model.md formulas).

    * caller present               → the caller's display.
    * daemon workflow member       → "workflow:{workflow_name} (service) on behalf of {armed_by or 'unknown'}".
    * daemon agent trigger-run     → "service:{agent_name} on behalf of {armed_by or 'unknown'}".
    * user_delegated trigger-run   → the arming user (armed_by).
    """
    if caller is not None:
        return _caller_display(caller)
    if workflow_name is not None:
        return f"workflow:{workflow_name} (service) on behalf of {armed_by or 'unknown'}"
    if is_service:
        return f"service:{agent_name} on behalf of {armed_by or 'unknown'}"
    return armed_by or "unknown"


async def _lookup_service_subject(agent_name: str, db: AsyncSession) -> Optional[str]:
    """Return the active (non-revoked) service-account subject for an agent, or None."""
    result = await db.execute(
        select(AgentIdentity)
        .where(
            AgentIdentity.agent_name == agent_name,
            AgentIdentity.revoked_at.is_(None),
        )
        .order_by(AgentIdentity.provisioned_at.desc())
    )
    identity = result.scalars().first()
    return identity.sa_subject if identity else None


async def resolve_principal(
    agent: Any,
    caller: Optional[Mapping[str, Any]],
    trigger: Any,
    db: AsyncSession,
) -> Principal:
    """Resolve the acting principal for a run.

    Args:
        agent:   The executable ORM object (Agent / CompositeWorkflow) — read
                 for `.name` and `.agent_class`.
        caller:  The authenticated JWT claims (from require_user /
                 get_optional_user) for an interactive run, or None for a
                 trigger-driven run. Presence of a caller — NOT the agent class
                 — decides interactive vs triggered.
        trigger: The `AgentTrigger` that fired (has `.armed_by`), or None for an
                 interactive run.
        db:      Async session (used only for the daemon service-identity lookup).

    Raises:
        PrincipalResolutionError: user_delegated trigger-run with no armer, or a
            daemon with no provisioned service identity (fail-closed).
    """
    agent_class = getattr(agent, "agent_class", "") or ""

    # 1. Caller present → caller identity (any class; R3 floor, not a cap).
    if caller is not None:
        sub = str(caller.get("sub") or "")
        display = _caller_display(caller)
        return Principal(
            run_by=sub,
            user_id=sub,
            display=display,
            is_service=False,
            agent_class=agent_class,
        )

    # 2. Caller absent → triggered run. The class selects the fallback identity.
    armed_by = getattr(trigger, "armed_by", None) if trigger is not None else None
    agent_name = getattr(agent, "name", "") or ""

    if agent_class == "daemon":
        service_subject = await _lookup_service_subject(agent_name, db)
        if not service_subject:
            raise PrincipalResolutionError(
                f"daemon agent '{agent_name}' has no active service identity; "
                "cannot resolve principal (fail-closed)"
            )
        return Principal(
            run_by=service_subject,
            user_id="",  # no live human on a daemon trigger-run
            display=principal_display(agent_name=agent_name, armed_by=armed_by, is_service=True),
            is_service=True,
            agent_class=agent_class,
        )

    # user_delegated (or any non-daemon) trigger-run → the arming human.
    if not armed_by:
        raise PrincipalResolutionError(
            f"user_delegated agent '{agent_name}' trigger-run has no caller and no "
            "armer (trigger.armed_by); denying (fail-closed) — never downgrade to service"
        )
    return Principal(
        run_by=str(armed_by),
        user_id=str(armed_by),
        display=principal_display(armed_by=armed_by),
        is_service=False,
        agent_class=agent_class,
    )


def workflow_service_subject(workflow_name: str) -> str:
    """Deterministic service-identity subject for a daemon workflow's production
    orchestrator ServiceAccount (WS-2 T016 / D1).

    Unlike an agent, a workflow does **not** get a persisted `agent_identities` row: the
    production reconciler calls `register_agent_identity(workflow_name, ...)`, but the
    create endpoint (`routers/agents.py::create_agent_identity`) 404s any name absent from
    `agents`, and `AgentIdentity.agent_name` is a FK → `agents.name`. So a daemon workflow's
    service subject is derived by **convention**, mirroring deploy-controller
    `k8s_client.ensure_service_account` — `system:serviceaccount:{ns}:{sa}` with
    `ns = production-{wf}` (`production_reconciler._build_workflow_deployment_dict`) and
    `sa = agent-{wf}-sa`.

    The workflow PARENT run makes no tool calls (members do, each under its own pod SA), so
    this subject is an **audit principal** stamped on `agent_runs.run_by`, not a subject
    resolved against the OPA bundle. Keep in sync with deploy-controller
    `k8s_client.py::ensure_service_account` naming (flagged in the WS-2 gap ledger)."""
    return f"system:serviceaccount:production-{workflow_name}:agent-{workflow_name}-sa"


async def resolve_workflow_principal(
    workflow: Any,
    caller: Optional[Mapping[str, Any]],
    trigger: Any,
    db: AsyncSession,
) -> Principal:
    """Resolve the acting principal for a composite-workflow run (WS-2 T016 / D1).

    Twin of `resolve_principal` for a `CompositeWorkflow`. The workflow's **own**
    `agent_class` is the run-tree authority; members inherit it via `run_by` and their own
    class is ignored at runtime (D1 — one authority per run tree). The SOLE difference from
    the agent path is the service-subject SOURCE: a workflow has no `agent_identities` row,
    so a daemon workflow's subject is the persisted identity if one somehow exists, else the
    deterministic production-SA convention (`workflow_service_subject`). Same
    caller/armed_by/fail-closed rules as agents:

      * caller present            → caller identity (R3 floor, not a cap).
      * daemon, no caller         → the workflow's service identity (`user_id` empty).
      * user_delegated, no caller → the arming human (`trigger.armed_by`).
      * user_delegated, no armer  → PrincipalResolutionError (fail-closed — never downgrade).

    Explicit `caller` param — never sniff the class to decide interactive-vs-triggered.

    Raises:
        PrincipalResolutionError: user_delegated workflow trigger-run with no armer.
    """
    agent_class = getattr(workflow, "agent_class", "") or ""
    workflow_name = getattr(workflow, "name", "") or ""

    # 1. Caller present → caller identity (any class; R3 floor). Exercised by an
    #    interactive builder/playground workflow run, not the trigger path.
    if caller is not None:
        sub = str(caller.get("sub") or "")
        return Principal(
            run_by=sub,
            user_id=sub,
            display=_caller_display(caller),
            is_service=False,
            agent_class=agent_class,
        )

    # 2. Caller absent → trigger-driven workflow run. Class selects the fallback identity.
    armed_by = getattr(trigger, "armed_by", None) if trigger is not None else None

    if agent_class == "daemon":
        # Prefer a persisted identity row (future-proof if workflows ever get one);
        # otherwise the deterministic production-SA convention above.
        subject = await _lookup_service_subject(workflow_name, db)
        if not subject:
            subject = workflow_service_subject(workflow_name)
        return Principal(
            run_by=subject,
            user_id="",  # no live human on a daemon workflow trigger-run
            display=principal_display(workflow_name=workflow_name, armed_by=armed_by),
            is_service=True,
            agent_class=agent_class,
        )

    # user_delegated workflow trigger-run → the arming human (fail-closed if none).
    if not armed_by:
        raise PrincipalResolutionError(
            f"user_delegated workflow '{workflow_name}' trigger-run has no caller and no "
            "armer (trigger.armed_by); denying (fail-closed) — never downgrade to service"
        )
    return Principal(
        run_by=str(armed_by),
        user_id=str(armed_by),
        display=principal_display(armed_by=armed_by),
        is_service=False,
        agent_class=agent_class,
    )

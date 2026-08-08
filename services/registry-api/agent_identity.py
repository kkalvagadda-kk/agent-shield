"""Verify that a caller IS the agent pod it claims to be.

Companion to `auth_middleware.require_user` (humans, Keycloak) — this is the workload half.

WHY A SECOND IDENTITY TYPE AT ALL
---------------------------------
Three identities exist in this platform and they are routinely confused:

  1. WHICH POD is asking          -> Kubernetes ServiceAccount   <- this module
  2. WHICH HUMAN authorized a run -> RunContext HMAC (identity P0/P1)
  3. WHICH END USER               -> the Keycloak sub inside (2)

A pod resolving its tool definitions does so at STARTUP, before any run exists, so (2) and
(3) do not apply — there is no human to name yet. Only (1) is available, and (1) is enough:
the question being asked is "give me MY agent's bound tools", and the SA subject says which
agent that is.

WHY NOT JUST A HEADER
---------------------
Because `X-Agent-Name: whatever` is forgeable, and this repo has now deleted that exact
shape three times (`create_agent`, `publish_agent`, five handlers in `admin.py`). A pod
asserting its own identity is not identity. The cluster signs the SA token; TokenReview
verifies the signature; the name comes out of the verified token and not out of the request.

WHY TOKENREVIEW AND NOT LOCAL JWT VALIDATION
--------------------------------------------
The projected token is a Kubernetes-issued JWT, not a Keycloak one, so `require_user`'s JWKS
path cannot validate it. TokenReview is the API server's own answer to "is this token valid,
and who is it" — it handles rotation, revocation and the bound-token lifetime without this
service tracking any of it. `mcp-proxy` already does exactly this for its own audience; this
is the same mechanism, second consumer.

THE AUDIENCE MATTERS
--------------------
Agent pods carry THREE projected tokens, each audience-scoped:
`agentshield-opa`, `agentshield-mcp-proxy`, and (added with this module)
`agentshield-registry-api`. Audience binding is what stops a token minted for one service
being replayed against another. We verify the audience we expect, and no other.
"""
from __future__ import annotations

import logging
import os
import re

from fastapi import Depends, HTTPException, Request, status

logger = logging.getLogger(__name__)

AUDIENCE = os.environ.get("AGENT_TOKEN_AUDIENCE", "agentshield-registry-api")

# system:serviceaccount:<namespace>:<sa-name>
_SA_SUBJECT = re.compile(r"^system:serviceaccount:(?P<ns>[^:]+):(?P<sa>[^:]+)$")
# deploy-controller's k8s_client.ensure_service_account names them `agent-{name}-sa`.
_SA_NAME = re.compile(r"^agent-(?P<agent>.+)-sa$")


class AgentIdentity:
    """A VERIFIED agent pod. Constructed only from a TokenReview success."""

    __slots__ = ("sa_subject", "namespace", "sa_name", "agent_name")

    def __init__(self, sa_subject: str, namespace: str, sa_name: str, agent_name: str) -> None:
        self.sa_subject = sa_subject
        self.namespace = namespace
        self.sa_name = sa_name
        self.agent_name = agent_name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AgentIdentity agent={self.agent_name!r} sa={self.sa_subject!r}>"


def _parse(sa_subject: str) -> AgentIdentity | None:
    """Derive the agent name from a verified SA subject, or None if it is not an agent SA.

    Returning None rather than raising: plenty of legitimate ServiceAccounts in the cluster
    are not agents (deploy-controller, scheduler, the API's own). They are simply not THIS
    identity, and the caller decides what to do about that.
    """
    m = _SA_SUBJECT.match(sa_subject or "")
    if not m:
        return None
    sa_name = m.group("sa")
    n = _SA_NAME.match(sa_name)
    if not n:
        return None
    return AgentIdentity(
        sa_subject=sa_subject,
        namespace=m.group("ns"),
        sa_name=sa_name,
        agent_name=n.group("agent"),
    )


async def _token_review(token: str) -> str | None:
    """Ask the API server to validate the token. Returns the authenticated username, or None.

    Fail CLOSED on every error path. A TokenReview that errors is not a token that passed —
    treating an unreachable API server as "probably fine" would make every gate built on this
    module advisory.
    """
    try:
        from kubernetes import client, config as k8s_config  # type: ignore[import]
    except Exception:  # pragma: no cover - kubernetes is a runtime dep of the image
        logger.error("agent_identity: kubernetes client unavailable — refusing the token")
        return None

    try:
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        api = client.AuthenticationV1Api()
        review = client.V1TokenReview(
            spec=client.V1TokenReviewSpec(token=token, audiences=[AUDIENCE])
        )
        # create_token_review is sync; the router is async. It is a single in-cluster call to
        # the API server on the pod's own network, and it is cached by the API server. If it
        # ever shows up in a latency profile, wrap it in run_in_executor rather than caching
        # the RESULT here — a cached "valid" outlives revocation.
        result = api.create_token_review(review)
    except Exception as exc:  # noqa: BLE001 — see the docstring on failing closed
        logger.warning("agent_identity: TokenReview call failed: %s", exc)
        return None

    st = getattr(result, "status", None)
    if st is None or not getattr(st, "authenticated", False):
        logger.warning(
            "agent_identity: TokenReview says NOT authenticated (%s)",
            getattr(st, "error", "no error given"),
        )
        return None

    # The API server echoes the audiences it actually validated against. An empty echo means
    # it did not honour our request, which we must not read as success.
    audiences = getattr(st, "audiences", None) or []
    if AUDIENCE not in audiences:
        logger.warning(
            "agent_identity: token audience mismatch — wanted %r, got %r", AUDIENCE, audiences
        )
        return None

    user = getattr(st, "user", None)
    return getattr(user, "username", None)


def _bearer(request: Request) -> str | None:
    raw = request.headers.get("authorization") or ""
    if not raw.lower().startswith("bearer "):
        return None
    return raw.split(" ", 1)[1].strip() or None


async def get_optional_agent(request: Request) -> AgentIdentity | None:
    """Verified agent identity, or None. Never raises.

    For routes that accept EITHER a human or an agent. The route decides what to do when
    both are absent — this function's job is only to answer "is there a verified agent here".
    """
    token = _bearer(request)
    if not token:
        return None
    username = await _token_review(token)
    if not username:
        return None
    return _parse(username)


async def require_agent(request: Request) -> AgentIdentity:
    """401 unless the caller presents a valid agent-pod ServiceAccount token."""
    ident = await get_optional_agent(request)
    if ident is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "This route requires an agent ServiceAccount token "
                f"(audience {AUDIENCE!r}). Agent pods mount one at "
                "/var/run/secrets/agentshield/registry-token/token."
            ),
        )
    return ident


def require_own_agent(path_param: str = "name"):
    """Depends that 401s an unverified pod and 403s one asking about a DIFFERENT agent.

    This is the whole point of authenticating the pod. Without it,
    `GET /agents/{name}/tools` lets any pod read any agent's bindings by editing the path.
    With it, the name in the path must equal the name in the verified SA subject.

    Returns the identity so the handler can use it instead of re-deriving anything.
    """

    async def _check(request: Request, ident: AgentIdentity = Depends(require_agent)) -> AgentIdentity:
        wanted = request.path_params.get(path_param)
        if wanted != ident.agent_name:
            logger.warning(
                "agent_identity: DENY pod %s asked for agent %r", ident.sa_subject, wanted
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This token belongs to agent '{ident.agent_name}'; it cannot read "
                    f"'{wanted}'."
                ),
            )
        return ident

    return _check

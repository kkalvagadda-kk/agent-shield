"""Make the run-context signing key available to agent pods.

Identity propagation P0 — design §4.3.

An agent pod must VERIFY the run-context token registry-api minted, and EXTEND it with its
own hop. Both are HMAC operations against a shared secret, so the secret has to exist in
the agent's namespace: a pod cannot mount a Secret from another namespace.

WHY A MODULE AND NOT TWO INLINE CALLS
-------------------------------------
There are two reconcilers — `reconciler.py` (sandbox) and `production_reconciler.py` —
and they already carry a comment saying the tool-secret path is shared "so the two paths
can't drift". Identity has the same property and a worse failure mode when it drifts: an
agent deployed through the path that forgot the copy would silently run with no verifiable
identity, and OPA Gate 6 would deny every one of its tool calls with an error that names
the tool, not the missing secret. One function, two callers.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Same name in both namespaces. deploy-cpe2e.sh / deploy-eks.sh create it in the platform
# namespace; this copies it beside each agent.
RUN_CONTEXT_SECRET = "agentshield-run-context"
SIGNING_KEY_FIELD = "AGENTSHIELD_INTERNAL_SIGNING_KEY"


async def ensure_run_context_secret(namespace: str, k8s, settings) -> bool:
    """Copy the run-context Secret into `namespace`. Returns True if it is present after.

    Best-effort by design, and the return value is the point: `copy_secret` already logs
    and skips when the source is missing (an install that predates the Secret), so this
    reports what happened rather than raising. The caller logs a loud warning instead of
    crash-looping the whole reconcile — an agent that starts without identity is broken in
    one specific way, but an operator who cannot deploy anything at all is worse, and the
    OPA denial that follows is loud.
    """
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: k8s.copy_secret(
                RUN_CONTEXT_SECRET, settings.platform_namespace, namespace
            ),
        )
    except Exception as exc:  # noqa: BLE001 — see the docstring on why this degrades
        logger.warning(
            "run-context secret copy into %s FAILED (%s). Agents in this namespace will "
            "carry no verifiable identity and OPA Gate 6 will deny their tool calls with "
            "missing_user_identity. Check that Secret %s/%s exists.",
            namespace, exc, settings.platform_namespace, RUN_CONTEXT_SECRET,
        )
        return False
    return True

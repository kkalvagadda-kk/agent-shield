"""
Upstream-identity resolution (WS-C — contracts/mcp-proxy-internal-phase2.md §2).

`resolve_headers` is the single seam that decides WHICH credential the proxy presents to
an upstream MCP server. It first checks the ORTHOGONAL WS-2 `external_auth_mode` selector
(OAuth), then falls through to the WS-C `identity_mode` matrix, keyed additionally on an
explicit `is_data_plane` context (discover/health = admin plane; tools/call = data plane).
It is deliberately a pure, stateless selection layer: the only state (the minted-token /
access-token caches) lives in keycloak_client and oauth_tokens.

Selection matrix (THE authority is the contract §1/§2 — this mirrors it exactly):

  external_auth_mode  plane          result
  ------------------  -----------    ------------------------------------------------------------
  oauth               any, no sub    raise OAuthUserRequired  (fail-closed — never downgrade)
  oauth               any, sub set   {**auth_headers, "Authorization": "Bearer <access token>"}
  static (default)    any            fall through to the identity_mode matrix below (BYTE-IDENTICAL)

  identity_mode      plane          result   (reached only when external_auth_mode == "static")
  ---------------    -----------    ------------------------------------------------------------
  none               any            connection.auth_headers  (Phase-1 static creds — BYTE-IDENTICAL)
  service_identity   any            {**auth_headers, "Authorization": "Bearer <service token>"}
  on_behalf_of       admin          service token (discovery/health list an OBO server AS the platform)
  on_behalf_of       data, no sub   raise OnBehalfOfIdentityRequired  (fail-closed — never downgrade)
  on_behalf_of       data, sub set  mint_on_behalf_of_token(...) → STUB raises OnBehalfOfNotAvailable

Security invariants (violating any is a privilege-escalation):
  - The OAuth branch is CHECKED FIRST and fails closed: an OAuth server with no `user_sub`
    raises OAuthUserRequired, and an unavailable/unauthorized token raises via oauth_tokens —
    it NEVER downgrades to the (empty) static auth_headers or a service token.
  - `external_auth_mode == "static"` (the default / every pre-WS-2 Secret) SKIPS the OAuth
    branch entirely — the identity_mode matrix below is byte-identical to Phase 2.
  - `none` returns connection.auth_headers UNCHANGED — a Phase-1 server behaves exactly as before.
  - The on-behalf-of exchange is a STUB that RAISES. It NEVER substitutes a service token or any
    other real credential for a user — fail closed, loud. (Blocked on Decision 29 — see
    docs/design/identity-propagation-architecture.md.)
  - Data-plane on_behalf_of with no user_sub raises OnBehalfOfIdentityRequired — never a silent
    downgrade to the platform's service identity.
  - resolve_headers only ever RAISES for OAuth/OBO outcomes (a per-call error the caller renders
    as a 200 is_error body) or lets keycloak_client's RuntimeError propagate (identity-mint
    failure, also rendered as a 200 error body) — never a 5xx.
"""
from __future__ import annotations

import logging

import keycloak_client
import oauth_tokens
from credentials import ServerConnection

# Re-export the OAuth exceptions so main.py catches them as `identity.OAuth*` — symmetric
# to how it catches `identity.OnBehalfOf*` (identity is the one seam main.py imports for
# credential-selection outcomes). oauth_tokens remains the sole owner + raiser.
from oauth_tokens import (  # noqa: F401 — re-exported for callers (main.py)
    OAuthAuthorizationRequired,
    OAuthTokenUnavailable,
    OAuthUserRequired,
)

logger = logging.getLogger(__name__)


class OnBehalfOfIdentityRequired(Exception):
    """A data-plane call to an on_behalf_of server arrived with no user identity.

    Fail-closed: the platform must NOT impersonate no-one / fall back to its own service
    identity for a server that demands a user subject (FR-MCP-21 pt 4)."""


class OnBehalfOfNotAvailable(Exception):
    """The on-behalf-of upstream-identity exchange is not yet implemented.

    Phase-2 STUB — blocked on Decision 29 (a durable verified subject + a Keycloak
    impersonation client). It must NEVER be silently replaced by a service token."""


async def mint_on_behalf_of_token(user_sub: str, audience: str | None) -> str:
    """STUB (Phase 2): ALWAYS raises OnBehalfOfNotAvailable.

    This is the ONLY intentional placeholder in WS-C. It must fail closed and loud — it
    must NOT return a service-account token or any other real credential in place of the
    user's, which would be a privilege-escalation (the platform acting AS the user without
    the user's verified identity).

    When Decision 29 lands, replace this body with the Keycloak token-exchange /
    impersonation grant (client credentials + requested_subject=user_sub + audience),
    minting a fresh token per call (no caching). See:
      - docs/design/identity-propagation-architecture.md
      - docs/plan/mcp-tool-source-phase2/research.md (C7/C11)
    """
    raise OnBehalfOfNotAvailable(
        "on-behalf-of upstream-identity exchange is not yet available (blocked on Decision 29)"
    )


async def _service_identity_headers(connection: ServerConnection) -> dict[str, str]:
    """Mint (or reuse) the platform service token and merge it OVER the static headers —
    the minted bearer wins on `Authorization`. Lets keycloak_client's RuntimeError
    propagate (the endpoint renders it as a 200 identity-error body)."""
    token, _exp = await keycloak_client.mint_service_account_token(connection.identity_audience)
    return {**connection.auth_headers, "Authorization": f"Bearer {token}"}


async def resolve_headers(
    connection: ServerConnection,
    *,
    user_sub: str | None = None,
    is_data_plane: bool,
) -> dict[str, str]:
    """Resolve the upstream MCP connection headers for `connection`. See the module
    docstring / contract §2 for the full matrix.

    Args:
        connection: the parsed per-server Secret (carries identity_mode / identity_audience).
        user_sub:   the calling end-user's subject (data plane only; None on admin plane).
        is_data_plane: True for tools/call; False for discover/health.
    """
    # WS-2 (Phase 4) — the FIRST-checked branch. An EXTERNAL server registered with
    # external_auth_mode == "oauth" presents a short-lived, per-user upstream ACCESS token
    # (pulled from registry-api, cached in-memory by oauth_tokens). Checked BEFORE the
    # identity_mode matrix because OAuth is an ORTHOGONAL selector: external servers are
    # always identity_mode == "none", so without this ordering they'd fall to the static
    # path and never authenticate. FAIL-CLOSED — no user_sub raises OAuthUserRequired (both
    # data and admin plane: registry-api always supplies the authorizing user for an OAuth
    # discover), and an unavailable/unauthorized token raises via get_oauth_access_token.
    # It NEVER downgrades to the (empty) static auth_headers or a service token.
    if connection.external_auth_mode == "oauth":
        if not user_sub:
            raise OAuthUserRequired(
                "oauth server requires a user identity; none provided"
            )
        token = await oauth_tokens.get_oauth_access_token(connection.server_id, user_sub)
        return {**connection.auth_headers, "Authorization": f"Bearer {token}"}

    # external_auth_mode == "static" (default) → the WS-C identity_mode matrix, UNCHANGED.
    mode = connection.identity_mode

    if mode == "none":
        # Phase-1 path — return the static creds UNCHANGED (byte-identical to before).
        return connection.auth_headers

    if mode == "service_identity":
        # Any plane: present the platform's own service-account bearer.
        return await _service_identity_headers(connection)

    if mode == "on_behalf_of":
        if not is_data_plane:
            # Admin plane (discover/health): the platform lists an OBO server's tools AS
            # itself — discovery never impersonates a user. Use the service token.
            return await _service_identity_headers(connection)
        if not user_sub:
            # Data plane with no user identity → fail closed (never downgrade to service).
            raise OnBehalfOfIdentityRequired(
                "on_behalf_of server requires a user identity; none provided"
            )
        # Data plane with a user identity → the (stubbed) exchange, which RAISES.
        token = await mint_on_behalf_of_token(user_sub, connection.identity_audience)
        return {**connection.auth_headers, "Authorization": f"Bearer {token}"}

    # Unknown identity_mode (the DB CHECK constrains it to none|service_identity|on_behalf_of,
    # so this is a malformed-Secret defensive branch). Fail SAFE to the static Phase-1 creds —
    # never mint/leak the platform identity for an unrecognised mode.
    logger.warning(
        "mcp-proxy identity: unknown identity_mode %r — falling back to static auth_headers",
        mode,
    )
    return connection.auth_headers

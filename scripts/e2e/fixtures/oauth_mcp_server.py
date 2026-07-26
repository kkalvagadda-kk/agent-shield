"""
Stub OAuth Authorization Server + bearer-gated MCP server — TEST FIXTURE ONLY.

Used by suite-87-mcp-oauth.sh + smoke-mcp4-cp2/cp3. It is the OAuth analogue of
scripts/e2e/fixtures/stub_mcp_server.py: a single, self-contained process that plays
BOTH halves of the Phase-4 WS-2 flow so an OAuth authorization-code + PKCE dance can be
driven NON-INTERACTIVELY (no human consent screen) from a bash/kubectl suite:

  * an OAuth 2.1 Authorization Server (RFC 8414 / 9728 / 7591 / 7636 / 9700), and
  * a bearer-gated MCP surface at POST /mcp that 401s without a valid access token and
    serves tools/list + tools/call(echo) with one.

Because the interactive dance (discovery + code exchange + refresh) all runs INSIDE
registry-api (the single writer), this fixture is started INSIDE the registry-api pod on
127.0.0.1:9100, so registry-api can reach its .well-known/token/authorize endpoints with
no new cluster object:

    kubectl exec ... -c registry-api -- \
        sh -c 'setsid nohup python3 /tmp/oauth_mcp_server.py --port 9100 >/tmp/oauth_stub.log 2>&1 &'

Then register an MCPServer with server_url=http://127.0.0.1:9100/mcp,
is_external=true, external_auth_mode=oauth and drive:
    POST /mcp-servers/{id}/oauth/authorize   → authorization_url (points at /authorize)
    GET  <authorization_url>                 → 302 to the callback with ?code=&state=
    GET  /mcp-servers/oauth/callback?code=…  → 302 …?oauth=connected  (grant authorized)

Guarded by ``if __name__ == "__main__"`` so importing this module never starts a server —
it is INERT on import (same contract as stub_mcp_server.py).

Deliberately a PLAIN FastAPI/uvicorn app (no ``mcp`` SDK): the registry-api image ships
FastAPI + uvicorn + httpx + pydantic but NOT necessarily the ``mcp`` SDK, and the suite
drives every surface over plain HTTP (the OAuth well-knowns via httpx GET, the token/
authorize endpoints via httpx, and the /mcp bearer-gate via a direct POST). /mcp speaks a
minimal JSON body ({"method": "tools/list"|"tools/call", ...}) — enough to PROVE the
bearer-gate (401 without a token, 200 with the dance's access token), NOT the full MCP
streamable-http wire protocol (that runtime path is proven by the CP3 smoke + the real
proxy, not by this stub).

Rotation (RFC 9700 / OAuth 2.1): /token on grant_type=refresh_token ALWAYS issues a NEW
refresh token and invalidates the presented one — so a second pull observes a ROTATED
refresh token (the property T-S87-011 asserts). A stale refresh token after rotation is
rejected (invalid_grant), exactly as a spec-compliant AS would.
"""
from __future__ import annotations

import argparse
import json
import logging
import secrets
import urllib.parse
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

logger = logging.getLogger(__name__)

# The externally-reachable base URL of THIS fixture. Overwritten in __main__ from
# --base (or http://{host}:{port}); every advertised .well-known endpoint is built from
# it so the issuer check (RFC 9207) and discovery both resolve to this one origin.
BASE = "http://127.0.0.1:9100"

# Scopes the AS advertises + grants (echoed into the token response `scope`).
SCOPES = ["mcp:tools", "read"]

# ── Real MCP surface (FastMCP streamable-http) ───────────────────────────────────────
# The proxy dials /mcp with the official MCP streamable-http CLIENT, which requires the
# full protocol handshake (initialize → notifications/initialized → tools/list). A
# hand-rolled JSON endpoint can't satisfy that (it 404s `initialize`), so serve a REAL
# FastMCP app — the same library/transport the proxy uses (mirrors stub_mcp_server.py) —
# and gate it behind the AS-issued bearer. One `echo` tool: returns its input verbatim
# (the de-anonymize / round-trip proof).
# DNS-rebinding protection validates the Host header against localhost by default and
# 421s anything else. This fixture is dialed cross-pod via the proxy pod's IP (e.g.
# http://10.233.x.y:9100/mcp), so that guard would reject every real MCP request — disable
# it (a test fixture, not an internet-exposed server).
_mcp = FastMCP(
    "agentshield-oauth-stub-mcp",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@_mcp.tool()
def echo(text: str) -> str:
    """Return the provided text verbatim."""
    return text


# Starlette ASGI app serving the streamable-http MCP endpoint at /mcp. Its lifespan starts
# the StreamableHTTP session manager and MUST run — a mounted sub-app's lifespan does NOT
# fire on its own, so thread it through the parent FastAPI app's lifespan below.
_mcp_asgi = _mcp.streamable_http_app()


@asynccontextmanager
async def _lifespan(_app):
    async with _mcp_asgi.router.lifespan_context(_mcp_asgi):
        yield


app = FastAPI(title="agentshield-stub-oauth-mcp", docs_url=None, redoc_url=None,
              lifespan=_lifespan)

# ── In-memory stores (fixture-scoped, per-process; never persisted) ──────────────────
# DCR-registered clients: client_id -> client_secret.
_clients: dict[str, str] = {}
# Live authorization codes (single-use): code -> {client_id, scope, redirect_uri}.
_codes: dict[str, dict] = {}
# Live access tokens the /mcp bearer-gate accepts.
_access_tokens: set[str] = set()
# Live refresh tokens; rotated (old discarded, new issued) on every refresh.
_refresh_tokens: set[str] = set()
# refresh_token -> the access_token issued ALONGSIDE it (the most recent in its lineage).
_token_pairs: dict[str, str] = {}
# refresh_token -> the SET of every access token in its rotation lineage. registry-api's
# revoke sends only the current (rotated) refresh token, and the proxy may be serving a
# cached access token minted by an EARLIER pull in the same lineage (the background health
# loop rotates independently). Tracking the whole lineage lets /revoke invalidate the exact
# token the proxy holds — without which the fail-closed-on-revoke path (CP3 step 5) could
# never fire. Keyed per grant (each user's authorize starts a fresh lineage), so revoking
# one user's grant never touches another's tokens (suite-87 isolation).
_token_lineage: dict[str, set] = {}


def _u(path: str) -> str:
    """Absolute URL for a fixture path (built from BASE)."""
    return f"{BASE}{path}"


def _bearer(authorization: Optional[str]) -> Optional[str]:
    """Extract the raw token from an ``Authorization: Bearer <token>`` header, else None."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


async def _form(request: Request) -> dict[str, str]:
    """Parse an ``application/x-www-form-urlencoded`` body WITHOUT Starlette's
    ``request.form()`` — the OAuth clients (registry-api + the suite) always POST
    urlencoded, so parsing the raw body here keeps the fixture free of any
    ``python-multipart`` dependency the registry-api image may not ship."""
    raw = (await request.body()).decode("utf-8", "replace")
    return {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}


class _BearerGatedMCP:
    """ASGI wrapper: reject any request whose ``Authorization: Bearer`` is absent or not a
    token THIS AS issued with a ``401`` + RFC 9728 ``WWW-Authenticate`` (so fail-closed is
    observable); otherwise delegate to the real FastMCP app UNTOUCHED. Pure ASGI (no body
    buffering) because streamable-http may stream the response."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            hdrs = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            tok = _bearer(hdrs.get("authorization"))
            if not tok or tok not in _access_tokens:
                body = json.dumps({
                    "error": "invalid_token",
                    "error_description": "missing or invalid access token",
                }).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate",
                     f'Bearer resource_metadata="{_u("/.well-known/oauth-protected-resource")}"'.encode()),
                ]})
                await send({"type": "http.response.body", "body": body})
                return
        await self._app(scope, receive, send)


# ---------------------------------------------------------------------------
# Discovery — RFC 9728 (protected-resource) + RFC 8414 (authorization-server)
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    """Liveness/readiness probe for the suite's start-wait loop."""
    return {"ok": True}


@app.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    """RFC 9728 — the MCP endpoint is the protected resource; it points at THIS AS."""
    return {
        "resource": _u("/mcp"),
        "authorization_servers": [BASE],
        "scopes_supported": SCOPES,
    }


@app.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    """RFC 8414 — the AS metadata registry-api's discover_oauth_metadata consumes."""
    return {
        "issuer": BASE,
        "authorization_endpoint": _u("/authorize"),
        "token_endpoint": _u("/token"),
        "registration_endpoint": _u("/register"),
        "revocation_endpoint": _u("/revoke"),
        "scopes_supported": SCOPES,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
    }


@app.get("/.well-known/openid-configuration")
def openid_configuration():
    """OIDC discovery alias — same document (some discovery orders try this URL)."""
    return authorization_server_metadata()


# ---------------------------------------------------------------------------
# Dynamic Client Registration — RFC 7591
# ---------------------------------------------------------------------------
@app.post("/register")
async def register(request: Request):
    """RFC 7591 DCR — issue a fresh confidential client (client_id + client_secret)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a malformed body still gets a client (test stub)
        body = {}
    client_id = "stub-client-" + secrets.token_hex(6)
    client_secret = secrets.token_hex(16)
    _clients[client_id] = client_secret
    return JSONResponse(
        status_code=201,
        content={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": body.get("redirect_uris", []),
            "grant_types": body.get(
                "grant_types", ["authorization_code", "refresh_token"]
            ),
            "response_types": body.get("response_types", ["code"]),
            "token_endpoint_auth_method": "client_secret_post",
        },
    )


# ---------------------------------------------------------------------------
# Authorization endpoint — auto-approves (non-interactive) → 302 back with a code
# ---------------------------------------------------------------------------
@app.get("/authorize")
def authorize(
    response_type: str = Query(""),
    client_id: str = Query(""),
    redirect_uri: str = Query(""),
    state: str = Query(""),
    code_challenge: str = Query(""),
    code_challenge_method: str = Query(""),
    scope: str = Query(""),
    resource: str = Query(""),
):
    """Auto-approve the consent (no human in the loop) and 302 to ``redirect_uri`` with a
    one-time ``code`` + the echoed ``state`` + ``iss`` (RFC 9207). This is what makes the
    dance scriptable: the suite follows this redirect and hands the code back to the
    callback. A non-``code`` response_type or a missing redirect_uri is an error redirect
    (never a 5xx)."""
    if not redirect_uri:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "error_description": "missing redirect_uri"},
        )
    sep = "&" if "?" in redirect_uri else "?"
    if response_type != "code":
        return RedirectResponse(
            url=f"{redirect_uri}{sep}error=unsupported_response_type&state={urllib.parse.quote(state)}",
            status_code=302,
        )
    code = "stub-code-" + secrets.token_hex(8)
    _codes[code] = {
        "client_id": client_id,
        "scope": scope or " ".join(SCOPES),
        "redirect_uri": redirect_uri,
    }
    location = (
        f"{redirect_uri}{sep}code={urllib.parse.quote(code)}"
        f"&state={urllib.parse.quote(state)}&iss={urllib.parse.quote(BASE)}"
    )
    logger.info("stub-oauth: authorize auto-approved client=%s → code issued", client_id)
    return RedirectResponse(url=location, status_code=302)


# ---------------------------------------------------------------------------
# Token endpoint — authorization_code → access+refresh; refresh_token → ROTATED refresh
# ---------------------------------------------------------------------------
@app.post("/token")
async def token(request: Request):
    """RFC 6749 token endpoint (``application/x-www-form-urlencoded``).

    ``authorization_code`` → a fresh access + refresh token (the code is single-use).
    ``refresh_token``      → a fresh access token + a ROTATED refresh token; the presented
                             refresh token is invalidated (RFC 9700 rotation). A stale/
                             unknown refresh token → ``400 invalid_grant`` (revoked lockout,
                             exactly what the internal endpoint maps to a fail-closed
                             ``error`` grant)."""
    form = await _form(request)
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code":
        code = form.get("code")
        info = _codes.pop(code, None) if code else None
        if info is None:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_grant", "error_description": "unknown or used code"},
            )
        access = "stub-access-" + secrets.token_hex(12)
        refresh = "stub-refresh-" + secrets.token_hex(12)
        _access_tokens.add(access)
        _refresh_tokens.add(refresh)
        _token_pairs[refresh] = access
        _token_lineage[refresh] = {access}
        logger.info("stub-oauth: code exchanged → access+refresh issued")
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": refresh,
            "scope": info["scope"],
        }

    if grant_type == "refresh_token":
        old = form.get("refresh_token")
        if not old or old not in _refresh_tokens:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_grant",
                    "error_description": "unknown or expired refresh token",
                },
            )
        # ROTATE (RFC 9700): the presented refresh token is now dead; issue a new one.
        # Re-key the pairing onto the NEW refresh token but KEEP the old access token valid:
        # a real AS leaves already-issued access tokens live until they expire, and the proxy
        # may still be serving a cached access token from an earlier pull. (Deliberately NOT
        # invalidating the old access token here — doing so races the background health loop,
        # which pulls/rotates independently and would then kill the proxy's in-use token.)
        _refresh_tokens.discard(old)
        carried = _token_pairs.pop(old, None)
        access = "stub-access-" + secrets.token_hex(12)
        refresh = "stub-refresh-" + secrets.token_hex(12)
        _access_tokens.add(access)
        _refresh_tokens.add(refresh)
        # Map the new refresh token to EVERY access token in this lineage, so revoking it
        # later invalidates the proxy's cached token regardless of which pull minted it.
        _token_pairs[refresh] = access
        _lineage = _token_lineage.pop(old, set()) | {carried, access}
        _lineage.discard(None)
        _token_lineage[refresh] = _lineage
        logger.info("stub-oauth: refresh exchanged → access + ROTATED refresh issued")
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": refresh,
            "scope": " ".join(SCOPES),
        }

    return JSONResponse(
        status_code=400,
        content={"error": "unsupported_grant_type", "error_description": str(grant_type)},
    )


# ---------------------------------------------------------------------------
# Revocation — RFC 7009 (best-effort; disconnect calls this)
# ---------------------------------------------------------------------------
@app.post("/revoke")
async def revoke(request: Request):
    """RFC 7009 — revoke the presented token. registry-api sends the (rotated) REFRESH
    token on disconnect, so also invalidate the access token issued alongside it: a real AS
    kills a grant's access tokens when the grant is revoked, and without this the proxy's
    cached access token would keep working until expiry (the fail-closed-on-revoke path
    could never be observed). Always ``200``."""
    form = await _form(request)
    tok = form.get("token")
    if tok:
        _refresh_tokens.discard(tok)
        _access_tokens.discard(tok)
        _token_pairs.pop(tok, None)
        # Revoking a refresh token kills EVERY access token in its lineage (the proxy's
        # cached token may be any of them) — mirroring an AS that invalidates a grant's
        # tokens on revocation.
        for acc in _token_lineage.pop(tok, set()):
            _access_tokens.discard(acc)
    return JSONResponse(status_code=200, content={})


# ---------------------------------------------------------------------------
# The bearer-gated MCP surface is a REAL FastMCP streamable-http app, mounted in __main__
# (after every AS route is registered) at "/" behind _BearerGatedMCP, so POST/GET /mcp
# reaches the real protocol handler while /authorize, /token, /.well-known/* etc. keep
# matching first. See the _mcp / _BearerGatedMCP definitions above.
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="AgentShield stub OAuth AS + bearer-gated MCP server (test fixture)"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument(
        "--base",
        default=None,
        help=(
            "Externally-reachable base URL advertised in the .well-known documents "
            "(default http://{host}:{port}). Set this when the fixture is reached via a "
            "name other than 127.0.0.1 (it must match how the OAuth client dials it)."
        ),
    )
    args = parser.parse_args()

    BASE = args.base or f"http://{args.host}:{args.port}"
    logger.info("stub-oauth: serving AS + bearer-gated MCP at %s", BASE)
    # Mount the bearer-gated real MCP app LAST — after every @app AS route is registered —
    # at "/" so the specific AS paths (/authorize, /token, /.well-known/*, /register,
    # /revoke, /healthz) match first and only the otherwise-unmatched /mcp falls through to
    # the FastMCP handler. Mount("/") passes the path through unchanged, so the sub-app sees
    # "/mcp" (its default streamable_http_path) — no trailing-slash remount needed.
    app.mount("/", _BearerGatedMCP(_mcp_asgi))
    # Blocks — serves every endpoint above at http://{host}:{port}.
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")

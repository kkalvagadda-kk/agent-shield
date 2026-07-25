"""
Stub MCP server — TEST FIXTURE ONLY (suite-84 discovery smoke + suite-85 list_changed).

A minimal, protocol-compliant MCP server built on the official `mcp` SDK's FastMCP
(pinned <2.0, same lib the proxy uses). It is COPIED into the mcp-proxy image
(/app/fixtures/) and started on demand INSIDE the running proxy pod:

    kubectl exec ... -- python3 fixtures/stub_mcp_server.py &

Because it shares the proxy's network namespace, registering an MCPServer with
server_url=http://127.0.0.1:9999/mcp lets the proxy dial a real MCP server with no
new cluster object. It is guarded by `if __name__ == "__main__"` so importing this
module never starts a server — it is inert in a normal deployment.

Tools:
  echo(text) -> text    returns its input VERBATIM — the de-anonymize proof (a
                        de-anonymized PII value round-trips unchanged).
  add(a, b)  -> a + b    a trivial typed tool for a non-string result path.
  simulate_tool_change(action="add"|"remove") -> str
                        CONTROL tool (Phase 2 / WS-B): at runtime it registers
                        (action="add") or removes (action="remove") a tool named
                        `dynamic_echo` and emits `notifications/tools/list_changed` to
                        connected sessions, so a subscribed client re-lists tools. This
                        is what drives the proxy's subscription_manager → registry-api
                        `/internal/mcp/list-changed` re-sync path.

list_changed capability:
  The fixture forces `capabilities.tools.listChanged=true` at `initialize` (FastMCP's
  default is false, so it must be patched — see `_advertise_list_changed`). Without this
  the proxy would set `list_changed_supported=false` and never subscribe.

Runtime-emit vs. the `--toolset` FALLBACK (read before extending):
  The PRIMARY mechanism is runtime mutation + `send_tool_list_changed()` (above). It is
  proven by the quickstart's throwaway client, which SUBSCRIBES (registers a
  message_handler) and calls `simulate_tool_change` on the SAME session — that session
  reliably receives the notification. Emitting to a SEPARATE, idle subscriber (e.g. the
  proxy's held-open subscription session, which never itself calls a tool) depends on the
  deployed FastMCP minor's session bookkeeping and is best-effort here (we broadcast to
  every session the fixture has seen via a tool call). For a deterministic in-cluster
  proof that does NOT rely on cross-session broadcast, use the documented FALLBACK:

      python3 stub_mcp_server.py --toolset extended    # dynamic_echo present from startup
      python3 stub_mcp_server.py --toolset base         # default: echo/add/simulate only

  Restarting the fixture with a different `--toolset` changes which tools exist at
  startup, so a plain `POST /api/v1/internal/mcp/list-changed` (or the health loop's next
  discover) re-lists and picks up / drops `dynamic_echo` — simulating the tool-set change
  without needing a cross-session runtime notification. `--toolset` still advertises
  listChanged. (This fallback exists precisely because a FastMCP build may not broadcast a
  runtime notification to an unrelated subscriber — confirm the runtime path at CP2.)
"""
from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# FastMCP (1.x) takes transport host/port in the constructor; streamable-http mounts
# the MCP endpoint at /mcp by default. 127.0.0.1 only — reachable solely from inside
# the proxy pod that exec'd it.
mcp = FastMCP("agentshield-stub-mcp", host="127.0.0.1", port=9999)

# Name of the tool that appears/disappears under simulate_tool_change — the list_changed
# proof (a subscribed client sees it added/removed after a re-sync).
_DYNAMIC_TOOL_NAME = "dynamic_echo"

# Sessions the fixture has observed via a tool call — best-effort broadcast targets for
# notifications/tools/list_changed. A plain set (fixture-scoped, short-lived); dead
# sessions are pruned when a send fails. See the module docstring on broadcast limits.
_tracked_sessions: set = set()


# ---------------------------------------------------------------------------
# Static tools (Phase 1 — unchanged)
# ---------------------------------------------------------------------------

@mcp.tool()
def echo(text: str) -> str:
    """Return the provided text verbatim (used to prove de-anonymize substitution)."""
    return text


@mcp.tool()
def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


# ---------------------------------------------------------------------------
# Runtime tool mutation (WS-B) — the dynamic_echo tool + register/unregister helpers
# ---------------------------------------------------------------------------

def _dynamic_echo(text: str) -> str:
    """Only exists after simulate_tool_change('add') — the list_changed proof tool."""
    return f"dynamic:{text}"


def _tool_registry() -> dict:
    """FastMCP's internal name->Tool map (mcp 1.x ToolManager._tools).

    Accessed defensively: a version that renamed the internal store degrades to an
    empty map (add/remove become no-ops) rather than crashing the fixture.
    """
    tm = getattr(mcp, "_tool_manager", None)
    return getattr(tm, "_tools", {}) if tm is not None else {}


def _register_dynamic_echo() -> bool:
    """Register dynamic_echo if absent. Returns True iff the tool set changed."""
    if _DYNAMIC_TOOL_NAME in _tool_registry():
        return False
    mcp.add_tool(
        _dynamic_echo,
        name=_DYNAMIC_TOOL_NAME,
        description="Runtime-added echo tool (proves notifications/tools/list_changed).",
    )
    return True


def _unregister_dynamic_echo() -> bool:
    """Remove dynamic_echo if present. Returns True iff the tool set changed."""
    registry = _tool_registry()
    if _DYNAMIC_TOOL_NAME in registry:
        registry.pop(_DYNAMIC_TOOL_NAME, None)
        return True
    return False


def _track_current_session() -> None:
    """Record the session running the current request so we can notify it later.

    Uses `mcp.get_context()` — the ambient-context accessor valid across the pinned
    mcp>=1.2,<2.0. (mcp 2.0 removes it in favour of a `ctx: Context` tool parameter;
    when the pin moves to 2.0, inject `ctx` and read `ctx.session` instead.)
    """
    try:
        ctx = mcp.get_context()
        session = getattr(ctx, "session", None)
        if session is not None:
            _tracked_sessions.add(session)
    except Exception:  # noqa: BLE001 — no active request context / SDK shape drift
        pass


async def _broadcast_list_changed() -> None:
    """Emit notifications/tools/list_changed to every known session (best-effort).

    Always reaches the CALLING session (the quickstart's subscribed client). Reaching a
    separate idle subscriber depends on it having been tracked via a prior tool call —
    see the module docstring; use `--toolset` for a deterministic cross-session proof.
    """
    _track_current_session()
    dead: list = []
    for session in list(_tracked_sessions):
        try:
            await session.send_tool_list_changed()
        except Exception:  # noqa: BLE001 — closed/broken session
            dead.append(session)
    for session in dead:
        _tracked_sessions.discard(session)


@mcp.tool()
async def simulate_tool_change(action: str = "add") -> str:
    """Mutate the tool set at runtime and emit notifications/tools/list_changed (WS-B).

    action='add'    → register `dynamic_echo` (idempotent — re-adding is a no-op).
    action='remove' → remove `dynamic_echo` (idempotent — removing when absent is a no-op).
    In both cases a list_changed notification is broadcast so a subscribed client
    re-lists and observes the change; a subsequent tools/list reflects the new set.
    """
    normalized = (action or "add").strip().lower()
    if normalized == "remove":
        changed = _unregister_dynamic_echo()
    else:
        normalized = "add"
        changed = _register_dynamic_echo()
    await _broadcast_list_changed()
    present = _DYNAMIC_TOOL_NAME in _tool_registry()
    return f"action={normalized} changed={changed} dynamic_echo_present={present}"


# ---------------------------------------------------------------------------
# Capability advertisement — force tools.listChanged=true at initialize
# ---------------------------------------------------------------------------

def _advertise_list_changed() -> None:
    """Make FastMCP advertise capabilities.tools.listChanged=true.

    FastMCP builds its initialize capabilities from NotificationOptions(), whose
    `tools_changed` default is False — so listChanged is NOT advertised out of the box.
    We wrap the low-level server's create_initialization_options to inject
    NotificationOptions(tools_changed=True). Guarded: an SDK-internal change degrades to
    "not advertised" (the proxy would then report list_changed_supported=false) rather
    than crashing the fixture.
    """
    try:
        from mcp.server.lowlevel.server import NotificationOptions

        srv = mcp._mcp_server
        _orig = srv.create_initialization_options

        def _patched(notification_options=None, experimental_capabilities=None, *args, **kwargs):
            opts = notification_options or NotificationOptions()
            try:
                opts.tools_changed = True
            except Exception:  # noqa: BLE001
                opts = NotificationOptions(tools_changed=True)
            return _orig(opts, experimental_capabilities, *args, **kwargs)

        srv.create_initialization_options = _patched  # type: ignore[method-assign]
    except Exception as exc:  # noqa: BLE001
        logger.warning("stub: could not force tools.listChanged capability: %s", exc)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="AgentShield stub MCP server (test fixture)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument(
        "--toolset",
        choices=["base", "extended"],
        default="base",
        help=(
            "base = echo/add/simulate_tool_change (default); extended additionally "
            "registers dynamic_echo at startup. Restarting with a different --toolset "
            "simulates a tool-set change without a runtime notification (see module docstring)."
        ),
    )
    args = parser.parse_args()

    mcp.settings.host = args.host
    mcp.settings.port = args.port

    # Advertise tools.listChanged so the proxy subscribes; select the startup tool set.
    _advertise_list_changed()
    if args.toolset == "extended":
        _register_dynamic_echo()

    # Blocks — serves the streamable-HTTP MCP endpoint at http://{host}:{port}/mcp.
    mcp.run(transport="streamable-http")

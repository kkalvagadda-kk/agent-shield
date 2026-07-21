"""
Stub MCP server — TEST FIXTURE ONLY (suite-84 / CP2 discovery smoke).

A minimal, protocol-compliant MCP server built on the official `mcp` SDK's FastMCP
(pinned <2.0, same lib the proxy uses). It is COPIED into the mcp-proxy image
(/app/fixtures/) and started on demand INSIDE the running proxy pod:

    kubectl exec ... -- python3 fixtures/stub_mcp_server.py &

Because it shares the proxy's network namespace, registering an MCPServer with
server_url=http://127.0.0.1:9999/mcp lets the proxy dial a real MCP server with no
new cluster object. It is guarded by `if __name__ == "__main__"` so importing this
module never starts a server — it is inert in a normal deployment.

Tools:
  echo(text) -> text   returns its input VERBATIM — the de-anonymize proof (a
                       de-anonymized PII value round-trips unchanged).
  add(a, b)  -> a + b   a trivial typed tool for a non-string result path.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

# FastMCP (1.x) takes transport host/port in the constructor; streamable-http mounts
# the MCP endpoint at /mcp by default. 127.0.0.1 only — reachable solely from inside
# the proxy pod that exec'd it.
mcp = FastMCP("agentshield-stub-mcp", host="127.0.0.1", port=9999)


@mcp.tool()
def echo(text: str) -> str:
    """Return the provided text verbatim (used to prove de-anonymize substitution)."""
    return text


@mcp.tool()
def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AgentShield stub MCP server (test fixture)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    # Blocks — serves the streamable-HTTP MCP endpoint at http://{host}:{port}/mcp.
    mcp.run(transport="streamable-http")

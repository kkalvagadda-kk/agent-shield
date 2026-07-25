# Quickstart — MCP as a Tool Source, Phase 2

> **All build/deploy steps here are DEFERRED for the planning run** (the plan produces artifacts only). They are recorded so a later implementer can run them. Do not `helm`/`kubectl`/`docker` while only producing the plan. Phase 2 builds on the **deployed Phase-1 stack** — the `mcp-proxy` service, the `mcp_servers`/`internal_mcp` routers, and `suite-84` must already be green before starting.

## Prerequisites

- Phase-1 MCP-as-tool-source deployed and green (`bash scripts/e2e/suite-84-mcp-tools.sh`). If it isn't, Phase 2 has nothing to extend.
- Local k8s running the AgentShield platform (`charts/agentshield` via `scripts/deploy-cpe2e.sh`); `kubectl` at that cluster, namespace `agentshield-platform`; the `agentshield-mcp` namespace exists (Phase-1 sub-chart).
- The platform **Keycloak** reachable in-cluster (WS-C service-identity). A confidential client for the proxy must exist — see "Keycloak client" below.
- Python 3.12 + `pip` for local iteration on `services/mcp-proxy`; Node/npm for Studio; `docker` for rebuilt images.

## One-time setup for this feature

1. **No migration to create.** Confirm the head is still `0072` (Phase 2 adds none):
   ```bash
   ls services/registry-api/alembic/versions/ | sort | tail -3        # 0072_mcp_server_fields.py is head
   kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- alembic current   # 0072
   ```
   If a `0073`+ landed on the branch, that is someone else's migration — Phase 2 still adds none.

2. **Pin the `mcp` SDK notification + FastMCP mutation hooks (Task 1).** Against the version already pinned in `services/mcp-proxy/requirements.txt` (`mcp>=1.2,<2.0`):
   ```bash
   python3 -c "import mcp, inspect; from mcp import ClientSession; print(mcp.__version__); print([p for p in inspect.signature(ClientSession.__init__).parameters])"
   python3 -c "from mcp.server.fastmcp import FastMCP; import inspect; print([m for m in dir(FastMCP) if 'tool' in m.lower() or 'notif' in m.lower()])"
   ```
   Record: the notification-handler hook (`message_handler=` kwarg vs. iterating `session.incoming_messages`), the `list_changed` notification type name, and how `FastMCP` adds/removes a tool at runtime + emits `notifications/tools/list_changed`. These feed Tasks 5 and 6. If the installed SDK can't emit a runtime notification, use the documented fallback (restart the fixture with a different toolset) and record it.

3. **Confirm the Keycloak token endpoint (Task 1 / WS-C).**
   ```bash
   grep -nE "KEYCLOAK_URL|KEYCLOAK_REALM|openid-connect/token" services/registry-api/auth_middleware.py services/registry-api/keycloak_client.py
   ```
   The proxy's client-credentials call targets `{KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/token`. Wire this into `charts/agentshield/values.yaml` `mcp-proxy.keycloak.tokenUrl`.

4. **Confirm the current image tags before bumping** (captured baseline: `MCP_PROXY_TAG=0.1.0`, `REGISTRY_API_TAG=0.2.226`, `STUDIO_TAG=0.1.161`, `DECLARATIVE_RUNNER_TAG=0.1.60`, `sdk.__version__=0.2.3`):
   ```bash
   grep -E '^(MCP_PROXY_TAG|REGISTRY_API_TAG|STUDIO_TAG|DECLARATIVE_RUNNER_TAG)=' scripts/deploy-cpe2e.sh
   ```
   Never reuse a claimed tag; mirror each bump in `charts/agentshield/values.yaml` (and, for `mcp-proxy`, the sub-chart values).

5. **Confirm the e2e suite number.** `suite-84` is Phase 1; Phase 2 uses `suite-85`.
   ```bash
   ls scripts/e2e/ | grep -oE 'suite-[0-9]+' | sed 's/suite-//' | sort -n | tail -3
   ```

## Keycloak client (WS-C, deploy prerequisite)

Service-identity needs a Keycloak **confidential client** for the proxy (client-credentials / service-account grant enabled). The OBO fill-in later also needs an impersonation grant on it — not now. Provision it like every other platform Keycloak client; put its client secret in the chart:
```yaml
# charts/agentshield/values.yaml
mcp-proxy:
  keycloak:
    tokenUrl: "http://keycloak.agentshield-platform:8080/realms/agentshield/protocol/openid-connect/token"
    clientId: "agentshield-mcp-proxy"
    clientSecret: "<from a sealed/managed secret; or set existingSecret to reference one>"
```
The chart mounts the secret at `/var/run/secrets/mcp-proxy-keycloak/client-secret` (a file, not an API read — the proxy's `get secrets` RBAC stays scoped to `agentshield-mcp`).

## Running the MCP Proxy locally (fast iteration on Tasks 2/6/9)

`services/mcp-proxy` has **no DB dependency**. The health probe, subscription manager, and identity branch are testable standalone with the Keycloak call + Secret read + TokenReview stubbed:
```bash
cd services/mcp-proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export REGISTRY_API_URL=http://localhost:8000
export MCP_SECRETS_NAMESPACE=agentshield-mcp
export MCP_PROXY_AUDIENCE=agentshield-mcp-proxy
export KEYCLOAK_TOKEN_URL=http://localhost:8081/realms/agentshield/protocol/openid-connect/token
export MCP_PROXY_KEYCLOAK_CLIENT_ID=agentshield-mcp-proxy
export MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH=/tmp/mcp-proxy-keycloak-secret   # echo a dummy secret into it
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

## Simulating a `list_changed` event with the stub fixture (Task 5)

The Phase-2 fixture gains a control tool `simulate_tool_change`. Run it, subscribe with a throwaway `mcp` client, and trigger a change:
```bash
# terminal 1 — the extended fixture (advertises tools.listChanged; supports runtime mutation)
python3 scripts/e2e/fixtures/stub_mcp_server.py --port 9999

# terminal 2 — a throwaway client: connect, subscribe, call simulate_tool_change, observe the notification
python3 - <<'PY'
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
async def main():
    async with streamablehttp_client(url="http://127.0.0.1:9999/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:   # register a message_handler per Task-1's pinned hook
            await s.initialize()
            print("tools before:", [t.name for t in (await s.list_tools()).tools])
            await s.call_tool("simulate_tool_change", {"action": "add"})
            await asyncio.sleep(0.5)
            print("tools after:", [t.name for t in (await s.list_tools()).tools])  # includes dynamic_echo
asyncio.run(main())
PY
```
In-cluster (what `suite-85` does): start the fixture inside the proxy pod (`kubectl exec ... python3 fixtures/stub_mcp_server.py &`), register it via `POST /api/v1/mcp-servers` against `http://127.0.0.1:9999/mcp`, then `simulate_tool_change` and assert the registry auto-re-synced (a new `Tool` row appears on `GET /mcp-servers/{id}`).

## Watching the health loop (Task 3)

```bash
kubectl logs -n agentshield-platform deploy/agentshield-registry-api -f | grep -i "mcp health"
# register a server pointing at a dead URL, then watch status flip after >=3 cycles:
watch -n 5 'kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- \
  psql "$DATABASE_URL" -c "select name,status,health_detail->>'"'"'consecutive_failures'"'"' from mcp_servers"'
```
Single-flight: `kubectl scale deploy/agentshield-registry-api --replicas=2` and confirm `consecutive_failures` advances by 1 per interval (not 2).

## Building and deploying (DEFERRED)

```bash
bash scripts/deploy-cpe2e.sh          # rebuilds mcp-proxy / registry-api / studio / declarative-runner (bumped tags)
kubectl rollout status deployment/agentshield-mcp-proxy -n agentshield-platform --timeout=3m
kubectl auth can-i --as=system:serviceaccount:agentshield-platform:agentshield-mcp-proxy get secrets -n agentshield-mcp        # yes
kubectl auth can-i --as=system:serviceaccount:agentshield-platform:agentshield-mcp-proxy get secrets -n agentshield-platform   # NO (unchanged — Keycloak secret is a mount, not an API read)
```

## Running the new backend e2e suite (Task 11, DEFERRED)

```bash
bash scripts/e2e/suite-85-mcp-health-notify-identity.sh
bash scripts/e2e/run-all.sh
```

## Running the regression sweep (Task 13, DEFERRED)

Phase-2's blast radius (WS-C `resolve_headers` in the tool-call path; the `_materialize_and_discover` extraction in `/sync`):
```bash
bash scripts/e2e/suite-84-mcp-tools.sh          # whole Phase-1 MCP path — must stay byte-identical for none-servers
bash scripts/e2e/suite-18-opa-governance.sh
bash scripts/e2e/suite-4-hitl.sh
bash scripts/e2e/suite-3-safety.sh
```

## Running Studio tests

```bash
cd studio && npm run test -- McpServerDetailPage        # Vitest — Task 4 Health panel
cd studio && npm run typecheck
bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts       # Playwright (Task 12, DEFERRED — against deployed Studio)
```

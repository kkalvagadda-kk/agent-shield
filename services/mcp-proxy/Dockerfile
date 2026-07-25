FROM python:3.12-slim

WORKDIR /app

# BUILD CONTEXT IS THE REPO ROOT (docker build -f services/mcp-proxy/Dockerfile .)
# because the stub fixture below lives outside services/mcp-proxy/. Every COPY
# source is therefore repo-root-relative — bare `requirements.txt` would resolve
# to the repo root and fail.
COPY services/mcp-proxy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Flat module layout — run as `uvicorn main:app` (matches python-executor + quickstart).
# GLOB every module (NOT an explicit per-file list): a hand-maintained list silently
# drops any NEW module (identity.py / keycloak_client.py / subscription_manager.py were
# added in Phase 2 and a stale list crash-looped the pod with ModuleNotFoundError —
# docs/bugs/mcp-proxy-dockerfile-missing-modules.md). The glob resolves against the
# repo-root build context; services/mcp-proxy/ holds only shippable modules (no tests).
COPY services/mcp-proxy/*.py ./

# Test-only stub MCP server, started on demand inside the pod via `kubectl exec`
# (never auto-started — inert unless run explicitly).
COPY scripts/e2e/fixtures/stub_mcp_server.py /app/fixtures/

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

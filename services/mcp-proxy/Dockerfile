FROM python:3.12-slim

WORKDIR /app

# BUILD CONTEXT IS THE REPO ROOT (docker build -f services/mcp-proxy/Dockerfile .)
# because the stub fixture below lives outside services/mcp-proxy/. Every COPY
# source is therefore repo-root-relative — bare `requirements.txt` would resolve
# to the repo root and fail.
COPY services/mcp-proxy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Flat module layout — run as `uvicorn main:app` (matches python-executor + quickstart).
COPY services/mcp-proxy/config.py services/mcp-proxy/schemas.py services/mcp-proxy/k8s_client.py \
     services/mcp-proxy/authn.py services/mcp-proxy/credentials.py services/mcp-proxy/authz.py \
     services/mcp-proxy/mcp_client.py services/mcp-proxy/session_cache.py services/mcp-proxy/main.py ./

# Test-only stub MCP server, started on demand inside the pod via `kubectl exec`
# (never auto-started — inert unless run explicitly).
COPY scripts/e2e/fixtures/stub_mcp_server.py /app/fixtures/

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

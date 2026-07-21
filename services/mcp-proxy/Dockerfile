FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Flat module layout — run as `uvicorn main:app` (matches python-executor + quickstart).
COPY config.py schemas.py k8s_client.py authn.py credentials.py authz.py \
     mcp_client.py session_cache.py main.py ./

# Test-only stub MCP server, started on demand inside the pod via `kubectl exec`
# (never auto-started — inert unless run explicitly). Build context is the repo root.
COPY scripts/e2e/fixtures/stub_mcp_server.py /app/fixtures/

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

# mcp-proxy Dockerfile — COPY paths incompatible with its own build context

**Found/Fixed:** 2026-07-23 · fixed in mcp-proxy `0.1.0` (branch `mcp-tool-source`, first-ever build for the EKS test cluster).

## Symptom

`docker buildx build --platform linux/amd64 -f services/mcp-proxy/Dockerfile --push .` (repo-root context) failed on the first COPY:

```
ERROR: failed to compute cache key: failed to calculate checksum of ref ...:
"/requirements.txt": not found
```

registry-api `0.2.225` built fine immediately before; only mcp-proxy failed, and it failed all 4 retry attempts (the retry loop re-runs the same broken build, so retries can't help a path bug).

## Root cause

The Dockerfile mixed two mutually-exclusive context assumptions:

- `COPY requirements.txt .` and `COPY config.py schemas.py … main.py ./` use **bare** filenames → resolve relative to the build context, so they only work when the context is `services/mcp-proxy/`.
- `COPY scripts/e2e/fixtures/stub_mcp_server.py /app/fixtures/` reaches **outside** `services/mcp-proxy/` → only works when the context is the **repo root**.

No single build context satisfies both. The documented invariant (deploy scripts, quickstart, this plan's CP2 note) is **repo-root context** — required so the stub fixture is reachable — which is exactly the context under which the bare-path COPYs fail. So the image could never have built as written; it was never cluster-proven (CP2 was a deferred, unrun checkpoint), so the defect surfaced only at the first real build during the EKS deploy.

## Fix

Make every COPY source repo-root-relative, consistent with the one build context that can include the fixture:

```dockerfile
COPY services/mcp-proxy/requirements.txt .
COPY services/mcp-proxy/config.py services/mcp-proxy/schemas.py … services/mcp-proxy/main.py ./
COPY scripts/e2e/fixtures/stub_mcp_server.py /app/fixtures/
```

This is the class-fix (not a per-file patch): the Dockerfile now has ONE consistent context assumption — repo root — matching `deploy-eks.sh` / `deploy-cpe2e.sh` / `deploy-mcp-cp2.sh`, all of which build it as `-f services/mcp-proxy/Dockerfile .`. A comment at the top states the invariant so the next edit doesn't reintroduce a bare path.

## Lessons

- A "written but not executed" checkpoint (CP1/CP2 deferred) hides build-time defects. The Dockerfile passed AST/YAML static checks but had never been fed to a builder.
- Retry loops around a deterministic failure (a missing COPY source) waste 4× the time and obscure the real cause — the log's first COPY error is the signal, not the retry count.

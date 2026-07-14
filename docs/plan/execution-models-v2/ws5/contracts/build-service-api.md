# WS-5 Contract — build-service API + Kaniko Job + registry-api build endpoints

## build-service (`services/build-service/`)

```
POST /builds
  body: {"agent_name": "refund-bot", "version_id": "uuid", "team": "payments", "source_url": "..."}
  202:  {"build_id": "uuid", "status": "building"}

GET  /builds/{build_id}/logs        (SSE) → text/event-stream of Kaniko log lines; terminal event carries
                                            {"status": "succeeded"|"failed", "image": "registry/.../tag"}
```

- On `POST /builds`: fetch `agent.py` from MinIO (`source_url`), render the Kaniko Job manifest, apply it in
  namespace `agentshield-builds`, and watch it. Update `build_status` via a callback to registry-api
  (`building` on start, `succeeded`/`failed` on completion).
- **Baked Dockerfile** (a server-side constant delivered to Kaniko via a ConfigMap — the user never supplies
  it):

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir agentshield-sdk
WORKDIR /app
COPY agent.py /app/agent.py
CMD ["python", "-m", "agentshield_sdk.server"]
```

No user-controlled `FROM`, no build args from the user, no `RUN` injection — the only user input is the
`agent.py` bytes, `COPY`d in. Illegal build states (custom base, privilege escalation) are unrepresentable.

## Kaniko Job (`kaniko_job.py`) — the build sandbox

- Namespace `agentshield-builds` (isolated); ServiceAccount with **only** the RBAC to run Builds (no
  cluster-wide access).
- Kaniko runs **unprivileged** (`gcr.io/kaniko-project/executor`) — no Docker daemon, no privileged pod, no
  host Docker socket.
- **NetworkPolicy** restricts egress to: the internal image registry + PyPI (for `pip install`). No other
  egress. A malicious `agent.py` can't exfiltrate or reach the cluster network.
- Job TTL + resource limits; logs streamed to the build-service, which relays them over SSE.
- Pushes the built image to the internal registry with a unique tag (per version — never reuse a tag, matching
  the platform image-versioning rule).

## registry-api build endpoints

```
POST /api/v1/agents/{name}/builds
  body: {"source_code": "def build(): ..."}          # the agent.py contents from Monaco
  202:  {"version_id": "uuid", "build_status": "pending"}
  # side effects: PUT agent.py → MinIO agent-source/{team}/{name}/{version}/agent.py;
  #               set agent_versions.source_url + build_status='pending';
  #               POST build-service /builds.

GET  /api/v1/agents/{name}/versions/{version_id}/build-logs   (SSE)
  # proxies the build-service /builds/{build_id}/logs stream to the browser.

POST /internal/builds/{version_id}/status   (build-service → registry-api callback, internal auth)
  body: {"status": "succeeded", "image": "registry/.../tag"}  |  {"status": "failed", "error": "..."}
  # on succeeded → build_status='succeeded' + auto-create agent_version(image, source_url) [+ optional deploy]
  # on failed    → build_status='failed'; NO version created, NO deploy (fail-closed)
```

## Egress + safety summary

| Boundary | Control |
|---|---|
| Dockerfile | server-side constant (ConfigMap); no user override |
| Base image | pinned `python:3.12-slim` |
| Build privileges | Kaniko unprivileged; isolated namespace SA |
| Build egress | NetworkPolicy → registry + PyPI only |
| Failed build | no version, no deploy (fail-closed) |
| Image tag | unique per version (no reuse) |

# WS-5 Implementation Plan — SDK in-browser build (Kaniko) — the SDK onboarding path

**Slice:** WS-5 of Execution Models v2 (spec §5 WS-5; `docs/spec.md` §"In-Browser SDK Agent Editor +
Platform-Managed Image Build"). **Covers WS-5 ONLY.**
**Depends on WS-1 (SDK `/run` exists)** so browser-built SDK agents can be durable. Independent of WS-2/3/4.
**Companion artifacts:** `data-model.md` (`source_url` + `build_status`), `contracts/build-service-api.md`.

> **Migration PROVISIONAL** — next free after the spine. WS-5 adds `agent_versions.source_url` +
> `build_status`. Confirm head at impl. **Greenfield verified 2026-07-12:** no `services/build-service/`, no
> `source_url`/`build_status` columns, and Studio's CodeForm stores `metadata.source_code` as a **stub** with
> no build pipeline — SDK onboarding still needs local Docker today.

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

## 1. Goal

Turn "SDK durable exists" (WS-1) into "a non-DevOps user ships a durable SDK agent from a browser tab." No
local Docker toolchain. Concretely, after WS-5:

1. **`services/build-service/` (new)** — FastAPI that accepts `{agent_name, source_code}`, runs a **Kaniko
   K8s Job** (BuildKit alternative) in a dedicated `agentshield-builds` namespace, streams build logs, updates
   the version `build_status`. The **Dockerfile is baked in** (not user-editable); base is always
   `python:3.12-slim` + `agentshield-sdk`; **no `FROM` override**; egress limited to the registry + PyPI.
2. **MinIO `agent-source` bucket** — source at `{team}/{agent}/{version}/agent.py` (reuse the existing MinIO,
   already deployed for Langfuse).
3. **Registry API** — `agent_versions.source_url` (MinIO pointer) + `build_status` columns; on build success
   → auto-create an `agent_version` (+ optional deploy-immediately).
4. **Studio** — Monaco editor in `CreateAgentPage` (replaces the `metadata.source_code` **stub** CodeForm) +
   `EditAgentPage`; a build-log stream panel via SSE (`GET /agents/{name}/versions/{id}/build-logs`).
5. **Flow:** write `agent.py` in Monaco → submit → save to MinIO → Kaniko Job builds → SSE logs → push to the
   internal registry → auto-create version → deploy. No local Docker.

**Out of scope:** editing the Dockerfile / base image (locked for safety); non-Python SDK agents; the durable
execution semantics themselves (WS-1). WS-5 is **onboarding**, not execution — but it's what makes the flipped
SDK-durable scope reachable by non-DevOps users.

## 2. Architecture

```
Studio Monaco (CreateAgentPage/EditAgentPage)
   │ POST /api/v1/agents/{name}/builds  {source_code}
   ▼
registry-api: save agent.py → MinIO agent-source/{team}/{agent}/{version}/agent.py
   │ set agent_versions.source_url + build_status='pending'
   │ POST build-service /builds {agent_name, version_id, source_url}
   ▼
build-service (services/build-service/): spawn Kaniko K8s Job in ns agentshield-builds
   │ baked Dockerfile: FROM python:3.12-slim; pip install agentshield-sdk; COPY agent.py
   │ egress-restricted (registry + PyPI only); no FROM override
   │ stream logs → build_status: building → succeeded/failed
   ▼ on success: image pushed to internal registry → callback registry-api
registry-api: build_status='succeeded'; auto-create agent_version(source_url, image); optional deploy
   ▲ SSE GET /agents/{name}/versions/{id}/build-logs  ← Studio streams the Kaniko log
```

**Safety posture (No-Bandaid, make illegal states unrepresentable):** the Dockerfile is a server-side
constant — the user supplies only `agent.py`. There is **no** user-controlled `FROM`, no arbitrary build
args, and the build Job has a NetworkPolicy restricting egress to the registry + PyPI. A malicious `agent.py`
can't escape the sandboxed build (Kaniko runs unprivileged; the Job namespace is isolated).

## 3. Migration / Schema — see `data-model.md`

`agent_versions.source_url TEXT NULL` + `build_status VARCHAR NULL` (values `pending|building|succeeded|
failed`). Idempotent, guarded.

## 4. Constitution / retro gates (condensed)

- **Parity:** WS-5 has no sandbox/prod fork — it's a single build path. The Studio Monaco editor is shared by
  create + edit (one `<AgentCodeEditor>` component, not two).
- **Golden-path per environment:** bash `suite-60` POSTs source → build-service spawns a Kaniko Job → polls
  `build_status` to `succeeded` → image in the internal registry → `agent_version` auto-created with
  `source_url`; bad code → `build_status=failed` + logs surfaced. Playwright: Monaco → submit → build-log SSE
  streams → version appears → deploy → **durable SDK agent runs** (ties WS-1 + WS-5 in one journey). Fails
  (not skips) if the build namespace/registry is unreachable.
- **Ship the gate's producer:** `build_status` (producer = build-service callback) ships with its reader
  (Studio status panel + auto-create-version) — no orphan gate.
- **Fail-closed:** a failed build → `build_status='failed'` + logs; it does **not** auto-create a version or
  deploy a broken image.
- **No-Bandaid:** Dockerfile baked server-side (illegal `FROM` unrepresentable); build egress restricted.

## 5. File Structure

### build-service (new)
| File | C/M | Responsibility |
|---|---|---|
| `services/build-service/main.py` | **C** | FastAPI `POST /builds`, `GET /builds/{id}/logs` (SSE); spawn + watch the Kaniko Job. |
| `services/build-service/kaniko_job.py` | **C** | K8s Job manifest (baked Dockerfile via ConfigMap, egress NetworkPolicy, unprivileged). |
| `services/build-service/Dockerfile` | **C** | The build-service's own image. |
| `charts/agentshield/templates/build-service.yaml` | **C** | Deployment + SA + RBAC (Jobs in `agentshield-builds`) + NetworkPolicy. |
| `charts/agentshield/values.yaml` | M | `buildService` block + image tag; `agentshield-builds` namespace. |

### registry-api
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/routers/agents.py` (or `builds.py`) | M/**C** | `POST /agents/{name}/builds`, `GET /agents/{name}/versions/{id}/build-logs` (SSE proxy), build-success callback → auto-create version. |
| `services/registry-api/models.py` | M | `AgentVersion.source_url` + `build_status`. |
| `services/registry-api/alembic/versions/00NN_agent_version_build.py` | **C** | `source_url` + `build_status` (provisional number). |
| `services/registry-api/minio_client.py` | M/**C** | `agent-source` bucket put/get (reuse existing MinIO client). |

### Studio
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/components/AgentCodeEditor.tsx` | **C** | Monaco editor (shared by create + edit); submit → build. |
| `studio/src/pages/CreateAgentPage.tsx` | M | Replace the `metadata.source_code` CodeForm **stub** with `<AgentCodeEditor>`. |
| `studio/src/pages/EditAgentPage.tsx` | M/**C** | Edit `agent.py` + rebuild. |
| `studio/src/components/BuildLogPanel.tsx` | **C** | SSE build-log stream. |
| `studio/src/api/registryApi.ts` | M | `submitBuild`, `streamBuildLogs`. |

### Tests + infra
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-60-sdk-build.sh` | **C** | POST source → Kaniko Job → `build_status=succeeded` → image → version auto-created; bad code → failed + logs. |
| `scripts/e2e/run-all.sh` | M | Register suite-60. |
| `studio/e2e/sdk-build.spec.ts` | **C** | Monaco → submit → build-log SSE → version → deploy → durable SDK run (WS-1+WS-5 journey). |
| `studio/src/components/AgentCodeEditor.test.tsx` | **C** | Vitest: editor renders, submit posts source. |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Add `BUILD_SERVICE_TAG`; bump registry-api, studio. |
| `docs/experience/playground.md` | M | In-browser SDK build UX. |

## 6. Tasks (dependency-ordered)

### T1 — Migration + models (`source_url` + `build_status`)
- **Files:** migration `00NN` (C), `models.py` (M). Contract: `data-model.md`.
- **Acceptance:** upgrade round-trips; `build_status` CHECK in `{pending,building,succeeded,failed}`; mapper
  configures. **Deps:** none. **Verify:** `ast.parse` + `configure_mappers()`; migration up/down/up.

### T2 — build-service + Kaniko Job (baked Dockerfile, egress-restricted)
- **Files:** `services/build-service/*` (C), chart template + values (C/M). Contract: `contracts/build-service-api.md`.
- **Acceptance:** `POST /builds` spawns a Kaniko Job in `agentshield-builds`; the Job uses the baked
  Dockerfile (no user `FROM`); a NetworkPolicy restricts egress; logs stream; `build_status` transitions
  pending→building→succeeded/failed. **Deps:** T1. **Verify:** `ast.parse`; `kubectl apply --dry-run` on the Job manifest; suite-60 build cases.

### T3 — registry-api build endpoints + MinIO + auto-create version
- **Files:** `routers/agents.py`/`builds.py` (M/C), `minio_client.py` (M/C).
- **Contract:** `POST /agents/{name}/builds` saves to MinIO + sets `source_url`/`build_status=pending` +
  calls build-service; SSE `GET .../build-logs` proxies the build-service stream; success callback →
  auto-create `agent_version`. **Fail-closed:** failed build → no version, no deploy.
- **Acceptance:** happy path auto-creates a version with `source_url`; failed build leaves no version.
- **Deps:** T1, T2. **Verify:** suite-60 (success + failure cases).

### T4 — Studio Monaco editor + build-log panel
- **Files:** `AgentCodeEditor.tsx` (C), `CreateAgentPage.tsx` (M), `EditAgentPage.tsx` (M/C),
  `BuildLogPanel.tsx` (C), `registryApi.ts` (M), `AgentCodeEditor.test.tsx` (C).
- **Acceptance:** Monaco replaces the stub CodeForm; submit posts source; build logs stream via SSE; version
  appears on success. `npm run typecheck` clean.
- **Deps:** T3. **Verify:** `cd studio && npm run typecheck && npm run test`.

### T5 — E2E (WS-1+WS-5 journey) + deploy
- **Files:** `suite-60-sdk-build.sh` (C), `sdk-build.spec.ts` (C), `run-all.sh` (M),
  `deploy-cpe2e.sh`+`values.yaml` (M), `docs/experience/playground.md` (M).
- **Acceptance:** suite-60 green; Playwright drives Monaco → build → deploy → **durable SDK agent runs**;
  `BUILD_SERVICE_TAG` added to both files; registry-api + studio bumped.
- **Deps:** T1–T4. **Verify:** `bash scripts/e2e/suite-60-sdk-build.sh`; `bash scripts/studio-e2e.sh`.

## 7. Gap Ledger
| Item | Status | Note |
|---|---|---|
| User-editable Dockerfile / base image | **out of scope (intentional, safety)** | Baked server-side; no `FROM` override — illegal by construction. |
| Non-Python SDK agents | deferred (intentional) | Base is `python:3.12-slim`; other runtimes are a follow-up. |
| Build cache / layer reuse across versions | not-yet-optimized (debt, low) | Kaniko cold builds first; caching is a perf follow-up. |
| BuildKit alternative to Kaniko | documented option | Kaniko chosen (unprivileged, no daemon); BuildKit noted as an alternative in spec. |

No orphan flags: `source_url`/`build_status` (producer=build-service callback, reader=Studio panel +
auto-create-version), build-service `/builds` (caller=registry-api), SSE logs (reader=BuildLogPanel).

## 8. Execution Notes
- **After WS-1** — the WS-1+WS-5 golden journey (build → deploy → **durable** SDK run) is the acceptance
  proof; sequence WS-5 so SDK `/run` already exists.
- **Reuse existing MinIO + image-versioning + deploy path** — don't stand up a second object store.
- **Kaniko unprivileged + egress NetworkPolicy** — the build sandbox is the safety boundary; do not grant the
  build Job cluster-wide network or a Docker socket.
- **Fail-closed** — a failed build must not auto-create a version or deploy a stale image.

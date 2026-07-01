# AgentShield — Gap Remediation Tasks

**Generated**: 2026-06-29  
**Source**: Gap Analysis Report (gap-analysis session, post-Langfuse integration)  
**Execution strategy**: Sequential  
**Total tasks**: 44 (25 implementation + 19 test)  
**Phases**: 5  

**Design rule**: Every implementation task is paired with a test task in the **same phase**. Tests are not deferred. A phase is not done until both the code change and its test pass.

---

## Cross-cutting conventions

All new e2e test cases follow the existing format in `scripts/e2e/`:
- `pass()`, `fail()`, `check_manual()` helpers
- `PASS / FAIL / MANUAL` counters
- Exit `0` on all-pass, `1` on any failure
- Assertions via `kubectl exec -n $NAMESPACE $API_POD -- python3 -c "..."`
- Langfuse assertions via `curl http://localhost:4000/api/public/...` with pre-shared keys

All image rebuilds must bump the tag and update `deploy-cpe2e.sh` in the same commit (per image-versioning rule).

---

## Phase G1 — Observability Infrastructure + Tests (CRITICAL)

**Why first**: The platform is operationally dark. No request-scoped traces, no cost tracking, no latency instrumentation. E2E tests pass while this is completely broken. This phase fixes both the instrumentation and adds assertions that would have caught the gap.

**Addresses**: FR-010, FR-034–040; gap analysis items 1, 2, 8.

---

### G1-001 — `agent_runs` table (central invocation primitive)

**Files**: `services/registry-api/alembic/versions/0007_agent_runs.py`, `services/registry-api/models.py`, `services/registry-api/schemas.py`

**What to implement**:
- New Alembic migration `0007_agent_runs.py`:
  ```sql
  CREATE TABLE agent_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name VARCHAR(256) NOT NULL,
    agent_version_id UUID REFERENCES agent_versions(id) ON DELETE SET NULL,
    session_id VARCHAR(256),
    user_id VARCHAR(256),
    input TEXT,
    output TEXT,
    langfuse_trace_id VARCHAR(256),
    cost_usd NUMERIC(10, 6),
    prompt_tokens INT,
    completion_tokens INT,
    latency_ms INT,
    status VARCHAR(32) DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed', 'blocked')),
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    completed_at TIMESTAMP WITH TIME ZONE,
    context VARCHAR(32) DEFAULT 'production' CHECK (context IN ('production', 'playground'))
  );
  CREATE INDEX ix_agent_runs_agent_name ON agent_runs(agent_name);
  CREATE INDEX ix_agent_runs_session_id ON agent_runs(session_id);
  CREATE INDEX ix_agent_runs_started_at ON agent_runs(started_at DESC);
  ```
- `AgentRun` SQLAlchemy ORM model in `models.py` (append, no changes to existing models)
- `AgentRunCreate`, `AgentRunUpdate`, `AgentRunResponse` Pydantic schemas in `schemas.py`
- `POST /api/v1/agent-runs` and `GET /api/v1/agent-runs?agent_name=&session_id=` in new router `services/registry-api/routers/agent_runs.py`; mount in `main.py`

**Verify**: `curl -X POST .../api/v1/agent-runs -d '{"agent_name":"test-agent","session_id":"s1"}' ` returns 201 with UUID; `GET /api/v1/agent-runs?agent_name=test-agent` returns it.

---

### G1-002 — Safety Orchestrator: per-scan Langfuse spans with trace propagation

**Files**: `services/safety-orchestrator/orchestrator.py`, `services/safety-orchestrator/config.py`

**What to implement**:
- Read `X-AgentShield-Trace-ID` header in `POST /api/v1/scan/input` and `POST /api/v1/scan/output` FastAPI routes; pass `trace_id` down to `orchestrator.scan_input()` / `scan_output()`.
- In `orchestrator.py`, for each scanner in the fan-out (`asyncio.gather`), emit a Langfuse **span** with:
  - `name`: `"safety-scan-{scanner_name}"` (e.g. `safety-scan-llm-guard`, `safety-scan-presidio`)
  - `input`: truncated message text (first 200 chars)
  - `output`: `{"blocked": bool, "reason": str, "latency_ms": int}`
  - `metadata`: `{"agent_name": str, "session_id": str, "scanner": str}`
  - `start_time` / `end_time` to capture per-scanner latency
- The parent trace is created with `trace_id` from header (if present) — this stitches safety spans into the agent's broader trace.
- All Langfuse calls remain in try/except — never block scanning.
- Re-emit `X-AgentShield-Trace-ID` in the scan response so callers can propagate it.

**Verify**: Call `POST /scan/input` with header `X-AgentShield-Trace-ID: test-trace-001`. Then `curl http://localhost:4000/api/public/traces/test-trace-001` returns trace with spans named `safety-scan-*`.

---

### G1-003 — Registry API: `X-AgentShield-Trace-ID` middleware

**Files**: `services/registry-api/main.py`, `services/registry-api/tracing.py`

**What to implement**:
- Add FastAPI middleware that reads (or generates) `X-AgentShield-Trace-ID` from request headers on every request.
- If absent, generate a UUID and set it on `request.state.trace_id`.
- Emit it in the response header so callers can stitch spans.
- Update `tracing.py`: `create_platform_trace(trace_id, route, user_id)` — called by middleware on write routes (`POST /agents`, `POST /deploy`, `PATCH /approvals/{id}`); emits Langfuse trace tagged with `route` and `user_id`.
- In `routers/deployments.py` deploy endpoint: create `AgentRun` record with `status="running"` on deploy trigger; store `langfuse_trace_id` from trace.

**Verify**: `POST /api/v1/agents/{name}/deploy` response contains `X-AgentShield-Trace-ID` header; Langfuse shows a trace for the deploy action.

---

### G1-004 — Safety Orchestrator image rebuild (0.1.2 → 0.1.3)

**Files**: `scripts/deploy-cpe2e.sh`, `charts/agentshield/charts/safety-orchestrator/templates/deployment.yaml`

**What to implement**:
- Safety orchestrator already has Langfuse SDK in `requirements.txt` and tracing code in `orchestrator.py` (from prior session) but was never rebuilt.
- Update `deploy-cpe2e.sh`: add `safety-orchestrator:0.1.3` build step (currently `--set safety-orchestrator.enabled=false`; keep disabled flag but build the image so it's ready).
- Update safety-orchestrator Helm deployment template: `image.tag: 0.1.3`.
- Update the comment block at the top of `deploy-cpe2e.sh` listing image versions.

**Verify**: `docker images | grep safety-orchestrator` shows `0.1.3`; Helm template renders `image: registry.internal/agentshield/safety-orchestrator:0.1.3`.

---

### G1-T001 [TEST] — Suite 1: add Langfuse health assertions

**File**: `scripts/e2e/suite-1-health.sh`

**Add these test cases at the end of Suite 1**:

**T-S1-007 — Langfuse Web Pod Ready**:
- `kubectl get pods -n $NAMESPACE -l app.kubernetes.io/name=langfuse-web -o jsonpath='{.items[0].status.phase}'` == `Running`
- Port-forward `svc/agentshield-langfuse-web` on `4000:3000` (or use in-cluster URL from registry-api pod)
- `GET http://agentshield-langfuse-web.{NAMESPACE}:3000/api/public/health` returns `{"status":"OK"}`

**T-S1-008 — Langfuse Project Auto-Bootstrapped**:
- `GET /api/public/projects` with `Authorization: Basic <base64(pk-lf-agentshield-dev-local-0001:sk-lf-agentshield-dev-local-0001)>`
- Assert response contains project with `id == "00000000-0000-0000-0001-agentshield01"` and `name == "AgentShield Platform"`

**T-S1-009 — Langfuse ClickHouse and MinIO Aliases Resolve**:
- From registry-api pod: `getent hosts agentshield-langfuse-clickhouse` resolves (not NXDOMAIN)
- `getent hosts agentshield-langfuse-s3` resolves

**Verify**: `bash scripts/e2e/suite-1-health.sh` exits 0 with T-S1-007 through T-S1-009 all PASS.

---

### G1-T002 [TEST] — Suite 3: add per-scan Langfuse trace assertion

**File**: `scripts/e2e/suite-3-safety.sh`

**Add after T-S3-006**:

**T-S3-007 — Safety Scan Emits Langfuse Trace**:
- Call `POST /api/v1/scan/input` with header `X-AgentShield-Trace-ID: e2e-s3-trace-$(date +%s)` and a known-injection payload.
- Wait up to 10s polling `GET http://agentshield-langfuse-web.{NAMESPACE}:3000/api/public/traces/{trace_id}` until it appears.
- Assert trace has at least one span named `safety-scan-*`.
- Assert the span's `output.blocked == true`.

**T-S3-008 — Scan Latency Recorded in Span Metadata**:
- Same trace as T-S3-007.
- Assert span has `startTime` and `endTime` both non-null.
- Compute `latency_ms = endTime - startTime`; assert `latency_ms > 0` and `latency_ms < 5000`.

**Verify**: Both tests PASS in `bash scripts/e2e/suite-3-safety.sh`.

---

### G1-T003 [TEST] — New: `scripts/e2e/suite-13-observability.sh`

**File**: `scripts/e2e/suite-13-observability.sh` (NEW)

**Purpose**: Dedicated observability test suite. A CI checkpoint — if this fails, the platform is operationally dark and cannot ship.

**Test cases**:

**T-S13-001 — Trace Appears Within 10s of Scan**:
- Submit clean-text scan with unique trace_id header.
- Poll Langfuse every 1s for up to 10s.
- Assert trace appears before timeout.

**T-S13-002 — Safety Scan Trace Has Agent Name in Metadata**:
- Submit scan with `agent_name: "observability-test-agent"`.
- Fetch trace from Langfuse.
- Assert trace or top-level span metadata contains `agent_name == "observability-test-agent"`.

**T-S13-003 — Blocked Scan Trace Records Reason**:
- Submit injection payload (`ignore previous instructions`).
- Fetch trace from Langfuse.
- Assert span `output.blocked == true` and `output.reason` is non-empty string.

**T-S13-004 — Clean Scan Trace Records Unblocked**:
- Submit benign payload (`What is the weather today?`).
- Fetch trace.
- Assert span `output.blocked == false`.

**T-S13-005 — Eval Run Trace Appears in Langfuse**:
- Create a dataset and eval-run via registry-api.
- Simulate result posting (`POST /eval-runs/{id}/results`).
- PATCH eval-run status to `completed`.
- Fetch Langfuse traces filtered by session.
- Assert at least one trace with `name` containing `eval-run`.

**T-S13-006 — Langfuse Worker Processes Events** (bucket/ClickHouse pipeline):
- Submit 3 scans with unique trace_ids in rapid succession.
- Wait 15s for Langfuse worker to flush ClickHouse.
- `GET /api/public/traces?limit=10` returns all 3 trace IDs in the list.

**T-S13-007 — X-AgentShield-Trace-ID Returned in Scan Response**:
- Submit scan with `X-AgentShield-Trace-ID: round-trip-test-001`.
- Assert response headers contain `X-AgentShield-Trace-ID: round-trip-test-001`.

**T-S13-008 — Agent Run Record Created on Deploy**:
- POST deploy for a test agent.
- GET `/api/v1/agent-runs?agent_name=<name>` returns a record with `status=running`.

Add `suite-13-observability.sh` to `scripts/e2e/run-all.sh`.

**Verify**: `bash scripts/e2e/suite-13-observability.sh` exits 0; all 8 test cases PASS.

---

**Phase G1 Verification**: `bash scripts/e2e/suite-1-health.sh && bash scripts/e2e/suite-3-safety.sh && bash scripts/e2e/suite-13-observability.sh` — all PASS.

---

## Phase G2 — OPA Bundle Server + Tests (HIGH)

**Why**: Policy changes must propagate to running agent OPA sidecars. Without a Bundle Server, deploy-controller pushes data.json to ConfigMaps but OPA sidecars have no mechanism to poll for updates. Static mounts don't hot-reload.

**Addresses**: gap analysis item 6; T-S1-003 (currently untestable).

---

### G2-001 — OPA Bundle Server Deployment

**Files**: `infra/opa-bundle-server/` (already exists per git status — extend), `charts/agentshield/charts/opa-bundle-server/` (new sub-chart)

**What to implement**:

`infra/opa-bundle-server/configmap-nginx.yaml`:
```yaml
# nginx.conf ConfigMap serving:
#   /bundles/agentshield/data.json    → /data/agentshield/data.json
#   /bundles/agentshield/policy.rego  → /data/agentshield/policy.rego
# /data/ is a shared EmptyDir volume written by bundle-sync initContainer
```

`charts/agentshield/charts/opa-bundle-server/templates/deployment.yaml`:
- Pod with two containers:
  - `nginx` — serves `/bundles/agentshield/` from volume at `/data/agentshield/`
  - `bundle-sync` — init/sidecar container that `wget`s `http://registry-api:8000/api/v1/bundle` every 30s and writes `data.json` + `policy.rego` to the shared volume
- `ClusterIP Service` on port 8181 named `agentshield-opa-bundle-server`

`services/registry-api/routers/bundle.py` (new):
- `GET /api/v1/bundle/data.json` — calls `bundle_generator.generate_data()`, returns JSON
- `GET /api/v1/bundle/policy.rego` — calls `policy_generator.generate_policy()`, returns Rego text
- Mount in `main.py`

Update `manifest_builder.py` OPA sidecar container args:
```yaml
args:
  - "run"
  - "--server"
  - "--bundle"
  - "http://agentshield-opa-bundle-server.agentshield-platform:8181/bundles/agentshield"
  - "--log-level=error"
```
(Replace static ConfigMap bundle mount with live bundle polling.)

---

### G2-002 — `bundle_generator.py` correctness

**Files**: `services/registry-api/bundle_generator.py`

**What to implement**:
- Audit `bundle_generator.generate_data()`: ensure it outputs `{"agents": { "<agent_name>": {"tools": [...], "team": "...", "agent_class": "...", "risk_level": "..."} }}` — the schema OPA policy Rego expects.
- Add `generate_data_for_agent(agent_name)` — single-agent snapshot (used for incremental cache busting).
- Add `/api/v1/bundle/data.json` endpoint in `bundle.py` that calls `generate_data()` and returns `application/json`.
- Add `/api/v1/bundle/policy.rego` endpoint that returns current policy Rego text.

---

### G2-T001 [TEST] — Suite 1: OPA Bundle Server assertions (fix T-S1-003)

**File**: `scripts/e2e/suite-1-health.sh`

The existing T-S1-003 is incomplete — add:

**T-S1-003 (expanded)**:
- `GET http://agentshield-opa-bundle-server.{NAMESPACE}:8181/bundles/agentshield/data.json` returns 200.
- Response parses as JSON with `"agents"` key present.
- `GET http://agentshield-opa-bundle-server.{NAMESPACE}:8181/bundles/agentshield/policy.rego` returns 200 with non-empty body.

**T-S1-010 — OPA Sidecar Polls Bundle Server**:
- Deploy a test agent; wait for pod ready.
- `kubectl exec -n agents-platform {agent_pod} -c opa -- curl -s http://localhost:8181/v1/data/agentshield` returns data matching what bundle server serves at `data.json`.

**Verify**: T-S1-003 and T-S1-010 PASS in `bash scripts/e2e/suite-1-health.sh`.

---

### G2-T002 [TEST] — Suite 2: bundle update propagates after new deploy

**File**: `scripts/e2e/suite-2-lifecycle.sh`

**T-S2-009 — Bundle Reflects New Agent After Deploy**:
- POST register a new agent `bundle-test-agent-{timestamp}` and deploy it.
- Wait 35s (OPA polls every 30s).
- `GET http://registry-api:{port}/api/v1/bundle/data.json`; assert `agents["bundle-test-agent-{timestamp}"]` key exists.

**Verify**: T-S2-009 PASS in `bash scripts/e2e/suite-2-lifecycle.sh`.

---

**Phase G2 Verification**: `bash scripts/e2e/suite-1-health.sh && bash scripts/e2e/suite-2-lifecycle.sh` — T-S1-003, T-S1-010, T-S2-009 all PASS.

---

## Phase G3 — Authorization Enforcement + Tests (CRITICAL)

**Why**: Two critical authorization gaps — machine identity (SA token validation) and Class B user intersection rule — are unverified and likely unenforced. Deploy gate has two missing pre-flight checks.

**Addresses**: gap analysis items 3, 4, 5.

---

### G3-001 — Class B user intersection rule in OPA policy

**Files**: `services/registry-api/policy_generator.py`, `sdk/agentshield_sdk/opa_client.py`

**What to implement**:

In `policy_generator.py`, update Rego policy template to add:
```rego
# Class B (user_delegated): BOTH agent scope AND user's grant required
allow {
    input.agent_class == "user_delegated"
    agent := data.agents[input.agent_name]
    input.tool_name == agent.tools[_]            # agent has tool in scope
    input.user_team == agent.allowed_teams[_]    # user's team matches agent's allowed teams
}

# Reason for rejection — emitted as structured reason field
deny_reason = "user_not_granted" {
    input.agent_class == "user_delegated"
    agent := data.agents[input.agent_name]
    input.tool_name == agent.tools[_]
    not input.user_team == agent.allowed_teams[_]
}

deny_reason = "agent_scope_denied" {
    agent := data.agents[input.agent_name]
    not input.tool_name == agent.tools[_]
}
```

In `sdk/agentshield_sdk/opa_client.py`, update the OPA input payload to include:
```python
{
    "agent_name": agent_name,
    "tool_name": tool_name,
    "agent_class": agent_class,  # "daemon" or "user_delegated"
    "user_team": user_team,      # extracted from JWT claim "team" or env USER_TEAM
    "sa_subject": sa_subject     # from mounted SA token projected volume
}
```

Read `USER_TEAM` from environment (injected by deploy-controller for Class B agents).

---

### G3-002 — Machine identity: OPA validates SA token subject

**Files**: `services/registry-api/policy_generator.py`, `services/deploy-controller/manifest_builder.py`

**What to implement**:

In `manifest_builder.py`, the OPA sidecar already has a projected volume for the SA token. Add:
- Env var `EXPECTED_SA_SUBJECT` in the OPA sidecar container: `system:serviceaccount:agents-{team}:{agent_name}-sa`
- This is the expected OIDC subject claim for the agent's ServiceAccount.

In `policy_generator.py`, add to Rego:
```rego
# Machine identity gate — rejects requests where SA subject doesn't match agent
allow {
    # existing class A or B allow rules
    input.sa_subject == data.agents[input.agent_name].expected_sa_subject
}
```

In `data.json` generation in `bundle_generator.py`, include `expected_sa_subject` field:
```json
{
  "agents": {
    "order-agent": {
      "expected_sa_subject": "system:serviceaccount:agents-platform:order-agent-sa",
      ...
    }
  }
}
```

---

### G3-003 — Deploy gate: adversarial eval + critical tool checks

**Files**: `services/deploy-controller/reconciler.py`

**What to implement**:

The reconciler's `_pre_flight_checks()` method (or equivalent) must enforce all 5 gates:

1. ✅ Deploying team owns the agent (already checked)
2. ✅ All required tool grants present (already checked — may be dev-mode)
3. ✅ `eval_passed` flag is true (already checked)
4. ❌ **ADD**: Adversarial eval — if `agent.risk_level == "high"`, assert `agent.adversarial_eval_passed == True`; if not, return 422 with `{"detail": "high-risk agent requires adversarial eval: set adversarial_eval_passed=true"}`
5. ❌ **ADD**: Critical tool rejection — for each tool bound to the agent, assert `tool.risk != "critical"`; if any critical tool found, return 422 with `{"detail": "agent has critical-risk tool: {tool_name}"}`

Remove any dev-mode fallback comments or `# may not be enforced` notes — the checks must be hard gates with specific HTTP status codes.

Also update `services/registry-api/models.py` to add `adversarial_eval_passed: bool = False` to `AgentVersion` if not already present, and ensure the field is included in `AgentVersionResponse`.

---

### G3-T001 [TEST] — Suite 7: SA token and Class B assertions

**File**: `scripts/e2e/suite-7-machine-identity.sh`

**T-S7-009 — Class B Agent: User Without Grant Gets 403**:
- Deploy a `user_delegated` class agent with tools allowed only for team `platform`.
- Call agent `/chat` with OPA input spoofed to include `user_team: "finance-team"` (wrong team).
- Assert OPA returns `allow: false` with `deny_reason: "user_not_granted"`.

**T-S7-010 — Class B Agent: User With Grant Gets Allow**:
- Same agent; call with `user_team: "platform"`.
- Assert OPA returns `allow: true`.

**T-S7-011 — SA Subject Mismatch Rejected**:
- Submit OPA query with `sa_subject: "system:serviceaccount:agents-platform:wrong-agent-sa"` for an agent whose expected subject is `order-agent-sa`.
- Assert OPA returns `allow: false`.

**T-S7-012 — SA Subject Match Allowed**:
- Submit OPA query with correct `sa_subject`.
- Assert `allow: true`.

**Verify**: All 4 new test cases PASS in `bash scripts/e2e/suite-7-machine-identity.sh`.

---

### G3-T002 [TEST] — Suite 6: deploy gate completeness

**File**: `scripts/e2e/suite-6-asset-lifecycle.sh`

**T-S6-LG-001 — Adversarial Eval Gate: High-Risk Agent Blocked**:
- Register an agent with `risk_level: "high"`, `adversarial_eval_passed: false`.
- `POST /agents/{name}/deploy` → assert HTTP 422.
- Assert error detail contains `"adversarial eval"`.

**T-S6-LG-002 — Adversarial Eval Gate: After Setting Flag Deploy Succeeds**:
- `PATCH /agents/{name}/versions/{id}` to set `adversarial_eval_passed: true`.
- `POST /agents/{name}/deploy` → assert HTTP 202/201 (accepted).

**T-S6-LG-003 — Critical Tool Gate: Deploy With Critical Tool Blocked**:
- Create a tool with `risk: "critical"`.
- Bind to a test agent.
- `POST /agents/{name}/deploy` → assert HTTP 422.
- Assert error detail contains tool name.

**T-S6-LG-004 — Deploy Gate Returns Structured Error (not generic 500)**:
- Each gate violation returns 422 (not 500, not 400).
- Error body is `{"detail": "..."}` with an actionable message.

**Verify**: All 4 test cases PASS in `bash scripts/e2e/suite-6-asset-lifecycle.sh`.

---

**Phase G3 Verification**: `bash scripts/e2e/suite-7-machine-identity.sh && bash scripts/e2e/suite-6-asset-lifecycle.sh` — all new tests PASS.

---

## Phase G4 — Playground Completion + Tests (HIGH)

**Why**: Playground is the primary developer experience surface. 40% of its API is missing. The trace panel, dataset curation, feedback, and eval-compare are all unimplemented despite having data models.

**Addresses**: gap analysis item 7; playground-spec.md §3–7.

---

### G4-001 — `GET /playground/runs/{id}/trace`

**Files**: `services/registry-api/routers/playground.py`, `services/registry-api/tracing.py`

**What to implement**:
- Fetch `PlaygroundRun` by `id`; assert `langfuse_trace_id` is set (else 404).
- Call Langfuse `GET /api/public/traces/{langfuse_trace_id}` using the platform API keys.
- Reshape the Langfuse response into a Playground-specific schema:
  ```python
  class PlaygroundTraceResponse:
      run_id: UUID
      langfuse_trace_id: str
      spans: list[PlaygroundSpan]  # name, latency_ms, input, output
      safety_decision: dict | None  # blocked, reason, scanner
      total_latency_ms: int
      token_count: int | None
      cost_usd: float | None
  ```
- Registry API calls Langfuse internally — Studio never hits Langfuse directly.

---

### G4-002 — `POST /playground/runs/{id}/save-to-dataset`

**Files**: `services/registry-api/routers/playground.py`

**What to implement**:
- Accept `{"dataset_id": UUID}` in body.
- Fetch the `PlaygroundRun`; assert it has `status=completed`.
- Create a `PlaygroundDatasetItem` (or similar) linking `run_id → dataset_id` with run's `input` and `output`.
- Return `{"dataset_item_id": UUID, "dataset_id": UUID}`.
- 409 if the run is already in the dataset.

---

### G4-003 — `POST /playground/runs/{id}/feedback`

**Files**: `services/registry-api/routers/playground.py`, `services/registry-api/models.py`

**What to implement**:
- Accept `{"score": float, "comment": str | None, "source": "human|judge"}`.
- Update `PlaygroundRun.judge_score` and `judge_score_source` (add these fields to model if missing; create migration `0008_playground_feedback.py`).
- Return updated run.
- 422 if score not in `[0.0, 1.0]`.

---

### G4-004 — Playground HITL: context filter + Slack suppression

**Files**: `services/registry-api/routers/approvals.py`, `services/registry-api/approval_notifier.py` (when Phase 11 ships)

**What to implement**:
- Add `GET /api/v1/approvals?context=playground` filter (add `context` query param to approvals list endpoint).
- In approval creation (`POST /approvals`): if `context == "playground"`, set `send_slack_notification = False` on the approval record (add `notify_slack BOOLEAN DEFAULT true` column to `approvals` via migration `0009_approval_notify_flag.py`).
- Document the field in schema so when Phase 11 Slack notifier ships, it checks `notify_slack` before sending.

---

### G4-005 — Async LLM-as-Judge scorer

**Files**: `services/registry-api/routers/playground.py`, `services/registry-api/judge.py` (new)

**What to implement**:

New file `services/registry-api/judge.py`:
```python
async def score_run(run_id: UUID, agent_name: str, input_text: str, output_text: str) -> float:
    """Calls the platform's LLM provider to score output quality 0.0-1.0."""
    # Uses ANTHROPIC_API_KEY or first available LLM provider for the agent's team
    # Prompt: "Rate the following response 0.0–1.0 for helpfulness and accuracy..."
    # Returns float; on any error returns None (non-blocking)
```

In the playground `POST /playground/runs` handler, after the run record is created and status set to `completed`, launch `asyncio.create_task(score_run(...))` — fires-and-forgets; the task calls `PATCH /playground/runs/{id}` to store `judge_score` once computed.

The judge must complete within 30s (timeout); if it doesn't, `judge_score` stays null and a `judge_status: "timeout"` field is set on the run.

---

### G4-T001 [TEST] — Suite 8: expand playground assertions

**File**: `scripts/e2e/suite-8-playground.sh`

**Add these test cases**:

**T-S8-008 — GET /playground/runs/{id}/trace Returns Reshaped Trace**:
- Create a playground run with a mocked `langfuse_trace_id` (store a real trace_id from an earlier scan).
- `GET /playground/runs/{id}/trace` → assert 200.
- Assert response has `spans` array (may be empty if trace has no spans yet).
- Assert `total_latency_ms >= 0` and `run_id` matches.

**T-S8-009 — Save Run to Dataset**:
- Create a playground run (status=completed).
- `POST /playground/runs/{id}/save-to-dataset` with a valid `dataset_id`.
- Assert 201 response with `dataset_item_id`.
- Re-submit same request → assert 409.

**T-S8-010 — Submit Feedback**:
- `POST /playground/runs/{id}/feedback` with `{"score": 0.8, "source": "human"}`.
- Assert 200 and `judge_score == 0.8` in response.
- Submit with `score: 1.5` → assert 422.

**T-S8-011 — Playground Approval Filtered by Context**:
- Create two approvals: one with `context=production`, one with `context=playground`.
- `GET /approvals?context=playground` → assert only playground approval in results.
- `GET /approvals?context=production` → assert only production approval in results.

**T-S8-012 — Playground Approval Has Slack Suppressed**:
- Create approval with `context=playground`.
- Assert `notify_slack == false` on the created approval record.

**Verify**: All 5 new test cases PASS in `bash scripts/e2e/suite-8-playground.sh`.

---

**Phase G4 Verification**: `bash scripts/e2e/suite-8-playground.sh` — T-S8-008 through T-S8-012 all PASS.

---

## Phase G5 — E2E Suite Observability Sweep (CRITICAL — Tests Only)

**Why**: The existing 12 suites pass without asserting anything about observability. This phase adds one Langfuse assertion to each suite that tests an action the suite already performs — no new features required, just connecting the dots.

**Rule**: Each test verifies that the action performed in that suite produces a trace or record in Langfuse. If it doesn't, that's a regression to fix immediately — not a SKIP.

---

### G5-001 [TEST] — Suite 2: trace emitted on deploy

**File**: `scripts/e2e/suite-2-lifecycle.sh`

**T-S2-LG-001 — Deploy Action Emits Langfuse Trace**:
- After `POST /agents/{name}/deploy` (already in T-S2-005), capture the `X-AgentShield-Trace-ID` from response header.
- Poll `GET /api/public/traces/{trace_id}` for up to 10s.
- Assert trace appears with `name` containing `deploy`.

---

### G5-002 [TEST] — Suite 4: HITL approval emits trace

**File**: `scripts/e2e/suite-4-hitl.sh`

**T-S4-LG-001 — HITL Approval Events Traced**:
- After `PATCH /approvals/{id}` to approve (already in T-S4-004), capture `X-AgentShield-Trace-ID`.
- Poll Langfuse for trace within 10s.
- Assert trace metadata includes `approval_id` and `status: "approved"`.

---

### G5-003 [TEST] — Suite 9: eval run trace in Langfuse

**File**: `scripts/e2e/suite-9-eval.sh`

**T-S9-LG-001 — Eval Run Creates Langfuse Trace**:
- After `POST /eval-runs/{id}/results` (already in T-S9-007), fetch Langfuse traces filtered by session_id matching the eval run's `id`.
- Assert at least one trace with metadata `eval_run_id` == the created run's UUID.

---

### G5-004 [TEST] — Suite 10: multi-agent trace stitched

**File**: `scripts/e2e/suite-10-multi-agent.sh`

**T-S10-LG-001 — Handoff Traces Share Session Context**:
- After multi-agent handoff (existing T-S10-004), fetch Langfuse traces with `session_id == shared_session`.
- Assert both agent names appear in trace metadata within the same session.

---

### G5-005 [TEST] — Suite 12: quarantine stops new traces

**File**: `scripts/e2e/suite-12-quarantine.sh`

**T-S12-LG-001 — No Traces Emitted After Quarantine**:
- Quarantine an agent (existing T-S12-002).
- Attempt to call the agent endpoint (expect 503 or blocked).
- Wait 5s; check Langfuse traces for `agent_name == quarantined_agent` in last 10s.
- Assert zero new traces after quarantine timestamp.

---

### G5-006 [TEST] — Update `run-all.sh` to include Suite 13

**File**: `scripts/e2e/run-all.sh`

- Add `bash scripts/e2e/suite-13-observability.sh` after Suite 12.
- Add summary line: `echo "==> Suite 13: Observability"`.
- Update header comment to note 13 suites total.

---

**Phase G5 Verification**: `bash scripts/e2e/run-all.sh` — all suites exit 0 including Suite 13.

---

## Summary

| Phase | Area | Tasks (impl + test) | Severity | Key Deliverable |
|-------|------|---------------------|----------|-----------------|
| G1 | Observability Infrastructure | 4 impl + 3 test | Critical | agent_runs table, per-scan spans, trace propagation, suite-13 |
| G2 | OPA Bundle Server | 2 impl + 2 test | High | Live bundle polling, T-S1-003 fixed |
| G3 | Authorization Enforcement | 3 impl + 2 test | Critical | Class B intersection rule, SA subject check, 5 deploy gates |
| G4 | Playground Completion | 5 impl + 1 test | High | Trace panel, dataset curation, feedback, HITL filter, judge |
| G5 | E2E Observability Sweep | 0 impl + 6 test | Critical | One Langfuse assertion per existing suite |
| **Total** | | **14 impl + 14 test = 28 tasks** | | |

### Execution order
G1 → G2 → G3 → G4 → G5

G1 must go first — all observability tests in G3–G5 assume the trace propagation infrastructure from G1 is in place.

G5 is pure test work. It can begin as soon as G1-002 (safety scan tracing) is complete — only T-S9-LG-001 and T-S10-LG-001 need G1-003 (registry API middleware).

### Image tags after this plan

| Service | Before | After |
|---------|--------|-------|
| registry-api | 0.2.18 | 0.2.19 |
| safety-orchestrator | 0.1.2 | 0.1.3 |
| deploy-controller | 0.1.7 | 0.1.8 |
| opa-bundle-server | — (new) | 0.1.0 |

### Not in this plan (deferred)
- Agent execution models (reactive/long-running/scheduled) — Phase 3 by design
- Agent memory (pgvector) — Phase 3 by design
- Portkey / Slack / Redis pub/sub — Phase 11 by design
- LangGraph cross-agent trace stitching (T152) — awaits Phase 10
- Appsmith dashboards (T157–T161) — Phase 12

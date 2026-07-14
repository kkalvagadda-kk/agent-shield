# Bug: durable workflow live run — seven defects on the never-tested pod path

## Symptom
A durable workflow triggered from the builder "Run Workflow" (e.g. `flow-conditional`)
**hung at the first agent** ("stuck at router", "agent finally timeout", trace shows no
spans). After the first fixes it would route correctly but a high-risk member's approval
went to the console instead of inline, and approving it **never advanced** the workflow.

Every defect lived on the live `trigger → dispatch → pod → LLM → callback → route → park →
approve → resume → advance` path — the exact path the e2e suites **faked** (suite-56
monkeypatched `_run_step`/`resolve_edge_graph`; suite-55 mocked httpx; suite-36 used a
"no-dispatch path"). So all six shipped green. Found only by running the product for real.

## Root causes (in discovery order — each hid the next)

### 1. Durable-member callback URL pointed at a non-existent Service
- **Where:** `services/registry-api/durable_dispatch.py::registry_internal_base`
- **Problem:** default was `http://registry-api.agentshield-platform.svc…`, but the Service is
  `agentshield-registry-api`. The member ran its LLM then `DNS: Name or service not known`
  posting its terminal step-update callback → orchestrator hit "no terminal callback within
  120s" → member failed at the first node.
- **Fix:** default to the correct FQDN `agentshield-registry-api.agentshield-platform.svc…`.

### 2. Interactive builder run hardcoded `context=production`
- **Where:** `routers/composite_workflows.py::start_workflow_run` (+ child in `workflow_orchestrator._run_step`)
- **Problem:** the builder test-run was `production`, so a high-risk member's approval routed
  to the reviewer console, never the inline run-panel card. (production/triggered runs use a
  *separate* `internal.py::_start_workflow_run`, so this is safe to change.)
- **Fix:** builder run is `playground`; children **inherit** the parent run's context.

### 3. Bedrock message content is a list of blocks, not a string
- **Where:** `sdk/agentshield_sdk/durable.py::_final_text` (+ write boundary in `routers/internal.py`)
- **Problem:** Anthropic/Bedrock final content = `[{"type":"text","text":"refund"}]`. Passed raw
  as `output_text` → written to a text column → `asyncpg DataError` → 500 on the callback →
  member failed right after the LLM.
- **Fix:** `_content_to_text` joins text blocks at the source; `internal._as_text` coerces at
  the DB-write boundary (defense-in-depth).

### 4. `_derive_context` didn't know workflow-member runs
- **Where:** `routers/approvals.py::_derive_context`
- **Problem:** it resolved `thread_id` only against `PlaygroundRun`. A workflow member's
  `thread_id` is its child **`AgentRun`** id → lookup missed → fell back to the pod's static
  claim (`production`). So a playground workflow's approval was `production` → never inline.
- **Fix:** also resolve `AgentRun` by id and inherit its context.

### 5. Resume hit the wrong pod, synchronously
- **Where:** `routers/approvals.py::_resume_and_advance`
- **Problem:** it posted to `_agent_pod_url()` = `{agent}-production…` (DNS-fails for a sandbox
  member) and used a synchronous `/resume` (never adding `run_id`/`callback_url`, gated on
  `parent_run_id is None`). On the DNS error it `return`ed early → the workflow never advanced.
- **Fix:** new `workflow_orchestrator.resume_durable_member` mirrors the forward
  `_dispatch_durable_member`: resolve the member's actual env, post `/resume` with
  `run_id`+`callback_url` (durable re-drive), poll the child to terminal, then
  `resume_orchestration`. Its poll waits for `completed`/`failed` (the child *starts* parked).

### 6. `resume_durable` re-ran the node instead of resuming the interrupt (deepest)
- **Where:** `sdk/agentshield_sdk/durable.py::resume_durable`
- **Problem:** it fed the decision as a plain state dict `{"messages":[],"resume":decision}` to
  `astream_events`. LangGraph only resumes a parked `interrupt()` via **`Command(resume=value)`**.
  A dict re-runs the interrupted node → `require_approval` calls `interrupt()` again → a NEW
  approval → the run re-parks forever. **This broke ALL durable HITL resume** — single-agent
  (T4) and workflow members alike.
- **Fix:** build `langgraph.types.Command(resume=decision)` (lazy import).

### 7. Inline approval card never rendered — mixed-content redirect (browser-only)
- **Where:** studio nginx `/api/` proxy + `studio/src/api/registryApi.ts::listPendingApprovals`
- **Problem:** the run panel fetched `GET /api/v1/approvals` (no trailing slash). FastAPI
  307-redirects to `/approvals/`, but behind the TLS-terminating edge (Envoy → nginx →
  registry-api are all plain http) the redirect `Location` is `http://` on the gateway host.
  The HTTPS page **blocks it as mixed content** → the approvals fetch silently fails →
  `pendingApprovals` stays empty → **no inline card**, even though the run parked correctly and
  every server-side check (context, thread_id correlation, API response) was right.
- **Fix (both layers):** studio nginx rewrites any `http://` redirect `Location` → `https://`
  (`proxy_redirect ~^http://(.+)$ https://$1;`, a **class fix** for every collection endpoint)
  + `listPendingApprovals` calls the canonical `/approvals/` to avoid the redirect entirely.
- **How found:** a **real** Playwright run capturing the browser console surfaced the
  `Mixed Content … blocked` error immediately. The API + data were correct all along — only a
  real HTTPS browser against the real edge exposes it. (The earlier *route-stubbed* inline test
  faked `/approvals`, so it never hit the redirect — another fake hiding a real bug.)

## Image tags
registry-api `0.2.160→0.2.164`, declarative-runner `0.1.38→0.1.40`, studio `0.1.131→0.1.132`.

## Files changed
`durable_dispatch.py`, `workflow_orchestrator.py`, `routers/composite_workflows.py`,
`routers/internal.py`, `routers/approvals.py`, `sdk/agentshield_sdk/durable.py`.

## Verification (real, no fakes)
- Real run of `flow-conditional`: router → **refund branch → wf-payout** → park → **playground**
  approval (inline) → approve → **resume → complete → workflow advances → completed**.
- **suite-58** (`scripts/e2e/suite-58-workflow-live-run.sh`): creates its own agents, DEPLOYS
  real pods, triggers a real run via `POST /workflows/{id}/runs`, asserts real completion +
  member output + `playground` context. 4/4. Registered in `run-all.sh`.
- **Playwright `workflow-inline-approval-live.spec.ts`** (real browser, no route stubs): drives
  the builder Run panel → real park → asserts the inline card renders (no mixed-content) →
  Approve fires `PATCH /approvals/{id}` → card clears. This is what caught bug #7.
- Regression: suite-56 6/6, suite-57 5/5.

## Lessons
- **A faked seam hides exactly the bugs that live in it.** All six defects were in the live
  dispatch→callback→resume path that suites 36/55/56 stubbed. Every one shipped green.
- **e2e must create real resources and drive the real path.** suite-58 is the model: create +
  deploy agents, trigger the real endpoint, assert real terminal state. No monkeypatch, no
  mocked httpx, no faked `_run_step`.
- **Cross-namespace URLs must be namespace-qualified FQDNs to the real Service name** — assert
  it once against `kubectl get svc`, not from memory.
- **Resuming a LangGraph interrupt requires `Command(resume=...)`**, never a state dict.
- **Route-stubbed browser tests are still fakes.** The stubbed inline test faked `/approvals`,
  so it never hit the trailing-slash redirect that a real HTTPS browser blocks as mixed content.
  A real Playwright run against the real backend (capturing the console) found bug #7 in seconds.
- **Behind a TLS-terminating edge, app-generated redirects downgrade to http.** Call canonical
  (trailing-slash) collection URLs from the client AND rewrite `http://`→`https://` redirect
  Locations at the proxy so no endpoint can silently break from mixed content.
</content>

# AgentShield Troubleshooting Playbook

**Purpose.** This is the knowledge base for a **troubleshooting agent** — when a
user reports a symptom, follow the investigation toolkit (Part 1), match the
symptom in the catalog (Part 2), and report the **root cause** using the
recurring patterns (Part 3). It is written from real debugging sessions; every
entry lists the *symptom*, the *investigation steps with exact commands*, the
*evidence that concluded it*, the *root cause*, and the *fix*.

The single most important meta-lesson (Part 3, P0): **a symptom's error message
usually names the wrong layer.** "Unreachable", "auth issue", "connection lost",
"can't locate revision" all had root causes in a *different* subsystem than the
message suggested. Always reason from the running system, not the message.

---

## Part 1 — Investigation toolkit (how to look)

Namespaces: platform services = `agentshield-platform`; agent pods = `agents-platform`.

### 1.1 Pod / deployment state
```bash
# Platform service pods + images
kubectl get pods -n agentshield-platform
kubectl get deploy <dep> -n agentshield-platform -o jsonpath='{.spec.template.spec.containers[0].image}'
# Agent pods (declarative agents run the declarative-runner image)
kubectl get pods -n agents-platform
# Per-container readiness + restarts (find a NOT-ready sidecar)
kubectl get pod <pod> -n <ns> -o jsonpath='{range .status.containerStatuses[*]}{.name}{" ready="}{.ready}{" restarts="}{.restartCount}{"\n"}{end}'
# INIT container images/state (a stale init container is a common trap — see C-6)
kubectl get pod <pod> -n <ns> -o jsonpath='{range .spec.initContainers[*]}{.name}{" -> "}{.image}{"\n"}{end}'
kubectl logs <pod> -n <ns> -c <init-or-sidecar-name> --tail=40
# K8s events (deletions, scaling, probe failures) — often the smoking gun
kubectl get events -n <ns> --sort-by='.lastTimestamp' | tail -30
# Service endpoints — EMPTY endpoints == "unreachable" even if the Service exists
kubectl get endpoints <svc> -n <ns> -o jsonpath='{.subsets[*].addresses[*].ip}'
```

### 1.2 Query the database (the source of truth for lifecycle + approvals)
Run Python inside the registry-api pod (it has `db.AsyncSessionLocal` + models):
```bash
REG=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n agentshield-platform "$REG" -c registry-api -- python3 -c "
import asyncio
from db import AsyncSessionLocal
from sqlalchemy import text
async def m():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text('SELECT ...'))
        for row in r: print(dict(row._mapping))
asyncio.run(m())
"
```
- `deployments` uses `agent_id` (NOT `agent_name`); join to `agents` for the name.
- Alembic version: `SELECT version_num FROM alembic_version`.
- Approvals provenance: `approvals.thread_id == playground_runs.id`.

### 1.3 Exercise the API the way the browser does (JWT, not the header)
Deployment/consumer chat endpoints require a real JWT (`require_user`); the
`X-User-Sub` header shortcut only works on `/playground/*`. Get a token in-cluster:
```python
tok = httpx.post('http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token',
    data={'grant_type':'password','client_id':'agentshield-studio',
          'username':'platform-admin','password':'PlatformAdmin2024'}).json()['access_token']
# users: platform-admin/PlatformAdmin2024, agent-reviewer/Reviewer2024
# subs: kalyan=643b0e62-..., platform-admin=75c7c8b3-...
```
Then POST the chat + stream the SSE and **count events** (`collections.Counter`)
to see `approval_requested`, `tool_call_start/end`, `token`, `done`.

### 1.4 Test the actual browser (the layer API tests can't see)
`kubectl exec` API tests bypass nginx, JWT, EventSource, and React. For any
UX-facing bug, drive Playwright against the **https gateway** (not the http
port-forward — see C-8): `PLAYWRIGHT_BASE_URL="https://agentshield.127.0.0.1.nip.io:8443" npx playwright test ...`.

### 1.5 Verify library behavior in the pod before a rebuild
Signature/schema/provider quirks: test the exact installed lib in the agent pod
(`kubectl exec ... -c <agent> -- python3 -c "..."`) BEFORE building a new image.
This caught the InjectedState schema behavior and the Bedrock parallel-tool-call
TypeError without a failed deploy cycle.

---

## Part 2 — Symptom → root-cause catalog

### C-1. "Agent pod is unreachable. It may still be starting." (chat)
- **Look:** `kubectl get pods -n agents-platform | grep <agent>` (pod missing?);
  `kubectl get endpoints <agent>-<env> -n agents-platform` (empty/NotFound?);
  DB: is the deployment `status='running'` with `k8s_deployment_name` set?;
  `kubectl get events -n agents-platform` for `Killing`/deleted.
- **Evidence pattern:** DB says `running`, but there is **no** K8s Deployment/Service/pod.
- **Root causes:**
  1. **Shared K8s name across deployment records** — every sandbox deployment of an agent is named `{agent}-{env}`; terminating an *old* record deletes the Deployment+Service a *newer running* one shares. (`manifest_builder.py` `k8s_name`; `main.py` `_handle_lifecycle_transitions`).
  2. **No drift healing** — the reconciler only builds pods for `status='pending'` (`main.py:127`); a `running` record whose pod was deleted is never rebuilt.
- **Recover:** `PATCH /api/v1/deployments/{id}` `{"status":"pending"}` (in-pod, localhost) → the reconciler rebuilds Service+Deployment+pod.
- Detail: `docs/bugs/deployment-shared-name-and-drift.md`.

### C-2. Agent says "authentication issue" / tools fail right after pod start
- **Look:** OPA sidecar logs (`kubectl logs <agent-pod> -c opa | grep -i bundle`) for the gap between pod start and "Bundle loaded"; is the agent's SA subject in the bundle yet?
- **Root cause:** OPA bundle didn't contain the new agent's identity yet. `bundle_generator.py` gated agents on `d.status='running'`, which only flips after the pod is Ready (chicken/egg) + poll intervals → ~5 min. During the window OPA returns empty → SDK fails closed → LLM narrates "auth issue".
- **Fix (done):** include `status IN ('deploying','running')`; OPA poll 30/60→5/15s; `/health?bundles` readiness probe. Cold start ~5 min → ~22s. Detail: `docs/debugging/003-opa-bundle-5min-cold-start.md`.

### C-3. HITL approval never appears / chat spins forever in the browser (but API works)
- **Look:** does the backend emit `approval_requested`? (§1.3 stream + Counter → yes). Does the **frontend page** handle it? Grep the page component's SSE `onmessage` for `approval_requested`.
- **Root cause:** the UI surface (`AgentChatPage`) never handled `approval_requested` — it was dropped. The backend was correct the whole time.
- **Meta:** API-green ≠ feature-works. Detail: `docs/debugging/002-hitl-no-response-in-browser.md` (follow-up section).

### C-4. Sandbox agent's approval lands in the PRODUCTION HITL queue
- **Look:** DB `approvals.context` for the run; is the deployment `environment='sandbox'` but `context='production'`?
- **Root cause:** the agent pod's env var (`AGENTSHIELD_PLAYGROUND=false`) is static — the *same pod* serves the Evaluate tab, deployment chat, and batch eval, so it can't tell callers apart. The SDK stamped `production`.
- **Fix:** decide `context` **registry-side** in `create_approval._derive_context` (join `thread_id → PlaygroundRun`): production run → production; sandbox deployment → sandbox; else playground. Design §8b.

### C-5. Migration didn't apply / `alembic ... Can't locate revision 'NNNN'` (init CrashLoopBackOff)
- **Look:** `kubectl get pod <registry-api-pod> -o jsonpath='{...initContainers...image}'` — is the `alembic-migrate` **init** container on an OLDER image than the main container?
- **Root cause:** `kubectl set image <dep> registry-api=<tag>` updates ONLY the main container; the init container keeps its old image, which lacks the newer migration file → can't locate the current DB revision.
- **Fix:** bump BOTH: `kubectl set image <dep> registry-api=<tag> alembic-migrate=<tag>` (or use helm/the deploy script, which sets both). Once current, the init container auto-runs `alembic upgrade head`.

### C-6. Agent pod CrashLoopBackOff after an SDK change — `KeyError: 'graph_state'` (or similar tool-schema error)
- **Look:** `kubectl logs <new-pod> -c <agent>` for the traceback; it points at `lc_tool()` / `create_schema_from_function`.
- **Root cause:** a governance-wrapper signature change (e.g. adding an `InjectedState` param) set `__signature__` but not `__annotations__`; langchain's schema builder needs both.
- **Fix:** set `__signature__` AND `__annotations__`. Verify in-pod (§1.5) that the injected param is excluded from `tool_call_schema` (LLM-facing) before rebuilding.

### C-7. Multi-tool: "Stream connection lost" after approving; duplicate external calls; panel piles up old approvals
- **Look (§1.3):** stream the compound query; Counter shows two `tool_call_start` but one `approval_requested`; DB shows **two** pending approvals for the thread (streaming surfaces only `pending[0]`). After approve+resume, DB shows an `approved` row AND a `pending` twin (same args).
- **Root causes:** (a) parallel high-risk tool calls interrupt in one super-step (shared interrupt id in langgraph 0.6.x → can't batch-resume); (b) LangGraph `interrupt()` **re-runs the whole tool node on resume**, so approved tools re-execute (duplicate API calls) and `governed_tool` re-POSTs the approval → **orphan pending** rows; (c) the resume proxy dropped `approval_requested` and the frontend didn't handle a re-interrupt → hang.
- **Fix (done):** provider-agnostic `post_model_hook` (`_one_hitl_tool_per_turn`) trims a turn to ONE high-risk tool call **only when 2+ are high-risk**; idempotent `create_approval`; resume proxies forward `approval_requested` + `AgentChatPage`/`ChatPane` handle re-interrupt (ref + nonce); the sandbox panel shows only the current approval (not the session list, which surfaced the benign orphans). Design §8b + §9.1.
- **Provider trap:** do NOT fix this with `bind_tools(parallel_tool_calls=False)` — `ChatBedrockConverse` (langchain_aws 0.2.35) has no such param and **TypeErrors** at runtime. Enforce it in the graph.

### C-8. Playwright/browser redirects to Keycloak login every time (SSO won't stick) / gateway 000
- **Root causes:** (a) the http **port-forward** can't carry Keycloak's `Secure` session cookies, so SSO silent-auth fails — run against the **https gateway** with `ignoreHTTPSErrors`; (b) the gateway port-forward wedges under load (000) — use a **self-healing loop** (`while true; do kubectl port-forward svc/gateway-port-8443 8443:8443 -n envoy-gateway-system; sleep 2; done`).

---

## Part 3 — Recurring root-cause PATTERNS (what to suspect)

- **P0 — The error message names the wrong layer.** "Unreachable" was a lifecycle-drift bug; "auth issue" was an OPA bundle timing bug; "connection lost" was a missing SSE handler + node re-run; "can't locate revision" was a stale init container. Diagnose from state, not the string.
- **P1 — DB vs cluster drift.** `status='running'` in the DB is a *claim*, not a guarantee of a live pod. Always cross-check the DB record against actual K8s objects (Deployment/Service/endpoints/pod).
- **P2 — Source of truth belongs to the registry, not the pod.** The agent pod can't distinguish callers/contexts (one pod serves all surfaces). Anything caller/environment-specific (approval context, requester) must be decided/enriched registry-side.
- **P3 — Shared/derived identifiers collide.** `{agent}-{env}` K8s names shared across deployment records; static pod env vars shared across request types. Prefer unique-per-record identifiers.
- **P4 — Partial rollouts leave stale pieces.** `kubectl set image` updates only the named container — init/sidecar containers, ConfigMaps, and agent pods (baked SDK) lag. Bump every container; redeploy agents for SDK changes.
- **P5 — LangGraph `interrupt()` re-runs the whole node on resume.** Anything before `interrupt()` in a tool wrapper (OPA check, approval POST, side effects) re-executes. Make it idempotent, or cap one interrupt per node (the trim), or move creation registry-side.
- **P6 — Don't depend on model-provider mechanics.** Providers implement the same capability differently (or not at all — Bedrock Converse has no parallel-tool-calls control and crashes on it). Enforce cross-cutting behavior in our graph/registry.
- **P7 — Test the layer that can actually fail.** `kubectl exec` API tests can't see nginx, JWT, EventSource, or React. Prove UX with Playwright against the https gateway. Verify library quirks in-pod before rebuilding.
- **P8 — Cold-start windows hide in poll intervals + readiness gates.** Multi-minute "flaky" waits are usually a sum of poll delays + a status gate (see C-2), not a single slow step.

---

## Part 4 — For the troubleshooting agent (how to run an investigation)

1. **Capture the exact symptom + surface** (which page/endpoint, sandbox vs production, right after a deploy?).
2. **Establish DB↔cluster ground truth** (§1.1–1.2) before theorizing (P1).
3. **Reproduce at the API layer** with a real JWT (§1.3) and **count SSE events** — this separates backend from frontend (P7).
4. **If API is fine but the browser isn't**, suspect the UI handler / auth cookie path (C-3, C-8).
5. **Match a pattern in Part 3** and name the root cause + the subsystem that owns the fix.
6. **Report:** symptom → evidence (with the commands/queries you ran) → root cause → the file/subsystem to change → and whether it needs a migration / agent redeploy / both-container bump (C-5) / SDK rebuild.

Reference debugging docs: `001-hitl-not-triggering.md`, `002-hitl-no-response-in-browser.md`, `003-opa-bundle-5min-cold-start.md`, and `docs/bugs/deployment-shared-name-and-drift.md`. Design context: `docs/design/hitl-approval-system.md` (§8b current model, §9 gaps).

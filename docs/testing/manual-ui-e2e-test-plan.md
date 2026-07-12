# Manual UI E2E Test Plan — Execution Modes, Memory, Workflows & Event Gateway

**Purpose:** Hands-on, click-through verification of the experience defined in the four design docs, done entirely from the Studio UI (with a few `curl`/`kubectl` helpers where the UI has no button yet).

**Design docs under test:**
- `docs/design/execution-models-and-memory.md` — shapes (reactive/durable), triggers, memory, workflows, isolation
- `docs/design/playground-execution-modes.md` — pre-publish evaluate surface (mode-aware playground)
- `docs/design/execution-modes-production.md` — post-publish operate surface (Agent Detail, approvals, alerting)
- `docs/design/event-gateway-threat-model.md` — public webhook ingress security

**Date written:** 2026-07-06 · verified against deployed cluster `agentshield-platform`.

---

## 0. Before you start

### 0.1 Access Studio

Studio's nginx proxies `/api` → registry-api and `/realms` → Keycloak, so one port-forward gives you a fully working app (login included):

```bash
kubectl port-forward -n agentshield-platform svc/agentshield-studio 8080:80
# then open http://localhost:8080
```

**Login:** `platform-admin` / `PlatformAdmin2024` (dev default). This user is `platform:admin` — it can see across teams, which matters for the isolation test (T0.3) and the approvals authority test (T7.3).

### 0.2 Helper terminals (keep these open)

You'll need two extra port-forwards for the production-webhook and event-log tests:

```bash
# Event Gateway — public webhook ingress (production event-driven tests, section 4B)
kubectl port-forward -n agentshield-platform svc/agentshield-event-gateway 8091:8091

# registry-api — direct API, used only for the helper snippets below
kubectl port-forward -n agentshield-platform svc/agentshield-registry-api 8000:8000
```

A shortcut to run API calls *inside* the cluster (no port-forward, no auth juggling) — exec into the registry-api pod:

```bash
RAPI=$(kubectl get pod -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it -n agentshield-platform "$RAPI" -- bash
# inside the pod you can `python3` + httpx against http://localhost:8000
```

### 0.3 ⚠ Known gaps — expected behavior, do NOT file these as bugs

These are deferred-by-design per the docs/memory. The plan works around them explicitly.

> **Update (registry-api 0.2.61 / studio 0.1.45):** G-1 and G-3 are **RESOLVED** — trigger creation is now in the create wizard **and** the Settings tab, and all four orchestration modes run. See the tagged rows below.

> **Update (Decision 24 pass #3):** G-4 is **RESOLVED** — workflow-level triggers are now wired (Triggers panel in the workflow builder, scheduler + event-gateway dispatch via `workflow_id`). G-9 (below) tracks the deferred pausable-HITL orchestrator.

| # | Gap | Why / where |
|---|-----|-------------|
| ~~G-1~~ | ✅ **RESOLVED.** Triggers are now creatable from the UI: the **create-agent wizard** (4-way type picker → Scheduled/Event-driven adds the trigger) and **Settings → "New schedule/webhook trigger"**. The API snippets in §3.0/§4.0 still work but are no longer required. | `createTrigger` wired into `CreateAgentPage` + `SettingsTab` |
| G-2 | **Webhook payloads are NOT input-scanned.** `safety-orchestrator.enabled: false` in this deployment, so the T-10 input-scan hop is absent. Per-tool OPA/HITL still governs every tool call. | threat model T-10 / residual risk R-5 |
| ~~G-3~~ | ✅ **RESOLVED.** All four orchestration modes run: **sequential** (edge chain), **conditional** (edge conditions route via the `filter_engine` DSL), **supervisor** (a `role=supervisor` member routes, with a `max_iterations` cap), **handoff** (agents pass control). Pick the mode in the builder's Save modal. | `workflow_orchestrator.orchestrate()`; suite-30 |
| ~~G-4~~ | ✅ **RESOLVED.** Workflow-level triggers are now wired: `POST /api/v1/workflows/{id}/triggers` (schedule + webhook), a **Triggers** panel in the workflow builder, and an `execution_shape` selector in the Save modal. The scheduler and event-gateway both dispatch workflow triggers via `POST /internal/runs/start` with `workflow_id`. See T6.4. | Decision 24 pass #3; migration 0031 |
| G-5 | **Publish is gated on two version flags, both set manually today.** (a) `eval_passed` — set via **Mark Version Passed** (auto-set from a passing batch eval, T-4, is not wired). (b) `adversarial_eval_passed` — required to publish **any agent whose version uses a high/critical-risk tool** (`agents.py` `has_risky` branch, 422 `adversarial_eval_not_passed`). This gate shipped in migration `0012` with **no producer**, so risky agents were unpublishable; a **Mark Adversarial Passed** button (Playground promote panel, studio ≥0.1.114) now PATCHes it — a distinct red-team sign-off, deliberately separate from the eval mark. Backend round-trip covered by suite-17 T-S17-006; button wiring by `PlaygroundPage.test.tsx`. **Residual (deferred-intentional):** no automated red-team eval runner yet — the adversarial pass is an operator judgment call, not an evaluated result. | playground doc T-4 / gate migration 0012 |
| G-6 | **Agent runs may not fully complete in sandbox.** Few agent pods are deployed; a durable/scheduled/workflow run may sit in `queued`/`running` (or fail fast at dispatch — the builder surfaces an "undeployed agents" warning). Assert the **UI wiring + run records + tree structure**, not necessarily a `completed` terminal state. | infra/local |
| G-7 | **Per-node tool/skill editing on the workflow canvas is deferred.** In the unified builder, an **inline** agent node edits its instructions/description/model in-place; **tools & skills** are managed on the agent's own page (link provided). Existing-agent nodes are read-only (edit on their page). | `AgentUpdate` has no tool-rebind field; documented follow-up |
| ~~G-8~~ | ✅ **RESOLVED (persistence).** `e2e/workflow-builder.spec.ts` "persisted edges survive a builder reload" seeds a workflow+edge via the API, loads the builder, and asserts **2 nodes + 1 edge (with its "approved" condition label) render after reload** — the real browser round-trip guarding the wipe-on-load regression. *Remaining nuance:* the drag-to-connect **gesture** still isn't automated (ReactFlow drag); it's exercised manually in T6. | Playwright `request` seeding + `.react-flow__edge` assertion |
| G-10 | **Sandbox HITL is environment-driven across 3 surfaces (2026-07-10, revised).** Context is decided registry-side (`create_approval._derive_context`), not by the pod. (1) **Sandbox deployment chat** → `context=sandbox`, a right-side **self-approve panel** (`ConversationApprovalPanel`) with inline Approve/Deny → auto-resume (leaves the production queue). (2) **Evaluate tab** → `context=playground`, existing inline `HitlPanel`. (3) **Dataset/batch eval** (`eval-runner`) → **auto-approve: the SDK skips the HITL interrupt** (gated on the trusted eval-runner identity, defense-in-depth; OPA allow/deny untouched) so batch runs never hang. **Production** deployment chat keeps the waiting-banner + console. Console shows **requested_by=username + team + deployment/env** (migration 0052). Proven by `e2e/hitl-deployment-chat.spec.ts` (sandbox panel) + suite-45 T-S45-009/010. Supersedes the earlier "console for all deployments" note. — **RESOLVED** | Design §8b |
| G-11 | **Playwright must run against the https gateway, not the http port-forward.** Keycloak now sets `Secure` session cookies, which Playwright won't replay over plain http — SSO silent-auth between specs breaks and every spec redirects to the login form. `scripts/studio-e2e.sh` auto-targets `https://agentshield.127.0.0.1.nip.io:8443` when reachable; `playwright.config.ts` + `global-setup.ts` set `ignoreHTTPSErrors`. Pre-existing specs failing on env/reseed drift (playground `Select Agent` text, agent-graphs/workflows/agents/deployment-overview visibility for `platform-admin`) are **unrelated to HITL** and tracked here as **not-yet-wired(debt)** — they assert on data/labels that reseed + RBAC changed, not on the HITL surfaces. | test-infra / reseed drift |
| G-12 | **Production deploy parity (2026-07-10).** Production agent pods now register their machine identity + enter the OPA bundle (migration 0055; shared `deploy-controller/identity.py`) and receive tool-credential `envFrom` (shared `tool_secrets.py`), so OPA governance + HITL + external-API tools work in production. **Still out of scope (documented, not regressions):** (a) **workflow-production member tool credentials** — `resolve_and_copy_tool_secrets` resolves via `/agents/{name}/tools`; a workflow name isn't an agent so it no-ops — **sandbox workflows have the identical limitation**, needs a member-aware resolver; (b) **Envoy HTTPRoute in production** — sandbox builds one, production doesn't; no impact until Envoy Gateway is installed. See `docs/design/sandbox-production-parity-architecture.md` + debugging 006/007/008. | Parity architecture doc |
| ~~G-14~~ | ✅ **RESOLVED (registry-api 0.2.149).** The M2 dashboard tool-call frequency/latency panel is shipped. It became feasible once OTEL `type=TOOL` spans ingested into Langfuse; the no-team-filter blocker is solved by fetching `type=TOOL` observations and keeping only those whose `traceId` is in the dashboard's own AgentRun population (team+env+window) — one paginated fetch + set-membership, no per-trace calls. `get_dashboard` returns `tool_calls[{tool_name,count,avg_latency_ms}]`; `ObservabilityDashboardPage` renders the panel. The dashboard is also now env-scoped (separate Production/Sandbox views). Verified live (sandbox: web_search 1×@1075ms). | routers/observability.py `_tool_call_stats` |
| G-13 | **Chat deployment pinning (2026-07-11) — wrong-deployment routing RESOLVED; parallel-prod deferred.** Consumer chat re-resolved the "most recent running" deployment at **stream** time instead of the deployment the run was pinned to at **POST** time, so a redeploy or a 2nd running deployment routed an in-flight chat (and HITL resume, whose thread checkpoint lives on the original pod) to the **wrong pod**. Fix: `_deployment_for_run` resolves the pod from the id stored on the run (`production_deployment_id`/`deployment_id`) — `stream_chat` + `resume_stream_chat` never re-resolve; `stream_deployment_chat` rejects a path `dep_id` that doesn't match the run (cross-agent guard); `start_chat` honors an optional `deployment_id` so a chat launched from a specific fleet row pins to exactly that deployment (Studio `DeploymentsPage` passes `?dep=`, `CatalogChatPage` forwards it). The **DeploymentOverviewPage "API Endpoint" card** also rendered the agent-scoped path for a *sandbox* deployment (real parallel pods) — now shows the deployment-pinned `/agents/{name}/deployments/{depId}/chat`; production stays agent-scoped (stable contract, one prod pod). Coverage: suite-46 (pin helper vs re-resolve + cross-agent reject), `CatalogChatPage.test.tsx` "pins the run to the ?dep deployment", `DeploymentOverviewPage.test.tsx` (sandbox endpoint card asserts the pinned path). **Deferred(intentional):** production runs **one** k8s Service per agent (`{agent}-production`, rolling updates — not parallel pods), so a deployment-scoped **URL** in prod resolves to the same pod; true blue/green parallel-prod Services are out of scope and would change the deploy model. | routers/chat.py; production_reconciler.py:108 |
| G-9 | **Pausable workflow-HITL orchestrator — sequential pause/resume implemented (WS-B); non-sequential and organic OPA deferred.** Backend: `agent_runs.orchestrator_state` JSONB checkpoint (migration 0032); authoritative pause-detection via pending `Approval` by child `thread_id`; `resume_orchestration` re-entry for sequential mode; parent run set to `awaiting_approval` with an amber badge in the WorkflowBuilderPage run tree and RunsTab. Deterministic coverage: suite-36. Organic OPA coverage: suite-37 — **gated on the OPA bundle/identity allow-path being green** (env fix applied in `manifest_builder.py`; bundle load + projected SA token identity must be canary-verified first). Prior notes said "Safety Orchestrator disabled" — that was a misdiagnosis; the Safety Orchestrator is a PII scanner and was never the approval origin (see Decision 26). Remaining deferred items: non-sequential auto-advance (conditional/supervisor/handoff modes halt at `awaiting_approval` but do not auto-resume-advance) — **deferred(intentional)**; organic OPA canary verification — **not-yet-wired(debt)**. | Decision 26 / WS-B — partially resolved |

### 0.4 Conventions

- **[UI]** = do it by clicking. **[API]** = helper snippet (a gap workaround).
- Use a unique prefix for everything you create, e.g. `mt-` (manual test), so cleanup is easy: `mt-reactive`, `mt-durable`, etc.
- Expected results are written as ✅ checks.

---

## T0 — Access, orientation, tenant isolation

### T0.1 — Login & shell renders `[UI]`
1. Open `http://localhost:8080`. You should be redirected to Keycloak.
2. Log in as `platform-admin`.
3. ✅ Studio loads with the left sidebar: **Build** (Agents, Skills, Tools, Workflows) / **Evaluate** (Eval Runs, Datasets) / **Catalog** (Marketplace, Approvals, Deployments) / **Observe** (Traces, Dashboard) / **Settings** (Models) / **Admin**.

### T0.2 — Agent list & detail shell `[UI]`
1. Click **Agents** (`/`).
2. Click any agent row (e.g. `research-assistant`).
3. ✅ Agent Detail shows the header (status + publish + shape badges), a **Deploy** and **Publish** button, and tabs: **Overview · Runs · Memory · Versions · Settings**.
   - _Maps to: production doc §3 (shared shell)._

### T0.3 — Tenant isolation (the fixed bug) `[UI]`
This verifies deny-by-default visibility from the execution-models spec §5.

1. On **Agents**, note the list.
2. ✅ As `platform-admin` you see published agents + your own. You should **not** see other tenants' private agents unless published or created by you.
3. Open the **Eval Runs** (playground) page → agent selector.
4. ✅ The selector list is scoped the same way (no foreign private agents leaking in).
   - _Maps to: execution-models spec §5.2/§5.5; the isolation fix in `list_agents`._

> Note: the 5 demo seeds (`research-assistant`, `calculator-bot`, `slack-notifier`, `echo-agent`, `order-agent`) are `created_by=system` + `private`. If you don't see them, that's isolation working — they're not published. Publish them or create your own agents for the tests below.

---

## T1 — Reactive agent: full lifecycle (create → sandbox → evaluate → publish)

_Maps to: playground doc §4, production doc §4._

### T1.1 — Create a reactive agent `[UI]`
1. **Agents → + Create Agent** (`/agents/new`).
2. Choose **No-code**.
3. Name `mt-reactive`, description "manual test reactive", **Execution Shape = Reactive**, edit the instructions template briefly, pick an LLM provider, select 1–2 tools.
4. **Create Agent**.
5. ✅ Redirects to `/agents/mt-reactive`; header shows a **Reactive** badge, publish status **Private**.

### T1.2 — Deploy to sandbox `[UI]`
1. On the detail page, click **Deploy** (→ `/agents/mt-reactive/deploy`).
2. Step 1: optionally enter an image tag → **Create Version** (or let deploy auto-create one).
3. Step 2: **Deploy** ("Deploy to Sandbox — ungated test deploy").
4. ✅ Toast "Sandbox deployment triggered"; Deployment History appears and polls; environment column reads **sandbox**.
   - _Maps to: playground doc §9 / OQ-D (`environment=sandbox`)._

### T1.3 — Eval Runs in the playground (chat) `[UI]`
1. Go to **Eval Runs** (`/playground`).
2. In the left selector, pick `mt-reactive`.
3. ✅ Center panel is the **ChatPane**; a purple **Sandbox mode** card + `sandbox` + `reactive` badges show.
4. Type a message → **Send**.
5. ✅ Response streams; tool-call chips appear if a tool is invoked; the **Trace panel** (right) logs events.
6. ✅ After completion a **Judge** score (0.0–1.0) appears; **👍/👎** feedback works; **Save to dataset** is available.
   - _Maps to: playground doc §4 + §8._

### T1.4 — Publish gate `[UI]`
1. Back on `/agents/mt-reactive`, click **Publish**.
2. ✅ Either a publish request is submitted (status → **Pending Review**), OR you're blocked with a clear reason (e.g. "agent has a critical-risk tool", or eval not passed — see G-5 / §8.4).
   - _Maps to: production doc §1 (eval-gated publish); Decision 20._

---

## T2 — Durable agent: run launcher, step tracker, HITL self-approve

_Maps to: playground doc §5, production doc §5._

### T2.1 — Create + deploy a durable agent `[UI]`
1. **Create Agent → No-code**, name `mt-durable`, **Execution Shape = Durable**, add a **high-risk** tool (so a HITL approval triggers), Create.
2. Deploy to sandbox (as T1.2).

### T2.2 — Launch a durable run in the playground `[UI]`
1. **Eval Runs** → select `mt-durable`.
2. ✅ Center panel is now the **RunLauncher** (not chat) — the header shows a `durable` badge.
3. Enter an input payload → **Launch Run**.
4. ✅ A **StepTracker** appears and fills in steps (`✓ completed` / `● running` / `○ pending`) streamed over SSE.
   - _Maps to: playground doc §5; component `InteractionSurface` → `RunLauncher` + `StepTracker`._

### T2.3 — HITL self-approve `[UI]`
1. When a step hits the high-risk tool, ✅ an **approval card / HITL overlay** appears showing tool · risk · **full args** (PII tokenized).
2. Review the args, click **Approve** (self-approval, sandbox — no authority check).
3. ✅ The run resumes from the checkpoint; step advances.
   - _Maps to: playground doc §5 notes (OQ-E: args always shown, no one-click approve)._
   - _If the run stalls in `running`/`awaiting_approval` and never completes → see G-6 (few agent pods)._

---

## T3 — Scheduled trigger: config, Run Now, production cron

_Maps to: playground doc §6, production doc §6. Scheduler is deployed (2/2 replicas)._

> **Scheduled agents now have a proper input contract (Decision 24 addendum).** A scheduled agent receives its schedule trigger's **`input_payload`** (a JSON "job spec") as its run input — the scheduler fires with only a `trigger_id` and `internal.py` resolves the payload. So: (1) the **create wizard** ships a scheduled-specific instructions template (autonomous parameterized worker — no "greet the user"), and picking **Scheduled** shows an **"Input payload (JSON)"** field; (2) the same field is on **Settings → New schedule trigger**, and one agent can carry several schedules with different payloads. Write instructions that parse the job spec, not a hard-coded task.

### T3.0 — Create a schedule trigger `[now in the UI]`
Create it in the **create-agent wizard** (pick **Scheduled** → set cron + optional Input payload JSON) or on an existing agent via **Settings → New schedule trigger**. The API snippet below still works headless (note the new optional `input_payload`):

```bash
RAPI=$(kubectl get pod -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec -i -n agentshield-platform "$RAPI" -- python3 - <<'PY'
import httpx
r = httpx.post("http://localhost:8000/api/v1/agents/mt-durable/triggers",
    headers={"X-User-Sub": "platform-admin"},
    json={"trigger_type":"schedule","cron_expression":"*/5 * * * *","timezone":"UTC",
          "enabled":True,"alert_on_failure":True,
          "input_payload":{"message":"run the nightly sync"}})
print(r.status_code, r.text)
PY
```
✅ `201` with the trigger id (and `input_payload` echoed back). When this fires, the agent's run `input` is resolved from that payload.

### T3.1 — Schedule config + alerting in Settings `[UI]`
1. Open `/agents/mt-durable` → **Settings** tab.
2. ✅ **Schedule Triggers** card now shows a row with the cron `*/5 * * * *`, a timezone dropdown, an **alert email** field, and an **"Email me when a run fails"** checkbox.
3. Enter an alert email, tweak the cron, tick **Enabled** → **Save**.
4. ✅ Toast "Trigger updated".
   - _Maps to: production doc §6 (alerting first-class, email at launch — PQ-2)._

### T3.2 — Scheduled Overview `[UI]`
1. Go to the **Overview** tab.
2. ✅ Because a schedule trigger exists, Overview renders the **scheduled** variant (`OverviewScheduled`) — cron, next fires, last-run status, run history.
   - _Maps to: production doc §6 wireframe._

### T3.3 — Run Now (test-fire) in the playground `[UI]`
1. **Eval Runs** → select the scheduled agent.
2. ✅ Center panel is the **RunNowPanel** (cron preview + **Run Now** button); a banner explains the schedule doesn't tick in the playground.
3. Click **Run Now (test-fire)**.
4. ✅ A run starts immediately (same code path as a real cron fire), StepTracker/history updates, judge scores it.
   - _Maps to: playground doc §6._

### T3.4 — Production cron fires automatically `[verify]`
1. With the trigger **enabled** and cron `*/5 * * * *`, wait up to ~5 min.
2. Check the agent's **Runs** tab (or query `agent_runs`).
3. ✅ A run appears with `trigger_type = schedule`, `run_by = serviceaccount:scheduler` (the scheduler service fired it).
   - _Maps to: production doc §6 flow; scheduler service._
   - _Disable the trigger afterward (Settings → untick Enabled → Save) so it stops firing._

---

## T4 — Event-driven trigger: filter, Test Trigger, production webhook + security

_Maps to: playground doc §7, production doc §7, event-gateway threat model. Event-gateway is deployed (2/2)._

### T4.0 — Create a webhook trigger `[API]` (gap G-1) — capture the token!
```bash
RAPI=$(kubectl get pod -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec -i -n agentshield-platform "$RAPI" -- python3 - <<'PY'
import httpx
r = httpx.post("http://localhost:8000/api/v1/agents/mt-reactive/triggers",
    headers={"X-User-Sub": "platform-admin"},
    json={"trigger_type":"webhook","enabled":True,
          "filter_conditions":[{"field":"event_type","op":"eq","value":"payment.fail"}]})
print(r.status_code)
print("TOKEN (shown ONCE):", r.json().get("token"))
print("trigger id:", r.json().get("id"))
PY
```
✅ `201`. **Copy the `token`** — it's returned once and only its hash is stored. You'll need it for T4.5.

### T4A — Playground (pre-publish) evaluate

#### T4.1 — Webhook Overview + Settings `[UI]`
1. `/agents/mt-reactive` → **Overview**: ✅ renders the **event-driven** variant (`OverviewEventDriven`) — masked webhook URL, filter, event log, matched/filtered counts.
2. **Settings** → **Webhook Triggers** card: ✅ shows the filter JSON and a **Rotate Token** button. Click **Rotate Token** → ✅ a fresh `/hooks/...` URL is shown once with a copy button; toast warns it won't be shown again.
   - _Maps to: production doc §7 (manual rotation — PQ-3); threat model T-1/T143._

#### T4.2 — Test Trigger: matched `[UI]`
1. **Eval Runs** → select `mt-reactive`.
2. ✅ Center panel is the **TestTriggerPanel** (filter shown, sample-payload editor, **Send Test Event**).
3. Payload that matches the filter:
   ```json
   { "event_type": "payment.fail", "amount": 12000 }
   ```
4. **Send Test Event**.
5. ✅ Event log shows **✓ matched → run**, a run starts (StepTracker), judge scores it.
   - _Maps to: playground doc §7 (same filter+run code path as production)._

#### T4.3 — Test Trigger: filtered (no run) `[UI]`
1. Send a non-matching payload: `{ "event_type": "payment.ok" }`.
2. ✅ Event log shows **⤫ filtered** with the reason; **no run** is created.
   - _Maps to: playground doc §7 (filtered ≠ dropped — critical for debugging)._

### T4B — Production webhook via the Event Gateway (threat model)

Requires the event-gateway port-forward (`:8091`) from §0.2. Uses the token from T4.0.

#### T4.4 — Valid webhook fires a run `[API/verify]`
```bash
curl -i -X POST "http://localhost:8091/hooks/mt-reactive/<TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"event_type":"payment.fail","amount":9999}'
```
✅ `202 Accepted`. Then in Studio → agent **Runs** tab (or `agent_events`): a matched event → run with `trigger_type=webhook`.
   - _Maps to: threat model acceptance criteria; production doc §7._

#### T4.5 — Security checks (threat model §5) `[API]`
Run each and confirm the expected code:

| Check | Command (abbrev) | ✅ Expected | Threat |
|-------|------------------|-------------|--------|
| Bad token | `POST /hooks/mt-reactive/WRONGTOKEN` | **401**, generic body | T-2 |
| Unknown agent | `POST /hooks/does-not-exist/<TOKEN>` | **401**, *same* body as bad-token | T-9 (no enumeration) |
| Wrong agent's path | `POST /hooks/mt-durable/<mt-reactive TOKEN>` | **401** | T-6 (cross-agent) |
| Filtered event | valid token, `{"event_type":"payment.ok"}` | **202**, logged `filtered`, **no run** | design invariant |
| Oversized body | valid token, >256 KiB JSON | **413** | T-5 |
| Rotated token | rotate in UI (T4.1), retry old token | old **401**, new works | T-3 / T143 |

✅ The event log (Overview) records `source_ip`, `status`, `received_at` for each.
   - _Maps to: threat model §5 acceptance criteria (should mirror suite-28)._

> Remember G-2: the payload reaches the agent **un-input-scanned** (safety-orchestrator off). That's expected here.

---

## T5 — Memory

_Maps to: execution-models spec §6; production doc §8.3. Memory tab is wired (`listMemory` / `deleteMemoryThread` / `clearAgentMemory`)._

### T5.1 — Enable memory `[UI or API]`
Memory is off by default. The create form doesn't expose the toggle, so enable it via API (gap-adjacent):
```bash
RAPI=$(kubectl get pod -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec -i -n agentshield-platform "$RAPI" -- python3 - <<'PY'
import httpx
r = httpx.patch("http://localhost:8000/api/v1/agents/mt-reactive",
    headers={"X-User-Sub":"platform-admin"}, json={"memory_enabled": True})
print(r.status_code, r.text[:200])
PY
```
Then `/agents/mt-reactive` → **Settings**: ✅ **Memory = Enabled**.

### T5.2 — Generate + inspect session memory `[UI]`
1. **Eval Runs** → chat with `mt-reactive` for a few turns.
2. `/agents/mt-reactive` → **Memory** tab.
3. ✅ Session threads appear (thread id, message count); selecting a thread shows its messages.
4. ✅ **PII is tokenized** in what's shown (no raw personal data) — per §5.8/OQ-3.
5. Click **Delete** on a thread → ✅ it's removed. **Clear All** → ✅ all memory cleared.
   - _Maps to: execution-models spec §6.6 (Memory UI)._

---

## T6 — Workflows (composite executable) — build from existing agents

_Maps to: execution-models spec §2.6/§4.5, playground/production "Workflows" callouts. This is the Decision 22 feature + the fixed "builder forces new agents" bug._

### T6.1 — Build a workflow from existing agents `[UI]`
1. Sidebar → **Workflows** (`/workflows`) → **New / Create** → `/workflows/new`.
2. ✅ Empty canvas with prompt "Add agents to build your workflow".
3. Click **Add Existing Agent**.
4. ✅ Modal lists **composable agents only** (agents with no active schedule/webhook trigger, filtered via `?composable=true`), scoped to one team. Search box works; already-added show "Added". This ensures workflow members are pure capabilities that won't double-fire.
5. Switch to the **Create New** tab. ✅ The execution-shape selector shows only **Reactive** and **Durable** — Scheduled and Event-driven are not offered (workflow members must not self-fire).
6. Add 2–3 same-team agents from the **Existing** tab. ✅ They appear as member nodes on the canvas.
7. ✅ Adding an agent from a **different team** is rejected with a "Cannot mix teams" toast.
   - _Maps to: execution-models spec §4.5; `AddAgentModal`; Decision 24 pass #3 composable filter._

### T6.2 — Save the workflow `[UI]`
1. Click **Save**.
2. In the modal: name `mt-workflow`, team is read-only (derived), choose an **Orchestration Mode** (Sequential, Conditional, Supervisor, or Handoff) and an **Execution Shape** (Reactive or Durable; default Durable).
3. **Save Workflow**.
4. ✅ Toast "saved"; URL becomes `/workflows/<id>/builder`; a **Run Workflow** button appears.

### T6.3 — Run the workflow → run tree `[UI]`
1. Click **Run Workflow** → the right **Run panel** opens.
2. Enter an input message → **Start Run**.
3. ✅ A **Workflow Run** card shows the parent status; **Agent Steps** lists the child runs (one per member, in order) with per-child status + latency; it polls for updates.
4. ✅ This is the parent→child **run tree** (`parent_run_id`) — the whole point of Decision 22.
   - _Maps to: execution-models spec §4.5 (run tree + StepTracker)._
   - _Children may sit in `queued`/`running` (G-6); the tree structure + records are what you're verifying._

### T6.4 — Workflow triggers: schedule + webhook `[UI]`

_Maps to: execution-models spec §4.4 / §4.5 [IMPLEMENTED — Decision 24 pass #3]; resolves G-4._

1. Open the `mt-workflow` builder (`/workflows/<id>/builder`).
2. ✅ A **Triggers** button appears in the builder toolbar (next to Save / Run Workflow).
3. Click **Triggers** → `WorkflowTriggersPanel` opens.
4. **Add a schedule trigger**: set a cron expression (e.g. `*/10 * * * *`), timezone, optional `input_payload` JSON → **Save**.
5. ✅ The schedule trigger row appears with status **Enabled** and the cron preview.
6. **Add a webhook trigger**: choose Webhook → **Save**.
7. ✅ A one-time webhook URL is shown (format `POST /hooks/workflow/{name}/{token}`). Copy it — it is shown once; only its hash is stored.
8. Reload the builder. ✅ Both triggers are still listed in the panel (token not re-shown; URL in masked form).
   - _Note G-9: sequential mid-workflow HITL pause/resume is implemented (WS-B) — the run tree shows an amber `awaiting_approval` badge when a member pauses. Non-sequential auto-advance and organic OPA firing are deferred (see G-9). Assert trigger creation and the run-tree badge; full approval-gate exercise requires the OPA allow-path canary to be green (suite-37)._

### T6.5 — Workflow webhook fires a run `[verify]`

_Requires the event-gateway port-forward (`:8091`) from §0.2 and the token from T6.4 step 7._

```bash
curl -i -X POST "http://localhost:8091/hooks/workflow/mt-workflow/<TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"event":"test"}'
```

✅ `202 Accepted`. In Studio → Workflows → `mt-workflow` → **Runs** tab (or query `agent_runs`): a parent workflow run appears with `trigger_type=webhook`.
   - _Maps to: event-gateway `POST /hooks/workflow/{name}/{token}`; execution-models spec §4.4 workflow run targeting._

---

## T7 — Production cross-cutting surfaces

### T7.1 — Runs tab is trigger-aware `[UI]`
1. On any exercised agent → **Runs** tab.
2. ✅ Table shows runs with `trigger_type` (manual / api / schedule / webhook), status, duration, cost, `run_by`, trace link.
   - _Maps to: production doc §8.2._

### T7.2 — Global Approvals Inbox `[UI]`
1. Sidebar → **Approvals** (`/approvals`).
2. ✅ Lists pending approvals across agents (or "No pending approvals. All clear."). Each item shows tool + risk + args + team.
   - _Maps to: production doc §8.1._

### T7.3 — Approval authority (production, not sandbox) `[UI]`
1. **Admin → Approvers** (`/admin/approval-authority`).
2. ✅ You can view/grant `agent:reviewer` authority for a team. (Production approvals are authority-checked, unlike sandbox self-approve.)
   - _Maps to: production doc §5 ops notes; spec §5.5 roles._

---

## T8 — Evaluation → publish wire (datasets + batch eval)

_Maps to: playground doc §8._

### T8.1 — Save to dataset `[UI]`
1. In the playground, after a good run, click **Save to dataset** (pin input/output to a golden set).
2. **Datasets** (`/playground/datasets`) → ✅ the item is listed.

### T8.2 — Batch eval `[UI]`
1. From Datasets, start a **batch eval run** for a dataset against the agent.
2. Open the eval run → `/playground/eval-runs/:id` (`EvalResultsPage`).
3. ✅ Per-item scores + pass/fail render (Haiku-judge scored, not keyword match — T-2 fix).

### T8.3 — Eval → publish `[UI]`
1. ✅ If the batch eval passes and auto-wire (T-4) is active, the version's `eval_passed` flips and **Publish** unblocks.
2. If it's still blocked (G-5), set it manually (§8.4), then Publish.

### T8.4 — Manual `eval_passed` fallback `[API]` (gap G-5)
```bash
RAPI=$(kubectl get pod -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec -i -n agentshield-platform "$RAPI" -- python3 - <<'PY'
import httpx
# find the latest version id, then PATCH eval_passed=true
v = httpx.get("http://localhost:8000/api/v1/agents/mt-reactive/versions",
    headers={"X-User-Sub":"platform-admin"}).json()
vid = v[0]["id"]
r = httpx.patch(f"http://localhost:8000/api/v1/agents/mt-reactive/versions/{vid}",
    headers={"X-User-Sub":"platform-admin"}, json={"eval_passed": True})
print(r.status_code, r.text[:200])
PY
```

---

## 9. Cleanup

After testing, remove what you created so you don't pollute the platform (isolation test T0.3 relies on a clean list):

1. **[UI]** Delete `mt-*` agents and the `mt-workflow` (Agent Detail / Workflows list → delete).
2. Disable/delete any triggers you created (Settings → untick Enabled, or delete via API).
3. Bulk test-artifact purge (gated): `bash scripts/purge-test-agents.sh` (dry-run), then `--yes`.
   - ⚠ The purge predicate currently keeps agents created by real user UUIDs — if your `mt-*` agents were created as `platform-admin`, delete them by hand.

---

## Coverage map — plan ↔ design docs

| Design doc | Sections covered | Test cases |
|------------|------------------|------------|
| execution-models-and-memory.md | shapes, triggers, memory, workflows, isolation | T0.3, T1–T6 |
| playground-execution-modes.md | mode-aware evaluate surface (all 4 modes + workflows) | T1.3, T2.2, T3.3, T4.2–4.3, T6.3, T8 |
| execution-modes-production.md | Agent Detail shell, per-mode Overview, Runs, Approvals, alerting, memory | T0.2, T3.1–3.2/3.4, T4.1/4.4, T5, T7 |
| event-gateway-threat-model.md | token, cross-agent, enumeration, filter, size, rotation, event log | T4.4–4.5 |

**Not covered here (deferred / out of UI scope):** rate-limit 429 + replay 409 (threat model T-3/T-4 — automated in suite-28, hard to trigger by hand), safety-orchestrator input scan (G-2, service off), non-sequential workflow-HITL auto-advance (G-9, deferred(intentional)), organic OPA require_approval firing (G-9, not-yet-wired — suite-37 gated on OPA bundle/identity allow-path canary).

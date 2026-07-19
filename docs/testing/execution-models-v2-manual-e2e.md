  # Manual E2E Test Plan — Execution Models v2 + Eval v2

**Purpose:** hands-on, click-through verification that the 10 Execution Models v2 / Eval v2
workstreams actually work from the Studio UI — not just that their bash suites are green.
Bash suites `kubectl exec` into the pod and test the API; they cannot see a broken screen.
This plan drives the real user journey for each workstream and tells you exactly what "good"
looks like.

**Written:** 2026-07-16 · verified against deployed cluster `agentshield-platform`.
**Scope:** the execution cube — `execution_shape` {reactive, durable} × trigger {manual/api,
schedule, webhook} × `agent_class` {user_delegated, daemon} — plus Eval v2 (E-1…E-6).

---

## Status at a glance — 9 of 10 shipped

| # | Workstream | What it delivers | Status | Automated gate (suite + Playwright) |
|---|---|---|---|---|
| **WS-1** | Execution shapes | reactive (streaming chat) vs durable (checkpointed, resumable multi-step run) | ✅ done | suite-19/20/55/60 · `durable-stream.spec` |
| **WS-2** | Identity / `agent_class` | `user_delegated` needs a caller identity for OPA; `daemon` runs with machine identity; fail-closed | ✅ done | suite-54/70 · (`identity.py`) |
| **WS-3** | Scheduled execution | cron trigger fires runs; scheduler service; next-run surface | ✅ done | suite-21/26/71 · `scheduled-overview.spec` |
| **WS-4** | Webhook auth | trigger born `token`, one-way upgrade to `client_signed`; HMAC verify; uniform 401 | ✅ done | suite-28/76 · `webhook-clients.spec`, `webhook-public-url.spec` |
| **WS-5** | In-browser SDK build | write `agent.py` in a browser tab → running governed agent, no local Docker | 🟡 **deferred** | — (no suite-78; see §WS-5) |
| **WS-6** | Operate parity | one shape→Overview dispatcher, N consumers, zero inline forks | ✅ done | suite-79 · `catalog-overview-parity.spec`, `deployment-overview.spec` |
| **E-1** | Eval v2 durable | eval a durable agent — trajectory + tool_call + response dims | ✅ done | suite-72 · `eval-v2-durable.spec` |
| **E-2** | Eval record seam | record real *governed* tool calls (after OPA + HITL) into an eval dataset | ✅ done | suite-61/74 · `eval-mode-plumbing.spec`, `eval-side-effects.spec` |
| **E-3** | Scheduled eval | eval a scheduled agent via its armed schedule trigger (`mode='scheduled'`) | ✅ done | suite-75 · `eval-v2-scheduled.spec` |
| **E-4** | Webhook eval | eval a webhook agent — filter decision + action + prompt-injection robustness | ✅ done | suite-77 · `eval-v2-webhook.spec` |
| **E-5** | Workflow eval | eval a multi-node workflow | ✅ done | suite-73 · `eval-v2-workflow.spec` |
| **E-6** | Regression gate + pass policy | per-run `pass_threshold` + `dimension_weights`; veto semantics; **UI reads the run's threshold, not a hardcoded 0.7** | ✅ done | suite-80 · (UI fixed in `7b3e3fc`) |

**The only gap is WS-5.** Everything else is shipped and gated. WS-5 is an *ergonomics*
win, not a *capability* unlock — the bring-your-own-image SDK path already works (see §WS-5).

**Deployed stack (re-read from `scripts/deploy-cpe2e.sh` — this drifts):** registry-api
`0.2.200` · studio `0.1.147` · deploy-controller `0.1.36` · declarative-runner `0.1.49` ·
eval-runner `0.1.14` · scheduler `0.1.1` · event-gateway `0.1.3` · alembic head `0064`.

---

## Prerequisites — get into Studio

Studio's nginx proxies `/api` → registry-api and `/realms` → Keycloak, so **one
port-forward gives you a fully working app, login included:**

```bash
kubectl port-forward -n agentshield-platform svc/agentshield-studio 8080:80
# then open http://localhost:8080
```

**Login:** `platform-admin` / `PlatformAdmin2024` (dev default). This user is
`platform:admin` — it can see across teams, which matters for the operate-parity and
approvals-authority checks.

Two extra port-forwards, only for the webhook and raw-API tests (WS-4, E-4):

```bash
kubectl port-forward -n agentshield-platform svc/agentshield-event-gateway 8091:8091
kubectl port-forward -n agentshield-platform svc/agentshield-registry-api  8000:8000
```

**In-cluster API shortcut** (no port-forward / auth juggling) — exec into the pod:

```bash
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- \
  python3 -c "import httpx; print(httpx.get('http://localhost:8000/api/v1/agents').status_code)"
```

**Golden rule for every case below:** the test isn't done when the API returns 200 —
it's done when you **save → reload the page → and the data survived**, and the *UI*
reflects the real backend state. If a value only lives in the store, it's not shipped.

---

## WS-1 — Execution shapes (reactive vs durable)

**What to prove:** the same agent runs differently by shape — reactive streams a chat
answer; durable executes a checkpointed, multi-step run you can watch step-by-step and
that survives a mid-run reload.

| ID | Steps | Expected (pass criteria) |
|---|---|---|
| M-WS1-1 | Create/open a **reactive** agent → Playground → send a message | Response streams token-by-token in the ChatPane; a Trace panel shows the LLM span(s). No step checkpoints. |
| M-WS1-2 | Open a **durable** agent → Playground → start a run | The run shows **discrete steps/checkpoints** (not a single stream). Each step appears as it completes in the Trace panel. |
| M-WS1-3 | **Resume/reload test:** start a durable run, reload the browser mid-run | The run is still there after reload (state came from the backend, not the store); completed steps persist; the run continues or shows its last checkpoint. |
| M-WS1-4 | Trace panel on a durable run | Each tool call shows a span with duration; a governed call shows the OPA decision. (A ~10-15ms tool span = a governance round-trip, healthy.) |

**Gate if you'd rather run it:** `bash scripts/studio-e2e.sh durable-stream.spec.ts`.

---

## WS-2 — Identity / `agent_class` (user_delegated vs daemon)

**What to prove:** `user_delegated` agents must carry a real caller identity to pass OPA;
`daemon` agents run under a machine identity. The decision is keyed on **caller-presence**,
never on sniffing `agent_class` — and it fails **closed**.

| ID | Steps | Expected |
|---|---|---|
| M-WS2-1 | Create an agent, Settings tab → set **`agent_class = user_delegated`** → save | Field persists; **reload** → still `user_delegated`. |
| M-WS2-2 | Run that agent from the Playground **as yourself** and invoke a governed tool | Tool call allowed (you are a present, non-empty caller); OPA `allow`. |
| M-WS2-3 | Trigger the same agent with **no user context** (e.g. a schedule/daemon path) invoking a `user_delegated`-gated tool | Tool call **denied** — `user_identity_ok` floor blocks it. This is the fail-closed behavior; a *safe-looking* success here would be the bug. |
| M-WS2-4 | Set **`agent_class = daemon`**, save, **reload** | Persists; daemon runs proceed under machine identity without a user turn (no empty-user-turn sent to the provider). |

**Also verify (version integrity — the recent fix):** after changing `agent_class` in
Settings and redeploying, **a new agent version is minted** (Versions tab shows a new row).
Any change to the agent definition must bump the version — this was previously broken for
fields outside a hardcoded allow-list. See suite-44 / `version-management`.

---

## WS-3 — Scheduled execution

**What to prove:** an agent/workflow with a schedule trigger fires runs on cron, the
scheduler service picks them up, and the operate surface shows next-run + run history.

| ID | Steps | Expected |
|---|---|---|
| M-WS3-1 | Workflow Builder → **⚡ trigger** button → add a **schedule** trigger (e.g. every 5 min) → save | Trigger persists; **reload** → still there with its cron expression. |
| M-WS3-2 | Open the **Scheduled Overview** surface | The trigger is listed with a **next-run** time. |
| M-WS3-3 | Wait for the cron to fire (or set a near-term schedule) | A new **run** appears in the run history for that agent/workflow, started by the scheduler (not by you). |
| M-WS3-4 | Inspect the scheduled run | It ran with the correct shape and identity; no empty user turn was sent (daemon/scheduled kickoff). |

**Gate:** `bash scripts/studio-e2e.sh scheduled-overview.spec.ts`.

---

## WS-4 — Webhook auth (token → client_signed)

**What to prove:** a webhook trigger is born `token`, upgrades **one-way** to
`client_signed` the first time a client registers, verifies HMAC signatures, and returns a
**uniform 401** on any auth failure (no oracle).

| ID | Steps | Expected |
|---|---|---|
| M-WS4-1 | Create a **webhook** trigger on an agent | Trigger created with `auth_mode = token`; a public webhook URL is shown (routed at `/hooks/…`). |
| M-WS4-2 | POST a payload to the public URL with the correct token | Run fires; the event appears in the run history. |
| M-WS4-3 | Register a **signing client** for the trigger | `auth_mode` flips **one-way** to `client_signed`; **reload** → stays `client_signed`. There is no API path back (invariant: `client_signed ⟺ ≥1 client`). |
| M-WS4-4 | POST a correctly **HMAC-signed** payload | Accepted, run fires. |
| M-WS4-5 | POST an **unsigned** or wrong-signature payload | **401** — and the same uniform 401 for "no such trigger", "bad signature", "unknown client" (no distinguishing message). |

**Public-URL note:** the webhook is served at `/hooks/` at the Envoy edge (not `/webhooks/`,
which hits the SPA). **Gate:** `webhook-clients.spec.ts`, `webhook-public-url.spec.ts`.

---

## WS-5 — In-browser SDK build 🟡 DEFERRED

**Not shipped.** Do not test an in-browser build path — there is none yet. What you *can*
(and should) verify is the **bring-your-own-image SDK path that already works today**, which
is the honest interim story:

| ID | Steps | Expected |
|---|---|---|
| M-WS5-1 | Build an SDK agent image locally (`services/echo-agent/` is the working reference: Dockerfile + `server.py`) → `docker build -t <tag>` | Image is visible to the kubelet (Docker Desktop shares the host image store with the nodes). |
| M-WS5-2 | Create an `sdk` agent, register a version with that **`image_tag`**, deploy | Pod reaches **Running** on **your** image with the **OPA governance sidecar** attached (~5s). |
| M-WS5-3 | Invoke a governed tool from that pod | Governance applies (real OPA, not mock). |

**Known honest-path gap (tracked, not WS-5):** Studio's "Code" create option today writes
`metadata.source_code` — a field **nothing reads** — so it produces a success toast for an
agent that can never run. The ~½-day fix is to expose an `image_tag` input in the UI, wire
the orphan `createVersion` client, and carry `image_tag` forward on auto-minted versions
(`deployments.py:471` currently sets `None`). Design + escalation:
`docs/design/todo/ws5-in-browser-sdk-build-design.md`. **The one open decision that gates
WS-5:** in-cluster `registry:2` vs external registry (§7 of that doc).

---

## WS-6 — Operate parity

**What to prove:** the deployed-agent operate surface is driven by **one** shape→Overview
dispatcher with per-shape consumers and **zero inline forks** — and the bundle the cluster
actually serves contains that code (not just the source tree).

| ID | Steps | Expected |
|---|---|---|
| M-WS6-1 | Deploy a **reactive** agent → open its Detail/Overview | Overview renders the reactive operate surface (chat/consumer view). |
| M-WS6-2 | Deploy a **durable** agent → open its Detail/Overview | Overview renders the durable operate surface (runs/checkpoints). |
| M-WS6-3 | Deploy a **scheduled** agent → open Overview | Scheduled operate surface (next-run, run history). |
| M-WS6-4 | Deploy a **webhook** agent → open Overview | Webhook operate surface (event log / clients). |
| M-WS6-5 | Catalog / cross-team view as `platform-admin` | Overview parity holds across all shapes; no shape renders a wrong or empty panel. |

**Gate:** `catalog-overview-parity.spec.ts`, `deployment-overview.spec.ts`,
`scheduled-overview.spec.ts`; API-level suite-79.

---

## Eval v2 — E-1 … E-6

**The Eval v2 headline claim (verify this first, it's the whole point):** a run where the
**trajectory** is dropped (score `0.0`) **fails the gate** even when the **response** is
still correct (score `1.0`) — composite `< threshold`, `eval_passed` stays `False`. A
weighted mean that silently *passes* the failure the eval exists to catch is the bug this
was built to kill. Exact-fact failures **veto** to `0.0`; fuzzy checks only cost weight.

| ID | Workstream | Steps | Expected |
|---|---|---|---|
| M-E1-1 | E-1 | Playground → **Evaluate** a **durable** agent against a dataset | EvalResults shows per-dimension scores (trajectory / tool_call / response) + composite + verdict. |
| M-E1-2 | E-1 | **Reload** EvalResults | Scores + verdict persist (read back from `eval_runs`, not the store). |
| M-E2-1 | E-2 | Run an agent in **record** mode invoking a governed tool | The recorded item captures the **real governed** call (post OPA + HITL) — not a mocked one. Unclassifiable steps are mocked fail-closed. |
| M-E2-2 | E-2 | Inspect the recorded dataset | Side effects are captured as expected (`eval-side-effects`). |
| M-E3-1 | E-3 | Evaluate a **scheduled** agent | Eval runs in `mode='scheduled'`, resolved from the agent's **armed schedule trigger** (not from `execution_shape`). |
| M-E4-1 | E-4 | Evaluate a **webhook** agent | Filter decision is scored (agent must **not** run on events it should filter), plus action correctness + **prompt-injection robustness**. |
| M-E5-1 | E-5 | Evaluate a **multi-node workflow** | Per-node + overall scores; the workflow's trajectory is evaluated end-to-end. |
| M-E6-1 | E-6 | On the launch surface, set a **per-run `pass_threshold`** (e.g. 0.9) and run | The threshold is persisted + honored (422 on out-of-range / negative weight). |
| M-E6-2 | E-6 | View a run scoring **0.85** with threshold **0.9** on **EvalResultsPage** | The UI shows **"failed"** — it reads the **run's** threshold, not a hardcoded 0.7 (fixed in `7b3e3fc`). Same `0.85` at threshold `0.7` shows **"passed"**. This is the "the product must not lie" check. |
| M-E6-3 | E-6 | The veto check (headline) | A dropped trajectory (`0.0`) with a correct response (`1.0`) → composite `0.4 < 0.7` → verdict **fail**, `eval_passed` **False**. The golden baseline flips it **True** on the same agent. |

**Gates:** suite-72/73/74/75/77/80 + `eval-v2-*.spec.ts`.

---

## Cross-cutting checks (run these regardless of workstream)

| ID | Check | Why it matters |
|---|---|---|
| M-X-1 | **Version bump on any settings change.** Edit *any* agent field (instructions, model, agent_class, execution_shape, memory), redeploy → **new version row** appears. | Change-detection is a deny-list now (any change bumps; only identity/audit/lifecycle fields are exempt). A field that *doesn't* bump is the regression. |
| M-X-2 | **Save → reload → survived**, on every create/edit surface you touch. | Most past rework was an unclosed persistence round-trip — state in the store, never in the DB. |
| M-X-3 | **Governance is real, not mocked.** A governed tool call shows an OPA decision with a real round-trip latency; a denied call is actually blocked. | The dangerous failures this session were fail-*open* (mock governance looking like success). |
| M-X-4 | **No empty user turn** on daemon/scheduled runs. | The provider rejects an empty turn; daemon kickoff must synthesize input. |

---

## Known gaps — don't be alarmed by these

These are **ledgered and intentional / low-priority**, not test failures:

- **WS-5 not built** — in-browser build deferred; BYO-image path works (§WS-5). One open
  infra decision gates it.
- **Honest-path UI gap** — the "Code" create option writes a dead `source_code` field;
  ~½-day fix scoped in the WS-5 design doc. Until then, deploy SDK agents via `image_tag`.
- **python-type tools** can crash the agent pod (`graph_builder.py:316 KeyError('kwargs')`) —
  use HTTP-type tools for a clean demo.
- **115 legacy string `run_steps.output` rows** still 500 on read — pre-existing data, not
  a new-run problem.
- **Stale packaged sub-charts** (`charts/agentshield/charts/*.tgz`) shadow their source; a
  sub-chart tag edit can silently no-op. Top-level `values.yaml` pins are authoritative
  (`check-tag-content-coupling.sh` encodes which pin wins per service).
- **Webhook client router** has no trigger-scoped ownership check (mirrors its siblings;
  latent, not exploitable in the single-tenant dev cluster).

---

## If you'd rather run the automated gates

Every case above has a backing suite. The full backend sweep + the Studio browser suite:

```bash
bash scripts/e2e/run-all.sh          # all bash+curl API suites (1..80)
bash scripts/studio-e2e.sh           # Playwright browser E2E (real Keycloak login)
```

Per-workstream Playwright: `bash scripts/studio-e2e.sh <spec>.spec.ts`. The bash suites
prove the API; the Playwright specs prove the screen. **Both** must be green before a
workstream is "done" — and this manual plan is the third leg: a human driving the real
journey the way a user would.

# Execution Models v2 — Durable (user_delegated / daemon) + Scheduled + Event-Driven, End-to-End — **Agents & Workflows**

**Status:** DRAFT for review — not yet implemented (all design decisions resolved; adversarial-critique fixes folded in)
**Date:** 2026-07-11 (rev 2026-07-12 — decisions D1–D4/R1–R3 closed; critique fixes B1–B4, M1–M6, S2–S4 applied — see `execution-models-v2-critique-and-fixes.md`)
**Author:** kkalyan + Claude
**Related:** `execution-models-and-memory.md`, `playground-execution-modes.md`,
`execution-modes-production.md` (the design intent); `todo/execution-models-gap-analysis.md`,
`todo/slice-implementation-assessment.md` (recent gap ledgers — this doc complements them,
focusing on the **durable execution engine + daemon identity + trigger dispatch + webhook auth**,
which those two do not cover), `todo/authorization-model-spec.md`,
`identity-propagation-architecture.md` (daemon identity backbone). **Decisions reviewed:** 22
(Agent|Workflow are two kinds of one executable), 24 (workflow triggers), 26 (workflow orchestrator
checkpoint + sequential resume).

> **Scope covers both executables.** Per Decision 22, an **Agent** (atomic) and a **Workflow**
> (composite: a collection of agents) are two kinds of one executable on the shared substrate. The
> full cube — `execution_shape` × `trigger` × `agent_class` — applies to **both**. This doc makes it
> real end-to-end for agents *and* workflows; where they differ, the workflow specifics are called
> out (§4a and per-workstream).

---

## 0. Acceptance bar — retro gates (apply to EVERY workstream)

Derived from the **2026-07-11 production-HITL-parity retro** (whose bug chain 006–009 was hours of
one-at-a-time discovery caused by parallel sandbox/prod code + layer-not-journey testing). The retro's
**pre-flight checklist is the canonical acceptance bar** — this doc and
`execution-models-gap-analysis.md` both reference it. No workstream is "done" until:

1. **Parity = shared code, not mirrored.** Where a capability has a sandbox and a production variant
   (`playground.py`↔`internal.py`/`chat.py`, sandbox↔production reconciler), the logic lives in **one
   shared helper both call** — per `sandbox-production-parity-architecture.md`'s anti-drift rule.
   Every edit to one path greps its sibling. Copies are banned; that split *is* the 006–009 root cause.
2. **Golden-path e2e per environment (sandbox AND production).** One test drives the real journey
   through the real door (browser/gateway → pod), fails at the first broken seam, and **fails (not
   skips) when its fixture is missing.** Layer pokes / `kubectl exec` simulations are progress, not done.
3. **Ship every gate's producer in the same change.** No required flag/field without the code that
   sets it (the `adversarial_eval_passed` orphan-gate lesson).
4. **Governance/safety paths fail loud + fail closed.** HITL-park / approval writes log the full
   signal and deny on error — never swallow-and-proceed (bug 009 hung chat by swallowing).
5. **"Done" = observed user-visible end state, proven adversarially.** Try to disprove "it works" by
   driving the real path; a suspiciously-fast green is verified against fresh DB rows.
6. **2nd bug of the same shape → audit the class**, don't fix the 3rd instance.

---

## 1. Why v2

The three execution-model design docs describe a clean **three-axis** model:

| axis | values | question it answers |
|---|---|---|
| `execution_shape` | reactive · durable | *how a run behaves* |
| `trigger` | manual · api · schedule · webhook | *what starts it* |
| `agent_class` | user_delegated · daemon | *whose authority the run carries* |

"Scheduled" and "event-driven" are **triggers, not shapes** — the create wizard's 4-way "Agent
type" picker flattens two orthogonal axes into four cards. The running product has **collapsed the
cube**: for single agents, three independent code paths each strip a degree of freedom, so every
*triggered* run executes **reactive + user_delegated** regardless of what was configured. The
durable and daemon dimensions are unreachable end-to-end.

v2 makes the full cube **reachable, real, and honest** end-to-end for durable(user_delegated),
durable(daemon), scheduled, and event-driven — plus a dual-mode **client-id + HMAC signing** upgrade
for the webhook gateway.

## 2. Verified current state (code-checked 2026-07-11)

### Built (credit where due)

| Area | State | Evidence |
|---|---|---|
| Playground eval surface, **all 4 modes** | ✅ `InteractionSurface` branches reactive→Chat, durable→StepTracker, **schedule→RunNowPanel**, **webhook→TestTriggerPanel** | `InteractionSurface.tsx:66,78,91` |
| Mode-aware Agent Detail Overviews (reactive/durable/scheduled/event) | ✅ built | gap-analysis §"DONE" |
| `execution_shape` column; playground durable dispatch (`/run`), step callbacks, SSE, `run_steps` | ✅ built | `playground.py:105,225`; migration 0018 |
| Scheduler (HA advisory-lock, cron→`/internal/runs/start`, input_payload, failure alerting) | ✅ built | `scheduler/main.py`, `ha.py`, `alerting.py` |
| Event-gateway (token auth, filter, rate-limit, replay window, uniform 401) | ✅ built | `event-gateway/main.py`, `filter_engine.py`, `rate_limiter.py` |
| Composite-workflow durable checkpoint + **sequential** park→resume→advance | ✅ built | `workflow_orchestrator.py`, `agent_runs.orchestrator_state` (0032) |
| `agent_class` column → OPA input → pod env; `agent_identities` (service identity) | ✅ built | `models.py:166,182`; `opa_client.py:134`; `manifest_builder.py:154` |
| Eval auto-gate (`eval_passed` auto-set on score ≥ threshold) | ✅ built | `eval_runner.py:309,326` |
| **Workflow triggers** — schedule + webhook (`agent_triggers.workflow_id`, scheduler UNION, `/hooks/workflow/{name}/{token}`) | ✅ built (Decision 24) | `internal.py:204`, `scheduler/main.py`, `event-gateway/main.py:199` |
| **Workflow authoritative pause-detection** (approval-query, not empty-output) | ✅ built (Decision 26) | `workflow_orchestrator.py:262` |

> **So the pre-publish / playground half is largely done.** The gaps are in the **production
> durable execution engine, the trigger→run dispatch, daemon identity/routing, and webhook client
> auth** — which the other two gap docs do not address.

### Code-verified gaps (the v2 targets)

| Gap | State | Evidence |
|---|---|---|
| **`agent_class` authoring** | ❌ **none** — absent from create wizard AND Settings → always NULL → `user_delegated` at deploy | `CreateAgentPage.tsx` / `AgentDetailPage.tsx` (zero hits); `manifest_builder.py:128` coalesces NULL |
| **Triggered run honors `execution_shape`** | ❌ **no** — `internal.py` never reads shape; `_dispatch_and_complete` hardcodes `/chat` | `internal.py:199-283`, `:53` |
| **Durable single-agent real steps** | ❌ hardcoded 2-step skeleton (`input_processing`, `agent_execution`) | `declarative-runner/main.py:551-595` |
| **Durable HITL park (production)** | ❌ not emitted — `RunExecutor.await_approval` defined, never called | `run_executor.py:64` orphan |
| **Durable checkpoint save** | ❌ `save_checkpoint` has **no caller** → resume always hits "lost state" | `checkpoint.py:30` orphan; loaded at `main.py:115,137` |
| **OPA daemon identity rule** (`user_identity_ok`) | ❌ design-only — rego decides `require_approval` by **risk only** | `agentshield.rego:103`; rule only in identity doc §4.6 |
| **Daemon async approval routing** ("service:X on behalf of Y" → on-call) | ❌ design-only | production doc §5; identity doc §4.2 |
| **`RunContext` / signed RCT / actor_chain** identity threading | ❌ **entirely unbuilt** | grep: zero hits |
| **Webhook client-id / allowlist / request signing** | ❌ coarse per-trigger bearer token only (agent AND workflow hooks) | `event-gateway/main.py:99,199` |
| SDK durable `/run` | ⚠️ endpoint absent, but **primitives present** — SDK already has LangGraph checkpointer + `interrupt()` + `/resume` + `astream_events`; only the `/run` plumbing is missing (WS-1 shared harness) | `sdk/server.py`; `runner.py:49,160`; `graph_builder.py:10` |
| **SDK in-browser build (Kaniko)** | ❌ **designed, not built** — no `services/build-service/`, no Kaniko, no `source_url`/`build_status`; Studio CodeForm stores `metadata.source_code` as a **stub** with no build pipeline → SDK onboarding still needs local Docker | `docs/spec.md:1134-1163`; `CreateAgentPage.tsx` CodeForm |
| **`workflows.agent_class`** — the daemon/user_delegated axis for workflows | ❌ **column does not exist** — workflows can't be classed at all | `models.py` CompositeWorkflow (no `agent_class`) |
| **Workflow `execution_shape=reactive` runtime** | ❌ cosmetic — `_start_workflow_run` always runs the durable orchestrator loop | `internal.py:186`; `workflow_orchestrator.orchestrate` |
| **Workflow non-sequential durable resume** (cond/supervisor/handoff) | ⚠️ parks but never auto-advances (Decision 26 deferred) | `workflow_orchestrator.py:451,497,541` |
| **Workflow member dispatch honors member shape** | ❌ members always dispatched via `/chat` | `workflow_orchestrator._dispatch:76` |
| **Workflow run identity/authority + member propagation** | ❌ no class, no actor_chain threading | (see WS-2) |

**Latest alembic head = `0057`** (verified 2026-07-12 — `0056`/`0057` were taken since this doc's
first draft; an earlier "0055" note was stale). Migration allocation for v2: `0058` = **add
`workflows.agent_class` + set `agents.agent_class`/`workflows.agent_class` `NOT NULL` with backfill**
(WS-0, M3, `down_revision="0057"`); `0059` = `webhook_clients` + `agent_triggers.auth_mode` (WS-4);
`0060` = `agent_versions.source_url` + `build_status` (WS-5). `deployments.environment` already allows
`sandbox`. **(Re-verify the head before writing each migration — the doc number is indicative.)**

## 3. The three collapse points (root causes)

1. **No `agent_class` authoring** → daemon dead; scheduled/event agents mislabeled `user_delegated`.
2. **`internal.py` ignores `execution_shape`** → every triggered single-agent run is reactive `/chat`.
3. **Durable single-agent is a facade** → checkpoint-save orphan + HITL not emitted + 2-step skeleton
   → no real resume, no park.

Everything else (daemon identity, approver routing, client-id auth) layers on top of these three.

## 4. Target — the cube made real

| shape × class | example | v2 behavior |
|---|---|---|
| reactive · user_delegated | chat assistant | ✅ already solid |
| durable · user_delegated | user kicks off contract review, approves own gate | real steps, park→approve→resume, survives restart (WS-1) |
| reactive · daemon | webhook → quick check | runs as service identity, OPA skips live-user (WS-0, WS-2) |
| durable · daemon | 3am fraud/refund job, async on-call approval | real durable run + service identity + approval routed to on-call (WS-1+WS-2) |

Triggers (schedule/webhook) start any of these; the run then behaves per its shape (WS-0 makes that
true). Webhook senders are authenticated per-application (WS-4).

## 4a. Workflows across the cube (critical, permanent decisions)

Per Decision 22 a Workflow carries `execution_shape` + triggers like an Agent, plus members +
orchestration. The trigger axis is already done (Decision 24). v2 adds the **class axis** and makes
the **shape axis real** for workflows. Four decisions were reviewed — **all now locked** (D1–D4):

**LOCKED — D1: Workflow owns authority; members inherit.**
Add `workflows.agent_class ∈ {user_delegated, daemon}`. The **workflow run's principal** is the
run's authority: user_delegated → the invoking user; daemon → the workflow's service identity +
authorizing human. That principal is **threaded to every member via the B1 dispatch headers**
(`X-Run-Principal` / `X-Actor-Chain`, shipped in WS-2) — members act under the *workflow's* authority
and never independently re-borrow a user. A member's own `agent_class` matters only when it runs
standalone; **inside a workflow it is ignored at runtime**. One authority per run tree; no per-member
authority negotiation.
**Producer (B1) — the concrete mechanism, NOT the deferred signed token.** An **explicit, unsigned
member-identity pass shipped in WS-2**: `workflow_orchestrator._dispatch` adds `X-Run-Principal`
(service identity or `user_sub`) + `X-Actor-Chain` headers on the member POST; the member reads them
into its OPA input instead of deriving identity from its own class; a **NetworkPolicy** restricts the
member's dispatch endpoint to registry-api only (same trust model as today's `run_by`). The full
*signed* RCT token stays deferred (§9). Without this WS-2 producer D1 is unimplementable — today
`_dispatch` (`workflow_orchestrator.py:69`) sends zero identity.

**LOCKED — D2: Reactive workflow is a real synchronous runtime.**
`execution_shape=reactive` on a workflow means the orchestrator runs the span **synchronously to
completion on the caller's connection and cannot durably park** for async approval (a lightweight
hand-off → one response). `durable` means full `orchestrator_state` checkpoint + park + resume across
time. This makes `execution_shape` a real distinction for workflows (today it's cosmetic — the
orchestrator always checkpoints). Reactive workflows with an approval gate either run the gate
synchronously (caller waits) or are rejected at author time — see the §9 out-of-scope note.

**LOCKED — D3: Non-sequential durable resume — finish all four modes.**
Member pause/resume is per-agent (LangGraph, mode-independent). The reason non-sequential resume was
deferred (Decision 26) is a **code-structure artifact** — the conditional/supervisor/handoff loops
hold their traversal cursor in local variables and `_halt_for_approval` never checkpointed it, so
`resume_orchestration` had nothing to re-enter. Routing is cheap to re-derive: conditional + handoff
are **Markovian** (next = f(current node, output) → tiny node checkpoint); supervisor persists its
outer-loop accumulator (workers done + outputs + iteration). v2 applies the sequential re-entry
pattern to all three → **sequential + conditional + handoff + supervisor all durably
park→resume→advance.** Full effort analysis in §9.

**LOCKED — D4: Member durability = "+ Visibility" tier (durable members via `/run`, child steps in
the tree). Crash-restart NOT included.**
A **durable member** is dispatched via **`/run`** (the WS-1 shared harness) instead of `/chat`, so its
internal `run_steps` are written with `parent_run_id` = the member's run → they appear in the workflow
run tree and the **StepTracker zooms into member-internal steps**. This is cheap (reuses WS-1's
emitter; `parent_run_id` run-tree already exists). Reactive members still dispatch via `/chat`.
*Accepted limitation (documented, §9) — within-member crash-restart is NOT guaranteed:* if
a member pod **crashes mid-execution** (not at an HITL interrupt), the orchestrator does **not**
detect the crash and re-dispatch to `/resume` — that member restarts. The orchestrator re-dispatches
only after an **approval decision**, not after a crash; adding crash-detection + resume is the
expensive "full nested" tier, deferred. So: member steps are **visible + HITL-pausable**, but a
mid-member pod crash loses that member's in-flight progress. **Surfaced, not silent (S3):** a member
dispatch failure/timeout mid-execution marks the **parent run `failed`** with an `error_message`
naming the crashed member + "in-flight progress lost (crash-restart not supported — D4)." The user
sees a failed run, never a hang.

## 5. Workstreams

### WS-0 — Foundation: `agent_class` authoring + shape-aware dispatch *(unlocks all)*
- **[R1 DECIDED] Fix the reactive/durable taxonomy + split the wizard.** (a) **Spec reword** —
  reactive = *ephemeral, in-request, synchronous, no cross-time persistence*; durable = *checkpointed,
  parks + resumes across time, survives restart*. Drop "single-shot" and the false implication that
  multi-step/HITL is durable-only. (b) **Split the 4-way "Agent type" picker** (which flattens two
  axes and forces scheduled/event → reactive) into **three independent selectors — Shape
  (reactive/durable) · Trigger (manual/api · schedule · webhook, one or more) · Class
  (user_delegated/daemon)**. Unlocks every cube cell (durable+scheduled, reactive+webhook, …) that
  the flattened picker made un-authorable. Files: `CreateAgentPage.tsx`, `docs/spec.md` (definition).
- **Explicit `agent_class` selector** (the Class selector above) in create wizard + Settings,
  pre-defaulted from intent (scheduled/event → `daemon`; reactive/durable → `user_delegated`),
  user-overridable. Send on create + PATCH. Files: `CreateAgentPage.tsx`, `AgentDetailPage.tsx`
  (SettingsContent), `registryApi.ts`; backend already accepts it (`agents.py:90`).
- **[M3] Make the class un-droppable — no NULL-coalesce band-aid.** Migration `0058` makes
  `agents.agent_class` **and** `workflows.agent_class` **`NOT NULL`** with an explicit default set at
  CREATE (backfill existing NULLs). **Remove** the `manifest_builder.py:128` `or "user_delegated"`
  coalesce — deploy reads the column directly; a NULL class becomes impossible (illegal state
  unrepresentable), not a silent downgrade to `user_delegated`.
- **Shape-aware triggered dispatch.** In `internal.py` `_dispatch_and_complete`, branch on the
  agent's `execution_shape`: `durable` → runner pod `/run` (durable callback+steps path);
  `reactive` → keep `/chat`. **Parity gate (per `sandbox-production-parity-architecture.md` +
  the 2026-07-11 HITL retro):** the durable-dispatch logic is **extracted into ONE shared helper**
  that both `playground.py` (sandbox `_dispatch_durable_run`) and `internal.py` (production) call —
  **not mirrored.** Parallel copies are exactly the root cause of the 006–009 production-only bug
  chain; shared code is the anti-drift rule. File: `routers/internal.py` + a shared dispatch module.
- **Workflows (D1, D2):** migration `0058` adds `workflows.agent_class` (`NOT NULL`, per M3);
  workflow builder **Save modal** gets the same explicit class selector (`WorkflowPropertiesPanel` /
  Save modal). **[M6] Reactive workflow = a real awaited path, not fire-and-forget.**
  `_start_workflow_run` branches on `execution_shape`: `reactive` → **`await orchestrate()`** (not
  `asyncio.create_task` as today, `internal.py:186`) and returns the final output in the response,
  with a **hard wall-clock cap** (reuse the run-timeout, OQ-10) — exceeding → `failed`, never a hung
  connection; `durable` → today's background checkpointing orchestrator.
  **[S2] Reactive workflow + approval gate — runtime fail-closed + save-time warn.** Authoritative:
  if a reactive workflow raises `require_approval` at **runtime**, the run **fails** with a clear
  message ("approval gate in a reactive workflow — set shape=durable"), never blocks the caller.
  Best-effort: at **save**, if a member has a statically high-risk tool, **warn** the author. (Pure
  save-time rejection can't see dynamic OPA-risk gates, so runtime is the authoritative seam.)
  Files: `routers/internal.py` (`_start_workflow_run`), `workflow_orchestrator.py`,
  `studio/.../WorkflowPropertiesPanel.tsx`, workflow save-validation.

### WS-1 — Durable single-agent, real & resumable *(user_delegated; declarative + SDK, shared harness)*

> **Shared durable harness (decision #2 — declarative + SDK).** Both the SDK and the declarative-runner already run on the
> **same LangGraph substrate** — the declarative-runner imports `agentshield_sdk.checkpointer`
> (`workflow_executor.py:56`), and the SDK already has the checkpointer, `interrupt()` HITL pause,
> `/resume/{thread_id}`, and `astream_events()` node stream. So the durable-run harness
> (**astream_events → `run_steps` callbacks + `/run` handler + checkpoint bookmark**) is built **once,
> inside `agentshield_sdk`**, and consumed by both: the declarative-runner imports it (as it already
> imports the checkpointer), and `sdk/server.py` mounts a native `/run`. One durable engine, two
> consumers — not "declarative-only." SDK agents gain durable on **one image rebuild** (trivial in
> active dev; no migration/back-compat burden).

- **Real per-node steps** — map LangGraph `astream_events()` node boundaries → one `run_steps` row per
  node/tool (harness in `agentshield_sdk`), replacing the declarative-runner's 2-step skeleton. The
  declarative-runner's `workflow_executor` and the SDK `runner` both feed the same emitter. Reuse
  `run_executor.py::RunExecutor` as the callback poster.
- **[B3 DECIDED] One checkpoint of record = LangGraph `PostgresSaver`.** `PostgresSaver` (keyed by
  `thread_id`, already imported by both runtimes via `get_checkpointer`) is the single checkpoint of
  the single-agent graph state; `_resume_interrupted_runs` resumes **from it**. `run_steps` = the
  durable step *ledger* (observability, not a checkpoint); `agent_runs.orchestrator_state` = the
  *workflow* checkpoint. No dedicated `run_checkpoint` column (it would duplicate `PostgresSaver`).
- **[B2 FIX] Delete the `trigger_payload` checkpoint hack.** `checkpoint.py`'s
  `PATCH {trigger_payload:{_checkpoint}}` (`:32-39`) **overwrites the webhook/schedule input** — a
  triggered durable run that parks would destroy its own event body. `trigger_payload` is
  **write-once input, read-only after run start**; resume state lives only in `PostgresSaver`. Remove
  the `checkpoint.py` write entirely. Files: `declarative-runner/checkpoint.py` (delete the hack),
  executor.
- **Emit HITL park — [B4] emitter in the SHARED harness, not declarative's `RunExecutor`.** The
  park-emit logic (on OPA `require_approval`: create `Approval` → post `awaiting_approval` step →
  durably wait) lives in the **shared `agentshield_sdk` emitter** called by BOTH the declarative-runner
  and `sdk/server.py`'s `/run` — so **SDK durable agents park too** (`RunExecutor` is declarative-only
  today, `run_executor.py`, so leaving it there orphans SDK park → an SDK daemon runs a high-risk tool
  unapproved). Reuse `approvals`, `run_steps.approval_id`, `approval_timeout_worker.py` (TTL).
  **[M5] Resume reuses the existing production proxy** — `chat.py:905 resume_stream_chat` already
  proxies pod `/resume/{thread_id}/stream`; the durable path adds step/checkpoint bookkeeping around
  that **same** proxy (one resume helper, not two). **Fail-loud + fail-closed (retro gate #4):** if
  the approval/park write errors, log the full signal and **deny** — never swallow-and-interrupt (that
  exact swallow hung production chat = bug 009).
- **[M2] Global Approvals Inbox — EXTEND, don't "build" (it exists), + 3-renderer parity.**
  `studio/src/pages/ApprovalsInboxPage.tsx` already exists — WS-1 **extends** it (durable/daemon runs,
  authority, badge from WS-6), not builds it. There are **three** approval renderers today —
  `HitlPanel.tsx` (playground), `chat/ConversationApprovalPanel.tsx` (consumer), `ApprovalsInboxPage.tsx`
  (production); v2 mandates **one shared `ApprovalCard` + one data hook** all three render through
  (grep proves no divergent renderer). Authority = `agent:reviewer` (producer shipped in WS-2, M1);
  for user_delegated the approver is the run's initiating user / manager.
- **SDK durable (in scope, via the shared harness):** add the `/run` endpoint to `sdk/server.py`
  wired to the shared emitter; SDK agents rebuilt once to pick up the new `agentshield_sdk`. No longer
  a deferred gap.
- **Workflows:** the durable workflow engine exists for **sequential** (park→resume→advance).
  WS-1 workflow scope = (i) the **reactive synchronous** path from WS-0/D2, (ii) **[D4 DECIDED]
  "+ Visibility"** — dispatch durable members via `/run` so their child `run_steps` show in the run
  tree (StepTracker zoom); crash-restart NOT included (documented limitation, §9), (iii) **[D3
  DECIDED] finish non-sequential resume for all four modes** — apply the
  `_run_sequential_from` re-entry pattern to conditional/handoff/supervisor: checkpoint the traversal
  cursor in `_halt_for_approval`, add `_run_*_from` re-entry, dispatch per mode in
  `resume_orchestration` (supervisor persists its accumulator). Inter-agent HITL reuses the same
  `approvals` + `run_steps.approval_id` the orchestrator already queries (`workflow_orchestrator.py:262`).

### WS-2 — Durable daemon: identity + async approval routing
- **OPA daemon rule** — add `user_identity_ok` to `agentshield.rego` (daemon: no live `user_id`
  required; user_delegated: `input.user_id != ""` required). Existing risk-based `require_approval`
  unchanged. Tests already assert intent in `agentshield_test.rego`.
- **Service identity as principal** — daemon `run_by` = the agent's service identity
  (`agent_identities`); capture the **authorizing human** (who armed the trigger, stored on the
  trigger/run). Approval + audit read *"service:X on behalf of Y."*
- **[B1 — the D1 producer] Member-identity propagation (unsigned headers + NetworkPolicy).**
  `workflow_orchestrator._dispatch` (`:69`, today sends zero identity) adds `X-Run-Principal`
  (workflow service identity or `user_sub`) + `X-Actor-Chain` (`[workflow_name,…]`) on the member
  POST. The member (shared harness) reads them into its OPA input (`user_id`/`sa_subject`) instead of
  deriving from its own class. A **NetworkPolicy** restricts each agent pod's dispatch endpoint to
  registry-api only (so the unsigned header can't be forged by a rogue pod — same trust model as
  `run_by`). The full *signed* RCT token stays deferred (§9). Without this, D1 has no producer.
- **[M1 — authority producer, shipped with the gate] Reviewer authority on approve.** Pull the
  approval-authority slice of gap-analysis TODO-5 into WS-2: extract `agent:reviewer` from the JWT in
  `auth_middleware.py`; enforce on `POST /approvals/{id}/decide` that only a reviewer for the run's
  team may decide (else 403); filter the inbox query to the reviewer's teams. Without this the inbox
  is open — any authenticated user could approve a daemon's 3am refund. (Runs/memory row-filtering
  stays in TODO-5.)
- **[R3 DECIDED] Identity is entry-path-determined, not class-determined — daemon agents keep
  `/chat`.** `agent_class` governs authority/identity, **not** which endpoints are exposed (a daemon
  may expose chat for testing / a human console). A single daemon agent's runs carry different
  identity by entry path: an **interactive `/chat`** run always has an authenticated JWT caller →
  it runs under the **caller's** identity (OPA sees a `user_id`, normal governance); a
  **trigger-driven** run (cron/webhook) has no live user → it runs under the **service identity**
  and the daemon `user_identity_ok` rule applies. So the daemon "no live user required" rule is a
  **floor for the trigger case**, never a license to drop a user who is present. No `/chat` blocking;
  no unauthenticated-chat hole (the edge always requires a JWT). **[S4] Enforced at ONE seam —
  run creation:** `/chat` (`chat.py`) stamps `run.run_by = caller_sub` from the JWT; `/internal/runs/
  start` stamps `run.run_by = service_identity`. OPA `user_id` derives from `run.run_by` at that single
  place, so no path can create a run with a spoofed or missing principal (structural, not convention).
- **[R2 DECIDED] Async approver routing — role-based, into the Global Approvals Inbox.** A paused
  daemon durable run routes its approval to a configured **reviewer role** (e.g. `agent:reviewer` /
  on-call), surfaced in the same Global Approvals Inbox (WS-1) filtered to that role. Reuses the
  existing `approvals` machinery; durable wait = WS-1's checkpoint. **Email/webhook approval
  notification = future improvement** (§9), reusing the alerting transport.
- **Workflows (D1):** a **daemon workflow** runs under the workflow's **service identity**; that
  principal is threaded to every member via the **B1 dispatch headers** (`X-Run-Principal` /
  `X-Actor-Chain`, above) — members act as the workflow's service identity, not any user. The OPA
  `user_identity_ok` rule applies to member tool calls using the *workflow's* class. Inter-agent approvals for a daemon workflow route async to
  the workflow's approver policy (on-call), reading *"workflow:X (service) on behalf of Y"*. The
  authorizing human is whoever armed the workflow trigger. `run_by` on the parent + child runs
  carries the service identity.

### WS-3 — Scheduled, end-to-end
- Scheduled agents authored `agent_class=daemon` (WS-0 default). With WS-0, a scheduled **durable**
  agent runs durable; HITL parks + routes async (WS-2). Scheduler, input_payload, HA, alerting exist.
- Provisioning flow captures daemon approver + arming human. Verify/complete scheduled Overview
  (schedule health / next-fire / last-run / alert config) against production doc §6.
- **Workflows:** scheduled workflows already fire (Decision 24, scheduler UNION-queries workflow
  triggers). With WS-0 a scheduled **durable daemon workflow** runs the checkpointing orchestrator
  under the workflow service identity; **all four modes** park+resume async (WS-2, D3). Members
  restricted to **composable** agents (no active own trigger, per Decision 24) still holds.

### WS-4 — Event-driven e2e + **dual-mode client-id / allowlist / signing** *(independent slice)*
- **`webhook_clients`** table `(id, trigger_id FK, client_id, secret_hash, enabled, created_by,
  created_at, UNIQUE(trigger_id, client_id))`. Migration `0059`.
- **`agent_triggers.auth_mode ∈ {token, client_signed}`** — dual-mode: new webhook triggers default
  `client_signed`; existing stay `token`; gateway accepts either per-trigger. Migrate senders one at
  a time, delete the flag later. (No-bandaid: explicit mode, no silent fallthrough.)
- **Wire contract:** `X-Client-Id`, `X-Timestamp`, `X-Signature: sha256=HMAC_SHA256(secret,
  f"{ts}.{raw_body}")`. Verify order: client-id ∈ allowlist+enabled → `|now−ts|≤300s` → constant-time
  HMAC compare → existing filter + rate-limit → dispatch. Uniform 401 for unknown/bad-sig/stale.
  Stamp `agent_events.client_id`.
- **Registration API** `POST /api/v1/triggers/{id}/clients` (returns secret **once**, stores hash).
- **Studio panel (in this slice):** trigger-config client registration — add client, **reveal secret
  once**, enable/disable, per-client audit; event log shows resolved `client_id`.
- Files: `event-gateway/main.py`, migration, `registry-api` registration endpoint, Studio trigger UI.
- **[M4] Two independence tiers.** The **client-id/HMAC auth is fully independent** (ship anytime).
  But the **event → durable-daemon run** path is **gated on WS-2** (service identity + approval
  routing): shipping WS-4 before WS-2 gives an event-triggered durable daemon run no principal/routing.
  Event agents authored `agent_class=daemon`; with WS-0 the run behaves durable, but its daemon
  identity/routing needs WS-2.
- **Workflows:** the same auth applies to the **workflow webhook endpoint**
  `POST /hooks/workflow/{name}/{token}` (`event-gateway/main.py:199`). `webhook_clients` keys on
  `trigger_id`, and workflow triggers are rows in `agent_triggers` (with `workflow_id` set), so the
  client-id/allowlist/signing path covers workflow hooks with **no schema change** — the verify code
  wraps both the agent and workflow hook handlers. Event-triggered **durable daemon workflows** then
  run under the workflow service identity (WS-2).
- **Documented gap:** replay uses timestamp window only; nonce store deferred.

### WS-5 — SDK in-browser build (Kaniko) — the SDK onboarding path

Turns "SDK durable exists" (WS-1) into "a non-DevOps user can ship a durable SDK agent from a browser
tab." Full design already in `docs/spec.md` §"In-Browser SDK Agent Editor + Platform-Managed Image
Build" — this workstream implements it. Sequenced **after WS-1** (SDK `/run` should exist so
browser-built SDK agents can be durable), independent of WS-2/3/4.

- **`services/build-service/` (new)** — FastAPI: accepts `{agent_name, source_code}`, runs a **Kaniko
  K8s Job** (BuildKit alt) in a dedicated `agentshield-builds` namespace, streams build logs, updates
  version `build_status`. Dockerfile **baked in** (not user-editable); base always
  `python:3.12-slim` + `agentshield-sdk`; **no `FROM` override**; egress limited to registry + PyPI.
- **MinIO** `agent-source` bucket — source at `{team}/{agent}/{version}/agent.py` (reuse existing
  MinIO; already deployed for Langfuse).
- **Registry API** — `agent_versions.source_url` (MinIO ptr) + `build_status` columns (migration
  `0060`); on build success → auto-create `agent_version` (+ optional deploy-immediately).
- **Studio** — Monaco editor in `CreateAgentPage` (replaces the current `metadata.source_code`
  CodeForm **stub**) + `EditAgentPage`; build-log stream panel via SSE
  (`GET /agents/{name}/versions/{id}/build-logs`).
- **Flow:** write `agent.py` in Monaco → submit → save to MinIO → Kaniko Job builds → SSE logs →
  push to internal registry → auto-create version → deploy. No local Docker toolchain.
- **Note:** onboarding, not execution semantics — but it's what makes the flipped SDK-durable scope
  actually reachable by non-DevOps users. Reuses image-versioning + deploy path already in place.

### WS-6 — Operate surface + cross-cutting (folded in from gap-analysis, 2026-07-12)

Five items moved from `execution-models-gap-analysis.md` into this plan so they're sequenced with the
execution work. All clear the §0 acceptance bar. (The other gap-analysis TODOs — alerting, role-based
access, Redis hot path — stay in that doc.)

- **Approvals inbox badge (gap TODO-1).** Nav-level pending-approval **count badge**, authority-checked
  (`agent:reviewer`), polling `GET /api/v1/approvals?status=pending`. **Pairs with WS-1's Global
  Approvals Inbox** — a durable/daemon run that parks must show here. Files: `Sidebar.tsx`, a
  lightweight count endpoint in `approvals.py` if the list is too heavy.
  *Parity:* the count must include **production** approvals (`chat.py` path) **and** sandbox.
  *Golden-path:* Playwright — a real production durable run parks → badge increments → click → item in inbox.
- **Mode-aware catalog overviews (gap TODO-4) — this IS a parity fix.** Make `CatalogDetailPage`
  (production) render the **SAME parameterized** mode-aware Overview components as `AgentDetailPage`
  (sandbox) — reactive/durable/scheduled/event, pointed at production data. **One shared component
  set, no parallel copy** — the former "Option B: accept the split" is rejected (it's the retro's
  root cause). Files: `studio/.../Overview*.tsx` (parameterize data source), `CatalogDetailPage.tsx`,
  `catalogApi.ts`.
  *Golden-path:* the same Overview renders on both `/agents/:name` (sandbox) and `/catalog/:id` (prod).
- **Auto `eval_passed` (gap TODO-3) — ✅ shipped dependency.** Already live (`eval_runner.py:309`,
  score ≥ threshold → `eval_passed=true`) — the publish gate the v2 lifecycle relies on. Tracked here
  as a **done dependency**, not new work; listed so the plan is self-contained.
- **Browser-cache prevention (gap "Current UI Bug").** Studio assets are served `immutable` + 1y
  expiry, so a reused Vite content hash serves a stale bundle. The `window.__STUDIO_BUILD` marker in
  `main.tsx` forces a unique hash per build — ensure every Studio image bump carries it. Small
  hardening (no user journey), but it prevents "I deployed but see the old UI" false bugs.
- **Sandbox run TTL / auto-cancel (gap TODO-7).** A durable **sandbox** run (`playground_runs`) stuck
  `running`/`awaiting_approval` past a wall-clock TTL (default 10 min, per-agent configurable)
  auto-cancels → `status=cancelled`.
  *Parity:* share the timeout logic with the **production** run-timeout (`approval_timeout_worker.py`,
  OQ-10) — **one worker parameterized by scope** (`playground_runs` vs `agent_runs`), not two.
  *Golden-path:* a durable run left `awaiting_approval` past TTL → `cancelled` + timeout `error_message`.

Sequence: WS-6 is **parallel / anytime** (operate-surface), except the **approvals badge** pairs
naturally with WS-1's inbox and should land with it.

## 6. Sequence (locked)

```
Spine (in order):
  WS-0 reach + agent_class authoring   (small — unlocks all)
  WS-1 durable engine real+resumable   (hardest; harness in agentshield_sdk → declarative + SDK)
  WS-2 daemon identity + async approval
  WS-3 scheduled e2e
Parallel / anytime:
  WS-4 event client-id + HMAC signing  (auth independent; but event→durable-daemon gated on WS-2, M4)
  WS-5 SDK in-browser build (Kaniko)   (after WS-1; SDK onboarding — spec.md TODO)
  WS-6 operate surface + cross-cutting (parallel; approvals badge pairs with WS-1)
```

## 7. Locked decisions

1. **Sequence** — WS-0 → WS-1 → WS-2 → WS-3 spine; WS-4 parallel; **WS-5 after WS-1** (SDK
   in-browser build); **WS-6 parallel** (operate surface + cross-cutting, folded in from
   gap-analysis; approvals badge pairs with WS-1). All seven ship.
2. **Durable reach** — **declarative AND SDK**, via a **shared durable harness in `agentshield_sdk`**
   (the declarative-runner already imports the SDK checkpointer; both consume one engine). SDK agents
   rebuilt once to gain `/run` — a rollout checklist item, not a workstream. (Flipped from
   "declarative-only" — rollout friction moot in active dev / no migration.)
3. **`agent_class` authoring** — explicit selector, pre-defaulted from type, editable in Settings
   (agents **and** workflows).
4. **WS-4 surface** — full slice incl. Studio client-registration panel; covers agent **and**
   workflow webhook hooks.
5. **D1 — workflow owns authority; members inherit** via the B1 dispatch headers
   (`X-Run-Principal`/`X-Actor-Chain`, WS-2); member class ignored inside a workflow. (§4a)
6. **D2 — reactive workflow = real synchronous runtime** (no durable park); durable = checkpoint +
   park + resume. (§4a)
7. **D3 — finish non-sequential durable resume for ALL four modes** (sequential/conditional/handoff/
   supervisor). Member pause/resume is per-agent (LangGraph); the fix is re-entering the orchestrator
   loops from a checkpointed cursor. (§4a, §9)
8. **D4 — member durability = "+ Visibility"** — durable members via `/run`, child `run_steps` in the
   tree; **within-member crash-restart deferred** (documented limitation). (§4a, §9)
9. **R1 — fix reactive/durable taxonomy** — spec reword + split the wizard into independent Shape ·
   Trigger · Class selectors (folded into WS-0). (WS-0)
10. **R2 — daemon approver routing = role-based into the Global Approvals Inbox**; email/webhook
    notification is a future improvement. (WS-2, §9)
11. **R3 — daemon agents keep `/chat`; identity is entry-path-determined** (interactive = caller;
    triggered = service identity). Class = authority, not interface. (WS-2)

**Critique-fix decisions (from `execution-models-v2-critique-and-fixes.md`, confirmed 2026-07-12):**

12. **B1 — member identity = unsigned `X-Run-Principal`/`X-Actor-Chain` dispatch headers + a
    NetworkPolicy** locking the member dispatch endpoint to registry-api; the *signed* RCT token stays
    deferred. This is the producer for D1. (WS-2)
13. **B3 — single checkpoint of record = LangGraph `PostgresSaver`**; `run_steps` is a ledger,
    `orchestrator_state` is the workflow checkpoint; delete `checkpoint.py`'s `trigger_payload` hack
    (B2); no dedicated column. (WS-1)
14. **B4 — park emitter lives in the shared `agentshield_sdk` harness** (not declarative `RunExecutor`)
    so SDK durable agents park. (WS-1)
15. **M1 — reviewer-authority producer ships in WS-2** (TODO-5 slice) so the inbox/decide is gated.
16. **M3 — `agent_class NOT NULL`; deploy-time coalesce removed.** (WS-0)
17. **M6 / S2 — reactive workflow = awaited + capped; a runtime `require_approval` fails the run
    (save-time warn is best-effort).** (WS-0)
18. **S3 — mid-member crash surfaces as parent `failed`** (not a hang). (§4a D4)
19. **S4 — entry-path identity enforced at one seam (run creation).** (WS-2)

**All open decisions are now resolved.** No decision blocks implementation.

## 8. Verification (per Definition-of-Done + §0 acceptance bar)

Each slice ships with a real-journey proof, a save→reload→assert, and a no-orphan grep — **and meets
the §0 retro gates**: a **golden-path e2e per environment** (sandbox AND production, real door,
fails-not-skips on missing fixture), a **parity assertion** (the sandbox and production paths call the
same shared helper; grep proves no divergent copy), and — for WS-1/WS-2 governance paths — a test that
an approval-write error **denies** (fail-closed), never hangs.

- **WS-0** — bash suite: create durable+schedule agent, reload → `agent_class` persisted; scheduler
  fire → assert dispatch hit `/run` (durable) not `/chat`, `run_steps` rows exist. Playwright: wizard
  sets class, Settings edit persists on reload.
- **WS-1** — bash suite, run for **both a declarative and an SDK agent** (shared harness): durable run
  with an approval gate parks; decide → resumes → completes; kill + restart the pod mid-run → resumes
  from checkpoint (not "lost state"); `run_steps` show real per-node steps (not the 2-step skeleton).
  Playwright: Global Approvals Inbox shows pending → approve → completes.
- **WS-2** — rego unit tests (daemon no-user allow; user_delegated no-user deny); bash suite: daemon
  durable run parks, approval reads "service:X on behalf of Y", routes to reviewer.
- **WS-3** — bash suite: scheduled daemon durable fire → durable run + async park; alert on failure.
- **WS-4** — bash suite (`suite-NN`): valid signed client → 200 + dispatch; bad sig / stale ts /
  unknown client / disabled client / wrong-trigger client → 401 (identical body); legacy token works
  under `auth_mode=token`; **same suite exercises `/hooks/workflow/{name}/{token}`**. Playwright:
  register client in Studio, secret shown once, disable → 401. Signing helper doubles as sender ref.
- **WS-5** — bash suite: POST source_code → build-service spawns Kaniko Job → poll `build_status` to
  `succeeded` → image in internal registry → `agent_version` auto-created with `source_url`; bad code
  → `build_status=failed` + logs surfaced. Playwright: Monaco editor → submit → build-log SSE streams
  → version appears → deploy → durable SDK agent runs (ties WS-1 + WS-5 in one journey).
- **WS-6** — Playwright: production durable run parks → **approvals badge** count increments → click
  → item in inbox; the **same mode-aware Overview** renders on `/agents/:name` and `/catalog/:id`
  (parity). Integration: a sandbox durable run past TTL → `cancelled` (shared timeout worker). Auto
  `eval_passed` is already covered by existing eval-runner tests (shipped dependency).
- **Workflows (across slices):** bash suite — create durable+daemon workflow, reload → `agent_class`
  persisted; scheduled fire → parent + child runs under service identity, sequential member gate
  parks → async approve → resumes → completes; reactive workflow → synchronous one-response, no
  checkpoint row. Playwright — workflow builder class selector persists; run tree **zooms into a
  durable member's own `run_steps`** (D4 "+ Visibility"). **All four modes (sequential/conditional/
  handoff/supervisor): assert park→approve→resume→advance→complete** (D3); supervisor asserts the
  accumulator survived the pause.
- **Critique-fix regression tests (from the fixes doc):**
  - **B1** — a daemon workflow member's OPA input / approval record carries the workflow service
    identity (not `user_id=""`); a member POST from a non-registry-api pod is refused (NetworkPolicy).
  - **B2** — a webhook-triggered durable run keeps its original event body in `trigger_payload` across
    a park + resume.
  - **B4** — the park→approve→resume journey passes for an **SDK** agent (not only declarative).
  - **M1** — non-reviewer `POST /approvals/{id}/decide` → 403; reviewer → 200.
  - **M2** — the same approval renders identically across `HitlPanel` / `ConversationApprovalPanel` /
    `ApprovalsInboxPage` (shared `ApprovalCard`).
  - **M3** — create an agent via API without `agent_class` → no silent `user_delegated` at deploy.
  - **S2** — a reactive workflow that raises `require_approval` at runtime → run `failed` (clear msg),
    caller not blocked; save with a high-risk-tool member → warning surfaced.
  - **S3** — a mid-member pod crash → parent run `failed`, `error_message` names the member (not a hang).
- Image tag bumps (`deploy-cpe2e.sh` + `charts/agentshield/values.yaml`) per changed service;
  update `docs/experience/playground.md`; flip the three design-doc statuses from "not implemented"
  as each slice lands.

## 9. Decision analyses (all resolved) + future improvements + out of scope

### Decision analyses (all resolved — retained for the rationale)

- **D3 — non-sequential workflow durable resume. [Analysis — earlier "medium-large" estimate was
  overstated; corrected below.]**
  - **Key correction:** the member pause/resume is **per-agent and mode-independent** — LangGraph
    `interrupt()` → Postgres checkpoint → `/resume/{thread_id}` handles it identically for every mode.
    The orchestrator's only post-resume job is "member X finished, here's its output → what's next?"
  - **Why non-sequential is deferred today = a code-structure artifact, not a hard problem.**
    Sequential has a re-enterable `_run_sequential_from(order, start_index)` and checkpoints just
    `next_index` (`workflow_orchestrator.py:372`). The non-sequential loops
    (`orchestrate_conditional/handoff/supervisor`, `:451,497,541`) hold their cursor (`node`,
    `current_input`, `visited_count`) in **local variables**; `_halt_for_approval` checkpoints only
    `{mode,team,workflow_id}` — **not the cursor** — so `resume_orchestration` has nothing to
    re-enter and gives up (`:434-441`). The next-hop logic already exists inside those loops.
  - **Routing is cheap to re-derive:** *conditional* + *handoff* are **Markovian** — next = f(current
    node, its output) — so the checkpoint needed is **just the current node** (+ `visited_count`).
    *supervisor* is the one exception: an outer accumulator loop, so its checkpoint must persist the
    loop state (workers done + outputs + iteration count).
  - **Corrected work + size:** apply the sequential re-entry pattern to the 3 loops — (1) checkpoint
    the cursor (node, or the supervisor accumulator) in `_halt_for_approval`; (2) add `_run_*_from`
    re-entry functions mirroring `_run_sequential_from`; (3) `resume_orchestration` dispatches per
    mode. **Conditional + handoff = small (deterministic, tiny checkpoint). Supervisor = moderate
    (persist the accumulator).** Not "much larger than sequential."
  - **[DECIDED: finish all four modes in v2.]** Sequential + conditional + handoff + supervisor all
    get durable park→resume→advance. Supervisor persists worker-outputs + iteration count in
    `orchestrator_state`. No non-sequential resume gap remains after v2.
- **D4 — member durability. [DECIDED: "+ Visibility" tier.]** Durable members dispatch via `/run`
  (WS-1 harness) → their `run_steps` show in the run tree + StepTracker zoom. **Accepted limitation
  (documented):** *within-member crash-restart is out of scope* — a member pod that crashes
  mid-execution (not at an HITL interrupt) restarts that member; the orchestrator re-dispatches only
  after an approval decision, not after a crash. Crash-detection + re-dispatch-to-`/resume` is the
  "full nested" tier, deferred to a follow-up. Net for v2: member steps are visible + HITL-pausable;
  a mid-member crash loses that member's in-flight progress.

### Future improvements (deferred, ship-simple-first)

- **Email/webhook daemon approval notification** (R2) — v2 routes daemon approvals to a role in the
  Global Approvals Inbox; a future pass adds email/webhook notification reusing the alerting transport.
- **Full nested member durability / within-member crash-restart** (D4) — v2 ships "+ Visibility"
  (durable members via `/run`, steps in the tree). A future pass adds orchestrator crash-detection +
  re-dispatch-to-`/resume` so a member survives a mid-execution pod crash.
- **SDK durable in-browser onboarding** depends on WS-5 (Kaniko build) — until that ships, durable
  SDK agents are authored via the CLI + local Docker.

### Out of scope (documented gaps)

- **Only the *signed* RCT token is deferred.** WS-2 **does** ship the working member-identity pass
  (B1: unsigned `X-Run-Principal`/`X-Actor-Chain` dispatch headers + NetworkPolicy) + the `agent_class`
  OPA rule + service-identity `run_by` + authorizing-human capture. What remains out of scope is the
  **HMAC-signed** chain-of-custody token (identity-propagation §4.5) — a hardening upgrade over the
  unsigned-header-behind-a-NetworkPolicy model, not a blocker for v2.
- Webhook replay nonce store (timestamp window only in v1).
- **Within-member crash-restart** (D4) and **email/webhook daemon approval notification** (R2) — see
  Future improvements above.

> **Note (S2 resolved):** the reactive-workflow approval-gate rule is no longer ambiguous — it is
> **runtime fail-closed + save-time warn** (WS-0), not "synchronous OR reject."

---

*Companion: the acceptance bar (§0) is also applied to `execution-models-gap-analysis.md` (per-item
parity + golden-path fields, TODO-4 → shared Option A, cross-links to the parity doc + the retro).*

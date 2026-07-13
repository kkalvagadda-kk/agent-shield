# Execution Models v2 — Adversarial Critique & Remediation Plan

**Status:** ✅ APPLIED — all BLOCKER + MAJOR fixes folded into `execution-models-v2-e2e.md` (2026-07-12).
This doc is retained as the rationale/audit trail. The three design-bearing calls were **confirmed**:
**B1 = unsigned dispatch headers + NetworkPolicy** (signed RCT deferred); **B3 = LangGraph
`PostgresSaver` as the single checkpoint of record** (no dedicated column); **S2 = runtime fail-closed
+ save-time warn** for a reactive workflow's approval gate.
**Date:** 2026-07-12
**Author:** kkalyan + Claude
**Critiques:** `execution-models-v2-e2e.md` (the design under attack)
**Enforces:** `2026-07-11-production-hitl-parity-retro.md` (the acceptance bar), `sandbox-production-parity-architecture.md`, `identity-propagation-architecture.md`

> **Purpose.** An adversarial pass over the v2 design found **4 blockers + 6 majors** — claim-vs-code
> mismatches and undecided forks that would surface one-at-a-time during implementation (exactly the
> rework the retro exists to prevent). This doc lists each finding with `file:line` evidence, the
> **architecturally-correct fix**, the **v2-doc section it lands in**, and the **new test** that
> proves it. Apply all BLOCKER + MAJOR fixes into `execution-models-v2-e2e.md`, then re-verify.

---

## Verdict

**Not safe to implement as written.** The two worst: **D1's daemon-workflow member identity relies on
`actor_chain`, which is unbuilt and which §9 simultaneously defers** (no producer); and **wiring
`save_checkpoint` as written destroys the trigger payload of any triggered durable run** (band-aid →
data loss).

---

## BLOCKERS (must fix before coding)

### B1 — `actor_chain` orphan: daemon-workflow member identity has no producer
**Evidence:** `git grep actor_chain|RunContext` in `services/`+`sdk/` → zero (the `otel_run_context`
hits are OpenTelemetry tracing, unrelated). `workflow_orchestrator._dispatch` (`:69-94`) POSTs only
`{message, thread_id}` to the member `/chat` — **no identity**. v2 §9 out-of-scope defers
"actor_chain **token** threading," yet D1 (§4a) + WS-2 assert members inherit the workflow principal
"via `actor_chain`." The mechanism is deferred and the producer named nowhere.
**Failure:** a daemon workflow's member tool call runs with **no verifiable identity** → OPA sees
`user_id=""`, per-user policy impossible, the approval record can't say who authorized it → the
platform's core governance runs blind for every workflow member (the exact hole identity-propagation
exists to close).
**Fix (architecturally correct — minimal, not the full RCT):**
- WS-2 ships an **explicit, unsigned member-identity pass** (cluster-internal, same trust boundary as
  the existing `run_by`): `workflow_orchestrator._dispatch` adds headers `X-Run-Principal:
  {service_identity | user_sub}` and `X-Actor-Chain: {workflow_name[,…]}` on the member POST.
- The member pod (shared SDK harness) **reads these into the OPA input** (`user_id` / `sa_subject`)
  instead of deriving identity from its own class. Explicit parameter threaded through the call chain
  — no per-member re-derivation.
- **Resolve the §9 contradiction:** §9 defers only the *signed RCT token*; the *unsigned member
  identity pass* is **in WS-2**. State this explicitly so D1 has a producer.
**Lands in:** WS-2 (new bullet "Member identity propagation"), §4a D1 (add "producer = WS-2 dispatch
headers"), §9 (narrow the out-of-scope to the signed token only).
**Test:** bash suite — a daemon workflow member's OPA input / approval record carries the workflow
service identity (not `user_id=""`).

### B2 — `save_checkpoint` clobbers `trigger_payload` (band-aid → data loss)
**Evidence:** `checkpoint.py:32-39` — `save_checkpoint` does `PATCH agent-runs/{id}` with
`{"trigger_payload": {"_checkpoint": {...}}}`, **replacing the whole column**. A webhook/schedule run
stores its event body / job spec in `trigger_payload` (`internal.py:232-250`).
**Failure:** a webhook-triggered durable agent that parks → the first checkpoint **overwrites the
event payload** → on resume the run has no input, the audit trail of what triggered it is gone, and any
resume logic that re-reads input gets `{}`.
**Fix:** never overload `trigger_payload`. Checkpoint state has ONE home (see B3). `trigger_payload`
is **write-once input, read-only after run start**. Delete the `trigger_payload._checkpoint` write.
**Lands in:** WS-1 "Wire checkpoint SAVE" bullet.
**Test:** bash suite — a webhook-triggered durable run that parks + resumes still has its original
event body in `trigger_payload`.

### B3 — Checkpoint source of truth undecided ("prefer consolidating")
**Evidence:** v2 WS-1 says *"Prefer consolidating on the LangGraph `PostgresSaver` … reduce
checkpoint.py to a step-index bookmark."* A preference, not a decision. Three candidate stores exist:
`checkpoint.py`'s `trigger_payload` hack, LangGraph `PostgresSaver` (`get_checkpointer`, imported by
both runtimes), and `agent_runs.orchestrator_state` (workflows).
**Failure:** two implementers pick differently; resume correctness is undefined; the orphan store
(B2) survives.
**Fix — decide now (one store per fact):**
- **LangGraph `PostgresSaver` (keyed by `thread_id`)** = the single checkpoint of record for a single
  agent's graph state (pause/resume). Already exists; both runtimes import it.
- **`run_steps`** = the durable **step ledger** for UI/observability (written via callbacks) — NOT a
  checkpoint.
- **`agent_runs.orchestrator_state`** = the **workflow** orchestrator's checkpoint (already exists).
- **Delete `declarative-runner/checkpoint.py`'s `trigger_payload` write entirely.**
  `_resume_interrupted_runs` resumes from `PostgresSaver`, not a bookmark.
**Lands in:** WS-1 "Wire checkpoint SAVE" (replace "prefer" with this decision); §7 add a locked
decision.

### B4 — SDK durable HITL-park emitter orphan
**Evidence:** the park emit references `RunExecutor.await_approval`, but `RunExecutor` lives **only** in
`services/declarative-runner/run_executor.py` (`git grep "class RunExecutor" → declarative only`).
WS-1 says the durable harness lives in `agentshield_sdk` and `sdk/server.py` mounts `/run`.
**Failure:** an SDK **daemon** durable agent hits a high-risk tool → no park emitter on the SDK path →
it either runs the tool **unapproved** or hangs. Silent governance bypass.
**Fix:** the **park-emit logic** (create `Approval` → `await_approval` → post `awaiting_approval` step
→ **fail-closed**) lives in the **shared harness in `agentshield_sdk`**, called by BOTH
declarative-runner and `sdk/server.py`'s `/run`. `RunExecutor` becomes a thin caller of the shared
emitter, not the owner.
**Lands in:** WS-1 "Emit HITL park" + the shared-harness blockquote (move park-emit into the shared
module explicitly).
**Test:** WS-1 bash suite runs the park→approve→resume journey for an **SDK** agent, not only declarative.

---

## MAJORS

### M1 — Inbox reviewer-authority gate has no producer in v2
**Evidence:** `ApprovalsInboxPage.tsx` calls `listPendingApprovals(teamFilter)` with **no role check**
(no `reviewer` reference in the file). The role producer (Keycloak roles + `auth_middleware`
extraction) is **gap-analysis TODO-5**, left in that doc — NOT moved to v2. Yet WS-1/WS-2 claim the
inbox is "authority-checked (`agent:reviewer`)."
**Failure:** any authenticated team member approves any run — including a daemon's 3am refund.
**Fix:** pull the **approval-authority slice** of TODO-5 into **WS-2** (ship the gate's producer with
the gate): `agent:reviewer` extraction in `auth_middleware.py` + authority check on `POST
/approvals/{id}/decide` (reviewers-for-the-team only) + inbox query filtered to the reviewer's teams.
The runs/memory row-filtering stays in TODO-5.
**Lands in:** WS-2 (new bullet); WS-6 badge note (authority is now real).
**Test:** bash — non-reviewer decide → 403; reviewer decide → 200.

### M2 — "Build Global Approvals Inbox" is stale; three approval renderers can drift
**Evidence:** the inbox already exists (`studio/src/pages/ApprovalsInboxPage.tsx`). There are **three**
approval renderers: `HitlPanel.tsx` (playground), `chat/ConversationApprovalPanel.tsx` (consumer
chat), `ApprovalsInboxPage.tsx` (production). v2 says "Global Approvals Inbox (production)" as if new
and mandates no shared render.
**Failure:** three renderers drift (the retro's HitlPanel↔Inbox risk, unaddressed) → an approval shows
different args/PII handling depending on surface.
**Fix:** WS-1 wording → **extend** the existing inbox (not build). Add a **parity mandate**: one shared
`ApprovalCard` component + one shared data hook reading one approvals API shape; all three surfaces
render through it. Grep proves no divergent renderer.
**Lands in:** WS-1 (reword + parity mandate); §4 Parity coverage.
**Test:** the same approval renders identically across the three surfaces (Playwright).

### M3 — `agent_class` NULL-coalesce band-aid persists
**Evidence:** `manifest_builder.py:128` — `agent_class = agent.get("agent_class") or "user_delegated"`.
WS-0 adds the selector but never removes the coalesce or makes the column `NOT NULL`.
**Failure:** an agent created via API without the field silently deploys `user_delegated`; a
mis-integrated daemon runs with live-user governance semantics.
**Fix (make illegal states unrepresentable):** migration `0058` (head is `0057`, verified 2026-07-12 — `0056`/`0057` taken) — `agents.agent_class` +
`workflows.agent_class` **`NOT NULL`** with an explicit default set at CREATE; backfill existing NULLs.
**Remove** the `manifest_builder.py:128` `or "user_delegated"` coalesce — deploy reads the column
directly; NULL is impossible.
**Lands in:** WS-0 (agent_class bullet + migration note).
**Test:** WS-0 bash — create via API without class → 422 (or explicit default persisted), never a
silent downgrade at deploy.

### M4 — WS-4 sequencing: "independent" but durable-daemon event runs need WS-2
**Evidence:** §6 lists WS-4 "independent, off the spine"; WS-4 itself says event durable daemon runs
"run under the workflow service identity (WS-2)."
**Failure:** if WS-4 ships first, an event-triggered durable daemon run has no service identity /
routing → runs mislabeled or ungoverned.
**Fix:** split WS-4 explicitly: **client-id/HMAC auth is independent (ship anytime)**; the
**event → durable-daemon run** path is **gated on WS-2**. Note the dependency in §6 + WS-4.
**Lands in:** §6 sequence + WS-4 (dependency note).

### M5 — Two production resume paths (chat.py reactive vs WS-1 durable) can drift
**Evidence:** production reactive-chat HITL resume already exists — `chat.py:905 resume_stream_chat`
("After a production HITL approval, stream the resumed agent output"), proxying `/resume/{thread_id}
/stream`. WS-1's durable resume is a new path; the doc doesn't say they share plumbing.
**Failure:** two resume implementations diverge (the parity anti-pattern).
**Fix:** WS-1's durable resume **reuses** the existing `chat.py` resume proxy to the pod
`/resume/{thread_id}`; the durable path adds step/checkpoint bookkeeping around the **same** proxy.
Grep shows one resume-proxy helper.
**Lands in:** WS-1 "Emit HITL park" (resume reuses `chat.py` proxy).

### M6 — Reactive-workflow synchronous path contradicts the fire-and-forget architecture
**Evidence:** `_start_workflow_run` is fire-and-forget — `asyncio.create_task(orchestrate(...))`
(`internal.py:186`) — returns immediately. D2 asserts reactive workflow = "synchronous, hold the
caller's connection" with no spec for how, or a time bound.
**Failure:** either the reactive path is never actually synchronous (D2 stays cosmetic — the very bug
D2 claims to fix), or a multi-agent reactive workflow **holds an HTTP connection for 30s+** and times
out under load.
**Fix:** specify — for `execution_shape=reactive` workflows, `_start_workflow_run` **awaits**
`orchestrate()` (not `create_task`) and returns the final output in the response, with a **hard
wall-clock cap** (reuse the run-timeout, OQ-10); exceeding → `failed`, not hung. See S2 for the
approval-gate rule.
**Lands in:** WS-0 workflow bullet (reactive = awaited + capped).

---

## BAND-AID LIST (with the correct replacement)

| # | Band-aid (evidence) | Correct fix |
|---|---|---|
| B2 | `checkpoint.py` reuses `trigger_payload` as checkpoint storage (`:32-39`) | One checkpoint store (PostgresSaver); `trigger_payload` read-only after start |
| M3 | `agent_class … or "user_delegated"` (`manifest_builder.py:128`) | `NOT NULL` column, explicit default at create, remove coalesce |
| B3 | "prefer consolidating" checkpoints (WS-1) | Decide: PostgresSaver = record; delete the bookmark hack |

---

## SURPRISE / AMBIGUITY LIST → forced decisions

- **S2 — Reactive workflow + approval gate.** *Decide: reject at AUTHOR time.* At workflow **save**,
  if `execution_shape=reactive` AND any member (or its bound tools) can raise `require_approval` (a
  high-risk tool is present), **reject the save** with a clear error ("reactive workflows can't
  contain approval-gated tools — set shape=durable"). This is the producer for D2's "rejected at
  author time." Enforced in the save-validation seam, not asserted. **Lands in:** WS-0 workflow bullet
  + §9 (remove the "run synchronously OR reject" ambiguity).
- **S3 — Mid-member crash data loss (D4).** *Surface it, don't lose it silently.* On a member dispatch
  failure/timeout mid-execution, the orchestrator marks the **parent run `failed`** with an
  `error_message` naming the crashed member + "in-flight progress lost (crash-restart not supported —
  D4)." The user sees a failed run, never a hang. **Lands in:** §4a D4 + WS-1 workflow bullet.
- **S4 — Daemon `/chat` identity single seam.** *Enforce at one seam.* Identity is stamped at
  **run creation**: `/chat` (`chat.py`) sets `run.run_by = caller_sub` from the JWT; `/internal/runs
  /start` sets `run.run_by = service_identity`. OPA `user_id` derives from `run.run_by` at that single
  place. No path creates a run outside these two seams → R3's "interactive = caller, triggered =
  service" is structural, not convention. **Lands in:** WS-2 R3 bullet (name the seam).

---

## VERIFICATION-GAP LIST → tests to add to §8

| test | proves |
|---|---|
| daemon workflow member OPA input carries the workflow service identity | B1 |
| webhook-triggered durable run keeps `trigger_payload` across a checkpoint | B2 |
| non-reviewer `decide` → 403; reviewer → 200 | M1 |
| same approval renders identically across HitlPanel / ConversationApprovalPanel / ApprovalsInboxPage | M2 |
| create-agent-without-class → no silent `user_delegated` at deploy | M3 |
| SDK (not just declarative) durable agent park→approve→resume | B4 |
| save a reactive workflow with a high-risk-tool member → rejected | S2 |
| mid-member crash → parent run `failed` (not hung), error names the member | S3 |

---

## TOP MUST-FIX-BEFORE-CODING (ordered)

1. **B1** — member-identity producer in WS-2 (unsigned dispatch headers); narrow §9 to the signed
   token only. Without it, D1 is unimplementable and every workflow member is ungoverned.
2. **B2** — kill the `trigger_payload` checkpoint hack before wiring `save_checkpoint`.
3. **B3** — lock the single checkpoint of record (PostgresSaver); delete the bookmark.
4. **B4** — park emitter in the shared `agentshield_sdk` harness, not declarative's `RunExecutor`.
5. **M1** — move the reviewer-authority producer (TODO-5 slice) into WS-2.
6. **M3** — `agent_class NOT NULL`; remove the deploy coalesce.
7. **M2 / M5 / M6 / S2 / S3 / S4** — the parity + spec fixes above.

## Apply-to-v2 checklist (which sections change)

- **§4a D1** — add "member-identity producer = WS-2 dispatch headers (B1)."
- **§4a D4 / WS-1 workflow** — surface mid-member crash as `failed` (S3).
- **WS-0** — `agent_class NOT NULL` + remove coalesce (M3); reactive workflow = awaited + capped (M6);
  reject reactive+gated-member at save (S2).
- **WS-1** — checkpoint decision (B3); no `trigger_payload` write (B2); park emitter in shared harness
  (B4); resume reuses `chat.py` proxy (M5); **extend** (not build) the inbox + 3-renderer parity (M2).
- **WS-2** — member-identity dispatch headers (B1); reviewer-authority producer (M1); R3 single seam (S4).
- **§6 / WS-4** — event durable-daemon gated on WS-2 (M4).
- **§7** — add locked decisions: checkpoint of record (B3); `agent_class NOT NULL` (M3).
- **§8** — add the eight tests above.
- **§9** — narrow out-of-scope to the **signed** RCT token only (B1); remove the reactive-gate
  ambiguity (S2).

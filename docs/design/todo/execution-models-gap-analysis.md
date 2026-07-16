# Execution Models — Gap Analysis & Remaining Work

**Date:** 2026-07-08  
**Author:** Karthik + Claude  
**Sources:** `execution-models-and-memory.md`, `playground-execution-modes.md`, `execution-modes-production.md`

---

## Summary

The three design docs define a comprehensive execution models system. Most of the backend + playground surface is built. The primary gaps are in the **production operate surface** and **cross-cutting platform features** (alerting, role-based access, auto-eval-gate).

> **See also `execution-models-v2-e2e.md`** — the durable execution engine + daemon identity + trigger dispatch + webhook client-auth gaps (which this doc does not cover) live there.

---

## Acceptance bar (applies to EVERY TODO below)

Adopted from the **2026-07-11 production-HITL-parity retro** — its pre-flight checklist is the **canonical acceptance bar** (also referenced by `execution-models-v2-e2e.md` §0). The bug chain 006–009 was hours of one-at-a-time discovery caused by **parallel sandbox/prod code** + **layer-not-journey testing**. No TODO is "done" until it clears:

1. **Parity = shared code, not mirrored.** A capability with a sandbox and a production variant (`playground.py`↔`chat.py`/`internal.py`, sandbox↔production reconciler) lives in **one shared helper both call** — per [`sandbox-production-parity-architecture.md`](../sandbox-production-parity-architecture.md) (anti-drift rule, parity matrix, two-column FK). Every edit to one path greps its sibling. Copies **are** the 006–009 root cause.
2. **Golden-path e2e per environment** (sandbox AND production) through the real door (browser/gateway → pod); **fails — not skips — when its fixture is missing.** `kubectl exec` / API pokes are progress, not done.
3. **Ship every gate's producer in the same change** (the `adversarial_eval_passed` orphan-gate lesson).
4. **Governance/safety paths fail loud + fail closed** — never swallow-and-proceed.
5. **"Done" = observed user-visible end state, proven adversarially.**

Each TODO below carries a **Parity** and a **Golden-path** line stating how it clears this bar.

**Related:** [`sandbox-production-parity-architecture.md`](../sandbox-production-parity-architecture.md), [`../introspections/2026-07-11-production-hitl-parity-retro.md`](../../introspections/2026-07-11-production-hitl-parity-retro.md).

---

## What's DONE (aligned with design)

### Backend / Data Model

| Item | Migration | Notes |
|------|-----------|-------|
| `execution_shape` + `memory_enabled` on agents | 0016 | CHECK (reactive/durable) |
| `agent_runs` merged orchestration fields | 0017 | trigger_type, run_by, team, thread_id, parent_run_id |
| `run_steps` table | 0018 | With approval_id FK |
| `agent_triggers` (unified schedule+webhook) | 0019 + 0030 | Deviated: no separate `agent_schedules` — simpler, correct |
| `agent_events` table | 0025 | matched/filtered/rejected status |
| `agent_memory` + pgvector | 0021 + 0022 | Graceful degradation if pgvector unavailable |
| `orchestrator_state` JSONB | 0032 | Workflow pause/resume checkpoint |
| `workflow_edges` | 0029 | 4 orchestration modes |

### Services

| Service | Status | Notes |
|---------|--------|-------|
| Scheduler (`services/scheduler/`) | Functional | APScheduler + Postgres advisory lock HA (2 replicas) |
| Event Gateway (`services/event-gateway/`) | Functional | 7-stage security: rate limit, replay protection, token auth, filter engine |
| Workflow Orchestrator (`workflow_orchestrator.py`) | Functional | 639 lines — sequential, conditional, handoff, supervisor |
| `routers/memory.py` | Built | save/list/search/clear |
| `routers/triggers.py` | Built | Full CRUD + token rotation |
| `routers/events.py` | Built (minimal) | List only |
| `routers/agent_runs.py` | Built | Runs + embedded run_steps endpoints |

### Playground (pre-publish evaluate)

| Component | Status |
|-----------|--------|
| `InteractionSurface.tsx` | Built — full mode dispatch |
| `StepTracker.tsx` | Built (141 lines) |
| `RunLauncher.tsx` | Built (81 lines) |
| `RunNowPanel.tsx` | Built (88 lines) |
| `TestTriggerPanel.tsx` | Built (145 lines) |
| `PlaygroundPage.tsx` | Mode-aware, reads execution_shape |
| `AgentDetailPage.tsx` | 5 tabs, mode-aware Overview (OverviewReactive/Durable/Scheduled/EventDriven) |

---

## What's MISSING — TODO Items

### TODO-1: Global Approvals Inbox Badge (Production doc §8.1) — ✅ RESOLVED (WS-6, studio 0.1.145)

**Shipped.** The pending-count pill lives on the existing Approvals nav item in
`studio/src/components/Sidebar.tsx` (`data-testid="approvals-badge"`), routes to `/approvals`, and
hides at 0 (a "0" chip is noise that trains operators to ignore the badge).

**The count reuses the EXISTING producer — `listPendingApprovals()` (`registryApi.ts`). No new
endpoint, and deliberately no `getPendingApprovalsCount`:** a second API method over the same GET
would be a second path to one fact, which is the drift class WS-6 exists to delete.

Proven by: `Sidebar.test.tsx` (count N / absent at 0 / API-failure degrades to no-badge without
taking out navigation), `studio/e2e/approvals-badge.spec.ts` (real backend, no `page.route` stubs;
the DOM must match the count the real API returned), and `suite-79` **T-S79-003** (the producer is
live and honours `status=pending`).

> **Wire-shape note for anyone touching this:** `GET /approvals/` returns an **`{items, total}`
> envelope**, *not* a bare list — `listPendingApprovals` unwraps `.items`. The WS-6 plan asserted
> the endpoint "already returns `ApprovalInboxItem[]`", which is true of the client function
> *after* it unwraps and false of the wire; suite-79's first draft believed the plan and failed.
> Unfiltered, the endpoint also returns decided rows, so the `status=pending` param is
> load-bearing: drop it and the badge over-counts and never reaches 0.

---

### TODO-2: Alerting on Failure (Production doc §6, P-6)

**Design says:** Scheduled and event-driven agents alert on failure — email at launch; Slack/webhook/PagerDuty as future improvement.

**Current state:** shipped (email) — `alert_email`/`alert_on_failure` on `agent_triggers`; `alerting.dispatch_failure_alert` invoked from `internal.py::_dispatch_and_complete` on `status=failed` (the single shared dispatch path — scheduler and event-gateway both fire through it, so there is no per-service duplication); verified by suite-71 (T-S71-005). That test forces a scheduled run to fail and asserts the alert transport was invoked with the trigger's `alert_email`, and that a run with `alert_on_failure=false` does not alert. Studio's trigger Settings panel exposes both fields, and the Scheduled Overview surfaces an alert-config summary.

**Still future:** Slack webhook, PagerDuty, and alert-routing rules — richer transports layered on the same `dispatch_failure_alert` seam.

**Files (as built):**
- `services/registry-api/alerting.py` (`dispatch_failure_alert` — the shared helper)
- `services/registry-api/routers/internal.py` (`_dispatch_and_complete` invokes it on `status=failed`)
- `agent_triggers.alert_email` / `agent_triggers.alert_on_failure` columns (no separate migration 0039 — folded into the trigger schema)
- `studio/src/components/agent-detail/SettingsTab.tsx` (alert config fields) + `OverviewScheduled.tsx` (alert-config summary)

---

### TODO-3: Auto-set `eval_passed` from Passing EvalRun (T-4) — ✅ RESOLVED

**Shipped.** `eval_passed` is auto-set on a passing EvalRun (score ≥ threshold) — in
`services/registry-api/routers/eval_runner.py`, search `auto-set eval_passed=True` (one site for the
agent version, one for the workflow version). Tracked in `execution-models-v2-e2e.md` WS-6 as a
**done dependency** of the v2 publish gate.

> **WS-6 (2026-07-15): no work was owed here and none was done.** The plan listed TODO-3 as a WS-6
> deliverable, but its only deliverable was *this ledger line*, which was already on the page —
> so the task was **deleted**, not performed.
>
> The citation is now by **symbol, not line number**. It previously read `:309,326`; the real sites
> were `:576`/`:593` at the time of writing. Note the WS-6 task doc had *also* re-grounded that
> citation — to `:531`/`:548` — and was **itself already stale** within a day, because the file is
> under concurrent edit. Three different line numbers for one unchanged fact is the argument
> against line citations in prose: they rot silently and cost a re-verification every time. Grep
> the symbol.

---

### TODO-4: CatalogDetailPage — Reuse Mode-Aware Overviews (Gap #3) — ✅ RESOLVED (WS-6, studio 0.1.145)

**Shipped.** `studio/src/components/agent-detail/OverviewForShape.tsx` is now the ONE place that
answers "which Overview does this shape get". Both operate surfaces —
`pages/DeploymentOverviewPage.tsx` and `pages/CatalogDetailPage.tsx` — mount it, and **zero** direct
`Overview*` mounts remain in any page. The inline fork (its hand-derived `executionShape` and
3-branch `endpoints` builder) is **deleted**, not guarded.

**Correcting the record on two counts:**

1. **The fork's counterpart was `DeploymentOverviewPage`, not `AgentDetailPage`.** The plan said
   "`AgentDetailPage` uses them"; `AgentDetailPage` has **zero** `Overview*` references (its tabs
   are deployments/versions/settings). Fixing the fork against the page named in the plan would
   have converged the wrong two files.
2. **The fork had ALREADY drifted before anyone collapsed it.** It handled
   `reactive`/`durable`/`scheduled` and had **no event-driven branch at all**, while the shared set
   has **four** components — and its `scheduled` branch was unreachable dead code, because it
   dispatched on `config_snapshot.execution_shape` alone (a stored 2-value column: `reactive` |
   `durable`; `scheduled` and `event_driven` are **derived from triggers**, never stored). So the
   drift this item predicted *in the abstract* had already happened *in the concrete*. Collapsing
   the fork **restored event-driven to the catalog for free**.

**Design:** an **explicit `Record<OverviewShape, Component>` map, not a priority chain** — a new
shape is a compile error at the map rather than a silent fallthrough. On an unknown shape the
dispatcher **fails closed and loud** (visible error card + `console.error`), never a quiet fallback
to Reactive: *a quiet default is exactly how the fork lost event-driven without anyone noticing.*
`resolveOverviewShape` is the one place the webhook⇒`event_driven` / schedule⇒`scheduled` /
`durable` / else `reactive` derivation lives.

Proven by: `suite-79` **T-S79-000** (1 map, 2 consumers, **0** inline forks, all 4 shapes, fail-loud
present), `OverviewForShape.test.tsx` (all four shapes incl. `event_driven`; unknown shape ⇒ loud
card, asserted explicitly so a future refactor cannot quietly re-add the default),
`CatalogDetailPage.test.tsx`, and `studio/e2e/catalog-overview-parity.spec.ts` — which asserts the
**same** `data-testid="overview-for-shape"` renders on **both** the catalog artifact page and
`/agents/:name/d/:depId`. That cross-page identity IS the parity proof, and it **cannot** pass
against an inline fork, which never rendered that node.

---

### TODO-5: Role-Based Run/Memory Filtering (Production doc §5.5)

**Design says:** `agent:user` sees only own runs; `agent:reviewer` sees all in team; `agent:admin` full access. Memory similarly scoped.

**Current state:** Basic Keycloak JWT auth. No role-based filtering. Everyone in a team sees all runs and memory.

**What to implement:**

1. Define Keycloak roles: `agent:user`, `agent:reviewer`, `agent:admin`, `platform:admin`
2. Add role extraction to `auth_middleware.py` (from JWT `realm_access.roles`)
3. Runs endpoints: filter by `user_id = current_user.sub` unless reviewer/admin
4. Memory endpoints: same user-scoping pattern
5. Approvals: only `agent:reviewer` can decide

**Files:**
- `services/registry-api/auth_middleware.py` (extract roles)
- `services/registry-api/routers/agent_runs.py` (add user filter)
- `services/registry-api/routers/memory.py` (add user filter)
- Keycloak config (add roles to agentshield realm)

**⚠️ Orphan-gate risk:** the roles (`agent:reviewer`, etc.) are a **gate** — ship their **producer** (the Keycloak role assignment + `auth_middleware` extraction) in the **same change** as the filtering that reads them. A required role with nothing that grants it = the `adversarial_eval_passed` dead-end.
**Parity:** the user-scoping filter is the same on the runs and memory endpoints — put it in one shared dependency both routers use, not copied per router.
**Golden-path:** bash suite — user A cannot read user B's runs/memory; a reviewer can. Assert on the real endpoints with real JWTs, not a simulated header.

---

### TODO-6: Redis Memory Hot Path (Spec §6.2)

**Design says:** During a run, load message_history from Redis (< 1ms). Flush to PG on session end.

**Current state:** `agent_memory` table + router exist. All reads are direct PG queries. No Redis layer.

**What to implement:**

1. Add Redis as a Helm dependency (already in the chart for Langfuse — reuse or add a dedicated instance)
2. In the SDK's memory client (or in `routers/memory.py`):
   - On `save_turn`: write to both Redis (`mem:{agent_name}:{thread_id}`) and PG
   - On `list_memory`: check Redis first, fall back to PG
   - Set TTL on Redis keys matching `session_ttl_hours`
3. On run completion: flush Redis → PG final state, expire key

**Files:**
- `services/registry-api/routers/memory.py` (add Redis read-through)
- `charts/agentshield/values.yaml` (Redis config)
- `sdk/agentshield_sdk/` (memory client if it exists)

**Priority:** Low — PG is fine for current scale. Implement when latency matters.

**Parity:** the read-through cache wraps the memory read path used by **both** sandbox and production runs — one code path, no fork.
**Golden-path:** integration test — a warm Redis hit and a cold PG miss both return the same message history for a thread.

---

### TODO-7: Sandbox Run TTL / Auto-Cancel (Playground doc T-11) — ✅ ALREADY SHIPPED (the item was backwards)

**This gap does not exist, and never did in the form written.** `_sweep_stale_durable_runs()` in
`services/registry-api/approval_timeout_worker.py` sweeps **`PlaygroundRun`** — the
**sandbox/playground** run table — and filters on `execution_shape` / `status` / `started_at` **only,
with no environment or context predicate at all**. It has always swept every durable playground run
past `_DURABLE_RUN_TTL_MINUTES` (10).

The WS-6 plan described the worker as production-only and proposed "extend the same worker to also
sweep sandbox", i.e. **the exact inverse of the code**. There is no production-only scoping to
extend, and `PlaygroundRun` carries no `environment` column to scope *by* (it has `deployment_id`
**and** `production_deployment_id`). Building the planned `_sweep_once(scope)` would have
**manufactured a parameter for a distinction the table does not draw** — a fork invented to satisfy
a doc. **Task deleted in WS-6; no code written.**

> **The real bug in this worker is a different one, and it is still OPEN** — see the "Agent pod-URL
> resolution" entry below. Reading TODO-7 as "the timeout worker is fine now" would be exactly
> backwards.

---

### TODO-8: Agent pod-URL resolution — `environment="production"` default is never threaded — 🔴 OPEN (LIVE BUG)

**Filed by WS-6 (2026-07-15). Not built — `services/registry-api/**` was owned by a concurrent lane
at the time. This is a REAL, LIVE defect, not a cleanup.**

`_agent_pod_url(agent_name, team, environment: str = "production")` in
`services/registry-api/approval_timeout_worker.py` is **parameterized but never threaded**: both
call sites take the default (`_notify_agent`, and `routers/approvals.py`'s console-decide branch).
Sandbox Services are named **`{agent}-sandbox`** (`deploy-controller/manifest_builder.py` builds
`f"{agent_name}-{environment}"`), so a **sandbox** approval's `/resume` POST is addressed to a
**non-existent `-production` pod** — and the resulting `httpx.RequestError` is **swallowed to a
`logger.warning`**. The approval still gets marked resolved, so **the DB row looks correct while the
agent was never actually resumed.**

This is the textbook shape of the repo's #1 bug class: *two parallel paths drift while a safe
default hides it.* **The repo already knows** — `routers/approvals.py` works around it for the
durable branch with an inline hand-built f-string whose own comment names the bug ("the
`-production` default DNS-fails for a sandbox/playground agent"). **One concern, two builders, one
fixed and one not.**

**The fix (specified, not written)** — `services/registry-api/agent_endpoints.py` as the single home:
`team_namespace(team)` (today duplicated in `workflow_orchestrator.py` **and** `routers/internal.py`),
`agent_pod_base(agent_name, team, environment)` with **`environment` REQUIRED and NO default**, and
`async resolve_agent_pod_base(agent_name, team)`. The no-default is the load-bearing choice, not
style: the entire bug is a default two callers silently fell into, so forgetting to thread it must
become a **`TypeError` at the call site** rather than something a runtime check guards. Then delete
`_agent_pod_url` and the inline duplicate (`is_durable` may decide the resume *body*; it must not
decide the *URL*), and make `internal.py`'s deliberate `-production` an **explicit argument**.
~8 hand-built pod-URL f-strings span 5 files.

**Ledger:** `workflow_orchestrator.py`'s workflow-orchestrator pod URL and `playground.py`'s
`k8s_namespace`-derived URL are **out of scope** — different naming conventions; folding them in
would widen the helper to two conventions (the fork-by-generalisation trap).

**Testing note:** `suite-79` **deliberately does not assert** `def agent_pod_base`. Asserting
symbols nobody in that lane could write would either fail honestly or invite a stub — and a stub in
this exact seam is the fake that hides this exact bug (`docs/bugs/durable-workflow-live-path.md`:
six faked suites shipped green while the real path was broken). The gate is owed **with** the fix.

---

## Current UI Bug: Browser Cache — ✅ RESOLVED (WS-6, studio 0.1.145)

**Shipped.** `STUDIO_BUILD` now has ONE definition (`studio/src/lib/build.ts`) and **two readers**:
the `window.__STUDIO_BUILD` runtime marker (`main.tsx`) and a visible `data-testid="studio-build"`
element in the sidebar. It must equal `STUDIO_TAG` (`scripts/deploy-cpe2e.sh`) and the chart pin
(`charts/agentshield/values.yaml`).

**What it was:** `window.__STUDIO_BUILD` was assigned in `main.tsx` and **read by nothing** — a
grep across `studio/src`, `studio/e2e`, `scripts`, and `charts` returned exactly one line, its own
assignment. It sat at `"0.1.76"` while `STUDIO_TAG` reached `0.1.143`: **67 tags of a marker that
silently lied**, because *a value nothing reads cannot fail loudly*. It was a live orphan (DoD #3).

**Enforcement (this is the part that matters):** `suite-79` **T-S79-002** asserts a **five-way**
agreement — `build.ts` == `STUDIO_TAG` == chart pin == the **live pod's image tag** == the marker
in the **served bundle**. E-3 (`docs/bugs/e3-never-ran-tag-not-bumped.md`) proved agreement between
the two tag *files* is worthless on its own: they agreed **on a stale tag** while the cluster
faithfully served old code and every check stayed green. The live pod and the served bytes are the
two that cannot lie. `scripts/check-tag-content-coupling.sh` covers the complementary half
(source changed ⇒ tag bumped).

> **A marker must be greppable in the artifact to prove anything.** WS-6 shipped `0.1.144` with the
> badge testid composed at runtime (`` `${badgeKey}-badge` ``). The DOM was correct and Vitest
> passed — and the served-bundle grep still read **0 occurrences**, because the literal string never
> existed in the bundle. Vitest could never have caught it; only the content grep could. The testid
> is now a literal (`NavItem.badgeTestId`), and `0.1.145` is the tag that carries the fix.

---

## Build Priority Order

**Status as of 2026-07-15 (WS-6 landed its studio half).** Of the 5 items moved to WS-6:
**TODO-1** (nav badge), **TODO-4** (Overview fork), and **Browser Cache** (build marker) are
**✅ RESOLVED** above. **TODO-3** was **already shipped and already recorded** — the plan owed only
a ledger line that was on the page, so the task was deleted. **TODO-7** was **already shipped and
the item was written backwards** — deleted, no code. **[WS-0 OQ-10]** ("fold
`WORKFLOW_REACTIVE_TIMEOUT_S` into the timeout worker") is **REJECTED as a regression**, not
deferred: the reactive cap is an **in-request `asyncio.wait_for`** holding the caller's connection,
while `approval_timeout_worker` is a **60s-poll background sweep** of abandoned rows — a background
poll cannot cap an in-request await, and folding them would leave a reactive caller hanging up to
60s past its own cap. Two mechanisms that share the word "timeout"; correctly separate.

**One item moved the other way:** WS-6 found a live defect the plan never saw and filed it as
**TODO-8 (Agent pod-URL resolution)** above — 🔴 **OPEN**. Sandbox approval resumes are still sent
to a non-existent `-production` pod and the failure is still swallowed. **It is the highest-value
item in this doc.**

Remaining in this doc:

```
TODO-2 (Alerting)            — production safety net, 1-2 days
TODO-5 (Role-based access)   — privacy/compliance, 2 days (ship role producer with the gate)
TODO-6 (Redis hot path)      — performance, defer until needed
```

Every item above must clear the **Acceptance bar** (top of this doc) — golden-path e2e per environment + shared-code parity — before it counts as done.

---

## Eval v2 — mode-aware evaluation (NEW 2026-07-13)

Today's eval is **reactive-shaped**: dataset items are `{input_message, expected_output}` (text) and `judge.py` scores `input_text → output_text` via LLM-as-judge. Two gaps, to be closed by a dedicated **Eval v2** workstream (comprehensive plan being authored at `docs/plan/execution-models-v2/eval-v2/plan.md`):

- **Gap 1 — per-mode dataset schemas (batch eval).** Scheduled/webhook agents need `{trigger_payload, expected_output}` items (design decision OQ-C, `playground-execution-modes.md`); batch eval must interpret items by the agent's mode. Only text-message datasets + a text judge exist today.
- **Gap 2 — trajectory / tool-call / side-effect judging.** The judge scores only the final output text. It does not evaluate the step trajectory (right tools, right order), tool-call correctness, or verify side-effects (e.g., an email actually sent) — which is what matters for durable/scheduled/webhook agents.

**Sequencing (eval depends on the mode being REAL):** trajectory eval (Gap 2, durable) is meaningful only once durable runs write real `run_steps` (**WS-1**); payload-based batch eval (Gap 1, scheduled/webhook) only once those triggers are real (**WS-3/WS-4**). So Eval v2 lands **after the execution spine** — phased: durable trajectory eval after WS-1, per-mode batch eval after WS-3/WS-4. The single-run test-fire + judge surface already works, so this is a coverage/quality upgrade, not a blocker. Must clear the Acceptance bar.

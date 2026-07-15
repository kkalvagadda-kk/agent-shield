# WS-2 Implementation Plan — Durable daemon: identity + async approval routing

**Slice:** WS-2 of Execution Models v2 (spec §5 WS-2; decisions R2/R3, D1). **Covers WS-2 ONLY.**
**Depends on WS-0 (agent_class authored) + WS-1 (durable park/resume + inbox).**
**Companion artifacts:** `data-model.md` (armed-by capture), `contracts/opa-daemon-rule.md`.

> **Migration PROVISIONAL** — head `0057` + WS-0 `0058`. WS-2 needs **one** migration for
> `agent_triggers.armed_by` (authorizing human) — provisionally the next free number after WS-0/WS-1 land.
> `agent_runs.run_by` already exists (`models.py:1506`); no column needed for the service-identity principal.

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

## 1. Goal

Make **daemon** a real, governed authority — not a NULL-coalesced label. A daemon run carries the agent's
**service identity** as principal, records the **authorizing human** who armed the trigger, and routes its
HITL approvals **async to a reviewer role** (no live user is on the connection). Concretely, after WS-2:

1. **OPA daemon rule.** `agentshield.rego` gains `user_identity_ok`: a **daemon** trigger-run needs no live
   `user_id`; a **user_delegated** run requires `input.user_id != ""`. Existing risk-based `require_approval`
   is unchanged. (`opa_policy/agentshield_test.rego` already asserts the intent.)
2. **Service identity as principal.** A daemon trigger-run sets `run_by` = the agent's service identity
   (`agent_identities`), and captures the **authorizing human** = whoever armed the trigger
   (`agent_triggers.armed_by`, new). Approval + audit read **"service:X on behalf of Y."**
3. **[R3] Identity is entry-path-determined — daemon agents keep `/chat`.** `agent_class` governs authority,
   **not** which endpoints exist. A single daemon agent's runs carry different identity by entry path: an
   **interactive `/chat`** run has an authenticated JWT caller → runs under the **caller's** identity (OPA
   sees a `user_id`); a **trigger-driven** run (cron/webhook) has no live user → runs under the **service
   identity** and `user_identity_ok` applies. The daemon "no live user" rule is a **floor for the trigger
   case**, never a license to drop a present user. The edge always requires a JWT (no unauth-chat hole).
4. **[R2] Async approver routing — role-based, into the Global Approvals Inbox.** A paused **daemon durable**
   run routes its approval to a configured **reviewer role** (`agent:reviewer` / on-call), surfaced in the
   same inbox (WS-1) filtered to that role. Durable wait = WS-1's checkpoint. **Email/webhook notification =
   future** (reuse the alerting transport — §9).
5. **[D1] Workflows.** A **daemon workflow** runs under the workflow's **service identity**, threaded to every
   member via `actor_chain` (identity-propagation §4.1) — members act as the workflow's service identity, not
   any user. `user_identity_ok` applies to member tool calls using the *workflow's* class. Inter-agent
   approvals route async to the workflow's approver policy; audit reads **"workflow:X (service) on behalf of
   Y."** The authorizing human = whoever armed the workflow trigger.

**Out of scope:** the full signed **RCT / actor_chain token** chain-of-custody (WS-2 lands the OPA rule +
service-identity `run_by` + armed-by capture + the workflow→member actor_chain **concept/field**; the signed
token is a separate initiative — identity-propagation doc, §9). Email/webhook approval notification (future).

## 2. Architecture

```
Trigger arms (Studio/API): agent_triggers.armed_by = current_user.sub  (NEW — the authorizing human)
        │
scheduler/event-gateway ──POST /internal/runs/start──► registry-api start_internal_run
        │  (no JWT caller — trigger-driven)
        ▼ load Agent; agent_class == "daemon"?
   daemon ─► run_by = agent_identities.service_id ; principal_display = "service:{agent} on behalf of {armed_by}"
   user_delegated ─► run_by = trigger.armed_by (the arming user's identity)
        │
        ▼ OPA input includes {agent_class, user_id (empty for daemon trigger-run), risk, ...}
   agentshield.rego: user_identity_ok(input) gate  +  existing require_approval(risk)
        │
        ▼ on require_approval (WS-1 park): Approval.reviewer_scope = "agent:reviewer" (daemon)
                                            vs the initiating user (user_delegated)
        ▼ Global Approvals Inbox (WS-1) filtered by reviewer role → async decide → resume
```

**Interactive vs triggered (R3), one shared start path, explicit context (No-Bandaid):** the identity
decision is made by **whether a JWT caller is present**, passed as an explicit `caller: Principal | None`
param — **not** sniffed from `agent_class`. `/chat` (JWT present) → caller identity; `/internal/runs/start`
(trigger, no JWT) → service identity for daemon / armed_by for user_delegated. No `getattr`, no priority
fallthrough.

**Workflow member propagation (D1):** the workflow run's principal is stamped once on the parent
`agent_runs.run_by`; `_dispatch` (`workflow_orchestrator.py:69` — today sends only `{"message":...}`) is
extended to carry the `actor_chain` header so members act under the workflow's authority. Member `agent_class`
is **ignored inside a workflow** (one authority per run tree).

## 3. Migration / Schema

**One migration (provisional next-free):** `agent_triggers.armed_by TEXT NULL` — the authorizing human who
armed the trigger (idempotent `ADD COLUMN IF NOT EXISTS`). No column for the service principal (`run_by`
exists). No `Approval` DDL — reviewer scope is derived from `agent_class` + a config field; if a persisted
`reviewer_scope` is wanted it's a nullable add on `approvals` (documented as optional in `data-model.md`).
See `data-model.md`.

## 4. Constitution / retro gates (condensed)

- **Parity = shared code:** the identity decision lives in **one** helper `resolve_principal(agent, caller,
  trigger)` called by both `/chat` and `/internal/runs/start` — not duplicated per entry path.
- **Golden-path per environment:** rego unit tests (daemon no-user allow; user_delegated no-user deny) + bash
  suite: daemon durable run parks, approval reads "service:X on behalf of Y", routes to a reviewer, non-review
  user cannot decide. Fails (not skips) on missing fixture.
- **Ship the gate's producer:** `armed_by` is captured at trigger-arm time (producer) in the same change as
  the audit/approval readers.
- **Fail-closed:** `user_identity_ok` **denies** a user_delegated trigger-run with an empty `user_id` (a
  missing principal is a denial, not a default-to-service downgrade).
- **No-Bandaid:** explicit `caller` context param (not `agent_class` sniffing); illegal "user_delegated with
  no user" is denied, not silently run as service.

## 5. File Structure

### registry-api
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/opa_policy/agentshield.rego` | M | Add `user_identity_ok`; wire into the decision; keep `require_approval` risk logic. |
| `services/registry-api/opa_policy/agentshield_test.rego` | M | Assert daemon-allow / user_delegated-deny (intent partly there). |
| `services/registry-api/identity.py` (or `auth_middleware.py`) | M/**C** | `resolve_principal(agent, caller, trigger) -> Principal` — the one identity decision; `principal_display`. |
| `services/registry-api/routers/internal.py` | M | Trigger-run path calls `resolve_principal(caller=None)`; stamps `run_by` = service identity (daemon) / `armed_by` (user_delegated). |
| `services/registry-api/routers/chat.py` | M | Interactive path calls `resolve_principal(caller=jwt_user)`; unchanged for user_delegated. |
| `services/registry-api/routers/triggers.py` | M | Capture `armed_by = current_user.sub` on arm/create. |
| `services/registry-api/routers/approvals.py` | M | Daemon run → `reviewer_scope=agent:reviewer`; audit `principal_display`. |
| `services/registry-api/workflow_orchestrator.py` | M | `_dispatch` carries `actor_chain`; parent `run_by` = workflow service identity for daemon workflows. |
| `services/registry-api/alembic/versions/00NN_trigger_armed_by.py` | **C** | `agent_triggers.armed_by` (provisional number). |

### Studio
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/pages/AgentDetailPage.tsx` | M | Trigger settings: show/arm captures the authorizing human; daemon approver-role config field. |
| `studio/src/pages/ApprovalsInboxPage.tsx` | M | Show `principal_display` ("service:X on behalf of Y"); reviewer-role filter (extends WS-1). |

### Tests + infra
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-57-daemon-identity.sh` | **C** | Daemon durable run: service-identity `run_by`, armed-by audit, reviewer routing, user_delegated-no-user deny. |
| `scripts/e2e/run-all.sh` | M | Register suite-57. |
| `studio/e2e/approvals-inbox.spec.ts` | M | Inbox shows "service:X on behalf of Y"; reviewer filter. |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump registry-api, studio (+ OPA bundle if bundled). |
| `docs/experience/playground.md` | M | Daemon identity + async approval routing. |

## 6. Tasks (dependency-ordered)

### T1 — OPA `user_identity_ok` rule + tests
- **Files:** `agentshield.rego` (M), `agentshield_test.rego` (M). Contract: `contracts/opa-daemon-rule.md`.
- **Acceptance:** daemon + empty `user_id` → allowed by `user_identity_ok`; user_delegated + empty `user_id`
  → denied; risk-based `require_approval` unchanged (regression asserted).
- **Deps:** none. **Verify:** `opa test services/registry-api/opa_policy/`.

### T2 — `resolve_principal` shared helper + armed-by capture
- **Files:** `identity.py`/`auth_middleware.py` (M/C), `routers/triggers.py` (M), migration `00NN` (C).
- **Contract:** `resolve_principal(agent, caller, trigger)`; `armed_by` captured on trigger arm.
- **Acceptance:** trigger-arm persists `armed_by`; helper returns service principal for daemon-no-caller,
  user principal for caller-present, and **raises/denies** for user_delegated-no-caller-no-armer.
- **Deps:** T1. **Verify:** `ast.parse` + mapper import; migration idempotency round-trip.

### T3 — Wire both entry paths (chat + internal) to `resolve_principal`
- **Files:** `routers/internal.py` (M), `routers/chat.py` (M).
- **Contract:** internal → `caller=None`; chat → `caller=jwt_user`; stamp `run_by` accordingly.
- **Acceptance:** a daemon agent's `/chat` run (JWT) runs under the caller; the same agent's cron run runs
  under the service identity — proven by `run_by` on the two `agent_runs`.
- **Deps:** T2. **Verify:** suite-57 T-S57-001/002.

### T4 — Async reviewer routing on approvals + audit display
- **Files:** `routers/approvals.py` (M), `ApprovalsInboxPage.tsx` (M), `AgentDetailPage.tsx` (M).
- **Contract:** daemon run → `reviewer_scope=agent:reviewer`; inbox filters by role; `principal_display`
  shown; approver-role config in trigger settings.
- **Acceptance:** a daemon durable run parks → appears in the reviewer's inbox with "service:X on behalf of
  Y" → reviewer decides → run resumes (WS-1); a non-reviewer cannot decide.
- **Deps:** T3, WS-1 inbox. **Verify:** suite-57 + Playwright.

### T5 — Workflow daemon identity + member actor_chain (D1)
- **Files:** `workflow_orchestrator.py` (M).
- **Contract:** parent `run_by` = workflow service identity (daemon workflow); `_dispatch` carries
  `actor_chain`; member class ignored at runtime.
- **Acceptance:** a daemon workflow's parent + child runs carry the service identity; member tool calls hit
  OPA with the workflow's class; audit reads "workflow:X (service) on behalf of Y".
- **Deps:** T3. **Verify:** suite-57 workflow cases.

### T6 — Suite-57 + deploy
- **Files:** `suite-57-daemon-identity.sh` (C), `run-all.sh` (M), `deploy-cpe2e.sh`+`values.yaml` (M),
  `docs/experience/playground.md` (M).
- **Acceptance:** suite green; tags bumped in both files.
- **Deps:** T1–T5. **Verify:** `bash scripts/e2e/suite-57-daemon-identity.sh`.

## 7. Gap Ledger
| Item | Status | Note |
|---|---|---|
| Full signed RCT / actor_chain **token** chain-of-custody | **deferred (intentional) → identity-propagation initiative** | WS-2 lands the OPA rule + `run_by` + `armed_by` + actor_chain **field/concept**; the cryptographic token is separate. |
| Email/webhook daemon approval notification | deferred (intentional) → future | WS-2 routes to a role in the inbox; notification reuses the alerting transport later. |
| Persisted `approvals.reviewer_scope` column | optional (documented in data-model) | Scope is derivable from `agent_class`+config; add a column only if audit needs it persisted. |

No orphan flags: `user_identity_ok` (producer=rego, reader=decision), `armed_by` (producer=trigger-arm,
reader=audit/approval), `resolve_principal` (called by chat + internal), `actor_chain` (set by `_dispatch`,
read by member OPA input).

## 8. Execution Notes
- **`resolve_principal` is the anti-drift core** — both entry paths call it; never sniff `agent_class` to
  decide identity, pass `caller` explicitly.
- **user_delegated-no-user is a DENY**, not a silent downgrade to service (fail-closed).
- **OPA bundle** — if the rego ships via the bundle server (`infra/opa-bundle-server/policy.rego`), update
  that copy too and note the known "Bundle load Forbidden" governance item.

## 9. T001 Re-grounding against the live tree (2026-07-14) — supersedes the plan's indicative specifics
- **Alembic head = `0060`** (`0059_eval_v2_dataset_and_run_mode`, `0060_eval_v2_result_dimensions`). WS-2's
  `armed_by` migration is **`0061`**, `down_revision="0060"` (NOT the doc's `00NN`/`0059`).
- **Suite number = `suite-70-daemon-identity.sh`** — the tasks doc guessed `suite-61`, but **suite-61 is
  taken** (eval-v2 E-0); suites exist through `suite-69`. Use **70**.
- **`agent_runs.run_by`** = `models.py:1559` (`Mapped[str | None]`, `String(255)`). No new run column.
- **`AgentTrigger`** = `models.py:1655`; `input_payload` = `:1694` — add `armed_by` right after it. No
  `armed_by` column exists yet.
- **OPA rego ships BOTH copies** — `services/registry-api/opa_policy/agentshield.rego` **and**
  `infra/opa-bundle-server/policy.rego` (`configmap-policy.yaml` bundles it). **T004 is IN SCOPE** (mirror the rule).
- **`agentshield.rego`** structure: `default allow := false` (:18), `allow if {` (:95), `require_approval if {`
  (:103, untouched), `deny_reason` rules (:111+). `agent := data.agents[input.sa_subject]`.
- **Service identity source (T007)** = `AgentIdentity` model / `agent_identities` table (`models.py:192`).
  No existing `resolve_principal`/`Principal` — `identity.py` is a **new** module.
- **`_dispatch`** = `workflow_orchestrator.py:70`; `_run_step` = `:418`; `_dispatch_durable_member` = `:115` (T016 extends the workflow member path).
- **armed_by producers (T008)** = `routers/triggers.py:61` (agent trigger create) **and**
  `routers/composite_workflows.py:574` (workflow trigger create) — both `AgentTrigger(...)` call sites.
- **`/chat` (T010)** = `routers/chat.py:519` (`start_chat`, `Depends(require_user)`); `run_by=user_sub` already stamped at `:481`.

## 10. T022 Verification sweep (2026-07-14) — WS-2 COMPLETE

**Orphan-grep (each symbol has ≥1 live non-test caller/reader):**
- `user_identity_ok` → `opa_policy/agentshield.rego` (allow conjunct + rule) + served in the deployed bundle.
- `resolve_principal` → `identity.py` (def) · `routers/internal.py` (trigger path) · `routers/chat.py` (interactive).
- `resolve_workflow_principal` → `identity.py` (def) · `routers/internal.py` (`_start_workflow_run`) · `workflow_orchestrator.py`.
- `principal_display` → `identity.py` (def) · `routers/approvals.py` (audit) · `schemas.py` (response field).
- `armed_by` → models · migration 0061 · triggers.py + composite_workflows.py (producers) · approvals.py + identity.py (readers).
- `approver_role` → models · migration 0062 · triggers.py/composite_workflows.py (create+update) · schemas (req+resp) · approvals.py (reviewer_scope derive).
- `reviewer_scope` → approvals.py (derive + 403 gate) · schemas.py (response) · Studio inbox filter.
- `actor_chain` → **comments only** (no dead HTTP header) — pod-side propagation is the deferred identity-propagation initiative (gap ledger).

**Static/tests:** `ast.parse` green on all touched registry-api files; `opa test services/registry-api/opa_policy/` = **19/19**; `cd studio && npm run typecheck` clean + `npm run test` **205/205**.

**Cluster acceptance:** suite-70 **8/8** no-fakes (real `/internal/runs/start` prod door after the 0.2.179 `runner_url` fix) · Playwright `approvals-inbox.spec.ts` **2/2** · CP1 infra 3/3 + behaviour 7/7 · CP2b infra 5/5. Images: registry-api **0.2.179**, studio **0.1.135**, migrations at **0062**.

**Real bug found & fixed during CP2:** durable **trigger** dispatch (`internal.py` `_dispatch_and_complete`) omitted `runner_url` → hit the non-existent shared `declarative-runner` Service → production trigger→single-agent durable runs never reached the pod. Fixed to target `{agent}-production.{ns}:8080` (mirrors reactive + playground + workflow-member callers); proven end-to-end by the re-run.

## Definition-of-Done gate (WS-2)
- [X] Real user journey proven: suite-70 drives real deploy→trigger→park→decide→resume; Playwright drives the real inbox Approve.
- [X] Save→reload→assert: approver_role persists (trigger create/update → re-GET); armed_by persists; suite-70 re-reads committed run_by/approval rows.
- [X] No orphan: §10 sweep — every symbol has a live caller; actor_chain is comment-only by design.
- [X] Fail-closed governance: OPA `user_identity_ok` deny; user_delegated-no-armer refused; non-reviewer decide 403; dispatch failure marks run failed + alerts.
- [X] Honest gap ledger: T021 (manual test plan) — RCT token + email notify deferred; OPA-input propagation + workflow SA-convention = debt.
- [X] Image tags in BOTH files (registry-api 0.2.179 / studio 0.1.135); migrations 0061 + 0062.

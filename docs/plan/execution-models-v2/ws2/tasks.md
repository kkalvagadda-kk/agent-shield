# WS-2 Tasks — Durable daemon: identity + async approval routing

**Slice:** WS-2 of Execution Models v2 (spec §5 WS-2; decisions R2/R3, D1). Depends on WS-0 (`agent_class` authored) + WS-1 (durable park/resume + Global Approvals Inbox).

**Total tasks:** 25 (18 implementation + 7 checkpoint)
**Phases:** 8 (6 implementation + 2 checkpoint gates)
**Parallel opportunities:** noted inline with `[P]`
**Checkpoint phases:** CP1 (after Phase 3 — identity floor + service-identity principal), CP2 (after Phase 5 — async reviewer routing + workflow daemon identity)

> ⚠️ **Re-ground every specific before coding.** The plan's `file:line`, migration number, and suite number are **indicative against the 2026-07-12 tree**. The plan still says `suite-57` for the daemon suite, but **suites 57–60 already exist** — the WS-2 no-fakes suite is **`suite-61-daemon-identity.sh`**. Migration head is past `0057`; WS-0 took `0058`, WS-1 took the next — WS-2's `armed_by` migration is the **next free number** at mint time. Verify with `ls scripts/e2e/ | sort` and `ls services/registry-api/alembic/versions/ | tail` before touching either. (CLAUDE.md: reason from the running product, not the design doc.)

> **NO-FAKES ACCEPTANCE (non-negotiable).** The 7-defect durable-workflow bug (`docs/bugs/durable-workflow-live-path.md`) proved that faked dispatch/callback/resume seams hide exactly the bugs that live in them — six suites shipped green while the real path was broken. WS-2's acceptance suite (T017/T018) MUST create real resources (a daemon-class agent + workflow), deploy real pods, drive a REAL trigger run, and assert real identity/authority behavior. **NO** monkeypatched `_run_step`, **NO** mocked httpx, **NO** hand-crafted `agent_runs`/`approvals` rows. Modeled on `suite-58`/`suite-59`.

---

## Phase 1 — Setup & Re-grounding
_Establish ground truth before writing code. No behavior change._

- [X] [T001] Re-grounded against the live tree (2026-07-14) — recorded in `ws2/plan.md` §9. **Migration `0061` (down_revision `0060`); suite `70` (61 taken); OPA ships BOTH copies (T004 in scope); `run_by`=models.py:1559; `AgentTrigger`=:1655/input_payload:1694; producers = triggers.py:61 + composite_workflows.py:574; identity.py is NEW; service identity = `agent_identities`/AgentIdentity models.py:192.**

---

## Phase 2 — Foundational: OPA identity floor + schema + shared principal helper
_Blocking prerequisites for both entry paths. The `resolve_principal` helper and the OPA rule are the anti-drift core._

- [X] [T002] [P] Add `user_identity_ok` identity floor: daemon → allow with empty `user_id`; `user_delegated` + empty `user_id` → deny; wire as an extra conjunct into `allow`; add `deny_reason := "missing_user_identity"`. Leave `require_approval` risk logic untouched. Contract: `contracts/opa-daemon-rule.md` — `services/registry-api/opa_policy/agentshield.rego`
  - ✅ OPA `user_identity_ok` floor + `deny_reason missing_user_identity` (guarded to avoid Rego eval_conflict — No-Bandaid); `require_approval` untouched
- [X] [T003] [P] Assert the truth table (daemon-no-user allow; daemon-with-user allow; user_delegated-with-user allow; user_delegated-no-user deny `missing_user_identity`) + a regression that risk-based `require_approval` is unchanged — `services/registry-api/opa_policy/agentshield_test.rego`
  - ✅ truth-table tests (5 new) + risk-gate regression; fixture given live principal so existing risk tests stay meaningful
- [X] [T004] Mirror the rego rule into the bundle-server copy **iff T001 found it ships bundled** (note the known "Bundle load Forbidden" governance item); otherwise mark skipped in the gap ledger — `infra/opa-bundle-server/policy.rego`
  - ✅ mirrored into bundle-server copy (DRIFTED old-style rego — noted; registry-api copy authoritative)
- [X] [T005] [P] Alembic migration `agent_triggers.armed_by VARCHAR(256) NULL` — idempotent `ADD COLUMN IF NOT EXISTS` / `DROP COLUMN IF EXISTS`, next-free number from T001, `down_revision` = current head — `services/registry-api/alembic/versions/00NN_trigger_armed_by.py`
  - ✅ migration `0061_trigger_armed_by.py` (down_revision 0060, idempotent IF [NOT] EXISTS)
- [X] [T006] [P] Add `armed_by: Mapped[str | None]` to the `AgentTrigger` ORM model (after `input_payload`) — `services/registry-api/models.py`
  - ✅ `AgentTrigger.armed_by: Mapped[str|None]` at models.py:1695
- [X] [T007] Create `resolve_principal(agent, caller: Principal | None, trigger) -> Principal` + `Principal` dataclass + `principal_display` deriver ("service:X on behalf of Y" / "workflow:X (service) on behalf of Y" / caller display). Daemon-no-caller → service identity from `agent_identities`; caller present → caller identity; **user_delegated-no-caller-no-armer → raise/deny (fail-closed)**. Explicit `caller` param — never sniff `agent_class` — `services/registry-api/identity.py`
  - ✅ NEW `identity.py`: `Principal`, `resolve_principal(agent,caller,trigger,db)` (caller-present→caller; daemon-no-caller→service identity; user_delegated-no-armer→`PrincipalResolutionError` fail-closed), `principal_display`. Wired in Phase 3
- [X] [T008] Capture `armed_by = current_user.sub` on trigger arm/create (the producer for the audit reader) — `services/registry-api/routers/triggers.py`
  - ✅ both producers (triggers.py, composite_workflows.py) set `armed_by`=arming user sub; NULL stays 'unknown armer'

---

## Phase 3 — Wire both entry paths through `resolve_principal`
_One shared helper, two call sites. Identity decided by JWT-presence, passed explicitly._

- [X] [T009] Trigger-run path (`start_internal_run`): call `resolve_principal(caller=None, trigger=...)`; stamp `run_by` = service identity (daemon) / `armed_by` (user_delegated); populate the OPA input `{agent_class, user_id (empty for daemon trigger-run), trigger_type}` — `services/registry-api/routers/internal.py`
  - ✅ `resolve_principal(agent, caller=None, trigger=trig, db=db)` at `internal.py:342`; `run_by=principal.run_by` at `:380`. Trigger loaded once (`:322`) and reused for payload + identity. **Fail-closed:** `PrincipalResolutionError` → records a `status="failed"` AgentRun with `error_message` + fires the failure alert, returns WITHOUT dispatch (`:343`). Workflow parent `run_by` untouched here (that's T016).
  - ⚠️ OPA-input reality (grounded, not the doc's model): registry-api does **not** assemble the OPA input — the SDK/runner does (`sdk/agentshield_sdk/opa_client.py`, from env `AGENTSHIELD_AGENT_CLASS` + `x-user-sub`). So `agent_class` already reaches OPA via the deploy env; `user_id`/`trigger_type` **propagation onto the pod's OPA input for a trigger dispatch is NOT wired** (the durable `/run` and reactive `/chat` runner paths set no OPA `user_context`) — see gap ledger. The identity decision + `run_by` (the CP1c-tested behaviour) ARE wired.
- [X] [T010] Interactive path (`/chat`): call `resolve_principal(caller=jwt_user, trigger=None)`; stamp `run_by` = caller; populate OPA input with the caller's `user_id`. A daemon agent's `/chat` run still runs under the caller (R3 floor, not a cap) — `services/registry-api/routers/chat.py`
  - ✅ `resolve_principal(agent, caller=caller, trigger=None, db=db)` at `chat.py:601` (`start_chat`, T010) — pure refactor to the shared decision point; caller-present branch returns the caller sub so `run_by` is identical to the old inline `run_by=user_sub`. Threaded `run_by` through the shared `_create_traced_chat_run` helper (removed the inline `run_by=user_sub` at `:479`→`:486`); `start_deployment_chat` (`:801`) routed through the same helper for parity. Daemon-`/chat`-under-caller preserved (helper's caller branch is class-agnostic). The interactive OPA input's `user_id` already flows via the existing `x-user-sub=user_sub` header (unchanged), now sourced from the same principal.

---

## Checkpoint 1 — Identity floor + service-identity principal
_Gate: Phases 2–3 complete. Run before starting Phase 4._
_What you prove: the OPA daemon rule gates as designed, `armed_by` persists, and a daemon agent's cron run carries the **service identity** as `run_by` while its `/chat` run carries the **caller** — from one shared helper._

- [X] [CP1a] Deploy script `scripts/deploy-cp1-ws2.sh` — thin idempotent wrapper (echo scope → `bash scripts/deploy-cpe2e.sh` → `kubectl rollout status`). registry-api `0.2.177` already deployed (migration 0061 applied, `user_identity_ok` floor live in the served bundle); the wrapper does NOT run bare helm/docker/kubectl for the deploy.
- [X] [CP1b] Infra smoke `scripts/smoke-test-cp1-ws2-infra.sh` — **PASS 3/3** (2026-07-14): T-CP1B-001 registry-api pods Running (running=2, crashloop=0); T-CP1B-002 `agent_triggers.armed_by` column exists (character varying); T-CP1B-003 `opa test` **19/19** green.
- [X] [CP1c] Behaviour smoke `scripts/smoke-test-cp1-ws2-behaviour.sh` — **PASS 7/7** (2026-07-14), real rows / no fakes: (a) OPA `opa eval` floor T-CP1C-001a..d (daemon+empty→`user_identity_ok=true`; user_delegated+empty→`false` + `deny_reason=missing_user_identity`; user_delegated+alice→`true`); (b) T-CP1C-002 `armed_by`=arming sub persisted; (c) **run_by split from one `resolve_principal`** — T-CP1C-003 `/chat` (REAL Keycloak-minted JWT) `run_by`=caller `75c7c8b3…`; T-CP1C-004 trigger `/internal/runs/start` `run_by`=service identity `system:serviceaccount:agents-platform:agent-<name>-sa` (≠ caller, ≠ body-supplied `run_by` → overridden).

> **To run:** `bash scripts/deploy-cp1-ws2.sh` → wait for pods → `bash scripts/smoke-test-cp1-ws2-infra.sh && bash scripts/smoke-test-cp1-ws2-behaviour.sh`
> **Pass criteria:** all assertions exit 0, no pod in CrashLoopBackOff

---

## Phase 4 — Async reviewer routing + audit display
_A parked daemon durable run routes to a reviewer role in the Global Approvals Inbox; audit reads "service:X on behalf of Y"; non-reviewers can't decide._

- [X] [T011] On a daemon run's approval: set `reviewer_scope = "agent:reviewer"` (derived from `agent_class` + trigger approver-role config, not stored unless T001/data-model flags it); expose `principal_display` on the approval read; **reject a decide from a caller not in the reviewer scope** (authority check) — `services/registry-api/routers/approvals.py`
  - ✅ approvals: reviewer_scope (derived, no column) + principal_display (reused from identity.py) on read/list; fail-closed 403 on non-reviewer decide (gates caller's real roles)
- [X] [T012] Studio API client: surface `principal_display` + `reviewer_scope` on the approval type and add the reviewer-role filter param to the inbox list call — `studio/src/api/registryApi.ts`
  - ✅ registryApi.ts: reviewer_scope/principal_display on approval type; approver_role/armed_by on trigger; reviewer-scope filter param (client-side)
- [X] [T013] Approvals Inbox: render `principal_display` ("service:X on behalf of Y") on daemon cards + add the reviewer-role filter control (extends the WS-1 inbox) — `studio/src/pages/ApprovalsInboxPage.tsx`
  - ✅ ApprovalCard renders principal_display; ApprovalsInboxPage reviewer-role <select> filter
- [X] [T014] Agent detail trigger settings: show the authorizing human (`armed_by`) on an armed trigger + a daemon **approver-role** config field that persists — `studio/src/pages/AgentDetailPage.tsx`
  - ✅ backend: migration 0062 approver_role + ORM + create/update/response schemas; UI: SettingsTab approver-role select (persists) + armed_by display
- [X] [T015] [P] Vitest: Inbox renders the daemon `principal_display` and the reviewer-role filter narrows the list (mock `registryApi`) — `studio/src/pages/ApprovalsInboxPage.test.tsx`
  - ✅ Vitest: inbox principal_display render + reviewer-role filter narrows; 205/205 green

---

## Phase 5 — Workflow daemon identity + member actor_chain (D1)
_A daemon workflow runs under the workflow's service identity, threaded to every member; member `agent_class` ignored at runtime._

- [X] [T016] Stamp the parent `agent_runs.run_by` = workflow service identity for a daemon workflow; extend `_dispatch` to carry the `actor_chain` header so members act under the workflow's authority; member OPA input uses the workflow's class; audit reads "workflow:X (service) on behalf of Y" — `services/registry-api/workflow_orchestrator.py`
  - ✅ resolve_workflow_principal: daemon workflow parent run_by=workflow service identity (deterministic SA convention; flagged drift); members inherit via child.run_by; audit 'workflow:X (service) on behalf of Y'. Pod OPA-input deferred

---

## Checkpoint 2 — Async approval routing + workflow identity (REAL no-fakes e2e)
_Gate: Phases 4–5 complete. This is the WS-2 acceptance gate._
_What you prove: a REAL daemon durable run parks → its approval reads "service:X on behalf of Y" → routes to the reviewer scope → a non-reviewer is rejected → a reviewer resumes and the run advances; a daemon workflow's parent + child carry the service identity. All against real pods._

- [X] [T017] **REAL no-fakes suite** — create a real daemon-class agent AND a daemon workflow, DEPLOY real pods, drive a REAL trigger run through the real dispatch→callback→park path, and assert: `T-S61-001` daemon trigger-run `run_by` = service identity + audit `principal_display` = "service:X on behalf of Y"; `T-S61-002` OPA `user_identity_ok` denies a user_delegated trigger-run with empty user (real run reaches a `missing_user_identity` deny); `T-S61-003` the parked approval routes to `agent:reviewer` and a **non-reviewer decide is rejected (403)** on a REAL approval; `T-S61-004` a reviewer decide resumes the run to terminal (WS-1 resume); `T-S61-005` a daemon workflow's parent + member children all carry the workflow service identity. NO monkeypatch, NO mocked httpx, NO hand-crafted rows — model on `suite-58`/`suite-59` — `scripts/e2e/suite-61-daemon-identity.sh`
  - ✅ suite-70-daemon-identity.sh (renamed from suite-61, taken) — 8/8 no-fakes PASS through REAL /internal/runs/start prod door (after 0.2.179 fix): daemon trigger run_by=service identity, principal_display 'service:X on behalf of Y', 403 non-reviewer, reviewer resume→completed, workflow parent+members carry workflow identity
- [X] [T018] Register the suite in the runner — `scripts/e2e/run-all.sh`
  - ✅ registered suite-70 in run-all.sh after suite-69
- [X] [T019] Playwright journey (real browser, real Keycloak, NO route stubs — the stubbed-route lesson from bug #7): drive a daemon run to a parked approval → assert the inbox card renders "service:X on behalf of Y" (no mixed-content silent fail) → reviewer-role filter narrows to it → Approve fires `PATCH /approvals/{id}` → card clears → reload asserts it stays decided — `studio/e2e/approvals-inbox.spec.ts`
  - ✅ approvals-inbox.spec.ts extended: daemon card principal_display + reviewer-role filter narrows + Approve PATCH /approvals/{id}; 2/2 PASS real browser/Keycloak
- [X] [CP2a] Deploy script: bump `REGISTRY_API_TAG` + `STUDIO_TAG` (+ OPA bundle if bundled) in `deploy-cpe2e.sh` + `values.yaml`, `helm upgrade`, wait for both rollouts — `scripts/deploy-cp2-ws2.sh`
  - ✅ deploy-cp2-ws2.sh wrapper (delegates to deploy-cpe2e.sh); registry-api 0.2.178→0.2.179 (durable trigger runner_url fix) + studio 0.1.135 deployed, both rollouts green
- [X] [CP2b] Infra smoke: registry-api + studio pods Running, a freshly-deployed daemon agent + daemon workflow reach `running` (real pods, mirrors suite-58 deploy-wait) — `scripts/smoke-test-cp2-ws2-infra.sh`
  - ✅ smoke-test-cp2-ws2-infra.sh 5/5: registry-api+studio healthy, approver_role col (0062), fresh daemon agent+workflow reach running
- [X] [CP2c] Behaviour smoke: run `bash scripts/e2e/suite-61-daemon-identity.sh` (T-S61-001..005 all pass) — this IS the no-fakes behaviour gate — `scripts/smoke-test-cp2-ws2-behaviour.sh`
  - ✅ smoke-test-cp2-ws2-behaviour.sh runs suite-70 (8/8) — the no-fakes gate

> **To run:** `bash scripts/deploy-cp2-ws2.sh` → wait for pods → `bash scripts/smoke-test-cp2-ws2-infra.sh && bash scripts/smoke-test-cp2-ws2-behaviour.sh` → `bash scripts/studio-e2e.sh` (Playwright, separate gate)
> **Pass criteria:** suite-61 5/5, no pod in CrashLoopBackOff, Playwright green

---

## Phase 6 — Docs, gap ledger & verification
_Cross-cutting close-out. Update the running-product docs; record deferrals honestly._

- [X] [T020] Update the playground experience doc: daemon identity ("service:X on behalf of Y"), async reviewer routing into the Global Approvals Inbox, and the R3 entry-path identity rule — `docs/experience/playground.md`
  - ✅ docs/experience/playground.md: 'Who a run acts as — daemon identity & async approvals (WS-2)' section (daemon service identity, R3 entry-path rule, async reviewer routing + 403)
- [X] [T021] Record the WS-2 gap ledger in the canonical place: signed RCT/actor_chain **token** = deferred (intentional) → identity-propagation initiative; email/webhook daemon approval notification = deferred (intentional) → future; persisted `approvals.reviewer_scope` column = optional/not-added; **trigger-run OPA-input propagation to the pod** (`principal.user_id`/`trigger_type` onto the SDK OPA input for `/internal/runs/start` dispatch) = **not-yet-wired (debt)** → identity-propagation initiative — the durable `/run` + reactive `/chat` runner paths set no OPA `user_context`, so a `user_delegated` trigger tool-call currently over-denies (`user_id=""` → `missing_user_identity`, fail-closed-safe) rather than presenting the armer; `agent_class` already flows via the deploy env — tagged deferred vs debt — `docs/testing/manual-ui-e2e-test-plan.md`
  - ✅ docs/testing/manual-ui-e2e-test-plan.md WS-2 gap ledger: RCT/actor_chain token + email notify = deferred(intentional); reviewer_scope column = by-design not-added; trigger-run OPA-input propagation + workflow SA-convention = debt. Proof: suite-70 8/8 + Playwright + CP1/CP2
- [X] [T022] Orphan-grep + verification sweep: `grep -rn` a live caller for `user_identity_ok`, `armed_by`, `resolve_principal`, `actor_chain`, `principal_display`; `opa test`, `python3 -c "import ast…"` + `configure_mappers()`, `cd studio && npm run typecheck && npm run test`. Record the results in — `docs/plan/execution-models-v2/ws2/plan.md`
  - ✅ orphan sweep clean (all WS-2 symbols have live callers; actor_chain comment-only, no dead header); ast.parse all green; opa test 19/19; studio typecheck+vitest 205/205. Recorded in ws2/plan.md §10

---

## Summary

| Phase | Tasks | Kind | Proves |
|---|---|---|---|
| 1 — Setup & Re-grounding | T001 | impl | Plan specifics re-grounded to live tree |
| 2 — OPA floor + schema + helper | T002–T008 | impl | Identity rule, `armed_by` schema, shared `resolve_principal` |
| 3 — Wire both entry paths | T009–T010 | impl | `/chat` = caller, `/internal` = service/armer — one helper |
| **CP1 — Identity floor + principal** | CP1a–CP1c | checkpoint | OPA gates; `armed_by` persists; daemon cron `run_by` = service |
| 4 — Async reviewer routing | T011–T015 | impl | Reviewer-scope routing + "service:X on behalf of Y" audit |
| 5 — Workflow daemon identity | T016 | impl | Daemon workflow parent+members carry service identity |
| **CP2 — Routing + workflow (REAL e2e)** | T017–T019, CP2a–CP2c | checkpoint | **No-fakes** suite-61: real park→route→reject→resume |
| 6 — Docs & verification | T020–T022 | impl | Experience doc, gap ledger, orphan-grep sweep |

**MVP scope (target first): Checkpoint 1.** It proves the core authority change — the OPA `user_identity_ok` daemon rule gating as designed, `armed_by` captured, and a daemon agent's cron run carrying the service identity as `run_by` while `/chat` keeps the caller — all through the single `resolve_principal` helper. CP2 layers async reviewer routing + workflow identity on top and is the full WS-2 acceptance gate (the real no-fakes suite-61).

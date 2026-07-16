# POC-3 — User-Profile Response Preferences — Tasks

Canonical, dependency-ordered, executable task list generated from `plan.md` + `research.md` +
`data-model.md` + `contracts/{enums,composition-contract,endpoints}.md` +
`docs/design/context-storage-poc-3-preferences.md`.

**Branch**: `worktree-ux-preview-context-storage` — commit here ONLY; never merge/push/PR to main.
**Baseline**: registry-api 0.2.190 / studio 0.1.143 / declarative-runner 0.1.55.
**Target tags**: registry-api **0.2.191** / declarative-runner **0.1.56** / studio **0.1.144** (all verified unused — research.md R8).

Conventions:
- `[P]` = parallelizable (files disjoint from every other unstarted task and deps already met).
- Every task lists exact files (Create/Modify), a one-line acceptance, dependencies, and a `Verify:` command.
- Verify commands assume repo root `…/ux-preview-context-storage`.
- **Grounding corrections obeyed** (research.md): canonical enum vocab from `contracts/enums.md`
  (tone = `professional/neutral/casual`, format = `prose/bulleted/structured`, length = `concise/balanced/detailed`);
  **typed columns, not JSONB**; DB field is `response_length` (frontend key `length` → `response_length` on wiring);
  daemon discriminator = **empty `user_id`**; endpoints go in the **existing** `routers/me.py`;
  the runner applies the directive as a **leading `SystemMessage` at invoke time**, NOT in the cached `graph_builder` prompt;
  `AgentRun.user_id` stamped at **both** workflow-run creation sites.

---

## Phase 1 — Data layer (registry-api)

### T01 [P] — Migration 0065 `user_profiles`
- **Files**: `services/registry-api/alembic/versions/0065_user_profiles.py` (Create).
- **Contract**: `revision="0065"`, `down_revision="0064"` (research.md R0). `upgrade()` creates the
  typed-column table (`data-model.md`) + 5 guarded `CHECK`s (`contracts/enums.md`, copy 0064's
  `DO $$ … pg_constraint …$$` guard); `downgrade()` `DROP TABLE IF EXISTS`. Idempotent (`IF NOT EXISTS`).
- **Acceptance**: `upgrade head` → `downgrade -1` → `upgrade head` round-trips; re-running upgrade is a no-op.
- **Dependencies**: none.
- **Verify**: `python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0065_user_profiles.py').read())"`

### T02 [P] — `UserProfile` ORM model
- **Files**: `services/registry-api/models.py` (Modify).
- **Contract**: `class UserProfile(Base)` exactly per `data-model.md` — text PK `user_id`, 5 nullable
  `Text` enum columns (`response_length`, `tone`, `format`, `language`, `expertise`), `updated_at`
  `_TSTZ server_default=_NOW` (research.md R7). No JSONB.
- **Acceptance**: mappers configure; `__tablename__ == "user_profiles"`; columns match 0065; `user_id` is PK.
- **Dependencies**: none (parallel with T01; both cite the same contracts so names agree).
- **Verify**: `cd services/registry-api && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print(models.UserProfile.__tablename__)"`

### T03 — `preferences.py` (vocab + schemas + composition seam)
- **Files**: `services/registry-api/preferences.py` (Create).
- **Contract**: `PREFERENCE_VOCAB`, `PHRASE_MAP`, `ADVISORY_PREFIX`, `_FIELD_ORDER` verbatim from
  `contracts/enums.md` + `contracts/composition-contract.md`; `UserPreferencesUpdate` (5 `Optional[Literal]`)
  + `UserPreferences` (`+ updated_at`, `from_attributes`); `compose_preference_directive(prefs)->str|None`
  (pure; `language="auto"` emits no phrase; all-None ⇒ None); `load_user_preferences(db, user_id)` (missing
  row ⇒ all-None); `compose_directive_for_user(db, user_id)` — the ONE seam; falsy `user_id` ⇒ `None` with
  **no DB read** (structural daemon-skip, research.md R3).
- **Acceptance**: both worked examples emit verbatim; None on empty / `auto`-only; missing row ⇒ all-None.
- **Dependencies**: T02 (imports `UserProfile`).
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('preferences.py').read())" && python3 -c "from preferences import compose_preference_directive, UserPreferences as U; print(compose_preference_directive(U(response_length='concise', format='bulleted', expertise='expert')))"`

### T04 — `GET`/`PUT /api/v1/me/preferences` (existing me.py router)
- **Files**: `services/registry-api/routers/me.py` (Modify).
- **Contract**: `contracts/endpoints.md`. Add to the **existing** `/api/v1/me` router (research.md R2 — no
  new router/registration). Both `Depends(require_user)`, `user_id = claims["sub"]` (caller-scoped, no path id).
  GET → `UserPreferences` (all-null default if no row). PUT → upsert
  (`INSERT … ON CONFLICT (user_id) DO UPDATE SET … updated_at=now()`), body `UserPreferencesUpdate` (422 on
  out-of-vocab), returns persisted `UserPreferences`.
- **Acceptance**: PUT→GET returns saved values + `updated_at`; no-row GET is all-null; bad enum ⇒ 422.
- **Dependencies**: T02, T03.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('routers/me.py').read())" && python3 -c "import routers.me"`

## Phase 2 — Directive application (runner)

### T05 [P] — Runner: `workflow_executor` applies `user_directive` as leading SystemMessage
- **Files**: `services/declarative-runner/workflow_executor.py` (Modify).
- **Contract**: `run` and `run_streamed` gain `user_directive: str | None = None`. In both, before building
  state: `lead = [SystemMessage(content=user_directive)] if user_directive else []` and
  `state = {"messages": lead + history + [HumanMessage(content=safe_message)]}` (research.md R6 — applied at
  **invoke time**, NOT the cached `graph_builder` prompt; lands after `create_react_agent`'s author
  instructions). Import `SystemMessage` from `langchain_core.messages`. `None` ⇒ byte-identical to today.
- **Acceptance**: with a directive, a leading `SystemMessage` carrying that exact string is in the invoked
  state; with `None`, the message list equals the pre-change list.
- **Dependencies**: none (pure runner-side; field contract fixed in `contracts/endpoints.md`).
- **Verify**: `python3 -c "import ast; ast.parse(open('services/declarative-runner/workflow_executor.py').read())"`

### T06 — Runner: `ChatRequest.user_directive` + handler pass-through
- **Files**: `services/declarative-runner/main.py` (Modify).
- **Contract**: add `user_directive: str | None = None` to `ChatRequest`; `chat` (sync) and `chat_stream`
  pass `user_directive=req.user_directive` into `run(...)` / `run_streamed(...)` (research.md R6).
- **Acceptance**: POST `/chat/stream` with `user_directive` reaches `run_streamed`; without it, `None`.
- **Dependencies**: T05.
- **Verify**: `python3 -c "import ast; ast.parse(open('services/declarative-runner/main.py').read())"`

## Phase 3 — Directive composition + dispatch (registry-api)

### T07 — Registry: `pod_stream` carries `user_directive` in the body
- **Files**: `services/registry-api/pod_stream.py` (Modify).
- **Contract**: `stream_pod_chat_frames` gains `user_directive: str | None = None`; when truthy, set
  `body["user_directive"] = user_directive` (research.md R4 — the ONE shared pod-body reader). Omitted when None.
- **Acceptance**: body includes `user_directive` iff passed.
- **Dependencies**: none structurally, but field name must match T06; sequence after T06 so the runner accepts
  the field before registry emits it (deploy ordering).
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('pod_stream.py').read())"`

### T08 — Registry: `chat.py` composes + injects the directive (single-agent chat)
- **Files**: `services/registry-api/routers/chat.py` (Modify).
- **Contract**: `_proxy_agent_stream` gains `user_directive: str | None = None` → forwards to
  `stream_pod_chat_frames`. In `stream_chat` and `stream_deployment_chat`, before the stream generator,
  `user_directive = await compose_directive_for_user(db, run.user_id)` (import from `preferences`) and pass into
  `_proxy_agent_stream(...)` (research.md R4 — compose at STREAM time; `run.user_id` is always the interactive caller).
- **Acceptance**: a chat by a user with prefs sends `user_directive`; a user with no prefs sends none;
  ownership/trace behavior unchanged.
- **Dependencies**: T03, T07.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('routers/chat.py').read())" && python3 -c "import routers.chat"`

### T09 — Registry: stamp `user_id` on parent workflow runs (both creation sites)
- **Files**: `services/registry-api/routers/composite_workflows.py` (Modify),
  `services/registry-api/routers/internal.py` (Modify).
- **Contract** (research.md R5a): in `composite_workflows.run_workflow_stream`'s parent `AgentRun(...)` add
  `user_id=caller_sub`; in `internal.start_internal_run`'s workflow parent `AgentRun(...)` add
  `user_id=principal.user_id` (empty ⇒ daemon). `AgentRun.user_id` already exists (`models.py:1561`). No other change.
- **Acceptance**: interactive workflow run parent `user_id == caller_sub`; daemon trigger parent `user_id == ""`.
- **Dependencies**: none (independent of T08); MUST land before T10 (it is the discriminator source).
- **Verify**: `cd services/registry-api && python3 -c "import ast; [ast.parse(open(f).read()) for f in ('routers/composite_workflows.py','routers/internal.py')]"`

### T10 — Registry: workflow member injects the directive (reactive members)
- **Files**: `services/registry-api/workflow_orchestrator.py` (Modify).
- **Contract** (research.md R5): in `_run_step_stream`, extend the parent query to also select `AgentRun.user_id`;
  `user_directive = await compose_directive_for_user(s, parent_user_id)` (import from `preferences`; reuse session).
  `_dispatch_stream` gains `user_directive: str | None = None` → forwards to `stream_pod_chat_frames`;
  `_run_step_stream` passes it into `_dispatch_stream`. **Durable members unchanged** (documented gap).
- **Acceptance**: a reactive member of a `user_delegated` workflow receives the directive; a daemon workflow
  (`parent_user_id==""`) sends none.
- **Dependencies**: T03, T07, T09.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('workflow_orchestrator.py').read())" && python3 -c "import workflow_orchestrator"`

## Phase 4 — Frontend (studio)

### T11 [P] — Frontend API client methods
- **Files**: `studio/src/api/registryApi.ts` (Modify).
- **Contract**: `UserPreferences` interface + `getMyPreferences` + `updateMyPreferences` verbatim from
  `contracts/endpoints.md`; both use the shared `http` client (Bearer auto-attached; pattern = `getMe` R9).
- **Acceptance**: typecheck clean; both call `/me/preferences` (GET/PUT).
- **Dependencies**: none.
- **Verify**: `cd studio && npm run typecheck`

### T12 — Real PreferencesPage + route swap
- **Files**: `studio/src/pages/PreferencesPage.tsx` (Create), `studio/src/App.tsx` (Modify).
- **Contract**: real page with local `PREFERENCE_OPTIONS` (`contracts/enums.md` codes+labels; **key
  `response_length`, not `length`** — R1), 5 selectors + Language select, load-on-mount via `getMyPreferences`,
  Save via `updateMyPreferences` (optimistic + success/error toast), reflects saved state. Keep the directive
  preview but derive it from the same option set (display-only; never sends free text). `App.tsx`: swap the
  `/preferences` import from `./pages/preview/PreferencesPage` → `./pages/PreferencesPage` (no route/nav change, R9).
- **Acceptance**: page loads current prefs; changing a selector + Save issues a PUT; toast fires; reload shows
  saved values; no console errors.
- **Dependencies**: T11.
- **Verify**: `cd studio && npm run typecheck`

## Phase 5 — Tests + docs

### T13 [P] — pytest: `compose_preference_directive`
- **Files**: `services/registry-api/tests/test_preference_directive.py` (Create).
- **Contract**: assert each enum→phrase verbatim from `PHRASE_MAP`; NULLs omitted; `language='auto'` omitted;
  all-None ⇒ None; both worked examples verbatim; two-user divergence (A≠B, both non-None). Pure — no DB.
- **Acceptance**: `pytest tests/test_preference_directive.py` green.
- **Dependencies**: T03.
- **Verify**: `cd services/registry-api && python3 -m pytest tests/test_preference_directive.py -q`

### T14 [P] — Vitest: `PreferencesPage.test.tsx`
- **Files**: `studio/src/pages/PreferencesPage.test.tsx` (Create).
- **Contract**: `registryApi` mocked (`getMyPreferences`/`updateMyPreferences`) via `renderWithProviders`
  (R10): loaded state renders saved selection; empty (all-null) renders unselected defaults; selector-click +
  Save calls `updateMyPreferences` with the right payload and shows a toast; all enum options render (compose/
  option mapping).
- **Acceptance**: `cd studio && npm run test -- PreferencesPage` green.
- **Dependencies**: T12.
- **Verify**: `cd studio && npm run test -- PreferencesPage`

### T15 — Playwright: preferences persistence journey
- **Files**: `studio/e2e/preferences.spec.ts` (Create).
- **Contract**: real Keycloak login (`global-setup`); navigate `/preferences`; select `concise` + `bulleted`;
  Save with `page.waitForResponse` on PUT `**/me/preferences` 200; `page.reload()`; assert both selectors show
  the saved values (persistence round-trip). Optional soft: run agent + observe reply shape (warm-pods boundary).
- **Acceptance**: `bash scripts/studio-e2e.sh` runs the spec green (SKIP only on the accepted pod-availability
  boundary for the soft output check).
- **Dependencies**: T12 deployed (CP2).
- **Verify**: `bash scripts/studio-e2e.sh` (post-CP2).

### T16 [P] — Docs: experience + spec + gap ledger
- **Files**: `docs/experience/playground.md` (Modify), `docs/spec.md` (Modify),
  `docs/testing/manual-ui-e2e-test-plan.md` (Modify).
- **Contract**: playground.md — note `user_directive` injection into `user_delegated` chat/workflow dispatch;
  spec.md — record the `user_profiles` entity + advisory-directive seam; manual-ui-e2e-test-plan.md — gap-ledger
  the deferred items (durable-member directive; HITL-resume re-apply; language UI breadth), each tagged
  **deferred (intentional)**.
- **Acceptance**: all three docs updated to reflect the shipped behavior.
- **Dependencies**: T10 (behavior finalized).
- **Verify**: `bash -c 'test -s docs/experience/playground.md && grep -q user_directive docs/experience/playground.md'`

### T17 — suite-76 + register in run-all
- **Files**: `scripts/e2e/suite-76-preferences.sh` (Create), `scripts/e2e/run-all.sh` (Modify).
- **Contract** (copy the suite-75 harness — in-pod `kubectl exec … python3`, `AsyncSessionLocal`,
  `get_token()` password-grant for `platform-admin`, real curl/jq; R10):
  - **T-S76-001** — PUT `{response_length:concise, format:bulleted, expertise:expert}` → GET returns them (+`updated_at`).
  - **T-S76-002** — reload round-trip: a fresh GET (new client) still returns the saved values.
  - **T-S76-003** — ownership scoping: insert a row for a DIFFERENT `user_id` in DB; GET `/me/preferences` as
    admin returns admin's row, NOT the other user's (caller-scoped).
  - **T-S76-004** — enum-422: PUT `{tone:"loud"}` → 422.
  - **T-S76-005** — compose precedence + two-user differs: upsert two `user_profiles` rows (A: bulleted/concise,
    B: prose/detailed) in DB; `compose_directive_for_user` for each → both non-None, differ, each starts with
    `ADVISORY_PREFIX` (precedence-framed).
  - **T-S76-006** — daemon⇒None: `compose_directive_for_user(s, "")` and `(s, None)` return None (no DB read).
  - Register in `run-all.sh` after the suite-75 line (L124); `chmod +x`.
- **Acceptance**: `bash scripts/e2e/run-all.sh` runs suite-76; compose/DB/422 cases FAIL on breakage (never
  laundered to SKIP); only the optional pod-output check may SKIP on capacity.
- **Dependencies**: registry-api deployed (CP1) for live cases; script authored earlier.
- **Verify**: `bash -n scripts/e2e/suite-76-preferences.sh && grep -q suite-76 scripts/e2e/run-all.sh` (live run post-CP1).

## Phase 6 — Image bumps + gates

### T18 — Image bumps (all THREE files)
- **Files**: `scripts/deploy-cpe2e.sh` (Modify), `scripts/deploy-eks.sh` (Modify),
  `charts/agentshield/values.yaml` (Modify).
- **Contract** (research.md R8 — all three tags verified UNUSED): set registry-api `0.2.191`,
  declarative-runner `0.1.56`, studio `0.1.144` + comment "POC-3 user preferences + advisory directive".
  Locations: cpe2e L266/L273/L275; eks L67/L69/L70; values registry-api L597, `declarativeRunnerTag` L673,
  studio L917. Bump ALL THREE files in one commit (chart uses baked tags, no `--set`).
- **Acceptance**: `grep -rnE "0\.2\.191|0\.1\.56|0\.1\.144" scripts/ charts/` shows all three in all three files;
  no stale prior tag remains for these services.
- **Dependencies**: T01–T10 (backend + runner source complete before build).
- **Verify**: `bash -n scripts/deploy-cpe2e.sh && bash -n scripts/deploy-eks.sh && grep -c "0.2.191\|0.1.56\|0.1.144" charts/agentshield/values.yaml`

### T19 — No-orphan grep gate (every new symbol has a live caller/reader)
- **Files**: none (verification only).
- **Contract**: each new symbol resolves to a live caller/reader in the same slice (DoD 3):
  - `compose_preference_directive` — read by `compose_directive_for_user` (preferences.py) + tests.
  - `compose_directive_for_user` — called by `chat.py` (T08) + `workflow_orchestrator.py` (T10).
  - `UserProfile` — read by `load_user_preferences` (preferences.py) + migration parity.
  - `user_directive` field — set in `pod_stream.py`/`chat.py`/`workflow_orchestrator.py`, read by
    `main.py`/`workflow_executor.py`.
  - `getMyPreferences` / `updateMyPreferences` — called by `PreferencesPage.tsx`.
- **Acceptance**: every grep below returns at least one live caller/reader beyond the definition.
- **Dependencies**: T03, T05, T06, T07, T08, T10, T12.
- **Verify**:
  ```bash
  grep -rn "compose_preference_directive"           services/registry-api
  grep -rn "compose_directive_for_user"             services/registry-api
  grep -rn "UserProfile"                            services/registry-api
  grep -rn "user_directive"                         services/registry-api services/declarative-runner
  grep -rn "getMyPreferences\|updateMyPreferences"  studio/src
  ```

### T20 — Verification gate (typecheck / syntax / mappers / tests)
- **Files**: none (runs commands).
- **Acceptance**: `cd studio && npm run typecheck` clean; every changed Python file `ast.parse`s;
  `configure_mappers()` clean; `pytest tests/test_preference_directive.py` green; `cd studio && npm run test` green.
- **Dependencies**: T01–T19.
- **Verify**:
  ```bash
  cd studio && npm run typecheck && npm run test
  cd ../services/registry-api && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers()" && python3 -m pytest tests/test_preference_directive.py -q
  ```

---

## Checkpoint(s)

Deploy is a user-gated EKS step (`bash scripts/deploy-eks.sh`). Do NOT deploy without the user's go —
the cluster is shared (a full deploy from a stale branch downgrades untouched services).

### [CP1a] — Image bumps applied
Prereq: T01–T10 + T18 done. `grep -rnE "0\.2\.191|0\.1\.56|0\.1\.144" scripts/ charts/` shows all three tags
in `deploy-cpe2e.sh` + `deploy-eks.sh` + `values.yaml`. **Pass when**: 3 services × 3 files present, no stale tag.

### [CP1b] — Backend + runner deployed, API + suite-76 proven
Prereq: CP1a; T01–T13, T17 done.
```bash
# Deploy (user-gated; builds + helm upgrade on EKS test-cluster)
bash scripts/deploy-eks.sh

# Smoke — migration 0065 applied?
NS=agentshield-platform
POD=$(kubectl get pods -n $NS -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n $NS $POD -c registry-api -- python3 - <<'PY'
import asyncio; from db import AsyncSessionLocal; from sqlalchemy import text
async def main():
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("SELECT to_regclass('user_profiles')"))
        print("user_profiles:", r.scalar())
asyncio.run(main())
PY

# End-to-end: PUT/GET round-trip, ownership scoping, enum-422, compose precedence, two-user, daemon⇒None
bash scripts/e2e/suite-76-preferences.sh
```
**Pass when**: `user_profiles` is non-null; suite-76 reports all non-optional cases PASS (0 FAIL); only the
optional pod-output check may SKIP on capacity.

### [CP2a] — Studio deployed, component tests + typecheck green
Prereq: CP1b green; T11, T12, T14, T16 done; studio 0.1.144 built by CP1b's deploy.
```bash
cd studio && npm run typecheck && npm run test -- PreferencesPage
```
**Pass when**: typecheck clean; Vitest `PreferencesPage` green.

### [CP2b] — Studio journey proven (Playwright)
Prereq: CP2a; T15 done.
```bash
cd .. && bash scripts/studio-e2e.sh   # runs studio/e2e, incl. preferences.spec.ts
```
**Pass when**: `preferences.spec.ts` proves set→Save→reload→persisted (soft agent-output check may SKIP on
capacity only).

### [CP3] — Definition-of-Done gate (final)
Prereq: CP2b; T19, T20 done.
```bash
# No-orphan (each must return a live caller/reader)
grep -rn "compose_preference_directive"           services/registry-api
grep -rn "compose_directive_for_user"             services/registry-api
grep -rn "UserProfile"                            services/registry-api
grep -rn "user_directive"                         services/registry-api services/declarative-runner
grep -rn "getMyPreferences\|updateMyPreferences"  studio/src
# Full suites
bash scripts/e2e/run-all.sh                        # suite-76 registered + green
cd studio && npm run typecheck && npm run test
```
**Pass when**: every new symbol has a caller; suite-76 in `run-all.sh` green; typecheck + Vitest green;
Playwright (CP2b) green; deferred items are in the gap ledger.

---

## Known gaps (ledger — deferred intentional)
- **Durable workflow members** get no `user_directive` (they dispatch via `/run`, not `/chat/stream`).
  Reactive members + single-agent chat are covered. — deferred (intentional).
- **HITL-resume continuation** (`resume_stream_chat`, `/resume/{thread}/stream`) does not re-apply the
  directive; the initial turn's SystemMessage is not guaranteed on the resumed leg. Acceptable for enum
  presets. — deferred (intentional).
- **Language UI breadth** limited to `auto/en/es/fr/de/ja`; UI i18n out of scope. — deferred (intentional).

---

## Plan-task → tasks.md map (every plan task accounted for)

| plan.md | tasks.md |
|---|---|
| T01 migration | T01 |
| T02 model | T02 |
| T03 preferences.py | T03 |
| T04 me.py endpoints | T04 |
| T05 workflow_executor | T05 |
| T06 runner main.py | T06 |
| T07 pod_stream | T07 |
| T08 chat.py | T08 |
| T09 stamp user_id | T09 |
| T10 workflow_orchestrator | T10 |
| T11 pytest | T13 |
| T12 registryApi | T11 |
| T13 PreferencesPage + App | T12 |
| T14 Playwright | T15 |
| T15 Vitest + docs | T14 (Vitest) + T16 (docs, split to keep ≤3 files) |
| T16 suite-76 + run-all | T17 |
| T17 verification gate | T20 |
| T18 image bumps | T18 |
| (folded no-orphan greps) | T19 (dedicated grep gate) |

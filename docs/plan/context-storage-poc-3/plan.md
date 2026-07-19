# POC-3 — User-Profile Response Preferences — Implementation Plan

**Branch**: `worktree-ux-preview-context-storage` (commit here ONLY; never merge to main).
**Baseline**: registry-api 0.2.190 / studio 0.1.143 / declarative-runner 0.1.55.
**Target tags**: registry-api **0.2.191** / declarative-runner **0.1.56** / studio **0.1.144** (all verified unused).
**Authoritative spec**: `docs/design/context-storage-poc-3-preferences.md`. Grounding: `research.md`.

---

## Goal

Let a user set structured, enum-only response presets once (length / tone / format / language /
expertise) and have every `user_delegated` agent honor them as a **bounded, advisory** system
directive that never overrides task/format/safety/governance. Prove: two users get differently
shaped answers from the same agent; the profile survives reload.

## Architecture

A vertical slice: **UI selectors → `PUT /me/preferences` → `user_profiles` → read back on mount**,
plus **compose-on-dispatch → `user_directive` pod field → runner appends after author
instructions**.

```
Studio PreferencesPage ──PUT/GET /api/v1/me/preferences──► routers/me.py
                                                             │  (require_user, user_id = caller.sub)
                                                             ▼
                                                    user_profiles (0065)
                                                             ▲
        preferences.py: compose_directive_for_user(db, user_id) ── load + compose_preference_directive
                 │                                   (falsy user_id ⇒ None = daemon skip)
   ┌─────────────┴───────────────────────────┐
   │ chat.py stream_chat / stream_deployment  │  workflow_orchestrator._run_step_stream
   │  → _proxy_agent_stream(user_directive)   │   (reads parent AgentRun.user_id)
   │  → stream_pod_chat_frames(user_directive)│   → _dispatch_stream(user_directive)
   └──────────────────┬───────────────────────┘   → stream_pod_chat_frames(user_directive)
                      ▼  POST /chat/stream  {..., "user_directive": "<advisory>"}
             declarative-runner main.py ChatRequest.user_directive
                      → workflow_executor run/run_streamed(user_directive)
                      → messages = [SystemMessage(user_directive)] + history + [Human]
                        (lands AFTER create_react_agent's author-instructions prompt)
```

Design invariants: enums only (no free-text injection vector); composition is registry-side and
central; the runner only appends a platform-provided string; daemon runs (`user_id` empty) get
nothing.

## Tech Stack

FastAPI + SQLAlchemy 2 async + Alembic (registry-api, Py 3.11) · LangGraph/LangChain
(declarative-runner + SDK) · React 18 + Vite + TS + React Query + Tailwind + axios (studio) ·
Vitest/RTL + Playwright (studio tests) · pytest (registry-api) · bash+curl in-pod (e2e) ·
Helm/EKS deploy.

## Constitution Check (CLAUDE.md gates)

| Gate | How this plan satisfies it |
|---|---|
| **DoD 1 — real user journey** | T14 Playwright `preferences.spec.ts`: set prefs → Save (`waitForResponse` on PUT) → reload → selectors show saved values. |
| **DoD 2 — save→reload→assert** | Same spec reloads from backend; suite-76 T-S76-001 does PUT→GET round-trip from a cold session. |
| **DoD 3 — no orphan code** | T16 greps `compose_preference_directive`, `UserProfile`, `getMyPreferences`, `user_directive`, `compose_directive_for_user` each for a live caller. Every new symbol wired in the same slice. |
| **DoD 4 — vertical slice** | One path wired end-to-end (selector→API→DB→read back; compose→pod→prompt) before extras; durable-member directive explicitly deferred, not half-built. |
| **DoD 5 — gap ledger** | T15 records deferred items (durable member directive; HITL-resume re-apply) in `docs/testing/manual-ui-e2e-test-plan.md`. |
| **DoD 6 — reason from running product** | Whole plan grounded against 0.2.190/0.1.143/0.1.55; enum drift reconciled to the deployed frontend (research.md R1). |
| **No-Bandaid** | ONE composition seam `compose_directive_for_user` (not duplicated in two callers); daemon skip is structural (empty `user_id`), not a runtime type-sniff; directive threaded as an explicit param, not a global. |
| **Image bumps (all 3 files)** | T18 bumps `deploy-cpe2e.sh` + `deploy-eks.sh` + `values.yaml` for all three services. |
| **E2E + Vitest + typecheck** | T11 pytest, T12 Vitest, T14 Playwright, T15 suite-76 registered in `run-all.sh`, T17 typecheck/verify. |
| **Experience docs** | T15 updates `docs/experience/playground.md` (chat.py now injects `user_directive`) + `docs/spec.md`. |

## File Structure

| File | New/Edit | Responsibility (one line) |
|---|---|---|
| `services/registry-api/alembic/versions/0065_user_profiles.py` | New | Idempotent `user_profiles` table + guarded enum CHECKs; drop on downgrade. |
| `services/registry-api/models.py` | Edit | Add `UserProfile` ORM model (text PK `user_id`, 5 nullable enum cols, `updated_at`). |
| `services/registry-api/preferences.py` | New | `PREFERENCE_VOCAB`, `PHRASE_MAP`, `UserPreferences(Update)` schemas, `compose_preference_directive`, `load_user_preferences`, `compose_directive_for_user`. |
| `services/registry-api/routers/me.py` | Edit | Add caller-scoped `GET`/`PUT /api/v1/me/preferences` (upsert; 422 on out-of-vocab). |
| `services/registry-api/pod_stream.py` | Edit | Add `user_directive: str | None` param → new `user_directive` body field when set. |
| `services/registry-api/routers/chat.py` | Edit | `stream_chat`/`stream_deployment_chat` compose via `compose_directive_for_user(run.user_id)` → pass through `_proxy_agent_stream` → `stream_pod_chat_frames`. |
| `services/registry-api/routers/composite_workflows.py` | Edit | Stamp `user_id=caller_sub` on the interactive parent workflow `AgentRun`. |
| `services/registry-api/routers/internal.py` | Edit | Stamp `user_id=principal.user_id` on the trigger parent workflow `AgentRun` (empty ⇒ daemon). |
| `services/registry-api/workflow_orchestrator.py` | Edit | `_run_step_stream` reads parent `user_id`, composes directive, threads it through `_dispatch_stream` → `stream_pod_chat_frames` (reactive members). |
| `services/declarative-runner/main.py` | Edit | `ChatRequest.user_directive`; `chat`/`chat_stream` pass it into `run`/`run_streamed`. |
| `services/declarative-runner/workflow_executor.py` | Edit | `run`/`run_streamed` accept `user_directive`; prepend `SystemMessage(user_directive)` to messages. |
| `services/registry-api/tests/test_preference_directive.py` | New | pytest: each enum→phrase, NULLs omitted, `auto` omitted, empty⇒None, two-user differs. |
| `studio/src/api/registryApi.ts` | Edit | `UserPreferences` type + `getMyPreferences` + `updateMyPreferences`. |
| `studio/src/pages/PreferencesPage.tsx` | New | Real Preferences page: 5 selectors + `PREFERENCE_OPTIONS` + load-on-mount + Save + toast. |
| `studio/src/App.tsx` | Edit | Swap `/preferences` import from `pages/preview/PreferencesPage` → `pages/PreferencesPage`. |
| `studio/src/pages/PreferencesPage.test.tsx` | New | Vitest: load/empty/save/enum-options/toast (registryApi mocked). |
| `studio/e2e/preferences.spec.ts` | New | Playwright: set→Save(waitForResponse)→reload→persisted. |
| `scripts/e2e/suite-76-preferences.sh` | New | PUT/GET round-trip, ownership scoping, enum-422, compose precedence, two-user differs, daemon⇒None. |
| `scripts/e2e/run-all.sh` | Edit | Register suite-76 after the suite-75 line. |
| `scripts/deploy-cpe2e.sh` | Edit | Bump registry-api 0.2.191 / declarative-runner 0.1.56 / studio 0.1.144 + comments. |
| `scripts/deploy-eks.sh` | Edit | Same three tag bumps + comments. |
| `charts/agentshield/values.yaml` | Edit | registry-api tag L597, `declarativeRunnerTag` L673, studio tag L917. |
| `docs/experience/playground.md` | Edit | Note `user_directive` injection into user_delegated chat/workflow dispatch. |
| `docs/spec.md` | Edit | Record the `user_profiles` entity + advisory-directive seam. |
| `docs/testing/manual-ui-e2e-test-plan.md` | Edit | Add deferred gaps (durable-member directive; HITL-resume re-apply). |

Every file above appears in a task below; every task's files appear here.

## Key Interfaces (exact signatures — consistent across all tasks)

```python
# services/registry-api/preferences.py
PREFERENCE_VOCAB: dict[str, tuple[str, ...]]          # contracts/enums.md
PHRASE_MAP: dict[str, dict[str, str]]                 # contracts/composition-contract.md
ADVISORY_PREFIX: str
class UserPreferencesUpdate(BaseModel): ...           # 5 Optional[Literal[...]] fields
class UserPreferences(UserPreferencesUpdate):         # + updated_at, from_attributes
    updated_at: Optional[datetime] = None
def compose_preference_directive(prefs: UserPreferences) -> str | None: ...
async def load_user_preferences(db: AsyncSession, user_id: str) -> UserPreferences: ...
async def compose_directive_for_user(db: AsyncSession, user_id: str | None) -> str | None: ...

# services/registry-api/pod_stream.py
async def stream_pod_chat_frames(service_url, *, message, thread_id, conversation_id,
    scope, author, trace_id=None, user_id="", user_team="", deployment_id="",
    auto_approve=False, user_directive: str | None = None) -> AsyncGenerator[dict, None]: ...

# services/registry-api/routers/chat.py
async def _proxy_agent_stream(service_url, message, run_id, conversation_id, trace_id=None,
    user_id="", user_team="", deployment_id="", author="", user_directive: str | None = None
    ) -> AsyncGenerator[str, None]: ...

# services/registry-api/workflow_orchestrator.py
async def _dispatch_stream(agent_name, team, message, thread_id, conversation_id, scope,
    child_id, user_directive: str | None = None) -> AsyncGenerator[dict, None]: ...

# services/declarative-runner/main.py
class ChatRequest(BaseModel):
    ...; user_directive: str | None = None

# services/declarative-runner/workflow_executor.py
async def run(self, message, thread_id=None, trace_id=None, memory_context=None,
    user_directive: str | None = None) -> dict: ...
async def run_streamed(self, message, thread_id=None, trace_id=None, memory_context=None,
    user_directive: str | None = None) -> AsyncIterator[str]: ...
```

```ts
// studio/src/api/registryApi.ts
export interface UserPreferences { response_length: string|null; tone: string|null;
  format: string|null; language: string|null; expertise: string|null; updated_at?: string|null; }
export const getMyPreferences: () => Promise<UserPreferences>;
export const updateMyPreferences: (p: Omit<UserPreferences,"updated_at">) => Promise<UserPreferences>;
```

---

## Tasks

Dependency-ordered. `[P]` = parallelizable (disjoint files, deps already met). Verify commands
assume repo root `…/ux-preview-context-storage`.

### T01 [P] — Migration 0065 `user_profiles`
- **Files**: `services/registry-api/alembic/versions/0065_user_profiles.py` (new).
- **Interface contract**: `revision="0065"`, `down_revision="0064"`. `upgrade()` creates the table
  (`data-model.md`) + 5 guarded CHECKs (`contracts/enums.md`); `downgrade()` `DROP TABLE IF EXISTS`.
- **Acceptance**: `alembic upgrade head` then `alembic downgrade -1` then `upgrade head` round-trips
  cleanly; re-running `upgrade` is a no-op (idempotent).
- **Dependencies**: none.
- **Test cases**: apply on a DB already at 0064 → table + 5 constraints exist; insert an
  out-of-vocab `tone` → CHECK violation; `INSERT … ON CONFLICT` upsert works.
- **Verify**: `python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0065_user_profiles.py').read())"`

### T02 [P] — `UserProfile` ORM model
- **Files**: `services/registry-api/models.py` (edit).
- **Interface contract**: `class UserProfile(Base)` exactly as `data-model.md` (text PK `user_id`,
  5 nullable `Text` enum cols, `updated_at` `_TSTZ` `server_default=_NOW`).
- **Acceptance**: mappers configure; `__tablename__ == "user_profiles"` and columns match 0065.
- **Dependencies**: none (parallel with T01; they must agree on names — both cite the contracts).
- **Test cases**: import + `sqlalchemy.orm.configure_mappers()` raises nothing; `UserProfile.user_id`
  is the PK.
- **Verify**: `cd services/registry-api && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print(models.UserProfile.__tablename__)"`

### T03 — `preferences.py` (schemas + vocab + composition seam)
- **Files**: `services/registry-api/preferences.py` (new).
- **Interface contract**: exactly the Key-Interfaces block + `contracts/enums.md` +
  `contracts/composition-contract.md`. `compose_directive_for_user(db, "")` and `(db, None)` return
  `None` with NO DB read.
- **Acceptance**: `compose_preference_directive` returns the verbatim worked examples; None on empty
  / `auto`-only; `load_user_preferences` returns all-None for a missing row.
- **Dependencies**: T02 (imports `UserProfile` for `load_user_preferences`).
- **Test cases**: covered by T11.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('preferences.py').read())" && python3 -c "from preferences import compose_preference_directive, UserPreferences as U; print(compose_preference_directive(U(response_length='concise', format='bulleted', expertise='expert')))"`

### T04 — `GET`/`PUT /api/v1/me/preferences`
- **Files**: `services/registry-api/routers/me.py` (edit).
- **Interface contract**: `contracts/endpoints.md`. Both `Depends(require_user)`; `user_id =
  claims["sub"]`. GET → `UserPreferences` (all-null default if no row). PUT → upsert
  (`INSERT … ON CONFLICT (user_id) DO UPDATE SET … updated_at=now()`), body `UserPreferencesUpdate`
  (422 on out-of-vocab), returns persisted `UserPreferences`.
- **Acceptance**: PUT then GET returns the saved values + `updated_at`; a second GET with no prior
  row returns all-null; bad enum → 422.
- **Dependencies**: T02, T03.
- **Test cases**: suite-76 T-S76-001/002/003; unit-level shape asserted in T11 is composition-only
  (endpoint proven in suite-76).
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('routers/me.py').read())" && python3 -c "import routers.me"`

### T05 [P] — Runner: `workflow_executor` applies `user_directive`
- **Files**: `services/declarative-runner/workflow_executor.py` (edit).
- **Interface contract**: `run` and `run_streamed` gain `user_directive: str | None = None`. In
  both, before building `state`, `lead = [SystemMessage(content=user_directive)] if user_directive
  else []` and `state = {"messages": lead + history + [HumanMessage(content=safe_message)]}`. Import
  `SystemMessage` from `langchain_core.messages`. No other behavior change; `None` ⇒ byte-identical
  to today.
- **Acceptance**: with `user_directive` set, a leading SystemMessage carrying that exact string is
  in the invoked state; with `None`, the message list equals the pre-change list.
- **Dependencies**: none (parallel — pure runner-side; the field contract is fixed in
  `contracts/endpoints.md`).
- **Test cases**: exercised live in suite-76 T-S76-005 (best-effort output-shape) + Playwright soft
  assertion; primary guard is that `None` changes nothing.
- **Verify**: `python3 -c "import ast; ast.parse(open('services/declarative-runner/workflow_executor.py').read())"`

### T06 — Runner: `ChatRequest.user_directive` + handler pass-through
- **Files**: `services/declarative-runner/main.py` (edit).
- **Interface contract**: add `user_directive: str | None = None` to `ChatRequest`; `chat` (sync)
  and `chat_stream` pass `user_directive=req.user_directive` into `run(...)` / `run_streamed(...)`.
- **Acceptance**: a POST `/chat/stream` with `user_directive` reaches `run_streamed`; without it,
  `run_streamed(user_directive=None)`.
- **Dependencies**: T05.
- **Test cases**: suite-76 T-S76-005 (directive body accepted, 200 stream).
- **Verify**: `python3 -c "import ast; ast.parse(open('services/declarative-runner/main.py').read())"`

### T07 — Registry: `pod_stream` carries `user_directive` in the body
- **Files**: `services/registry-api/pod_stream.py` (edit).
- **Interface contract**: add `user_directive: str | None = None`; when truthy, set
  `body["user_directive"] = user_directive`. Nothing else changes.
- **Acceptance**: body includes `user_directive` iff passed; omitted when `None`.
- **Dependencies**: none structurally, but the field name must match T06 (`contracts/endpoints.md`).
  Order after T06 so the runner accepts the field before registry sends it (deploy ordering).
- **Test cases**: covered by T08/chat wiring proven in suite-76.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('pod_stream.py').read())"`

### T08 — Registry: chat.py composes + injects the directive
- **Files**: `services/registry-api/routers/chat.py` (edit).
- **Interface contract**: `_proxy_agent_stream` gains `user_directive: str | None = None` and passes
  it to `stream_pod_chat_frames`. In `stream_chat` and `stream_deployment_chat`, before the stream
  generator, compute `user_directive = await compose_directive_for_user(db, run.user_id)` (import
  from `preferences`) and pass it into `_proxy_agent_stream(...)`. (`run.user_id` is always the
  interactive caller.)
- **Acceptance**: a chat by a user with prefs sends `user_directive` to the pod; a user with no
  prefs sends none; ownership/trace behavior unchanged.
- **Dependencies**: T03, T07.
- **Test cases**: suite-76 T-S76-005 (dispatch payload carries the right directive per user).
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('routers/chat.py').read())" && python3 -c "import routers.chat"`

### T09 — Registry: stamp `user_id` on parent workflow runs
- **Files**: `services/registry-api/routers/composite_workflows.py` (edit),
  `services/registry-api/routers/internal.py` (edit).
- **Interface contract**: in `composite_workflows.run_workflow_stream`'s parent `AgentRun(...)` add
  `user_id=caller_sub`. In `internal.start_internal_run`'s workflow parent `AgentRun(...)` (or `run =
  AgentRun(...)`) add `user_id=principal.user_id` (empty string ⇒ daemon). No other change.
- **Acceptance**: an interactive workflow run's parent `AgentRun.user_id == caller_sub`; a daemon
  trigger workflow run's parent `user_id == ""`.
- **Dependencies**: none (independent of T08); MUST land before T10 (the discriminator source).
- **Test cases**: suite-76 T-S76-006 asserts a daemon-shaped parent (`user_id=""`) yields no
  directive via the seam.
- **Verify**: `cd services/registry-api && python3 -c "import ast,importlib; [ast.parse(open(f).read()) for f in ('routers/composite_workflows.py','routers/internal.py')]"`

### T10 — Registry: workflow member injects the directive (reactive)
- **Files**: `services/registry-api/workflow_orchestrator.py` (edit).
- **Interface contract**: in `_run_step_stream`, extend the parent query to also select
  `AgentRun.user_id`; compute `user_directive = await compose_directive_for_user(s, parent_user_id)`
  (import from `preferences`; reuse a session). `_dispatch_stream` gains `user_directive: str | None
  = None` and forwards it to `stream_pod_chat_frames`. `_run_step_stream` passes `user_directive`
  into `_dispatch_stream`. **Durable members unchanged** (documented gap).
- **Acceptance**: a reactive member of a user_delegated workflow receives the composer's directive;
  a daemon workflow (`parent_user_id==""`) sends none.
- **Dependencies**: T03, T07, T09.
- **Test cases**: suite-76 T-S76-006 (daemon⇒None) + T-S76-005 (user⇒directive).
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('workflow_orchestrator.py').read())" && python3 -c "import workflow_orchestrator"`

### T11 [P] — pytest: `compose_preference_directive`
- **Files**: `services/registry-api/tests/test_preference_directive.py` (new).
- **Interface contract**: assert each enum→phrase (verbatim from `PHRASE_MAP`); NULLs omitted;
  `language='auto'` omitted; all-None ⇒ None; the two worked examples verbatim; the two-user
  divergence (A≠B, both non-None). Pure — no DB (call `compose_preference_directive` directly).
- **Acceptance**: `pytest tests/test_preference_directive.py` green.
- **Dependencies**: T03.
- **Verify**: `cd services/registry-api && python3 -m pytest tests/test_preference_directive.py -q`

### T12 [P] — Frontend API client methods
- **Files**: `studio/src/api/registryApi.ts` (edit).
- **Interface contract**: `contracts/endpoints.md` (`UserPreferences`, `getMyPreferences`,
  `updateMyPreferences`).
- **Acceptance**: typecheck clean; both call the shared `http` client (Bearer auto-attached).
- **Dependencies**: none.
- **Verify**: `cd studio && npm run typecheck`

### T13 — Real PreferencesPage + route swap
- **Files**: `studio/src/pages/PreferencesPage.tsx` (new), `studio/src/App.tsx` (edit).
- **Interface contract**: real page with local `PREFERENCE_OPTIONS` (`contracts/enums.md` codes +
  labels; `response_length` key, not `length`), 5 selectors + Language select, load-on-mount via
  `getMyPreferences`, Save via `updateMyPreferences` (optimistic + success toast + error toast),
  reflects saved state. Keep the directive preview but derive it from the same option set (display
  aid only — it must not send free text). `App.tsx`: change the `/preferences` import to
  `./pages/PreferencesPage`.
- **Acceptance**: page loads current prefs; changing a selector + Save issues a PUT; a toast fires;
  reload shows saved values; no console errors.
- **Dependencies**: T12.
- **Verify**: `cd studio && npm run typecheck`

### T14 — Playwright: preferences persistence journey
- **Files**: `studio/e2e/preferences.spec.ts` (new).
- **Interface contract**: real Keycloak login (global-setup); navigate `/preferences`; select
  `concise` + `bulleted`; click Save with `page.waitForResponse(**/me/preferences PUT 200)`;
  `page.reload()`; assert both selectors show the saved values (persistence round-trip). Optional
  best-effort: run the agent and observe reply shape (soft — warm-pods boundary).
- **Acceptance**: `bash scripts/studio-e2e.sh` runs the spec green (or SKIPs only on the accepted
  pod-availability boundary for the soft output check).
- **Dependencies**: T13 deployed (CP2).
- **Verify**: `bash scripts/studio-e2e.sh` (post-CP2).

### T15 — Vitest + docs
- **Files**: `studio/src/pages/PreferencesPage.test.tsx` (new); `docs/experience/playground.md`
  (edit); `docs/spec.md` (edit); `docs/testing/manual-ui-e2e-test-plan.md` (edit). (≤3 code files;
  docs grouped.)
- **Interface contract**: Vitest with `registryApi` mocked (`getMyPreferences`/`updateMyPreferences`)
  via `renderWithProviders`: load state renders saved selection; empty (all-null) renders defaults
  unselected; clicking a selector + Save calls `updateMyPreferences` with the right payload and
  shows a toast; all enum options render. Docs: note `user_directive` injection into user_delegated
  chat/workflow dispatch (playground.md), add the `user_profiles` entity + advisory-directive seam
  (spec.md), and gap-ledger the two deferred items.
- **Acceptance**: `cd studio && npm run test` green; docs updated.
- **Dependencies**: T13.
- **Verify**: `cd studio && npm run test -- PreferencesPage`

### T16 — suite-76 + register
- **Files**: `scripts/e2e/suite-76-preferences.sh` (new), `scripts/e2e/run-all.sh` (edit).
- **Interface contract** (copy the suite-75 harness: in-pod `kubectl exec … python3`,
  `AsyncSessionLocal`, `get_token()` password-grant for `platform-admin`):
  - **T-S76-001** — PUT `{response_length:concise, format:bulleted, expertise:expert}` → GET returns
    them (+`updated_at`).
  - **T-S76-002** — reload round-trip: a fresh GET (new client) still returns the saved values.
  - **T-S76-003** — ownership scoping: insert a row for a DIFFERENT `user_id` directly in DB; GET
    `/me/preferences` as admin returns admin's row, NOT the other user's values (caller-scoped query).
  - **T-S76-004** — enum-422: PUT `{tone:"loud"}` → 422.
  - **T-S76-005** — compose precedence + two-user differs: upsert two `user_profiles` rows (A:
    bulleted/concise, B: prose/detailed) directly in DB; call `compose_directive_for_user` for each →
    both non-None, differ, and each starts with `ADVISORY_PREFIX`.
  - **T-S76-006** — daemon⇒None: `compose_directive_for_user(s, "")` and `(s, None)` return None.
  - **No-orphan greps** (in bash, out of pod): assert live callers exist for
    `compose_preference_directive`, `UserProfile`, `getMyPreferences`, `user_directive`,
    `compose_directive_for_user`.
  - Register in `run-all.sh` after the suite-75 line; `chmod +x`.
- **Acceptance**: `bash scripts/e2e/run-all.sh` runs suite-76; the compose/DB/422 cases FAIL on
  breakage (never laundered to SKIP); only the optional pod-output check may SKIP on capacity.
- **Dependencies**: registry-api deployed (CP1).
- **Verify**: `bash scripts/e2e/suite-76-preferences.sh` (post-CP1).

### T17 — Verification gate (typecheck / syntax / mappers)
- **Files**: none (runs commands).
- **Acceptance**: `cd studio && npm run typecheck` clean; every changed Python file `ast.parse`s;
  `configure_mappers()` clean; `pytest tests/test_preference_directive.py` green;
  `cd studio && npm run test` green.
- **Dependencies**: T01–T16.
- **Verify**: the commands above.

---

## Checkpoints (executable — deploy is a user-gated EKS step)

### CP1 — Backend + runner deployed, API + suite-76 proven
Prereqs: T01–T11, T16 done; image bump task **T18** applied.

**T18 — image bumps (all three files)** — Files: `scripts/deploy-cpe2e.sh`,
`scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`. Set registry-api `0.2.191`,
declarative-runner `0.1.56`, studio `0.1.144` (comments: "POC-3 user preferences + advisory
directive"). Locations: cpe2e L266/L273/L275; eks L67/L69/L70; values registry-api L597,
`declarativeRunnerTag` L673, studio L917.

```bash
# Deploy (user-gated; builds + helm upgrade on EKS test-cluster)
bash scripts/deploy-eks.sh

# Smoke — migration + endpoints, from inside the registry-api pod
NS=agentshield-platform
POD=$(kubectl get pods -n $NS -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
# 0065 applied? (user_profiles table should resolve)
kubectl exec -n $NS $POD -c registry-api -- python3 - <<'PY'
import asyncio; from db import AsyncSessionLocal; from sqlalchemy import text
async def main():
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("SELECT to_regclass('user_profiles')"))
        print("user_profiles:", r.scalar())
asyncio.run(main())
PY
# End-to-end API + compose + ownership + 422 + two-user + daemon
bash scripts/e2e/suite-76-preferences.sh
```
**Pass when**: `user_profiles` is non-null; suite-76 reports all non-optional cases PASS (0 FAIL);
no-orphan greps pass.

### CP2 — Studio deployed, journey + component tests green
Prereqs: CP1 green; T12–T15 done; studio 0.1.144 built by CP1 deploy.

```bash
# (studio image already built+deployed in CP1's deploy-eks.sh run)
cd studio && npm run typecheck && npm run test -- PreferencesPage
cd .. && bash scripts/studio-e2e.sh   # runs studio/e2e, incl. preferences.spec.ts
```
**Pass when**: typecheck clean; Vitest PreferencesPage green; Playwright `preferences.spec.ts`
proves set→Save→reload→persisted (soft agent-output check may SKIP on capacity only).

### CP3 — Definition-of-Done gate (final)
```bash
# No-orphan (each must return a live caller/reader)
grep -rn "compose_preference_directive"   services/registry-api
grep -rn "compose_directive_for_user"      services/registry-api
grep -rn "class UserProfile\|UserProfile"  services/registry-api
grep -rn "user_directive"                  services/registry-api services/declarative-runner
grep -rn "getMyPreferences\|updateMyPreferences" studio/src
# Full suites
bash scripts/e2e/run-all.sh                # suite-76 registered + green
cd studio && npm run typecheck && npm run test
```
**Pass when**: every new symbol has a caller; suite-76 in `run-all.sh` green; typecheck + Vitest
green; Playwright (CP2) green; deferred items are in the gap ledger.

## Known gaps (ledger — deferred intentional)
- **Durable workflow members** get no `user_directive` (they dispatch via `/run`, not
  `/chat/stream`). Reactive members + single-agent chat are covered. — deferred (intentional).
- **HITL-resume continuation** (`resume_stream_chat`, `/resume/{thread}/stream`) does not re-apply
  the directive; the initial turn's SystemMessage is not guaranteed on the resumed leg. Acceptable
  for enum presets. — deferred (intentional).
- **Language UI breadth** limited to `auto/en/es/fr/de/ja`; UI i18n out of scope. — deferred.

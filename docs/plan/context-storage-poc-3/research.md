# POC-3 — Research / Grounding Notes

Grounded against the deployed baseline on branch `worktree-ux-preview-context-storage`
(registry-api 0.2.190 / studio 0.1.143 / declarative-runner 0.1.55). Every line below was
verified in the current code, not read from the design doc. Where the design doc and the
running code disagree, the running code wins and the divergence is flagged.

---

## R0 — Alembic head is 0064 (confirmed)

`services/registry-api/alembic/versions/` tops out at `0064_agent_memory_shared_thread.py`
(`revision = "0064"`, `down_revision = "0063"`). The POC-3 migration is therefore **0065**
(`down_revision = "0064"`). The 0064 file is the idempotent-DDL template to copy: raw
`op.execute("... IF NOT EXISTS ...")`, guarded `DO $$ ... pg_constraint ...$$` for
constraints, symmetric `downgrade()` with `DROP ... IF EXISTS`.

## R1 — Enum vocabulary drift (THREE sources disagree — reconciled in `contracts/enums.md`)

| Dimension | POC-3 §3.1 SQL comment | Architecture §8 table | Running preview (`demo/mockData.ts`) | **Canonical (this plan)** |
|---|---|---|---|---|
| length | concise / balanced / detailed | (same) | concise / balanced / detailed | **concise / balanced / detailed** |
| tone | neutral / friendly / formal | professional / neutral / casual | professional / neutral / casual | **professional / neutral / casual** |
| format | prose / bullet_points / structured | prose / bulleted / structured | prose / bulleted / structured | **prose / bulleted / structured** |
| language | auto / en / es / fr / de … | locale | English / Spanish / French / German / Japanese | **auto / en / es / fr / de / ja** |
| expertise | beginner / intermediate / expert | (same) | beginner / intermediate / expert | **beginner / intermediate / expert** |

Decision: the POC-3 §3.1 SQL comment (`neutral/friendly/formal`, `bullet_points`) is a stale
draft; both the **deployed frontend** and **architecture §8** agree on
`professional/neutral/casual` + `bulleted`, so the canonical set matches the running product
(constitution: "reason from the running product"). Language is stored as bounded **codes** with
`auto` (the design's explicit intent: `auto` = don't force a language, and it keeps eval output
stable); the frontend maps codes → friendly labels. The DB column is **`response_length`** (not
`length`) per the authoritative doc; the frontend UI key is renamed `length` → `response_length`
when the preview page is wired to real data.

## R2 — There is already a caller-scoped `/api/v1/me` router — reuse it

`routers/me.py` mounts `APIRouter(prefix="/api/v1/me")` and `main.py:85` registers it
(`from routers.me import router as me_router`). `GET /api/v1/me` (`get_me`) is the exact
router pattern for the new endpoints: `claims: dict = Depends(require_user)`, `sub =
claims.get("sub")`, caller-scoped SQL keyed on `sub`. **The new `GET`/`PUT
/api/v1/me/preferences` are added to this existing router** — no new router, no new
registration (No-Bandaid: reuse the one caller-scoped surface that already exists).

## R3 — How `user_delegated` vs `daemon` is determined (identity.py)

`identity.py::resolve_principal(agent, caller, trigger, db)` returns a `Principal` whose
`user_id` is **the live human's sub, and EMPTY STRING for a daemon** (docstring lines 53-55,
166). The class is decided by an explicit `caller` param + `agent.agent_class`, **never by
sniffing** (docstring lines 10-17). So the correct discriminator for "apply preferences" is a
**non-empty `user_id`**: a real user has one, a daemon has `""`. This plan threads that as
`compose_directive_for_user(db, user_id)` returning `None` when `user_id` is falsy — the
structural daemon-skip.

## R4 — Chat dispatch seam (registry → pod). The composed directive rides in the pod body

The consumer-chat pod body is built in **`pod_stream.py::stream_pod_chat_frames`** (the ONE
shared reader). Body today: `{"message", "thread_id", "conversation_id", "scope"}` (L73-78);
identity rides in **headers** (`x-user-sub`, `x-agent-team`, `x-deployment-id`). The directive
is a NEW bounded body field **`user_directive`** added here.

Call chain (single-agent chat):
`routers/chat.py::stream_chat` / `stream_deployment_chat`
→ `_proxy_agent_stream(...)` (L374, already carries `user_id`, `author`, etc.)
→ `stream_pod_chat_frames(...)` (L410) → POST `{service_url}/chat/stream`.

**Composition happens at STREAM time** (the GET handler), where `run.user_id` is known and the
pod is actually hit — not at POST. `run.user_id` on a chat run is always the caller (interactive
= `user_delegated`), so chat always composes when a profile exists.

**No existing per-run system-prompt addendum exists to reuse** — the pod body has no such field
today; `user_directive` is net-new. (Identity/trace/deployment ride in headers; none is a
system-prompt string.)

## R5 — Workflow member dispatch seam (POC-2b just landed)

`workflow_orchestrator.py::_run_step_stream` (L492) is the ONE member leaf (the non-streaming
`_run_step` is a drain of it). A **reactive** member streams via
`_dispatch_stream` (L105) → `stream_pod_chat_frames` (L129). A **durable** member goes via
`_dispatch_durable_member` (L178) → `/run` (NOT `/chat/stream`).

- `_run_step_stream` already reads the parent run's `run_by`, `context` (L514-516). We ADD
  `user_id` to that query and compose the directive from it.
- Thread the directive `_run_step_stream` → `_dispatch_stream` (new param) → `stream_pod_chat_frames`
  (`user_directive=` body field). **Reactive members only.**
- **Durable members get no directive** (they use `/run`, a different handler) — recorded as a
  deferred gap. Acceptable: the primary interactive workflow path is reactive.

### R5a — Parent workflow run does NOT stamp `user_id` today → we add it

`composite_workflows.py::run_workflow_stream` (L539) creates the parent `AgentRun` with
`run_by=caller_sub` but **no `user_id`**. `internal.py::start_internal_run` (L258) creates it
with `run_by=principal.run_by` (service subject for daemon) and no `user_id`. So the member leaf
cannot read a reliable user discriminator today. Fix (structural, matches R3): stamp
`user_id = caller_sub` (interactive) and `user_id = principal.user_id` (trigger — empty for
daemon) on the parent run. `AgentRun.user_id` already exists (`models.py:1561`,
`Mapped[str | None]`, nullable). `_run_step_stream` then reads parent `user_id`; empty → daemon
→ `None` directive.

## R6 — Prompt-assembly point in the runner (where the directive is applied)

`build_graph` (`sdk/agentshield_sdk/graph_builder.py:442`) bakes the author instructions +
tool-reasoning nudge into a `create_react_agent` prompt at **build time** (L480-486) — it is
per-agent and cached, so a per-USER directive cannot go here. The per-request seam is the
**message list** assembled at invoke time:

- `services/declarative-runner/workflow_executor.py::run_streamed` (L800) and `run` (L704) build
  `state = {"messages": history + [HumanMessage(safe_message)]}` (L860 / L756), where `history`
  is the loaded transcript.
- Inject a leading **`SystemMessage(content=user_directive)`** into that list:
  `messages = ([SystemMessage(user_directive)] if user_directive else []) + history + [Human]`.
  `create_react_agent` prepends the base author-instructions system prompt at runtime, so our
  SystemMessage lands **after** the author instructions — position reinforces the advisory
  precedence exactly as the design requires.
- Plumb it in: `main.py::ChatRequest` gets `user_directive: str | None` (L186); `chat` (L526) and
  `chat_stream` (L586) pass `user_directive=req.user_directive` into `run` / `run_streamed`.
- Absent/empty field → messages exactly as today (daemon / no-prefs = zero behavior change).
- The runner **never** reads `user_profiles` or composes from raw input — it appends a
  platform-provided string only.

## R7 — Model + migration conventions (`models.py`)

Shared column helpers (`models.py:40-43`): `_UUID`, `_NOW = text("now()")`,
`_GEN_UUID`, `_TSTZ = TIMESTAMP(timezone=True)`. A `user_id`-keyed, text-PK table has no
precedent (all existing user tables use a UUID id + a `*_user_id` FK column), so `user_profiles`
uses `user_id` as a **`Text` primary key** directly (matches the design + JWT sub). `updated_at`
uses `_TSTZ, server_default=_NOW`. Enum columns are nullable `Text`/`String(16)` with a guarded
`CHECK` in the migration (app-layer Pydantic validation is the primary gate; CHECK is defense in
depth, mirroring `ck_agent_memory_scope`).

## R8 — Image tags 0.2.191 / 0.1.56 / 0.1.144 are all UNUSED

`grep -rnE "0\.2\.191|0\.1\.56|0\.1\.144" scripts/ charts/` → empty. Current tags live at:
`deploy-cpe2e.sh` L266/L273/L275; `deploy-eks.sh` L67/L69/L70; `values.yaml` registry-api L597,
`declarativeRunnerTag` L673, studio L917. All three files bump in one commit.

## R9 — Frontend wiring facts

- API client: `studio/src/api/registryApi.ts` uses a shared axios `http` (baseURL `/api/v1`, Bearer
  interceptor). `getMe` (L1197) + `MeResponse` (L1189) are the pattern for the two new methods.
- Route `/preferences` exists (`App.tsx:68`) importing `PreferencesPage` from
  `pages/preview/PreferencesPage.tsx`; sidebar link exists (`Sidebar.tsx:327`). Wiring = create a
  real `pages/PreferencesPage.tsx` and swap the App.tsx import (no route/nav change).
- The preview page (`pages/preview/PreferencesPage.tsx`) already renders 5 selectors + a directive
  preview + a Save button, but state is local and options come from `demo/mockData.ts`. The real
  page reuses its layout, sources options from a local `PREFERENCE_OPTIONS` const matching
  `contracts/enums.md`, loads on mount via `getMyPreferences`, and Saves via `updateMyPreferences`.

## R10 — Test scaffolding that already exists

- registry-api pytest: `services/registry-api/tests/test_*.py` (e.g. `test_side_effect_scorer.py`).
  `compose_preference_directive` unit test lands here as `tests/test_preference_directive.py`.
- Studio Vitest: colocated `*.test.tsx`, `renderWithProviders` from `src/test/utils.tsx`, APIs
  mocked via `vi.mock('../api/registryApi')`.
- Studio Playwright: `studio/e2e/*.spec.ts`, real Keycloak login via `e2e/global-setup.ts`.
- Bash e2e: `scripts/e2e/suite-75-context-storage.sh` is the harness template (in-pod
  `kubectl exec … python3` with `AsyncSessionLocal` + a real Keycloak password-grant token via
  `get_token()` for `platform-admin`). suite-76 is the next free number; register in
  `scripts/e2e/run-all.sh` after the suite-75 line (L124).

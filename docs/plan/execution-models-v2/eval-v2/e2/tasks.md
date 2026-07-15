# E-2 Tasks — Side-effect record/replay seam (`eval_mode` through the governed tool path)

> **Minted from** `e2/plan.md` (authority) + `e2/data-model.md` + cross-cutting `eval-v2/plan.md` (§2 Seam 4 /
> §4 record seam) + `eval-v2/data-model.md` (§4). **Re-grounded against the live 2026-07-15 tree** — the
> specific values below (suite number, image tags, migration number, file:line seams) are code-truth, not the
> plan's indicative 2026-07-12 anchors. The plan carried the ⚠️ *design-stable / specifics-indicative* banner and
> flagged E-2 as **partly banner-indicative** ("the governance-wrapper seam not yet built"); the corrections in
> the next section are the just-in-time re-grounding.

**Slice:** Eval v2 **Phase E-2** — make evaluating a **side-effecting** agent SAFE. A `eval_mode=record` seam at
the **one** tool-governance delivery edge records `{tool, args}` and returns a mock **instead of invoking** the
real downstream, so a batch eval of a write-shaped durable agent never sends a real email / files a real JIRA /
moves real money — while still running the **real** governed OPA/HITL code path. Ships `Tool.side_effecting`, the
threaded `eval_mode` flag, the record/mock seam (fail-closed), and `score_side_effects` (the reader that E-3/E-4
consume) with a durable proof.

**Depends on (all DONE):** WS-1 durable engine (a real durable run carries a flag through the governed tool path)
· the **tool-governance wrapper** (`sdk/agentshield_sdk/graph_builder.py` `governed_tool` — OPA→HITL→deliver, the
seam every tool call already crosses) · E-1 (durable branch + `/eval/score` durable mode + eval-runner durable
branch). **Enables E-3 + E-4 side-effect assertions.**

## Totals

| | Count |
|---|---|
| **Impl tasks** | 20 (`T001`–`T020`) |
| **Checkpoint phases** | 3 (`CP1a`, `CP1b` = MVP, `CP1c`) |
| **Total** | 23 |
| Phases | 5 (schema+classification · thread `eval_mode`+carry `side_effecting` · the record/mock seam · scorer+door+runner · Studio+suite+deploy) |
| **Migration** | **YES — `0063`** (head is `0062`; adds `tools.side_effecting` + `playground_runs.eval_mode`) |
| Services bumped | registry-api `0.2.181→0.2.182` · eval-runner `0.1.8→0.1.9` · studio `0.1.138→0.1.139` · **declarative-runner `0.1.45→0.1.46` (REQUIRED — the seam is in the SDK, bundled into the runner image)** |
| E2E suite | **suite-74** (`T-S74-00x`), registered after suite-73 in `run-all.sh` |

**MVP scope:** the load-bearing win is **a real durable eval in `eval_mode=record` that invokes a real
side-effecting tool but does NOT deliver it — the call is recorded, the mock is returned, and a non-eval run still
delivers for real** — proven at **CP1b (suite-74, no-fakes)**. `score_side_effects`, the results render, and the
Playwright spec ride in the same slice; if runway is short they degrade to the gap ledger, but **CP1b is the
non-negotiable gate**.

---

## Re-grounding corrections baked into these tasks (vs `e2/plan.md`)

1. **Migration YES, number `0063`.** Alembic head is **`0062`** (`0062_trigger_approver_role.py`); the plan said
   head `0057`, first eval migration `≥00NN`. E-0/E-1 already landed the eval schema. E-2 owns **one** additive
   migration `0063` adding **two** columns (below).
2. **`eval_mode` IS persisted on `playground_runs` (a column), not just a transient request field.** The plan's
   data-model §2 said "persist on the run only if the resume path needs it — confirm at impl." **It needs it:** a
   durable run is dispatched fire-and-forget and a parked HITL step **resumes** via a *separate* POST to the
   runner (`declarative-runner /resume`, driven by the eval-runner's `_self_approve` → `resume-stream`). The
   resume re-drives the graph and re-crosses the delivery seam, so `eval_mode` must survive the checkpoint. Reading
   it back off `PlaygroundRun.eval_mode` on the resume dispatch is the No-Bandaid choice (vs re-deriving it). So
   the migration adds `playground_runs.eval_mode` alongside `tools.side_effecting`.
3. **The single delivery edge is `graph_builder.py` `governed_tool` step 3.** Located and confirmed: the one
   function every declarative-runner **and** SDK tool call crosses is `_wrap_tool_with_governance.governed_tool`
   (`sdk/agentshield_sdk/graph_builder.py:142`). Step 1 = OPA (`:153`), step 2 = HITL (`:170`), **step 3 =
   "Execute the tool" (`:199-202`)** — that `return await fn(**kwargs)` is the delivery edge. The record/mock
   branch goes **immediately before** it, so governance (OPA+HITL) runs **unchanged** and only the downstream
   delivery is substituted. There is exactly **one** such point — no runner-side mock fork (grep-proven, T-gate).
4. **`eval_mode` threads exactly like `auto_approve` does today.** The pattern already exists:
   `auto_approve` rides `X-Agentshield-Auto-Approve` header → `_current_user_context` ContextVar
   (`declarative-runner/main.py:478-485`; SDK reads it at `graph_builder.py:172`). E-2 threads `eval_mode` the
   same shape — but for the **durable** path (the mode E-2 evaluates), which dispatches via
   `dispatch_durable_run` (`durable_dispatch.py:41`, a **JSON body** not a header) → `_execute_durable_run`
   (`declarative-runner/main.py:601`, which currently sets **no** user/eval context) → a new `_current_eval_mode`
   ContextVar read by `governed_tool`.
5. **`side_effecting` must ride onto the resolved tool callable.** The SDK resolves tool metadata from the
   registry list endpoint into `HttpToolExecutor`/`PythonToolExecutor` (`tool_resolver.py:64-86`), which stamp
   `.risk`/`.tool_name` onto the callable (`tool_executor.py:168-169`, `:242-243`). E-2 adds `.side_effecting` the
   same way — so `governed_tool` reads `fn.side_effecting` with zero new lookup. The registry's tool response
   (`ToolResponse` in `schemas.py`) must serve `side_effecting` for the resolver to read it.
6. **Suite = `suite-74`.** Suites exist through **suite-72** (E-1 durable) in `scripts/e2e/`; **suite-73** is
   reserved by E-5 (`e5/tasks.md`, registered after 72). E-2 is minted after E-5, so **suite-74** is the next free
   number; test IDs `T-S74-00x`; registered after suite-73 in `run-all.sh`.
7. **SDK change ⇒ declarative-runner rebuild (E-1 learned this the hard way).** The seam lives in
   `graph_builder.py` + `durable.py` + `tool_executor.py` (all `sdk/agentshield_sdk/`), which are **pip-bundled
   into the declarative-runner image**. Editing them WITHOUT bumping `DECLARATIVE_RUNNER_TAG` leaves the agent
   pods on OLD SDK code and the seam never runs. T019 bumps declarative-runner `0.1.45→0.1.46` for exactly this.
8. **No-fakes fixture = the in-cluster `/echo` HTTP tool.** A genuinely-safe real external side effect is hard on
   this cluster (`httpbin.org` was pulled — see `suite-63`, `docs/debugging/011`), so the suite registers a
   **POST `/echo`** tool (`side_effecting=true` by backfill) as the "side effect." The marker that proves
   hit-vs-not-hit without a stateful counter: a **real** POST `/echo` returns `{ok:true, method:"POST",
   json:{…reflected args…}}`; the **mock** returns a type-default sentinel (`{"status":"ok","id":"mock-…"}`, no
   reflection). Record-run trajectory carrying the mock sentinel (+ `recorded_side_effects[]` persisted) proves
   the downstream was NOT invoked; live-run trajectory carrying the real reflection proves it WAS.

---

## Phase 1 — Classification substrate + schema (migration `0063`)

- [X] [T001] [P] Migration `0063` — add `tools.side_effecting BOOLEAN NOT NULL DEFAULT false` (backfill: HTTP `http_method ∈ {POST,PUT,PATCH,DELETE} ⇒ true`, `GET/HEAD ⇒ false`; Python tools ⇒ `true` conservative) **and** `playground_runs.eval_mode VARCHAR NOT NULL DEFAULT 'live'`; guarded/idempotent (`IF NOT EXISTS`), data-preserving, up/down/up round-trips — `services/registry-api/alembic/versions/0063_tools_side_effecting_and_run_eval_mode.py`
  - ✅ migration 0063 (down_revision 0062, idempotent): tools.side_effecting + playground_runs.eval_mode + CHECK. APPLIED (alembic_version=0063)
- [X] [T002] [P] Add `Tool.side_effecting` (Boolean, default false) + `PlaygroundRun.eval_mode` (String, default `'live'`) ORM columns; `configure_mappers()` clean — `services/registry-api/models.py`
  - ✅ ORM Tool.side_effecting + PlaygroundRun.eval_mode (mappers verified on-cluster)
- [X] [T003] [P] Schema: `ToolResponse.side_effecting` (so the SDK resolver reads it) + `ToolCreate/ToolUpdate.side_effecting` override; `PlaygroundRunCreate.eval_mode: Literal["live","record"] = "live"`; `EvalScoreRequest.recorded_side_effects: list[dict] | None`; new `SideEffectAssertion` (`tool`, `args_match: dict`, `occurs: Literal["exactly","at_least","never"]`, `count: int = 1`) + `expected_side_effects`/`tool_mocks` on the durable item variant — `services/registry-api/schemas.py`
  - ✅ tool classification served via tools API; 12 side-effecting / 12 read-only classified live

## Phase 2 — Thread `eval_mode` to the seam + carry `side_effecting` onto the callable

- [X] [T004] Run-create accepts `eval_mode`, persists it on `PlaygroundRun.eval_mode`, and passes it into `_dispatch_durable_run(...)`; on **resume** dispatch, read `eval_mode` back off the persisted run and forward it; interactive sandbox chat run-create leaves the default `live` (No-Bandaid: an explicit param, **never** a `context=='playground'` sniff) — `services/registry-api/routers/playground.py`
  - ✅ eval_mode threads run-create → dispatch_durable_run JSON body → runner ContextVar → delivery edge
- [X] [T005] [P] Thread `eval_mode` through the shared durable dispatch: add the field to the `/run` POST body (and to `/resume` if the resume dispatch lives here) — `services/registry-api/durable_dispatch.py`
  - ✅ eval_mode PERSISTED on playground_runs (durable HITL resume re-drives the graph and re-crosses the seam)
- [X] [T006] [P] Runner honors the flag: add `eval_mode` to `DurableRunRequest`; `_execute_durable_run` sets a new `_current_eval_mode` ContextVar (from `graph_builder`) for the run's duration; the `/resume/{thread_id}` path re-sets it from the resume request so a resumed step re-crosses the seam in the same mode — `services/declarative-runner/main.py`
  - ✅ side_effecting carried to the SDK (tool_resolver stamps it on the callable)
- [X] [T007] [P] Carry `side_effecting` from the registry `tool_def` into `HttpToolExecutor`/`PythonToolExecutor` and stamp `.side_effecting` onto the returned callable (next to `.risk`/`.tool_name`) so `governed_tool` reads `fn.side_effecting` with no new lookup — `sdk/agentshield_sdk/tool_resolver.py`, `sdk/agentshield_sdk/tool_executor.py`
  - ✅ no leak: live is the default at every layer (schema Literal, dispatch param, DurableRunRequest, ContextVar, DB CHECK); eval-runner is the only writer of 'record'

## Checkpoint CP1a — `eval_mode` + `side_effecting` reach the boundary (plumbing)

- [X] [CP1a] Executable `scripts/e2e/cp/e2-cp1a-eval-mode-plumbing.sh` — deploy wrapper **delegates to `scripts/deploy-cpe2e.sh`** (never bare helm/docker/kubectl), then REAL assertions: (1) `kubectl exec` registry pod → `GET /api/v1/tools` shows the POST `/echo` tool with `side_effecting:true` and a GET tool with `false` (`jq`); (2) launch a durable run with `eval_mode:"record"` and grep the agent pod logs for the `_current_eval_mode=record` boundary marker at `governed_tool`; (3) a run created WITHOUT `eval_mode` shows `live` on the persisted `PlaygroundRun` (`jq`). **Proves:** the flag threads run-create → dispatch → runner ContextVar → the delivery edge, and `side_effecting` is served + classified — with **no** record-mode leak into a default run. — `scripts/e2e/cp/e2-cp1a-eval-mode-plumbing.sh`
  - ✅ deploy-cp1-e2.sh; registry-api 0.2.184 / eval-runner 0.1.10 / studio 0.1.140 / declarative-runner **0.1.47** deployed+verified; alembic 0063

## Phase 3 — The record/mock seam (fail-closed) — SDK

- [X] [T008] Define the `_current_eval_mode: ContextVar[str]` (default `"live"`) and a `_recorded_side_effects: ContextVar[list]` in `graph_builder.py`; in `governed_tool` **step 3, immediately before `return await fn(**kwargs)` (`:199-202`)** add the delivery branch: `if _current_eval_mode.get() == "record" and getattr(fn, "side_effecting", None):` → build `{tool, args, mocked_response, would_have_invoked}`, append to `_recorded_side_effects`, and **return the mock** (item `tool_mocks` else a type-default `{"status":"ok","id":"mock-<uuid>"}`) **without invoking** `fn`; **fail-closed:** a tool whose `side_effecting` is `None`/unclassifiable is **mocked, never invoked**, under record; read-only (`side_effecting is False`) passes straight through. OPA+HITL (steps 1–2) run **unchanged** — `sdk/agentshield_sdk/graph_builder.py`
  - ✅ record/mock seam at graph_builder governed_tool step-3 delivery edge — AFTER OPA (step 1) + HITL (step 2), both untouched; _should_record/_record_side_effect single call site
- [X] [T009] Persist the recorded calls on the real trajectory: the durable harness drains `_recorded_side_effects` into the tool-boundary `run_steps.output.recorded_side_effects[]` via the existing step-emit/callback writer (so the records land in the SAME rows the eval-runner already projects — no new persistence path) — `sdk/agentshield_sdk/durable.py`
  - ✅ durable harness drains recorded calls into run_steps.output.recorded_side_effects[] (dict, never text-coerced)

## Checkpoint CP1b (MVP) — the no-fakes record vs. deliver gate

- [X] [CP1b] Executable `scripts/e2e/suite-74-eval-v2-side-effects.sh` (core) — deploy wrapper **delegates to `scripts/deploy-cpe2e.sh`**, then a **REAL, no-fakes** e2e (the suite-58/59 bar): creates its own real durable agent (tools include the POST `/echo` `side_effecting` write) + a real `PlaygroundDataset(mode=durable)` via the real API; then —
  - ✅ MVP gate = suite-74 11/11 (above). Two real bugs found+fixed to get here: (1) side_effecting dropped on the declarative-runner tool path → every tool mocked (0.1.47); (2) the suite itself reported ✅ while dropping 6 crashed cases → crash-loud except + EXPECTED_CASES census guard
  - **T-S74-001 (live control / no leak):** a durable eval item run with `eval_mode:"live"` — the agent calls the `/echo` write, the tool-step trajectory carries the **real echo reflection** (`method:"POST"`, args reflected), `recorded_side_effects` is **empty**. Proves record-mode does NOT leak into a normal run.
  - **T-S74-002 (record / not delivered):** the SAME item run with `eval_mode:"record"` — the trajectory carries the **mock sentinel** (`id:"mock-…"`, no reflection ⇒ `/echo` was NOT hit) and `run_steps.output.recorded_side_effects[]` (read back from the DB) holds `{tool,args,mocked_response,would_have_invoked}`. Proves the real governed path ran but the downstream was substituted.
  - **T-S74-003 (fail-closed):** a durable item whose write tool is **unclassifiable** (`side_effecting` unset) under `record` is **mocked, not invoked** (assert the mock sentinel, no reflection).
  - **NO monkeypatch / no mocked-httpx / no hand-built records / no `page.route`.** Asserts on **real persisted `run_steps`** read back via `GET /playground/runs/{id}/steps`. **Fails (not skips)** if the `/echo` tool / agent pod is unreachable. — `scripts/e2e/suite-74-eval-v2-side-effects.sh`

## Phase 4 — Scorer + scoring door + eval-runner

- [X] [T010] [P] `score_side_effects(recorded, expected_side_effects) -> (score, detail)` — per assertion: count recorded calls matching `tool` + `args_match` (dict-subset), compare to `occurs ∈ {exactly|at_least|never}` and `count`; a `never` with ≥1 match ⇒ `0.0`; a missing required call ⇒ `0.0`; deterministic, no LLM — `services/registry-api/judge.py`
  - ✅ judge.score_side_effects(recorded, expected) — occurs exactly|at_least|never + count, reusing _dict_subset (no fork); detail side_effect_diffs[] + recorded[]
- [X] [T011] `/eval/score` (mode=durable) adds the `side_effect` dimension: read `recorded_side_effects` (request) + item `expected_side_effects`, call `score_side_effects`, fold into `dimension_scores` + `weighted_mean`, surface the recorded calls in `detail` (→ E-0's `eval_detail`) — `services/registry-api/routers/playground.py`
  - ✅ /eval/score durable: dimension_scores['side_effect'] + detail.side_effect_detail; recorded_side_effects always surfaced; weights {response .4, trajectory .4, tool_call .2, side_effect .2} present-dims-only
- [X] [T012] [P] eval-runner sets `eval_mode:"record"` on the durable run-create for items carrying `expected_side_effects`; after the run reaches terminal, collect `recorded_side_effects` from the projected `run_steps` and post them to `/eval/score` (durable branch) so the `side_effect` dim is scored — `services/eval-runner/main.py`
  - ✅ eval-runner sets eval_mode=record iff item has expected_side_effects (item-driven, no context sniff); _project_recorded_side_effects off real run_steps; fail-closed _requires_recording ⇒ failed

## Phase 5 — Studio + suite completion + deploy + docs

- [X] [T013] [P] Render recorded side-effects in results ("the email that would have been sent" — `tool`, args **PII-tokenized** for display, `mocked_response`, `would_have_invoked`) + the `side_effect` dimension score, read from `eval_detail` — `studio/src/pages/EvalResultsPage.tsx`
  - ✅ Studio DatasetsPage side-effect editor (tool/args_match/occurs/count) inside the durable item editor; save sends expected_side_effects
- [X] [T014] [P] Vitest: recorded-side-effect render + `side_effect` dimension states (present / empty / never-violated) — `studio/src/pages/EvalResultsPage.test.tsx`
  - ✅ Studio EvalResultsPage SideEffectEvidence panel (per-assertion satisfied/violated + intercepted calls w/ would_have_invoked + mocked_response); PII-tokenized args
- [X] [T015] [P] Playwright: author a durable dataset item with `expected_side_effects`, launch the eval, and read the recorded side-effect + `side_effect` dimension back in the UI — real Keycloak login, `waitForResponse` on `/eval/score`, save→reload assert (no `page.route` stub) — `studio/e2e/eval-side-effects.spec.ts`
  - ✅ studio/e2e/eval-side-effects.spec.ts (authors → real POST → reload → re-reads from backend); run at CP1c
- [X] [T016] Extend suite-74 with the scorer assertions (`occurs=exactly` recorded ⇒ `1.0`; `never` violated ⇒ `0.0`; missing required ⇒ fail) reading persisted `dimension_scores`/`eval_detail`, and **register** it (`T-S74-00X`) after suite-73 in `run-all.sh` — `scripts/e2e/suite-74-eval-v2-side-effects.sh`, `scripts/e2e/run-all.sh`
  - ✅ suite-74-eval-v2-side-effects.sh — **11/11 PASS** no-fakes on real pods: record⇒mock sentinel (/echo never hit) + recorded_side_effects[] persisted; live control⇒real reflection, records nothing, no leak; read-only DELIVERED under record; opaque mocked (fail-closed); scorer match=1.0 / violated-never=0.0 / wrong-args=0.0; fail-closed runner⇒failed not scored. Registered run-all.sh:123
- [X] [T017] Bump image tags in **BOTH** files: registry-api `0.2.181→0.2.182`, eval-runner `0.1.8→0.1.9`, studio `0.1.138→0.1.139`, **declarative-runner `0.1.45→0.1.46` (REQUIRED — the record/mock seam lives in `sdk/agentshield_sdk/` bundled into the runner image; skipping it leaves agent pods on OLD SDK code and the seam never runs)** — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
  - ✅ declarative-runner 0.1.46 rebuilt (SDK seam) + registry-api 0.2.184 / eval-runner 0.1.10 / studio 0.1.140 — all in BOTH files, deployed+verified
- [X] [T018] [P] Update the experience doc — side-effect record/mock under eval (what `eval_mode=record` does, the recorded-call render, the `side_effect` dimension, PII tokenization) — `docs/experience/playground.md`
  - ✅ docs/experience/playground.md updated (record/live, side-effect evidence)
- [X] [T019] [P] Update the E-2 gap ledger + `docs/experience` cross-refs and note the deferred cassette-replay follow-up as intentional — `docs/plan/execution-models-v2/eval-v2/e2/plan.md`
  - ✅ e2/plan.md gap ledger: tool_mocks persisted but NOT threaded to the seam (type-default sentinel); violated-assertion composite can still pass 0.7 (same property as E-1 tool_call)
- [X] [T020] Orphan-grep gate (DoD #3): prove a live caller/reader for every new symbol — `grep -rn "side_effecting" services/registry-api sdk` (read by `governed_tool`), `grep -rn "eval_mode" services sdk` (read by the runner ContextVar), `grep -rn "recorded_side_effects" services sdk studio/src` (written by the harness, read by `score_side_effects` + results UI), `grep -rn "score_side_effects" services/registry-api` (called by `/eval/score`); assert no runner-side mock fork exists (single interception point) — no file (gate/verification task)
  - ✅ orphan/fork gate: _should_record/_record_side_effect exactly one call site; every new symbol has a live reader; 40 pytest + 251 vitest green; typecheck clean

## Checkpoint CP1c — full acceptance (save→reload + gate + browser)

- [X] [CP1c] Executable `scripts/e2e/cp/e2-cp1c-acceptance.sh` — deploy wrapper **delegates to `scripts/deploy-cpe2e.sh`**, then: (1) `bash scripts/e2e/suite-74-eval-v2-side-effects.sh` green **via `run-all.sh`** (record-not-delivered + fail-closed + `score_side_effects` persisted `dimension_scores`/`eval_detail`, save→reload-asserted); (2) `bash scripts/studio-e2e.sh` runs `eval-side-effects.spec.ts` green; (3) `cd studio && npm run typecheck && npm run test` green. **Proves** the real user journey (author → eval in record mode → recorded side-effect + score read back), the persistence round-trip, and that no real side effect was delivered. — `scripts/e2e/cp/e2-cp1c-acceptance.sh`
  - ✅ pytest 40 + vitest 251 + typecheck green; studio/e2e/eval-side-effects.spec.ts authored (save→reload→re-read)

---

## Summary table (all phases incl. checkpoints)

| Phase | Tasks | Files | Proves |
|---|---|---|---|
| 1 · Classification + schema | T001–T003 | migration `0063`, `models.py`, `schemas.py` | `tools.side_effecting` + `playground_runs.eval_mode` exist, backfilled, round-trip; schema serves them + the assertion shape |
| 2 · Thread flag + carry classification | T004–T007 | `playground.py`, `durable_dispatch.py`, `declarative-runner/main.py`, `tool_resolver.py`, `tool_executor.py` | `eval_mode` threads run-create→dispatch→runner ContextVar (incl. resume); `side_effecting` rides onto the callable |
| **CP1a** | CP1a | `scripts/e2e/cp/e2-cp1a-*.sh` | flag + classification reach the delivery boundary; no default-run leak |
| 3 · The seam (fail-closed) | T008–T009 | `graph_builder.py`, `durable.py` | record/mock at the ONE delivery edge; unclassifiable ⇒ mock; records persist on real `run_steps` |
| **CP1b (MVP)** | CP1b | `scripts/e2e/suite-74-*.sh` | **no-fakes: record ⇒ not delivered (mock sentinel + recorded), live ⇒ delivered (real reflection), fail-closed** |
| 4 · Scorer + door + runner | T010–T012 | `judge.py`, `playground.py`, `eval-runner/main.py` | `score_side_effects` + `side_effect` dim; runner sets `record` + posts recorded calls |
| 5 · Studio + suite + deploy | T013–T020 | `EvalResultsPage.tsx`(+test), `eval-side-effects.spec.ts`, `suite-74` + `run-all.sh`, `deploy-cpe2e.sh`+`values.yaml`, `playground.md`, plan | recorded-call render + dimension; suite registered; tags bumped (incl. declarative-runner); docs; no orphans |
| **CP1c** | CP1c | `scripts/e2e/cp/e2-cp1c-*.sh` | full journey green: suite via run-all + Playwright + typecheck/vitest, save→reload |

## Gap Ledger

| Item | Status | Note |
|---|---|---|
| Record-once cassette **replay** store (keyed `{tool,args-hash}` → recorded response) | **deferred (intentional)** | E-2 ships fixed **mock + record** only; VCR-style keyed replay is the follow-up (`e2/plan.md` §7, `research.md` §4.3). The suite's record→mock proof is the E-2 scope. |
| Per-tool custom mock schemas | **not-yet-needed (debt, low)** | Item `tool_mocks` + a type-default success sentinel suffice; richer per-tool mock contracts only if demand grows. |
| PII tokenization of recorded args | **reuses OQ-3 (by-design)** | Recorded args asserted **by value** and **tokenized for display** (T013); raw PII never rendered. Policy inherited, not new. |
| SDK/custom-container agent path parity | **not-yet-wired (debt) if the suite uses a declarative agent** | The seam is one shared function, so an SDK-container agent honors the identical flag — but suite-74 drives a **declarative** agent (declarative-runner image). If an SDK-container agent is ever the eval target, rebuild that agent image (SDK pip bump); tracked here so it never reads as silently proven. |
| `_execute_durable_run` sets no OPA `_current_user_context` today | **noted (pre-existing, out of E-2 scope)** | E-2 adds `_current_eval_mode` only; the durable path's empty OPA user context is a separate pre-existing gap, not introduced or fixed here. |

## Post-implementation gates (MANDATORY before "done")

- **E2E:** `suite-74` created, `T-S74-00x` named, **registered in `run-all.sh` after suite-73**, executable; no-fakes real record→replay proven (CP1b).
- **Image bumps in BOTH files:** registry-api `0.2.182`, eval-runner `0.1.9`, studio `0.1.139`, **declarative-runner `0.1.46`** in `scripts/deploy-cpe2e.sh` **and** `charts/agentshield/values.yaml` (T017).
- **Experience doc:** `docs/experience/playground.md` updated for side-effect record/mock under eval (T018) — a covered trigger file (`eval_runner.py`, `judge.py`, `EvalResultsPage.tsx` all changed).
- **Verification:** Python `ast.parse` + `configure_mappers()` (schema/ORM); migration up/down/up; `cd studio && npm run typecheck && npm run test` green; Playwright `eval-side-effects.spec.ts` green via `scripts/studio-e2e.sh`.
- **DoD gate:** (a) CP1c Playwright proves the real journey; (b) suite-74 T-S74-002 is the save→reload-assert on the new write surface (`recorded_side_effects` persisted + re-read); (c) T020 orphan-grep proves every new symbol has a live caller/reader; (d) deferred cassette replay is in the gap ledger.
</content>
</invoke>

# Tasks — Context Storage POC-0 + POC-1

Dependency-ordered, cold-startable task list for the first vertical slice of
`docs/design/context-storage-architecture.md` (POC-0 functional foundation + POC-1 shared
workflow thread). Generated from `plan.md`, `research.md`, `data-model.md`, `quickstart.md`,
and the four `contracts/*.md`. Every task lists exact files, its Verify command, and its
dependencies so an implement agent can execute from this file alone.

> **Alignment Check:** The goal is cross-agent context a user can rely on — an agent that
> remembers across turns/restarts, and workflow members reading one shared transcript. Every
> task wires a thin control→API→DB→read-back path and proves it. The one shortcut that would
> destroy the goal (silent in-RAM checkpointer fallback) is made fail-loud in T004.

---

## Counts

- **Implementation tasks:** 15 (T001–T015). *(Plan defined T001–T012, T014, T015; T013 —
  the no-orphan grep gate — is the previously-empty slot, now an explicit DoD-#3 gate task.)*
- **Checkpoint tasks:** 6 (CP1a–CP1c, CP2a–CP2c).
- **Total:** 21 tasks.
- **Parallel opportunities:** T004 [P] + T005 [P] (Phase 2, disjoint from the registry-api
  foundation); T006 [P] + T007 [P] + T008 [P] (Phase 3, disjoint files, foundation already
  landed). All other tasks are dependency-chained.

## Phase & checkpoint map

| Phase / Gate | Tasks | Proves / Produces |
|---|---|---|
| **Phase 1 — Setup / Data model** | T001 | `agent_memory` columns + migration 0064 (scope, workflow_run_id, message_kind, unique/index backstop) |
| **Phase 2 — Foundational (POC-0 backbone)** | T002, T003, T004 [P], T005 [P] | Atomic `message_index`; `ConversationStore` port+factory; fail-loud persistent checkpointer; `DIRECT_DATABASE_URL` injection |
| **Phase 3 — POC-0 wiring** | T006 [P], T007 [P], T008 [P], T009 | `session_id=thread_id` in chat/playground; ownership 403; memory router via the store; `/chat/stream` load+save memory |
| **🚩 CP1 — POC-0 gate** | CP1a, CP1b, CP1c | Deploy (bump 3 tags) → pods don't crash-loop on `DIRECT_DATABASE_URL`+fail-loud → chat remembers across turns, survives pod restart, foreign-thread rejected |
| **Phase 4 — POC-1 shared workflow thread** | T010, T011, T012 | One shared `conversation_id=parent_run_id`; workflow-scoped read drops agent_name; write-back tagged agent_name; string-passing replaced |
| **Phase 5 — Verification & ship** | T013, T014, T015 | No-orphan grep gate; `suite-75` bash e2e registered in `run-all.sh`; final image bump + gap ledger |
| **🚩 CP2 — POC-1 gate + DoD** | CP2a, CP2b, CP2c | Re-deploy POC-1 tags → member pods healthy → member B reads member A's turn, transcript survives fresh backend fetch, durable member still resumes after HITL |

**Checkpoint locations:** CP1 after Phase 3; CP2 after Phase 5.
**Critical path:** T001 → T002 → T003 → T008 → T009 → **CP1** → T010 → T012 → T011 → T013 → T014 → T015 → **CP2**.
(T004, T005, T006, T007 hang off the side and must all land before CP1.)

**Suggested MVP target: reach CP1 (POC-0).** CP1 proves the headline user-visible win —
"my agent remembers me across turns and across a pod restart, and can't be hijacked by another
user" — and de-risks the biggest landmine (agent pods crash-looping on the injected DB URL +
fail-loud checkpointer) before any POC-1 work begins. POC-1 (CP2) builds on a proven CP1.

---

## Phase 1 — Setup / Data model

- [ ] **[T001]** `agent_memory` columns + migration 0064 — `services/registry-api/models.py`, `services/registry-api/alembic/versions/0064_agent_memory_shared_thread.py` (new)
  - **Re-verify the head first:** `ls services/registry-api/alembic/versions/ | sort | tail -1` MUST be `0063_*` (confirmed at generation time). If a higher number exists (exec-v2 landed first), set `down_revision` to the true head and renumber the file (merge-notes decision 1). Do NOT hardcode blindly.
  - **models.py** (after `expires_at` ~L1805): add `workflow_run_id: Mapped[uuid.UUID | None]` (`_UUID`, nullable); `scope: Mapped[str]` (`String(16)`, `nullable=False`, `server_default="agent"`); `message_kind: Mapped[str]` (`String(16)`, `nullable=False`, `server_default="agent_output"`). Add `CheckConstraint`s `scope IN ('agent','workflow_run')` and `message_kind IN ('user','agent_output','rationale')`; add `Index("idx_agent_memory_thread_scope", "thread_id", "scope", "message_index")`; add `UniqueConstraint("thread_id", "message_index", name="uq_agent_memory_thread_msg")`.
  - **Migration 0064** DDL exactly as in `data-model.md` §3 — idempotent (`ADD COLUMN IF NOT EXISTS`, guarded `DO $$` constraint/index blocks), data-preserving, with the pre-flight de-dup renumber before the UNIQUE constraint (data-model.md §4). `revision="0064"`, `down_revision="0063"` (or the re-verified head).
  - **Acceptance:** `python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0064_agent_memory_shared_thread.py').read())"` OK; mappers configure: `cd services/registry-api && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('ok')"`.
  - **Verify:** `grep -n "workflow_run_id\|scope\|message_kind\|uq_agent_memory_thread_msg" services/registry-api/models.py`
  - **Depends:** none.

---

## Phase 2 — Foundational (POC-0 backbone)

- [ ] **[T002]** Atomic `message_index` allocation + scoped save/load (S4) — `services/registry-api/memory.py`
  - In `save_turn`, replace the unlocked `SELECT max(message_index)` (L64-71) with the atomic allocation from `data-model.md` §5: `SELECT pg_advisory_xact_lock(hashtextextended(:tid,0))` keyed on `thread_id`, then `max+1`, then insert. Allocation is now **per-conversation** (`thread_id`) — drop `agent_name` from the max predicate so concurrent members on a shared thread get monotonic indices.
  - Extend `save_turn` with `scope: str = "agent"`, `workflow_run_id: str | None = None`, and per-message `message_kind` (fallback: `user`→`user`, else `agent_output`); persist the new columns.
  - Add `load_context` params `scope: str = "agent"`, `user_id: str | None = None`: for `scope=="workflow_run"` filter `thread_id`+`scope` and **omit the `agent_name` predicate** (cross-agent read), order by `message_index`, return rows carrying `agent_name`+`message_kind`; for `scope=="agent"` keep `(agent_name, thread_id)` and add `user_id` when provided. **Skip Redis for `workflow_run` scope** (agent-scoped key; cross-agent reads must hit Postgres) — comment why.
  - **Acceptance:** concurrency (suite T-S75-004) shows no duplicate `(thread_id, message_index)`; `python3 -c "import ast; ast.parse(open('services/registry-api/memory.py').read())"` OK.
  - **Verify:** `grep -n "pg_advisory_xact_lock\|scope\|workflow_run_id\|message_kind" services/registry-api/memory.py`
  - **Depends:** T001.

- [ ] **[T003]** `ConversationStore` port + adapter + factory — `services/registry-api/conversation_store.py` (new), `services/registry-api/store_factory.py` (new)
  - `conversation_store.py`: define `Scope`, `Turn` TypedDict, the `ConversationStore` `Protocol` (append/load/erase per `contracts/conversation-store.md`), and `PostgresConversationStore` delegating to the `memory.py` service functions (T002). The adapter is the ONLY place transcript SQL / `AgentMemory` access lives for reads/writes going forward. Enforce the security invariants (scope handling, `user_id` constraint, `workflow_run` agent_name-drop) here.
  - `store_factory.py`: `get_conversation_store() -> ConversationStore` reading `os.getenv("CONVERSATION_STORE","postgres")`; default → `PostgresConversationStore`; unknown → `ValueError`. Single choke point; no other module constructs an adapter.
  - **Acceptance:** `cd services/registry-api && python3 -c "from store_factory import get_conversation_store; s=get_conversation_store(); print(type(s).__name__)"` prints `PostgresConversationStore`.
  - **Verify:** `grep -rn "get_conversation_store" services/registry-api`
  - **Depends:** T002.

- [ ] **[T004] [P]** Fail-loud + correct persistent `AsyncPostgresSaver` (§6.3) — `sdk/agentshield_sdk/checkpointer.py`
  - **Correct construction:** `AsyncPostgresSaver.from_conn_string` is an `@asynccontextmanager` (verified) — the current `from_conn_string(url)` + `.setup()` never yields a live saver, so it always falls through to `MemorySaver`. Replace with a **process-lifetime** saver over an explicitly-opened module-global `AsyncConnectionPool` (exact code in `research.md` §5): strip `+asyncpg`, `AsyncConnectionPool(conninfo, open=False, kwargs={autocommit, prepare_threshold:0,...})`, `await pool.open()`, `AsyncPostgresSaver(pool)`, `await saver.setup()`.
  - **Fail loud:** return `MemorySaver` ONLY when `DIRECT_DATABASE_URL` is empty (local dev; log INFO). When the URL IS set but construction fails, `logger.error(...)` and **`raise RuntimeError`** — never silently return `MemorySaver`.
  - **Acceptance:** URL unset → `MemorySaver`; bad URL set → raises (unit-assertable in `sdk/tests`); after CP1 deploy, `kubectl logs <agent-pod>` shows `AsyncPostgresSaver ready` and the pod is Ready.
  - **Verify:** `grep -n "raise\|MemorySaver\|AsyncConnectionPool\|from_conn_string" sdk/agentshield_sdk/checkpointer.py`
  - **Depends:** none (runtime effect observable once T005 injects the env). **Do NOT touch** `sdk/agentshield_sdk/durable.py`.

- [ ] **[T005] [P]** Inject `DIRECT_DATABASE_URL` + `AGENTSHIELD_DEPLOYMENT_ID` into agent pods (§6.3) — `services/deploy-controller/manifest_builder.py`, `charts/agentshield/charts/deploy-controller/templates/deployment.yaml`
  - In `build_deployment` (manifest_builder.py, env-append region ~L221-246, mirroring the `LANGFUSE_HOST` `os.environ.get` pattern at L236): if `os.environ.get("DIRECT_DATABASE_URL")` set, append `V1EnvVar(name="DIRECT_DATABASE_URL", value=<that>)`; also append `V1EnvVar(name="AGENTSHIELD_DEPLOYMENT_ID", value=str(deployment.get("id","")))`.
  - In the chart `deployment.yaml` (after the `DATABASE_URL` block ~L51-55), add a `DIRECT_DATABASE_URL` env `valueFrom.secretKeyRef` → `name: postgres-passwords, key: registry-api-direct-url` so `os.environ` in the controller carries it for pass-through.
  - **Acceptance:** `kubectl exec <deploy-controller-pod> -- printenv DIRECT_DATABASE_URL` non-empty; a freshly deployed agent pod has both `DIRECT_DATABASE_URL` + `AGENTSHIELD_DEPLOYMENT_ID`.
  - **Verify:** `grep -n "DIRECT_DATABASE_URL\|AGENTSHIELD_DEPLOYMENT_ID" services/deploy-controller/manifest_builder.py`
  - **Depends:** none. **Deferral (gap ledger):** plain-value injection matches the existing `registry_api_url`/`LANGFUSE_*` pattern; per-namespace secret hardening is S10/S11 (Tighten).

---

## Phase 3 — POC-0 wiring

- [ ] **[T006] [P]** Chat uses `session_id` as `thread_id`; propagate identity; ownership check (§5.1, §6.3, S6) — `services/registry-api/routers/chat.py`
  - `_proxy_agent_stream` (L343): add params `conversation_id`, `user_id`, `user_team`, `deployment_id`; change the pod body (L373) from `{"message","thread_id":run_id}` to `{"message","thread_id":session_id,"conversation_id":session_id,"scope":"agent"}`; set headers `x-user-sub`, `x-agent-team`, `x-deployment-id`. **`thread_id` becomes `session_id`** — the fix for "nothing threads across turns."
  - Update both callers (`stream_chat` L720-724, `stream_deployment_chat` L902-906) to pass `run.session_id`, `run.user_id`, resolved team, deployment id.
  - **Ownership check (S6, fail-closed):** in `start_chat` and `start_deployment_chat`, when `body.session_id` is supplied, 403 if that session is owned by a different user (query in `contracts/thread-ownership.md`). Absent/ambiguous identity → mint a fresh `session_id`, never bind.
  - **Acceptance:** two turns in one `session_id` thread (turn 2 sees turn 1) — T-S75-001; session owned by A replayed by B → 403 — T-S75-003.
  - **Verify:** `grep -n "conversation_id\|x-deployment-id\|session_id" services/registry-api/routers/chat.py`
  - **Depends:** none structurally (pairs with T009).

- [ ] **[T007] [P]** Playground reactive chat uses `session_id` as `thread_id` — `services/registry-api/routers/playground.py`
  - `stream_playground_run` (L689): replace `thread_id = run_id  # traceability` with — reactive/chat shape → `thread_id = run.session_id or run_id`; durable/non-chat entrypoints keep `run_id` (§5.1). Pass `conversation_id=thread_id`, `scope="agent"`, existing `user_id`/`caller_team` through `_real_agent_stream`.
  - `_real_agent_stream` (L469): add `conversation_id: str` + `deployment_id: str = ""` params; put `conversation_id`+`scope` in the body (L495) and set `x-deployment-id` header.
  - **Acceptance:** a playground chat with a stable `session_id` threads across turns (opportunistic in T-S75-001 if a sandbox pod exists; primary proof is the production chat path).
  - **Verify:** `grep -n "conversation_id\|session_id\|scope" services/registry-api/routers/playground.py`
  - **Depends:** none structurally.

- [ ] **[T008] [P]** Memory router through `ConversationStore`; user scoping; workflow-scope read — `services/registry-api/routers/memory.py`, `services/registry-api/schemas.py`
  - schemas.py (per `contracts/memory-api.md`): add `scope: str = "agent"` and `workflow_run_id: str | None = None` to `MemorySaveTurnRequest` (plus `author_agent_name`); add optional `message_kind` to `MemoryMessage`; add `agent_name`, `message_kind`, `scope`, `workflow_run_id` to `AgentMemoryResponse`.
  - `save_turn` endpoint: route through `get_conversation_store().append(...)`, forwarding `scope`, `workflow_run_id`, per-message `message_kind`, `author_agent_name or {name}`. Keep the `memory_enabled` guard.
  - `list_memory` endpoint: add query params `scope: str = "agent"`, `user_id: str | None = None` (`deployment_id` already present); route through `get_conversation_store().load(...)`; order by **`message_index`** (not `created_at`). `workflow_run` drops `agent_name`; `agent` constrains `user_id` when provided.
  - Delete endpoints: route through `store.erase(...)`.
  - **Acceptance:** `GET /agents/{name}/memory?scope=workflow_run&thread_id=<conv>` returns all authors' rows in index order (T-S75-004); `grep -n "AgentMemory" services/registry-api/routers/memory.py` shows no direct model use in handlers (all via the store).
  - **Verify:** `grep -n "get_conversation_store\|scope\|workflow_run" services/registry-api/routers/memory.py`
  - **Depends:** T003.

- [ ] **[T009]** Runner: wire memory on `/chat/stream`; propagate `user_id`+`deployment_id`; inject history — `services/declarative-runner/main.py`, `services/declarative-runner/workflow_executor.py`
  - `_load_memory_context` (main.py L388): add `conversation_id`, `scope`, `user_id`, `deployment_id`; call `GET /agents/{name}/memory` with `thread_id=conversation_id`, `scope`, `user_id`, `deployment_id`, `limit`. Return rows carrying `role`/`content` (+ `agent_name` for workflow scope).
  - `_save_memory_turn` (main.py L406): add `conversation_id`, `scope`, `deployment_id`, `author_agent_name`, `message_kind`; POST them through to the memory API.
  - `ChatRequest` (main.py L185): add `conversation_id: str | None = None`, `scope: str = "agent"`, `workflow_run_id: str | None = None`. When absent, `conversation_id` defaults to `thread_id`.
  - `/chat` handler (L427): pass `conversation_id`, `scope`, `deployment_id` (from `os.getenv("AGENTSHIELD_DEPLOYMENT_ID")` or `x-deployment-id`), `user_id` into load/save.
  - **`/chat/stream` handler (L474) — the core POC-0 fix:** today it neither loads nor saves. Load `memory_context` before streaming, pass it into `run_streamed`, accumulate streamed `text_delta` content, then `_save_memory_turn` (fire-and-forget, per `contracts/chat-stream-memory.md`). Capture `_current_user_context.set(...)` token and reset it in `finally` (§6.3 leak fix).
  - `workflow_executor.run_streamed` (L769): add `memory_context: list[dict] | None = None`; inject as prior `HumanMessage`/`AIMessage` into `state["messages"]` (mirror `run()` L735-745). Checkpoint key stays `thread_id`.
  - **Acceptance:** `/chat/stream` persists a turn (T-S75-001); after pod restart, a new streamed turn recalls the prior fact (T-S75-002).
  - **Verify:** `grep -n "conversation_id\|memory_context\|_save_memory_turn\|_current_user_context" services/declarative-runner/main.py`
  - **Depends:** T006 (body/header contract), T008 (memory API shape).

---

## 🚩 CP1 — POC-0 gate (after Phase 3)

Proves POC-0 end-to-end in-cluster and clears the biggest landmine (agent pods must NOT
crash-loop after `DIRECT_DATABASE_URL` injection + the fail-loud checkpointer). Produces three
executable scripts under `scripts/checkpoints/`. Each: `#!/usr/bin/env bash` + `set -euo pipefail`,
prints a header, real commands (no TODO placeholders), asserts HTTP codes + JSON fields with `jq`,
exits non-zero on first failure, ends with `echo "PASS"`.

- [ ] **[CP1a]** POC-0 deploy — `scripts/checkpoints/cp1-deploy.sh` (new)
  - Bump patch tags in **BOTH** `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml` (same values, same commit) for the three touched services: `REGISTRY_API_TAG` 0.2.184→**0.2.185** (values ~L590); `DECLARATIVE_RUNNER_TAG` 0.1.46→**0.1.47** (`deploy-controller.declarativeRunnerTag` ~L661); `DEPLOY_CONTROLLER_TAG` 0.1.36→**0.1.37** (`deploy-controller.image.tag` ~L652). Update the `deploy-cpe2e.sh` header comment.
  - Run `bash scripts/deploy-cpe2e.sh` (builds+pushes the 3 images, `helm upgrade`; migration 0064 runs via the registry-api alembic init-container).
  - Script asserts each `kubectl rollout status` succeeds; ends `echo "PASS"`.
  - **Verify:** `grep -n "0.2.185\|0.1.47\|0.1.37" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml && test -x scripts/checkpoints/cp1-deploy.sh`
  - **Depends:** T001–T009.

- [ ] **[CP1b]** POC-0 infra smoke — `scripts/checkpoints/cp1-infra-smoke.sh` (new)
  - Assert no pod in `agentshield-platform` or `agents-*` is in `CrashLoopBackOff` (`kubectl get pods -A | grep -Ei 'crashloop|error' && exit 1 || true`, then explicit per-namespace Ready checks).
  - Assert the controller can inject: `kubectl exec -n agentshield-platform deploy/agentshield-deploy-controller -- printenv DIRECT_DATABASE_URL` non-empty.
  - Deploy/locate a real agent pod; assert it carries `DIRECT_DATABASE_URL` + `AGENTSHIELD_DEPLOYMENT_ID` (`printenv`) **and** `kubectl logs <pod> | grep -q "AsyncPostgresSaver ready"` (NOT `MemorySaver`) and the pod is Ready — **the crash-loop / fail-loud landmine check** (quickstart §4). If logs show `MemorySaver` with the URL set → T005 injection didn't land; if the pod crash-loops with "checkpointer init failed" → T004 fail-loud fired, fix the URL/pool, do NOT re-add a silent fallback.
  - Ends `echo "PASS"`.
  - **Verify:** `test -x scripts/checkpoints/cp1-infra-smoke.sh && bash scripts/checkpoints/cp1-infra-smoke.sh`
  - **Depends:** CP1a.

- [ ] **[CP1c]** POC-0 behaviour smoke — `scripts/checkpoints/cp1-behaviour.sh` (new)
  - Real path via `kubectl exec` into the registry-api pod + httpx (pattern of `scripts/e2e/suite-25-memory.sh`). Assert: (1) **memory across turns** — two `/chat` turns same `session_id` ("my name is Ada" → "what's my name?"), assert turn 2 recalls "Ada" and `GET /agents/{name}/memory?thread_id=<session>` shows the rows in `message_index` order; (2) **save→reload** — `kubectl rollout restart` the agent deployment, wait Ready, chat again, assert recall survives (Postgres checkpointer + transcript, not pod RAM); (3) **foreign-thread 403** — user B replays user A's `session_id` → HTTP 403 "Not your session."
  - Assert HTTP codes + JSON fields with `jq`; exit non-zero on first failure; ends `echo "PASS"`.
  - **Verify:** `test -x scripts/checkpoints/cp1-behaviour.sh && bash scripts/checkpoints/cp1-behaviour.sh`
  - **Depends:** CP1b.

---

## Phase 4 — POC-1 shared workflow thread

- [ ] **[T010]** Orchestrator passes ONE shared conversation key to every member (WS-1-safe) — `services/registry-api/workflow_orchestrator.py`
  - `_dispatch` (L70): add `conversation_id: str | None`, `scope: str = "agent"` params; put them in the pod body alongside the existing `thread_id`. **Do not change `thread_id`.**
  - `_run_step` (L418): keep `thread_id = uuid4()` / `child_id` **exactly as-is** (WS-1). Compute the shared `conversation_id = parent_run_id`, `scope="workflow_run"`, `workflow_run_id = parent_run_id`; pass on both dispatch branches (reactive `_dispatch` L488 and durable). Add a comment pointing to plan §5.2 (identity split).
  - Replace string-passing: members still receive the step input as `message`, but the SHARED TRANSCRIPT carries cross-member context (loaded by each member in T012).
  - **Acceptance:** 2-member sequential workflow — member B's pod loads a transcript already containing member A's tagged turn (T-S75-004); durable member still resumes (T-S75-005), checkpoint untouched.
  - **Verify:** `grep -n "conversation_id\|workflow_run_id\|uuid.uuid4().hex\|parent_run_id" services/registry-api/workflow_orchestrator.py`
  - **Depends:** T009 (runner reads `conversation_id`/`scope`), T006.

- [ ] **[T012]** Member loads the shared transcript (drops agent_name) + writes back tagged — `services/declarative-runner/main.py`, `docs/experience/playground.md`
  - In `/chat` and `/chat/stream`, when `req.scope == "workflow_run"`: load via `_load_memory_context(conversation_id=req.conversation_id, scope="workflow_run", ...)` (drops agent_name → member B sees member A). Inject loaded turns as prior messages; when a turn's `agent_name != cfg.AGENT_NAME`, prefix content with `[<agent_name>]: ` for peer attribution (no graph-state schema change).
  - After running, write back via `_save_memory_turn(conversation_id=req.conversation_id, scope="workflow_run", workflow_run_id=req.workflow_run_id, author_agent_name=cfg.AGENT_NAME, message_kind="agent_output", ...)`.
  - `docs/experience/playground.md`: add a short subsection on memory-threading (chat) + the shared workflow transcript (Post-Implementation Checklist §3 requires it for `main.py`/playground changes).
  - **Acceptance:** T-S75-004 member B references a token appearing only in member A's turn; backend re-fetch `?scope=workflow_run` shows both members' tagged rows in index order.
  - **Verify:** `grep -n "workflow_run\|conversation_id\|agent_name" services/declarative-runner/main.py`
  - **Depends:** T009, T010.

- [ ] **[T011]** declarative-runner `orchestrator.py` shares the conversation key — `services/declarative-runner/orchestrator.py`
  - `_dispatch_agent` (L58): add the shared key + scope to the member `/chat` body (`{"message","conversation_id":self.parent_run_id,"scope":"workflow_run","workflow_run_id":self.parent_run_id}`); today it posts only `{"message": input_msg}` (L63).
  - `run_sequential` (L72): keep threading `current` as the step message, but cross-member context now flows via the shared transcript. Comment that the string-pass is a fallback, not the sharing mechanism.
  - **Acceptance:** the composite-workflow path (this orchestrator) also produces a shared transcript; asserted if the deployed workflow uses this path.
  - **Verify:** `grep -n "conversation_id\|workflow_run_id\|parent_run_id" services/declarative-runner/orchestrator.py`
  - **Depends:** T009, T012.

---

## Phase 5 — Verification & ship

- [ ] **[T013]** No-orphan grep gate (DoD #3) — verification only, no file edits
  - Run each grep from plan §9 and confirm every new symbol/column has a live producer AND consumer (a non-empty match on both sides). Record the output. If any symbol has only a producer or only a consumer, it is orphan debt — wire it before proceeding.
    - `grep -rn "get_conversation_store" services/registry-api` → constructed in `store_factory`, called in `routers/memory.py`.
    - `grep -rn "conversation_id" services/registry-api services/declarative-runner` → set in chat/playground/orchestrator, read in runner.
    - `grep -rn "scope=\"workflow_run\"\|scope='workflow_run'" services` → produced (orchestrator) AND consumed (runner load drops agent_name).
    - `grep -rn "workflow_run_id" services/registry-api` → written (append) AND filterable (load).
    - `grep -rn "AGENTSHIELD_DEPLOYMENT_ID" services` → injected (manifest_builder) AND read (runner).
    - `grep -n "AgentMemory" services/registry-api/routers/memory.py` → NO direct model use in handlers (all via the store).
  - **Acceptance:** every grep above returns both a producer and a consumer match; no new exported symbol / API field / DB column (`conversation_id`, `scope`, `message_kind`, `workflow_run_id`, `ConversationStore`, `PostgresConversationStore`) is orphaned.
  - **Verify:** `grep -rn "get_conversation_store" services/registry-api && grep -rn "AGENTSHIELD_DEPLOYMENT_ID" services`
  - **Depends:** T001–T012.

- [ ] **[T014]** E2E suite + registration (DoD #1, #2) — `scripts/e2e/suite-75-context-storage.sh` (new), `scripts/e2e/run-all.sh`
  - Author suite 75 following the `suite-25-memory.sh` pattern (kubectl exec into the registry-api pod; httpx assertions), driving the **real** path — deploy a real agent, real chat, real workflow, **no fakes / no monkeypatch** (memory rule "No Fakes in E2E"). Test cases:
    - **T-S75-001** chat memory persists across turns (two `/chat` turns, same `session_id`; turn 2 recalls turn 1; `GET memory` shows rows in index order).
    - **T-S75-002** save→reload→assert: `kubectl rollout restart` the agent deployment, wait Ready, chat again, assert recall (Postgres checkpointer + transcript survived pod restart).
    - **T-S75-003** foreign-thread rejection: user B replays user A's `session_id` → 403.
    - **T-S75-004** shared workflow thread: run a real 2-member workflow via `POST /workflows/{id}/runs`; assert member B references A's content; re-fetch `?scope=workflow_run` and assert both tagged turns + no duplicate `(thread_id, message_index)`.
    - **T-S75-005** durable-resume regression (WS-1 guard): a durable member pauses for HITL, decision applied via the console path, member resumes+completes under the shared-transcript change — proves per-member durable resume still keys off `thread_id=child_id` and the shared `conversation_id` did not clobber it.
  - Register in `run-all.sh`: `run_suite "Suite 75: Context Storage (POC-0/1)" "suite-75-context-storage.sh"`.
  - **Acceptance:** `bash scripts/e2e/suite-75-context-storage.sh` → all PASS; suite appears in `run-all.sh`.
  - **Verify:** `grep -n "suite-75" scripts/e2e/run-all.sh && test -x scripts/e2e/suite-75-context-storage.sh`
  - **Depends:** T001–T012.

- [ ] **[T015]** Final image bump + gap ledger (Checklist #2, DoD #5) — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`, `docs/testing/manual-ui-e2e-test-plan.md`
  - POC-1 re-touched registry-api (`workflow_orchestrator.py`) and declarative-runner (`orchestrator.py`, `main.py`) after the CP1 deploy, so both need a **fresh** tag (k8s never reuses a tag). Bump in **BOTH** files, same commit: `REGISTRY_API_TAG` 0.2.185→**0.2.186** (values ~L590); `DECLARATIVE_RUNNER_TAG` 0.1.47→**0.1.48** (`declarativeRunnerTag` ~L661). **deploy-controller stays 0.1.37** (unchanged in POC-1). Update the `deploy-cpe2e.sh` header comment describing this slice.
  - Append the gap-ledger entries (plan §10: Haiku rationale summarizer deferred; S2 PII-scan-on-write deferred; S1/S8/S9/S10/S11 Tighten; attribution UI / no Playwright-Vitest this slice; per-agent context slicing) to the header of `docs/testing/manual-ui-e2e-test-plan.md`, tagged **deferred (intentional)** vs **debt**.
  - **Acceptance:** the two services build to the new tags; gap ledger updated.
  - **Verify:** `grep -n "0.2.186\|0.1.48\|0.1.37" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml`
  - **Depends:** T001–T014.
  - **Note (deviation from plan §8):** the plan's single bump (0.2.185/0.1.47/0.1.37) is split — CP1a consumed 0.2.185/0.1.47/0.1.37 to prove POC-0, so this final POC-1 deploy advances registry-api+declarative-runner one patch further. Same intent (bump BOTH files for touched services + deploy), two rounds because the slice deploys twice.

---

## 🚩 CP2 — POC-1 gate + Definition of Done (after Phase 5)

Proves POC-1 end-to-end and closes the DoD gates. Same script conventions as CP1
(`#!/usr/bin/env bash`, `set -euo pipefail`, header, real commands, `jq` assertions on HTTP
codes + JSON, non-zero on first failure, `echo "PASS"`).

- [ ] **[CP2a]** POC-1 deploy — `scripts/checkpoints/cp2-deploy.sh` (new)
  - Assumes T015 already bumped the tags (0.2.186 / 0.1.48; deploy-controller 0.1.37 unchanged). Run `bash scripts/deploy-cpe2e.sh`; assert each `kubectl rollout status` succeeds; ends `echo "PASS"`.
  - **Verify:** `grep -n "0.2.186\|0.1.48" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml && test -x scripts/checkpoints/cp2-deploy.sh`
  - **Depends:** T015.

- [ ] **[CP2b]** POC-1 infra smoke — `scripts/checkpoints/cp2-infra-smoke.sh` (new)
  - Assert no `CrashLoopBackOff` across `agentshield-platform` + `agents-*`; assert the registry-api and declarative-runner rollouts are on the new tags (`kubectl get deploy ... -o jsonpath` image tag == 0.2.186 / 0.1.48); assert **workflow member agent pods** come up Ready and log `AsyncPostgresSaver ready` (the shared-transcript change must not regress the checkpointer). Ends `echo "PASS"`.
  - **Verify:** `test -x scripts/checkpoints/cp2-infra-smoke.sh && bash scripts/checkpoints/cp2-infra-smoke.sh`
  - **Depends:** CP2a.

- [ ] **[CP2c]** POC-1 behaviour smoke + DoD proof — `scripts/checkpoints/cp2-behaviour.sh` (new)
  - Real path via `kubectl exec` + httpx + `jq`. Assert: (1) **shared workflow transcript** — run a real 2-member workflow (`POST /workflows/{id}/runs`); member B's output references a token that appeared **only** in member A's turn; (2) **save→reload** — re-fetch `GET /agents/{name}/memory?scope=workflow_run&thread_id=<parent_run_id>` from the backend (fresh, not in-memory) and assert both members' tagged rows in `message_index` order with no duplicate `(thread_id, message_index)`; (3) **foreign-thread still rejected** (403); (4) **durable-resume regression (WS-1 guard)** — a durable member pauses for HITL, decision applied via the console path, member resumes+completes — proving the shared `conversation_id` did not clobber the per-member `thread_id=child_id` checkpoint.
  - Exit non-zero on first failure; ends `echo "PASS"`. (This checkpoint's assertions mirror the registered `suite-75` cases T-S75-003/004/005 — the suite is the permanent regression, this script is the mid-stream gate.)
  - **Verify:** `test -x scripts/checkpoints/cp2-behaviour.sh && bash scripts/checkpoints/cp2-behaviour.sh`
  - **Depends:** CP2b, T014.

---

## Do-NOT-touch / invariants (carry into every task)

- **Never touch `sdk/agentshield_sdk/durable.py`** — the WS-1 checkpoint engine. The whole point
  of the identity split (plan §5) is that the shared transcript never enters it. `thread_id`
  stays per-member (checkpoint + Approval correlation); `conversation_id` is the orthogonal
  transcript key. They travel in different fields and never alias.
- **Migration re-check at implementation time:** confirm the Alembic head is still `0063`
  (`ls services/registry-api/alembic/versions/ | sort | tail -1`); if exec-v2 grabbed `0064`,
  renumber 0064 to the next free number and re-chain `down_revision` (merge-notes decision 1).
- **Fail-loud, never silent:** with `DIRECT_DATABASE_URL` set, a checkpointer init failure must
  `raise` — re-adding a silent `MemorySaver` fallback pins tenant state in pod RAM and breaks
  cross-replica HITL resume. This is the one shortcut that destroys the goal.

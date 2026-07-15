# Context Storage — POC-0 + POC-1 Implementation Plan

**Scope:** the FIRST vertical slice of `docs/design/context-storage-architecture.md` — **POC-0 (Functional foundation)** and **POC-1 (Shared workflow thread)**. Nothing else. No Knowledge Base/RAG, no user profile, no UX (POC-2), no Tighten items beyond S2/S4/S6 that are intrinsic to this slice.

**Source design:** `docs/design/context-storage-architecture.md` §4.1, §5, §6.3, §7 (S2/S4/S6), §11 (POC-0/POC-1).
**Merge constraints:** `docs/design/context-storage-vs-exec-v2-merge-notes.md` (migration sequencing; WS-1 durable-resume reconciliation).
**Constitution:** repo-root `CLAUDE.md` — Definition of Done + Post-Implementation Checklist.

> **Alignment Check:** The ultimate goal is cross-agent context sharing a user can rely on: an agent that remembers across turns/restarts, and workflow members that read one shared transcript. Every task below wires a thin UI-control→API→DB→read-back path and proves it; none degrade the goal to silence an error. The one place a shortcut would destroy the goal — silently falling back to an in-RAM checkpointer — is explicitly made fail-loud (T004).

---

## 1. Goal

Two provable end states:

1. **POC-0** — A deployed agent chat remembers turn N-1 **after a pod restart** (durable, Postgres-backed), scoped to the calling user, and a foreign `session_id`/`thread_id` is rejected at the edge.
2. **POC-1** — In a 2-member workflow, member B references content that appeared **only** in member A's turn (they share one transcript), and a supervisor re-reads a worker's output — without collapsing the per-pod governance model and **without breaking WS-1 durable resume**.

Both land behind the §4.1 `ConversationStore` port so the backend is a swappable detail.

---

## 2. Architecture

### 2.1 The one subsystem

`agent_memory` (Postgres) is the single transcript store for both tiers:
- **T1 conversation memory** — scope `agent`, keyed by the conversation identity (chat `session_id`).
- **T2 shared workflow thread** — scope `workflow_run`, keyed by the shared workflow conversation identity (the parent workflow `run_id`); the read **drops the `agent_name` filter** so members see each other.

One physical store, one `scope` parameter — not two code paths (design §3, §5.3).

### 2.2 Two identities, deliberately separated (the WS-1 reconciliation — see §5)

| Identity | Field name | Value (chat) | Value (workflow member) | Backs |
|---|---|---|---|---|
| **Checkpoint identity** | `thread_id` (existing, unchanged) | `session_id` | per-member (`uuid4` reactive / `child_id` durable) | LangGraph `AsyncPostgresSaver` config key + Approval correlation |
| **Conversation identity** | `conversation_id` (NEW) | `session_id` (defaults to `thread_id`) | shared workflow key = parent `run_id` | `agent_memory.thread_id` transcript key |

For a lone chat agent the two identities coincide (`conversation_id` defaults to `thread_id = session_id`) — the degenerate one-participant case. For a workflow member they diverge: the shared transcript is written under `conversation_id`, while the durable checkpoint stays per-member under `thread_id`. **The shared transcript never reuses the durable checkpoint key**, so per-member durable resume is untouched.

### 2.3 Data flow

```
POST /chat (chat.py)                     edge: thread_id = session_id
  ownership check: session→user           (reject foreign session)
  -> pod /chat/stream  body {message, thread_id=session_id,
                             conversation_id=session_id, scope='agent'}
     headers x-user-sub, x-agent-team, x-deployment-id
  -> runner: load transcript(conversation_id, scope, user_id, deployment_id)
             via GET /agents/{name}/memory  (ConversationStore.load)
     inject as prior messages; run graph (checkpoint keyed by thread_id)
     save turn(conversation_id, scope, user_id, deployment_id, agent_name)
             via POST /agents/{name}/memory (ConversationStore.append)

Workflow (workflow_orchestrator._run_step)   conversation_id = parent_run_id
  per member: thread_id stays per-member (checkpoint) ; conversation_id shared
  -> member pod loads scope='workflow_run' transcript (drops agent_name)
     writes back its turn tagged agent_name + workflow_run_id=parent_run_id
```

### 2.4 Storage port (§4.1)

`ConversationStore` (Protocol) with `append` / `load` / `erase`. Default adapter `PostgresConversationStore` over `agent_memory`. One construction choke point (`store_factory.get_conversation_store()` reading `CONVERSATION_STORE` env). The registry-api memory router and `memory.py` service depend on the interface; the adapter is the only place that touches `AgentMemory`/SQL for the transcript. This slice keeps the port minimal but real (design §4.1 "thin, backing the same POC backend, so the seam is real not aspirational").

---

## 3. Tech Stack

- **Backend:** FastAPI + SQLAlchemy async (`services/registry-api`), Alembic migrations.
- **Runner:** FastAPI (`services/declarative-runner`) + `agentshield_sdk` (LangGraph, `langgraph-checkpoint-postgres>=2.0`, `psycopg[asyncio]`).
- **Checkpointer:** `AsyncPostgresSaver` over a process-lifetime `psycopg_pool.AsyncConnectionPool` (see research.md §2).
- **Deploy:** deploy-controller injects pod env; Helm chart `charts/agentshield`.
- **E2E:** bash + `kubectl exec` httpx assertions (`scripts/e2e`).

---

## 4. Constitution Check (CLAUDE.md Definition of Done)

| DoD gate | How this plan satisfies it |
|---|---|
| **1. Real user journey proven** | `scripts/e2e/suite-75-context-storage.sh` deploys a **real** agent, drives real `/chat` + SSE across two turns, restarts the pod, re-chats and asserts recall; runs a **real** 2-member workflow via `POST /workflows/{id}/runs` and asserts B read A. No mocks/monkeypatch (memory rule "No Fakes in E2E"). No Studio surface changes in this slice (attribution UI is POC-2), so no Playwright/Vitest — recorded in the gap ledger. |
| **2. Save→reload→assert** | Suite T-S75-002 restarts the agent pod (`kubectl rollout restart` + wait) then re-fetches transcript from the **backend** (`GET /agents/{name}/memory`) and asserts turn N-1 survived. T-S75-004 re-fetches the workflow transcript from the backend after the run (not in-memory). |
| **3. No orphan code** | Every new symbol has a live caller; grep list in §8 (Execution Notes). New DB columns are read by `ConversationStore.load` (workflow read) + written by `append`; `conversation_id`/`scope` fields are read by the runner; `ConversationStore` is constructed at the `store_factory` choke point and injected into the memory router. |
| **4. Vertical slices** | T001-T009 wire the POC-0 chat path end-to-end and prove it (suite T-S75-001..003) **before** POC-1 (T010-T013) starts. |
| **5. Honest gap ledger** | §9 lists what's intentionally deferred (rationale/Haiku summarizer, S2 PII-scan-on-write, injection defense, attribution UI) vs debt. Also appended to the header of `docs/testing/manual-ui-e2e-test-plan.md`. |
| **6. Reason from running product** | Every path in this plan is grounded against the current code (file+line cited in tasks); §5 documents the AS-BUILT WS-1 checkpoint keying read from `durable.py`. |

Post-Implementation Checklist: e2e suite registered (T014); image bumps for registry-api + declarative-runner + deploy-controller in BOTH `deploy-cpe2e.sh` and `values.yaml` (T015); Python syntax + `configure_mappers()` check (T001/T002); experience doc — `docs/experience/playground.md` gets the memory-threading + shared-thread note (T009/T012).

---

## 5. WS-1 Durable-Resume Reconciliation (FIRST-CLASS CONCERN)

### 5.1 As-built (read from the code, not the design doc)

- `sdk/agentshield_sdk/durable.py::run_durable` / `resume_durable` build the LangGraph config as `{"configurable": {"thread_id": thread_id}}` (lines 287, 310). **`thread_id` is the single checkpoint-of-record key.** `resume_durable` re-enters the `AsyncPostgresSaver` checkpoint by that exact key.
- `services/registry-api/workflow_orchestrator.py::_run_step` (line 429) mints `thread_id = uuid.uuid4().hex` per member. For a **durable** member (lines 468-486) it overwrites `thread_id = child_id` (the member's own `AgentRun.id`), because the SDK creates its Approval with `thread_id=run_id` and the console resume (`approvals._resume_and_advance`, `resume_durable_member`) correlates by `child_id`. For a **reactive** member it passes the `uuid4` `thread_id` to the pod's `/chat` for Approval correlation.
- The member pod's `workflow_executor.run()` uses the received `thread_id` as its LangGraph checkpoint config key (line 741).

**Conclusion:** the per-member `thread_id` is load-bearing for (a) the LangGraph checkpoint, (b) Approval↔member correlation, and (c) the member's OTEL trace seed. If POC-1 made all members share ONE `thread_id`, their durable checkpoints would collide/overwrite and Approval correlation would break.

### 5.2 The reconciliation (concrete mechanism)

**Separate conversation identity from checkpoint identity.** Introduce a NEW dispatch field `conversation_id` that keys ONLY the `agent_memory` transcript. Leave `thread_id` — the LangGraph checkpoint + Approval-correlation key — exactly as WS-1 built it.

- `_run_step` keeps `thread_id = uuid4()` (reactive) / `child_id` (durable) **unchanged**. It additionally passes `conversation_id = parent_run_id` and `scope = 'workflow_run'` to the member (T010).
- The member pod uses `thread_id` for the LangGraph config key (unchanged) and `conversation_id` for `agent_memory` load/save (T012).
- Durable members: `run_durable`/`resume_durable` are **not touched**; they keep keying the checkpoint by the per-member `thread_id`. The shared transcript is an orthogonal HTTP read/write against `conversation_id`.

This is ports-and-adapters discipline applied to identity: the checkpoint is one concern (per-member, owned by WS-1), the conversation is another (shared, owned by context-storage). They travel in different fields and never alias.

### 5.3 Mandatory regression

Suite **T-S75-005** runs a durable workflow member that pauses for HITL, decides the approval via the console path (`POST /agents/{name}/deployments/.../` resume or `approvals` decide), and asserts the member **resumes and completes** under the shared-transcript change — proving per-member durable resume still keys off `thread_id=child_id` and the shared `conversation_id` did not clobber it.

---

## 6. File Structure

| File | New? | Task | POC | [P] |
|---|---|---|---|---|
| `services/registry-api/models.py` | edit | T001 | shared | |
| `services/registry-api/alembic/versions/0064_agent_memory_shared_thread.py` | **new** | T001 | shared | |
| `services/registry-api/memory.py` | edit | T002 | shared | |
| `services/registry-api/conversation_store.py` | **new** | T003 | shared | |
| `services/registry-api/store_factory.py` | **new** | T003 | shared | |
| `sdk/agentshield_sdk/checkpointer.py` | edit | T004 | POC-0 | [P] |
| `services/deploy-controller/manifest_builder.py` | edit | T005 | POC-0 | [P] |
| `charts/agentshield/charts/deploy-controller/templates/deployment.yaml` | edit | T005 | POC-0 | [P] |
| `services/registry-api/routers/chat.py` | edit | T006 | POC-0 | |
| `services/registry-api/schemas.py` | edit | T008 | POC-0 | |
| `services/registry-api/routers/playground.py` | edit | T007 | POC-0 | |
| `services/registry-api/routers/memory.py` | edit | T008 | POC-0 | |
| `services/declarative-runner/main.py` | edit | T009, T012 | POC-0/1 | |
| `services/declarative-runner/workflow_executor.py` | edit | T009 | POC-0 | |
| `services/registry-api/workflow_orchestrator.py` | edit | T010 | POC-1 | |
| `services/declarative-runner/orchestrator.py` | edit | T011 | POC-1 | |
| `docs/experience/playground.md` | edit | T012 | POC-1 | |
| `scripts/e2e/suite-75-context-storage.sh` | **new** | T014 | verify | |
| `scripts/e2e/run-all.sh` | edit | T014 | verify | |
| `scripts/deploy-cpe2e.sh` | edit | T015 | verify | |
| `charts/agentshield/values.yaml` | edit | T015 | verify | |
| `docs/testing/manual-ui-e2e-test-plan.md` | edit | T015 | verify | |

Every file above appears in a task; every task lists only files above.

---

## 7. Key Interfaces

Full signatures in `contracts/`. Summary:

- **`ConversationStore`** (`conversation_store.py`) — `contracts/conversation-store.md`:
  - `async def append(self, *, conversation_id: str, agent_name: str, team: str, turns: list[Turn], scope: Scope = "agent", user_id: str|None, deployment_id: str|None, workflow_run_id: str|None) -> list[AgentMemory]`
  - `async def load(self, *, conversation_id: str, scope: Scope = "agent", limit: int, agent_name: str|None, user_id: str|None, deployment_id: str|None) -> list[Turn]`
  - `async def erase(self, *, conversation_id: str|None = None, agent_name: str|None = None, user_id: str|None = None, deployment_id: str|None = None) -> int`
  - `Turn = {"role": str, "content": str, "agent_name": str|None, "message_kind": str}`; `Scope = Literal["agent","workflow_run"]`.
- **`get_conversation_store()`** (`store_factory.py`) — the single choke point; returns the configured adapter (default `PostgresConversationStore`).
- **Memory API** (`routers/memory.py`) — `contracts/memory-api.md`: `POST /agents/{name}/memory` (+ `scope`, `workflow_run_id`, per-message `message_kind`); `GET /agents/{name}/memory` (+ `scope`, `user_id`, `deployment_id` query params; `workflow_run` scope drops the agent_name filter).
- **Thread-ownership contract** — `contracts/thread-ownership.md`: at the chat/playground edge, a supplied `session_id` owned by a different `user_id` → HTTP 403.
- **`/chat/stream` dispatch body + SSE** — `contracts/chat-stream-memory.md`: body gains `conversation_id`, `scope`; headers gain `x-deployment-id`; `/chat/stream` loads+saves memory symmetric to `/chat`.

---

## 8. Tasks (dependency-ordered)

Acceptance criteria are concrete and testable. Verification commands assume repo root and a running dev cluster for deploy/e2e steps.

### Foundation (shared by POC-0 + POC-1)

#### T001 — `agent_memory` columns + migration 0064
**Files:** `services/registry-api/models.py`, `services/registry-api/alembic/versions/0064_agent_memory_shared_thread.py` (new)
**Do:**
- **Re-verify the head first:** `ls services/registry-api/alembic/versions/ | sort | tail -1` MUST be `0063_*`. If a higher number exists (exec-v2 landed first), set `down_revision` to the true head and renumber the file (merge-notes decision 1). Do NOT hardcode blindly.
- Add to `AgentMemory` (models.py, after `expires_at` ~L1805): `workflow_run_id: Mapped[uuid.UUID | None]` (`_UUID`, nullable); `scope: Mapped[str]` (`String(16)`, `nullable=False`, `server_default="agent"`); `message_kind: Mapped[str]` (`String(16)`, `nullable=False`, `server_default="agent_output"`). Add `CheckConstraint`s for `scope IN ('agent','workflow_run')` and `message_kind IN ('user','agent_output','rationale')`; add `Index("idx_agent_memory_thread_scope", "thread_id", "scope", "message_index")`; add `UniqueConstraint("thread_id", "message_index", name="uq_agent_memory_thread_msg")`.
- Migration 0064 DDL exactly as in `data-model.md` §3 — idempotent (`ADD COLUMN IF NOT EXISTS`, guarded constraint/index blocks), data-preserving. Include the **pre-flight de-dup guard** before adding the UNIQUE constraint (data-model.md §4).
**Acceptance:**
- `python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0064_agent_memory_shared_thread.py').read())"` OK.
- `revision = "0064"`, `down_revision = "0063"` (or the re-verified head).
- Mappers configure: `cd services/registry-api && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('ok')"`.
**Verify:** `grep -n "workflow_run_id\|scope\|message_kind\|uq_agent_memory_thread_msg" services/registry-api/models.py`

#### T002 — Atomic `message_index` allocation + scoped save/load (S4)
**Files:** `services/registry-api/memory.py`
**Do:**
- In `save_turn`, replace the unlocked `SELECT max(message_index)` (lines 64-71) with an atomic allocation: acquire a transaction-scoped Postgres advisory lock keyed on the conversation, then compute `max+1`, then insert. Allocation is now **per-conversation** (`thread_id`), NOT per-`(agent_name, thread_id)` — drop `agent_name` from the max predicate so concurrent members on a shared `thread_id` get monotonic indices. Exact SQL in `data-model.md` §5.
- Extend `save_turn` signature with `scope: str = "agent"`, `workflow_run_id: str | None = None`, and accept a per-message `message_kind` (fallback: `user`→`user`, else `agent_output`). Persist the new columns.
- Add a `load_context` variant/param `scope: str = "agent"` and `user_id: str | None = None`: for `scope == "workflow_run"` the Postgres query filters by `thread_id` + `scope` and **omits the `agent_name` predicate** (cross-agent read), ordered by `message_index`; for `scope == "agent"` keep the existing `(agent_name, thread_id)` filter but ALSO constrain `user_id` when provided. Return rows must carry `agent_name` + `message_kind` for the workflow read. **Skip Redis for the `workflow_run` scope** (the Redis key is agent-scoped; cross-agent reads must hit Postgres) — document why in a code comment.
**Acceptance:**
- Concurrency test in suite T-S75-004 shows no duplicate `(thread_id, message_index)` after two members write the same conversation.
- `python3 -c "import ast; ast.parse(open('services/registry-api/memory.py').read())"` OK.
**Verify:** `grep -n "pg_advisory_xact_lock\|scope\|workflow_run_id\|message_kind" services/registry-api/memory.py`
**Depends:** T001.

#### T003 — `ConversationStore` port + adapter + factory
**Files:** `services/registry-api/conversation_store.py` (new), `services/registry-api/store_factory.py` (new)
**Do:**
- `conversation_store.py`: define `Scope`, `Turn` typed dict, the `ConversationStore` `Protocol` (append/load/erase per contract), and `PostgresConversationStore` — the adapter delegates to the `memory.py` service functions (T002). The adapter is the ONLY place transcript SQL/`AgentMemory` access lives for reads/writes going forward.
- `store_factory.py`: `get_conversation_store() -> ConversationStore` reading `os.getenv("CONVERSATION_STORE", "postgres")`; default → `PostgresConversationStore`. Single choke point. No other module constructs an adapter.
**Acceptance:**
- `cd services/registry-api && python3 -c "from store_factory import get_conversation_store; s=get_conversation_store(); print(type(s).__name__)"` prints `PostgresConversationStore`.
- No `AgentMemory` import in `routers/memory.py` after T008 except through the store (grep check in §8 Notes).
**Verify:** `grep -rn "get_conversation_store" services/registry-api`
**Depends:** T002.

### POC-0 — Functional foundation

#### T004 — Fail-loud + correct persistent `AsyncPostgresSaver` (§6.3)  [P]
**Files:** `sdk/agentshield_sdk/checkpointer.py`
**Do:**
- **Correct the construction.** `AsyncPostgresSaver.from_conn_string` is an `@asynccontextmanager` (verified against the installed `langgraph-checkpoint-postgres` — it yields a saver, it does not return one), so the current `from_conn_string(url)` + `.setup()` never produces a live saver → today it always hits `except` → `MemorySaver`. Build a process-lifetime saver over an explicitly-opened pool instead (exact code in `research.md` §2): strip the `+asyncpg` SQLAlchemy suffix, open a module-global `AsyncConnectionPool` (kept alive for the pod lifetime), `AsyncPostgresSaver(pool)`, `await saver.setup()`.
- **Fail loud.** Keep `MemorySaver` ONLY for the genuinely-unset local-dev branch (`DIRECT_DATABASE_URL` empty → log INFO + return `MemorySaver`). When the URL IS set but construction fails, `logger.error(...)` and **`raise`** (RuntimeError) — never silently return `MemorySaver`. Silent fallback pins tenant state in pod RAM and breaks HITL resume across replicas (§6.3).
**Acceptance:**
- With `DIRECT_DATABASE_URL` unset: returns `MemorySaver` (dev unaffected).
- With a bad URL set: raises (does not return `MemorySaver`). Unit-assertable via `sdk/tests`.
- After T005 deploy, `kubectl logs <agent-pod>` shows `checkpointer=AsyncPostgresSaver` (not `MemorySaver`) and the pod is Ready.
**Verify:** `grep -n "raise\|MemorySaver\|AsyncConnectionPool\|from_conn_string" sdk/agentshield_sdk/checkpointer.py`
**Depends:** none (but its runtime effect is only observable once T005 injects the env).

#### T005 — Inject `DIRECT_DATABASE_URL` + `AGENTSHIELD_DEPLOYMENT_ID` into agent pods (§6.3)  [P]
**Files:** `services/deploy-controller/manifest_builder.py`, `charts/agentshield/charts/deploy-controller/templates/deployment.yaml`
**Do:**
- In `build_deployment` (manifest_builder.py, in the env-append region ~L221-246, mirroring the `LANGFUSE_HOST` pattern at L236 which reads `os.environ.get`): if `os.environ.get("DIRECT_DATABASE_URL")` is set, append `V1EnvVar(name="DIRECT_DATABASE_URL", value=<that>)`. Also append `V1EnvVar(name="AGENTSHIELD_DEPLOYMENT_ID", value=str(deployment.get("id","")))` so the runner can scope memory by deployment.
- In the deploy-controller chart deployment.yaml (after the `DATABASE_URL` block ~L51-55), add a `DIRECT_DATABASE_URL` env `valueFrom.secretKeyRef` → `name: postgres-passwords, key: registry-api-direct-url` (the same secret registry-api reads; the controller runs in the platform namespace where it exists). This makes the value available to `os.environ` in the controller for pass-through injection.
**Acceptance:**
- `kubectl exec <deploy-controller-pod> -- printenv DIRECT_DATABASE_URL` non-empty.
- A freshly deployed agent pod has `DIRECT_DATABASE_URL` + `AGENTSHIELD_DEPLOYMENT_ID` in `printenv`.
**Verify:** `grep -n "DIRECT_DATABASE_URL\|AGENTSHIELD_DEPLOYMENT_ID" services/deploy-controller/manifest_builder.py`
**Rationale/deferral:** plain-value injection of the DB URL into pod env matches the existing `registry_api_url`/`LANGFUSE_*` pattern; hardening it into a per-namespace secret is S10/S11 (Tighten) — recorded in the gap ledger.

#### T006 — Chat uses `session_id` as `thread_id`; propagate identity; ownership check (§5.1, §6.3, S6)
**Files:** `services/registry-api/routers/chat.py`
**Do:**
- `_proxy_agent_stream` (chat.py L343): add params `conversation_id`, `user_id`, `user_team`, `deployment_id`; change the pod body (L373) from `{"message", "thread_id": run_id}` to `{"message", "thread_id": session_id, "conversation_id": session_id, "scope": "agent"}` and set headers `x-user-sub`, `x-agent-team`, `x-deployment-id`. **`thread_id` becomes `session_id`** — this is the fix for "nothing threads across turns".
- Update both callers (`stream_chat` L720-724, `stream_deployment_chat` L902-906) to pass `run.session_id`, `run.user_id`, the resolved team, and the deployment id.
- **Ownership check (S6, fail-closed):** in `start_chat` and `start_deployment_chat`, when `body.session_id` is supplied, reject with 403 if that `session_id` is already owned by a different user. Implementation + query in `contracts/thread-ownership.md`. If identity is absent/ambiguous, do NOT bind to a shared session — mint a fresh `session_id` (most-restrictive default, S6).
- No schema change in this task; the dispatch body is built inline. (Memory-API schema edits are in T008.)
**Acceptance:**
- Two turns in one `session_id` produce a threaded transcript (turn 2 sees turn 1) — asserted by suite T-S75-001.
- A `session_id` owned by user A, replayed by user B → 403 (T-S75-003).
**Verify:** `grep -n "conversation_id\|x-deployment-id\|session_id" services/registry-api/routers/chat.py`
**Depends:** none structurally; pairs with T009 (runner honoring the body).

#### T007 — Playground reactive chat uses `session_id` as `thread_id`
**Files:** `services/registry-api/routers/playground.py`
**Do:**
- `stream_playground_run` (L689): replace `thread_id = run_id  # traceability` with: for the **reactive** shape (chat), `thread_id = run.session_id or run_id`; for durable/non-chat entrypoints keep `run_id` (per §5.1: non-chat → per-run). Pass `conversation_id = thread_id`, `scope="agent"` and the existing `user_id`/`caller_team` through `_real_agent_stream`.
- `_real_agent_stream` (L469): add `conversation_id: str` + `deployment_id: str = ""` params; put `conversation_id` + `scope` in the body (L495) and set `x-deployment-id` header (alongside the existing `x-user-sub`/`x-agent-team`).
**Acceptance:** a playground chat with a stable `session_id` threads across turns (asserted opportunistically in T-S75-001 if a sandbox pod is available; primary proof is the production chat path).
**Verify:** `grep -n "conversation_id\|session_id\|scope" services/registry-api/routers/playground.py`
**Depends:** none structurally.

#### T008 — Memory router through `ConversationStore`; user scoping; workflow-scope read
**Files:** `services/registry-api/routers/memory.py`, `services/registry-api/schemas.py`
**Do:**
- schemas.py: add `scope: str = "agent"` and `workflow_run_id: str | None = None` to `MemorySaveTurnRequest`; add optional `message_kind: str | None = None` to `MemoryMessage`; add `agent_name`, `message_kind`, `scope` to `AgentMemoryResponse`.
- `save_turn` endpoint: route through `get_conversation_store().append(...)`, forwarding `scope`, `workflow_run_id`, per-message `message_kind`. (Keep the `memory_enabled` guard.)
- `list_memory` endpoint: add query params `scope: str = "agent"`, `user_id: str | None = None`, `deployment_id` (already present). Route through `get_conversation_store().load(...)`. For `scope="workflow_run"` the store drops the `agent_name` filter; for `scope="agent"` it constrains `user_id` when provided. Order by `message_index` (not `created_at`) so the transcript is deterministic.
- Delete endpoints: route through `store.erase(...)`.
**Acceptance:**
- `GET /agents/{name}/memory?scope=workflow_run&thread_id=<conv>` returns all authors' rows ordered by index (proven by T-S75-004).
- `grep -n "AgentMemory" services/registry-api/routers/memory.py` shows no direct model use in handlers (all via the store) — DoD #3 no-shared-helper-without-context.
**Verify:** `grep -n "get_conversation_store\|scope\|workflow_run" services/registry-api/routers/memory.py`
**Depends:** T003.

#### T009 — Runner: wire memory on `/chat/stream`; propagate `user_id`+`deployment_id`; inject history
**Files:** `services/declarative-runner/main.py`, `services/declarative-runner/workflow_executor.py`
**Do:**
- `_load_memory_context` (main.py L388): add params `conversation_id`, `scope`, `user_id`, `deployment_id`; call `GET /agents/{name}/memory` with `thread_id=conversation_id`, `scope`, `user_id`, `deployment_id`, `limit`. Return rows carrying `role`/`content` (+ `agent_name` when workflow scope — used by T012 for attribution prefixing).
- `_save_memory_turn` (main.py L406): add `conversation_id`, `scope`, `deployment_id`, `agent_name`, `message_kind` params; POST them through to the memory API.
- `ChatRequest` (main.py L185): add `conversation_id: str | None = None`, `scope: str = "agent"`. When absent, `conversation_id` defaults to `thread_id` (single-agent case).
- `/chat` handler (L427): pass `conversation_id`, `scope`, `deployment_id` (from `os.getenv("AGENTSHIELD_DEPLOYMENT_ID")`), `user_id` into load/save.
- **`/chat/stream` handler (L474): the core POC-0 fix** — today it neither loads nor saves memory. Load `memory_context` before streaming (via `_load_memory_context`), pass it into `run_streamed`, accumulate the streamed `text_delta` content, and after the stream call `_save_memory_turn` (fire-and-forget, symmetric to `/chat`). Capture the reset token from `_current_user_context.set(...)` and reset it in a `finally` (§6.3 leak fix).
- `workflow_executor.run_streamed` (L769): add `memory_context: list[dict] | None = None` param and inject it as prior `HumanMessage`/`AIMessage` history into `state["messages"]` (mirror `run()` L735-745). The LangGraph checkpoint key stays `thread_id` (unchanged).
**Acceptance:**
- `/chat/stream` persists a turn: after a streamed chat, `GET /agents/{name}/memory?thread_id=<session>` shows the user+assistant rows (T-S75-001).
- After pod restart, a new streamed turn recalls the prior fact (T-S75-002).
**Verify:** `grep -n "conversation_id\|memory_context\|_save_memory_turn\|_current_user_context" services/declarative-runner/main.py`
**Depends:** T006 (body/header contract), T008 (memory API shape).

### POC-1 — Shared workflow thread

#### T010 — Orchestrator passes ONE shared conversation key to every member (WS-1-safe)
**Files:** `services/registry-api/workflow_orchestrator.py`
**Do:**
- `_dispatch` (L70): add `conversation_id: str | None`, `scope: str = "agent"` params; put them in the pod body (alongside the existing `thread_id`). **Do not change `thread_id`.**
- `_run_step` (L418): keep `thread_id = uuid4()` / `child_id` **exactly as-is** (WS-1). Compute the shared `conversation_id = parent_run_id` and `scope = "workflow_run"`, and pass them on both dispatch branches (`_dispatch` L488 and — for durable members — via the durable dispatch body; see T012 for the runner reading them). Pass `workflow_run_id = parent_run_id` so the write-back tags the row.
- Replace string-passing: the members still receive the current step input as `message`, but the SHARED TRANSCRIPT is what carries cross-member context (loaded by each member in T012). Add a code comment pointing to §5.2 of this plan for the identity split.
**Acceptance:**
- A 2-member sequential workflow: member B's pod loads a transcript that already contains member A's tagged turn (proven by T-S75-004).
- Durable member still resumes (T-S75-005) — `thread_id`/checkpoint untouched.
**Verify:** `grep -n "conversation_id\|workflow_run_id\|uuid.uuid4().hex\|parent_run_id" services/registry-api/workflow_orchestrator.py`
**Depends:** T009 (runner reads `conversation_id`/`scope`), T006.

#### T011 — declarative-runner `orchestrator.py` shares the conversation key
**Files:** `services/declarative-runner/orchestrator.py`
**Do:**
- `_dispatch_agent` (L58): add the shared conversation key + scope to the member `/chat` body (`{"message", "conversation_id": <shared>, "scope": "workflow_run", "workflow_run_id": <parent>}`); today it posts only `{"message": input_msg}` (L63). The shared key = `self.parent_run_id`.
- `run_sequential` (L72): keep threading `current` as the step message, but the cross-member context now flows via the shared transcript. Add a comment noting the string-pass is now a fallback, not the sharing mechanism.
**Acceptance:** the composite-workflow path (this orchestrator) also produces a shared transcript; asserted if the deployed workflow uses this path.
**Verify:** `grep -n "conversation_id\|workflow_run_id\|parent_run_id" services/declarative-runner/orchestrator.py`
**Depends:** T009, T012.

#### T012 — Member loads the shared transcript (drops agent_name) + writes back tagged
**Files:** `services/declarative-runner/main.py`, `docs/experience/playground.md`
**Do:**
- In `/chat` and `/chat/stream` (main.py), when `req.scope == "workflow_run"`: load via `_load_memory_context(conversation_id=req.conversation_id, scope="workflow_run", ...)` — this drops the agent_name filter so member B sees member A. Inject the loaded turns as prior messages; when a turn carries an `agent_name` different from this pod's, prefix its content with `[<agent_name>]: ` so the model can attribute peers (minimal, no schema change to the graph state).
- After running, write back the member's turn via `_save_memory_turn(conversation_id=req.conversation_id, scope="workflow_run", workflow_run_id=req.workflow_run_id, agent_name=cfg.AGENT_NAME, message_kind="agent_output", ...)`. `ChatRequest` gains `workflow_run_id: str | None = None`.
- `docs/experience/playground.md`: add a short subsection describing memory-threading on chat + the shared workflow transcript (Post-Implementation Checklist §3 requires it for `main.py`/playground changes).
**Acceptance:**
- T-S75-004: member B's output references a token that only appears in member A's turn.
- Backend re-fetch (`GET ...?scope=workflow_run`) shows both members' tagged rows in index order.
**Verify:** `grep -n "workflow_run\|conversation_id\|agent_name" services/declarative-runner/main.py`
**Depends:** T009, T010.

### Verification & ship

#### T014 — E2E suite + registration (DoD #1, #2)
**Files:** `scripts/e2e/suite-75-context-storage.sh` (new), `scripts/e2e/run-all.sh`
**Do:** author suite 75 following the `suite-25-memory.sh` pattern (kubectl exec into the registry-api pod; httpx assertions), driving the **real** path (deploy a real agent, real chat, real workflow — no fakes). Test cases:
- **T-S75-001** chat memory persists across turns (two `/chat` turns, same `session_id`; assert turn 2 recalls turn 1; assert `GET memory` shows the rows in index order).
- **T-S75-002** save→reload→assert: `kubectl rollout restart` the agent deployment, wait Ready, chat again, assert recall (Postgres checkpointer + transcript survived pod restart).
- **T-S75-003** foreign-thread rejection: user B replays user A's `session_id` → 403.
- **T-S75-004** shared workflow thread: run a real 2-member workflow via `POST /workflows/{id}/runs`; assert member B references A's content; re-fetch `?scope=workflow_run` and assert both tagged turns, no duplicate `(thread_id, message_index)`.
- **T-S75-005** durable-resume regression: a durable member pauses for HITL, decision applied, member resumes+completes under the shared-transcript change.
Register in `run-all.sh`: `run_suite "Suite 75: Context Storage (POC-0/1)" "suite-75-context-storage.sh"`.
**Acceptance:** `bash scripts/e2e/suite-75-context-storage.sh` → all PASS; suite appears in `run-all.sh`.
**Verify:** `grep -n "suite-75" scripts/e2e/run-all.sh && test -x scripts/e2e/suite-75-context-storage.sh`
**Depends:** T001-T012.

#### T015 — Image bumps + deploy + gap ledger (Checklist #2)
**Files:** `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`, `docs/testing/manual-ui-e2e-test-plan.md`
**Do:**
- Bump patch tags in `deploy-cpe2e.sh` AND mirror in `values.yaml` (same commit): `REGISTRY_API_TAG` 0.2.184→0.2.185 (values L590); `DECLARATIVE_RUNNER_TAG` 0.1.46→0.1.47 (`declarativeRunnerTag` values L661); `DEPLOY_CONTROLLER_TAG` 0.1.36→0.1.37 (values L652). Update the `deploy-cpe2e.sh` header comment describing this slice.
- Build+deploy: `bash scripts/deploy-cpe2e.sh` (per the "Deploy Script Only" rule — building+deploying is part of the change).
- Append the gap-ledger entries (§9) to the header of `docs/testing/manual-ui-e2e-test-plan.md`.
**Acceptance:** `helm`/deploy succeeds; the three services roll to the new tags; agent pods pick up `DIRECT_DATABASE_URL`.
**Verify:** `grep -n "0.2.185\|0.1.47\|0.1.37" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml`
**Depends:** T001-T014.

---

## 9. Execution Notes

- **Parallelizable [P]:** T004 (SDK), T005 (deploy-controller + chart) touch disjoint files from the registry-api foundation (T001-T003) and can run in parallel with them. The registry-api edits T006/T007/T008 touch different files from each other but all depend on the foundation; T006 and T007 are mutually [P]. The runner edits T009→T012 are sequential (same files). T010 depends on T009's contract.
- **Critical path:** T001 → T002 → T003 → T008 → T009 → T010/T011/T012 → T014 → T015. (T004, T005, T006, T007 hang off the side and must all land before T014.)
- **Orphan-grep gate (run before reporting done):**
  - `grep -rn "get_conversation_store" services/registry-api` → constructed in `store_factory`, called in `routers/memory.py`.
  - `grep -rn "conversation_id" services/registry-api services/declarative-runner` → set in chat/playground/orchestrator, read in runner.
  - `grep -rn "scope=\"workflow_run\"\|scope='workflow_run'" services` → produced (orchestrator) AND consumed (runner load drops agent_name).
  - `grep -rn "workflow_run_id" services/registry-api` → written (append) AND filterable (load).
  - `grep -rn "AGENTSHIELD_DEPLOYMENT_ID" services` → injected (manifest_builder) AND read (runner).
- **Migration re-check:** confirm head is `0063` at implementation time (`alembic heads` or the versions dir); if exec-v2 grabbed `0064`, renumber to the next free number and re-chain (merge-notes decision 1).
- **Do NOT touch** `sdk/agentshield_sdk/durable.py` (WS-1 checkpoint engine) — the whole point of §5 is that the shared transcript never enters it.

## 10. Deferred / Gap Ledger (honest, DoD #5)

- **Rationale/Haiku summarizer (design §5.2)** — the task scope for POC-1 asks each member to "write back its turn tagged with agent_name"; it does NOT include the Haiku rationale summarizer. This slice writes back `{query-context, verbatim output}` tagged `agent_output`; the distilled rationale + Haiku call + fallback-to-output-only is **deferred (intentional)** to a follow-up POC-1b. The `message_kind='rationale'` enum value ships now (unused writer) so the schema is ready.
- **S2 — memory-write PII scan/tokenize** — deferred (intentional) to Tighten T-A. This slice runs on synthetic/non-sensitive data only (design §11 POC exit gate). `agent_memory` persists raw content today.
- **S1 injection defense, S8 erasure-spanning-checkpoints, S9 access audit, S10/S11 encryption/mesh** — Tighten track; not in this slice.
- **Attribution UI (POC-2)** — no Studio change here; peer attribution is a `[<agent_name>]:` content prefix, not a UI element. No Playwright/Vitest in this slice — recorded as **deferred (intentional)**, lands in POC-2.
- **Per-agent context slicing** — every member reads the whole shared transcript (design §13); the `scope` read-parameter is the seam for future slicing.

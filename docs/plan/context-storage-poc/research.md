# Research & Decisions — Context Storage POC-0/1

Grounded decisions for the ambiguous/consequential parts of the slice. Each states the options considered, the choice, and why.

---

## 1. `message_index` atomicity (S4)

**Problem (as-built).** `memory.py::save_turn` (lines 64-71) does `SELECT max(message_index)` filtered by `(agent_name, thread_id)`, then inserts `max+i+1`. Two concurrent writers to the same thread read the same `max` and write duplicate/mis-ordered indices. The shared workflow thread (POC-1) makes this a live corruption path: supervisor/parallel members write the same `thread_id` concurrently. Also, once the transcript is shared, allocation must be **per-thread**, not per-`(agent, thread)`, or two agents on one conversation collide.

**Options.**
1. `UNIQUE(thread_id, message_index)` + `INSERT ... ON CONFLICT DO NOTHING` + retry loop.
2. `SELECT ... FOR UPDATE` on the thread's rows.
3. Per-thread Postgres sequence.
4. **Transaction-scoped advisory lock** (`pg_advisory_xact_lock`) keyed on the conversation, then `max+1`, then insert — lock auto-released at commit.

**Decision: (4) advisory lock as the allocator, PLUS (1) `UNIQUE(thread_id, message_index)` as a correctness backstop.**

Why:
- The advisory lock **serializes writers per-conversation** with zero retry storms and no schema dependency (`SELECT max ... FOR UPDATE` cannot lock a not-yet-existing gap row; a sequence proliferates one object per thread and can't be reset/erased cleanly for GDPR). Transaction-scoped means it's released exactly at `COMMIT`/`ROLLBACK` — no leak on failure.
- The `UNIQUE` constraint makes the illegal state (duplicate index) **unrepresentable** even if a future writer forgets the lock (CLAUDE.md "make illegal states unrepresentable, not guard with ifs"). It's defense-in-depth, not the primary mechanism.

Allocation SQL (in `save_turn`, all inside the existing transaction):
```python
# Serialize index allocation for this conversation. hashtextextended → bigint key;
# transaction-scoped lock is released on commit/rollback (no manual unlock).
await db.execute(
    text("SELECT pg_advisory_xact_lock(hashtextextended(:tid, 0))"),
    {"tid": thread_id},
)
max_idx = (await db.execute(
    select(func.max(AgentMemory.message_index)).where(AgentMemory.thread_id == thread_id)
)).scalar() or 0
# ... insert rows at max_idx + i + 1 ...
```
Note the `where` drops `agent_name` — allocation is now per-conversation.

**Existing-data caveat for the UNIQUE constraint.** Historically `thread_id = run_id` (unique per turn) and each thread had one agent, so `(thread_id, message_index)` is already effectively unique — but the migration must not fail on any stray duplicate. Migration 0064 runs a pre-flight guard that renumbers any duplicate rows within a `thread_id` before adding the constraint (data-model.md §4). Idempotent + data-preserving.

---

## 2. Conversation-identity vs checkpoint-identity reconciliation (WS-1)

**Problem.** POC-1 wants ONE shared key across all workflow members. WS-1 (`sdk/agentshield_sdk/durable.py`) made the LangGraph `AsyncPostgresSaver` checkpoint keyed by `{"configurable": {"thread_id": thread_id}}` the single checkpoint-of-record; `workflow_orchestrator._run_step` sets that `thread_id` **per member** (`uuid4` reactive, `child_id` durable) and the SDK correlates its HITL Approval by the same `thread_id`. One shared `thread_id` would collide per-member checkpoints and break Approval correlation.

**Decision: two fields, never aliased.**
- **`thread_id`** (existing) = **checkpoint identity + Approval correlation**. Unchanged from WS-1 — per member. `durable.py` is not touched.
- **`conversation_id`** (new) = **transcript identity** for `agent_memory` only. Shared across members of a workflow run (= parent `run_id`). For a lone chat agent, `conversation_id` defaults to `thread_id` (= `session_id`) — the degenerate one-participant case, where the two identities legitimately coincide because there is no checkpoint collision to worry about.

Why this is the architecturally-correct fix (not a bandaid):
- It removes an **implicit overload** — today `thread_id` silently means "checkpoint AND (for chat) conversation." We make the two concerns explicit, separately named fields (CLAUDE.md: "explicit parameters over implicit behavior").
- The checkpoint stays owned by WS-1; the transcript is an orthogonal HTTP read/write. The two subsystems compose without either reaching into the other. Durable resume keeps re-entering by `thread_id=child_id`; the shared transcript keeps keying by `conversation_id=parent_run_id`. No overwrite is possible because they are different values in different columns/keys.
- A durable-resume regression test (suite T-S75-005) proves the composition.

**Rejected alternative:** namespacing one shared `thread_id` with a per-member suffix for the checkpoint (e.g. `thread_id + ":" + agent`). That keeps the overload and forces every WS-1 call site to know about the split — more surface, more drift risk. Separate fields is cleaner.

---

## 3. `ConversationStore` interface shape (§4.1)

**Decision.** A narrow `Protocol` with three methods matching the design's port, adapted to the write-a-turn (not a single message) reality of `save_turn`:

```python
Scope = Literal["agent", "workflow_run"]
class Turn(TypedDict):
    role: str                 # user | assistant | system | tool
    content: str
    agent_name: NotRequired[str | None]
    message_kind: NotRequired[str]   # user | agent_output | rationale

class ConversationStore(Protocol):
    async def append(self, *, conversation_id, agent_name, team, turns, scope="agent",
                     user_id=None, deployment_id=None, workflow_run_id=None) -> list[AgentMemory]: ...
    async def load(self, *, conversation_id, scope="agent", limit,
                   agent_name=None, user_id=None, deployment_id=None) -> list[Turn]: ...
    async def erase(self, *, conversation_id=None, agent_name=None,
                    user_id=None, deployment_id=None) -> int: ...
```

Rationale:
- **Minimal but real** (design §4.1): only what POC-0/1 uses — append/load/erase — but backed by the actual Postgres adapter, so the seam ships from day one instead of being retrofitted.
- **Security invariants live in the port contract, not the adapter** (§4.1 rule): `scope` and `user_id` are required-by-contract parameters of `load`, so no adapter can forget them. The default adapter honors "workflow_run drops agent_name; agent constrains user_id."
- **One choke point:** `store_factory.get_conversation_store()` reading `CONVERSATION_STORE` (default `postgres`). Callers depend on the `Protocol`, never on `AgentMemory` directly.
- The adapter **delegates to the existing `memory.py` service functions** rather than re-implementing SQL — smallest change that still routes all transcript access through the port. `memory.py` remains the SQL layer; `conversation_store.py` is the interface the router binds to.

---

## 4. `DIRECT_DATABASE_URL` injection approach

**Problem.** Agent pods run in `agents-{team}` namespaces; the `postgres-passwords` secret lives in the platform namespace, so a `secretKeyRef` from an agent pod won't resolve. The pod needs the direct (`+asyncpg`, PgBouncer-bypassing) URL for `AsyncPostgresSaver`.

**Decision.** Two hops, mirroring the existing `LANGFUSE_HOST` pass-through:
1. The deploy-controller gets `DIRECT_DATABASE_URL` in its own env via `secretKeyRef → postgres-passwords/registry-api-direct-url` (it runs in the platform namespace where that secret exists — same source registry-api uses).
2. `manifest_builder.build_deployment` reads `os.environ.get("DIRECT_DATABASE_URL")` and injects it as a **plain-value** `V1EnvVar` into each agent pod (exactly how `LANGFUSE_HOST`/`registry_api_url` are already passed).

Why plain-value (not a per-namespace secret) for the POC: it matches the established pattern for controller-injected config, needs no new secret-replication machinery, and the DB URL is no more exposed than the LLM/tool secrets already mounted. Hardening it into a per-agent-namespace secret is **S10/S11 (Tighten)** — recorded in the gap ledger. Also inject `AGENTSHIELD_DEPLOYMENT_ID` (from `deployment["id"]`) so the runner can scope memory reads/writes by deployment.

The URL is `postgresql+asyncpg://...`; the checkpointer strips `+asyncpg` for psycopg (the readiness probe in `main.py` L313 already does the same `.replace("+asyncpg","")`).

---

## 5. Fail-loud checkpointer + correct construction

**Two problems, one file (`checkpointer.py`).**

1. **Silent fallback (the design's target).** Today any `AsyncPostgresSaver` init failure returns `MemorySaver`, pinning tenant state in pod RAM and breaking cross-replica HITL resume. Fix: return `MemorySaver` ONLY when `DIRECT_DATABASE_URL` is unset (local dev). When it IS set but init fails → `logger.error` + `raise RuntimeError`. Fail loud.

2. **Latent construction bug exposed by the fix.** `AsyncPostgresSaver.from_conn_string` is an `@asynccontextmanager` (verified: `inspect.getsource` shows `@asynccontextmanager async def from_conn_string(...) -> AsyncIterator[AsyncPostgresSaver]`). The current code does `saver = AsyncPostgresSaver.from_conn_string(url); await saver.setup()` — `saver` is a context-manager object, `.setup()` raises `AttributeError`, and today that's swallowed into the `MemorySaver` fallback. This is exactly why the design says "pods fall back to in-memory MemorySaver." The moment we (a) inject the URL and (b) make it fail-loud, the pod would crash-loop unless we also build the saver correctly.

**Decision.** Build a **process-lifetime** saver over an explicitly-opened pool (the langgraph pattern for a long-lived, non-`async with` saver), keeping a module-global reference so the pool is not GC'd for the pod's life:
```python
import os, logging
from psycopg_pool import AsyncConnectionPool
logger = logging.getLogger(__name__)
_pool = None  # module-global: keep the pool open for the pod lifetime

async def get_checkpointer():
    url = os.getenv("DIRECT_DATABASE_URL", "")
    if not url:
        logger.info("DIRECT_DATABASE_URL not set — using in-memory checkpointer (local dev)")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    global _pool
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        conninfo = url.replace("+asyncpg", "")            # psycopg wants a plain URL
        _pool = AsyncConnectionPool(
            conninfo=conninfo, max_size=10, open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": None},
        )
        await _pool.open()
        saver = AsyncPostgresSaver(_pool)
        await saver.setup()
        logger.info("AsyncPostgresSaver ready (pool-backed, pod-lifetime)")
        return saver
    except Exception as exc:
        logger.error("AsyncPostgresSaver init FAILED with DIRECT_DATABASE_URL set: %s", exc)
        raise RuntimeError(f"checkpointer init failed (fail-loud, no MemorySaver fallback): {exc}") from exc
```
Notes: `autocommit=True` + `prepare_threshold=0` are the langgraph-postgres requirements for the async saver over a pool; `open=False` then `await _pool.open()` avoids the "opening the async pool in the constructor" deprecation. `row_factory=None` keeps psycopg's default (langgraph sets its own on the connection). The implementer should confirm these kwargs against the pinned `langgraph-checkpoint-postgres` version and adjust if `setup()` complains — the load-bearing decisions are (a) pool-backed persistent saver and (b) fail-loud, not the exact kwargs.

**Verification hook:** after deploy, `kubectl logs <agent-pod> | grep checkpointer` must show `AsyncPostgresSaver ready`, and `/ready` must report `postgres: ok` (main.py L308-320). If it shows `MemorySaver` with the URL set, the injection (T005) didn't land.

---

## 6. Entrypoint → identity mapping (§5.1, S6)

- **Chat** (`routers/chat.py`, carries `session_id`) → `thread_id = session_id`, `conversation_id = session_id`, `scope='agent'`.
- **Playground reactive chat** → same, using `run.session_id` (fallback `run_id`).
- **Workflow member** (orchestrator) → `thread_id` per member (unchanged), `conversation_id = parent_run_id`, `scope='workflow_run'`, `workflow_run_id = parent_run_id`.
- **Non-chat entrypoints** (internal/scheduled/webhook/eval/`POST /workflows/{id}/runs` at the top level) → `conversation_id = run_id` (fresh; per-run), `scope='agent'` for a lone durable run.
- **Fail-closed (S6):** if the caller's identity is absent/ambiguous at the edge, do NOT bind to a supplied `session_id` — mint a fresh one (most-restrictive: session-ephemeral, no shared/cross-user read). The ownership check (contracts/thread-ownership.md) is the enforcement point.

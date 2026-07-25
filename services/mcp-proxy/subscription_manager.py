"""
list_changed subscription manager (WS-B / FR-MCP-07).

For every registered MCP server that advertised `tools.listChanged=true`, the proxy
holds ONE long-lived MCP session with a notification handler. When the upstream emits
`notifications/tools/list_changed`, the handler debounces a burst into a single
NetworkPolicy-trusted `POST {REGISTRY_API_URL}/api/v1/internal/mcp/list-changed
{"server_id": ...}` — exactly how authz.py already pokes registry-api. registry-api
owns the DB write (it re-runs the shared `_materialize_and_discover`); THE PROXY WRITES
NOTHING to any DB. That is the single hard invariant of this module: its only side
effect toward the platform is that one POST.

Design (contracts/mcp-proxy-internal-phase2.md §3, data-model.md §3d, research.md C4/C5):
  - `ensure_subscription(server_id)` is IDEMPOTENT — a re-discover / re-health of the
    same server never spawns a second subscriber (a live task is a no-op).
  - On a notification: per-server trailing-edge debounce for
    MCP_LIST_CHANGED_DEBOUNCE_SECONDS (each notification resets the timer; one POST
    fires once the burst goes quiet) — collapses a burst into one re-sync.
  - On a session drop: reconnect with MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS backoff,
    BOUNDED to MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS consecutive failures, then give up
    and tear the subscription down (the server was almost certainly deleted — its
    per-server Secret is gone). A successful reconnect resets the counter, so a live-but-
    flapping server never exhausts the cap. NEVER an infinite reconnect loop.
  - Per-replica, in-memory (`_subscriptions`). Lost on pod restart → re-established on the
    next discover/health that observes list_changed_supported=true. Nothing persisted.
  - Multi-replica: each replica holding a subscribed session may fire; registry-api
    coalesces (min-interval + idempotent upsert). Exactly-once is NOT guaranteed
    (ledgered) — that is by design, not a bug here.

The proxy still holds NO DB connection and NO AGENTSHIELD_ENCRYPTION_KEY; server
connection info + auth headers arrive only via the per-server K8s Secret
(credentials.read_server_secret), same as every other proxy path.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

import config
import credentials
import mcp_client

logger = logging.getLogger(__name__)

# The mcp 1.x notification `method` string for a server-side tool-set change. A
# ServerNotification wraps the concrete notification in `.root`; we read `.method`
# defensively so a version that surfaces the notification slightly differently still
# matches. (Pinned for CP2 confirmation against the installed mcp>=1.2,<2.0.)
_LIST_CHANGED_METHOD = "notifications/tools/list_changed"

# Keepalive/liveness cadence for a held-open subscriber (seconds). Between beats the
# SDK's background receive loop dispatches notifications to the handler concurrently, so
# this is NOT a change-poll — it is only how a clean transport EOF (which delivers no
# handler exception) is detected so the subscriber can reconnect. Deliberately not an env
# knob: an internal detail, not an operational tunable.
_SESSION_HEARTBEAT_SECONDS = 30.0


@dataclass
class SubscriptionState:
    """Per-server subscription bookkeeping (data-model.md §3d). In-memory, per-replica."""

    task: asyncio.Task              # the long-lived subscriber loop
    debounce_task: asyncio.Task | None = None   # pending debounced re-sync timer (or None)
    reconnect_attempts: int = 0     # consecutive failed reconnects (reset on success)
    last_fire_time: float = 0.0     # epoch seconds of the last POSTed re-sync callback


# Module-level registry: str(server_id) -> SubscriptionState. Per-replica, in-memory.
_subscriptions: dict[str, SubscriptionState] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def ensure_subscription(server_id: str) -> None:
    """Idempotently ensure a live list_changed subscriber for `server_id`.

    No-op if MCP_LIST_CHANGED_ENABLED is false, or if a subscriber task for this
    server is already running (the idempotency guard — a re-discover/re-health must
    NOT spawn a second task). If a prior task finished (gave up after the reconnect
    cap, or was torn down), a fresh one is started.

    Does not block on the upstream connection: it only spawns the subscriber task and
    returns, so a caller (discover/health) can fire-and-forget without its own response
    depending on subscription setup.
    """
    if not config.MCP_LIST_CHANGED_ENABLED:
        return

    server_id = str(server_id)
    existing = _subscriptions.get(server_id)
    if existing is not None and not existing.task.done():
        # Live subscriber already present → idempotent no-op.
        return

    # No await between this check and the assignment below, so within the single-
    # threaded event loop the create+register is atomic — no lock needed to keep it
    # idempotent against concurrent callers.
    task = asyncio.create_task(_run_subscription(server_id))
    _subscriptions[server_id] = SubscriptionState(task=task)
    logger.info("mcp-proxy list_changed: subscription started for server %s", server_id)


async def stop_subscription(server_id: str) -> None:
    """Cancel + remove the subscriber task (and any pending debounce) for `server_id`.

    Safe if no subscription exists. Awaits the task's cancellation so the caller knows
    the session is torn down.
    """
    server_id = str(server_id)
    state = _subscriptions.pop(server_id, None)
    if state is None:
        return

    if state.debounce_task is not None and not state.debounce_task.done():
        state.debounce_task.cancel()

    if not state.task.done():
        state.task.cancel()
        try:
            await state.task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mcp-proxy list_changed: subscriber for %s errored during stop: %s",
                server_id,
                exc,
            )
    logger.info("mcp-proxy list_changed: subscription stopped for server %s", server_id)


# ---------------------------------------------------------------------------
# Subscriber loop
# ---------------------------------------------------------------------------

async def _run_subscription(server_id: str) -> None:
    """Hold a long-lived session open; reconnect (bounded) on drop; tear down on give-up.

    Each iteration reads the per-server Secret fresh (so a rotated credential /
    deleted server is observed), opens a session with the list_changed handler, and
    holds it open until it drops. A successful connect resets the reconnect counter.
    """
    while True:
        session = None
        dropped = asyncio.Event()
        try:
            connection = await credentials.read_server_secret(server_id)
            handler = _make_message_handler(server_id, dropped)
            session = await mcp_client.connect_and_initialize(
                connection.server_url,
                connection.auth_headers,
                message_handler=handler,
            )
            # Connected — reset the consecutive-failure counter.
            state = _subscriptions.get(server_id)
            if state is not None:
                state.reconnect_attempts = 0
            logger.info(
                "mcp-proxy list_changed: session open for server %s (%s)",
                server_id,
                connection.server_url,
            )
            # Block here until the session drops (handler-observed exception or a
            # heartbeat that fails). Notifications fire concurrently via the SDK loop.
            await _hold_session_open(session, dropped)
            logger.info("mcp-proxy list_changed: session dropped for server %s", server_id)
        except asyncio.CancelledError:
            # stop_subscription / shutdown — close the session and exit cleanly.
            if session is not None:
                await session.close()
            raise
        except Exception as exc:  # noqa: BLE001 — connect/secret/transport failure
            logger.warning(
                "mcp-proxy list_changed: subscriber for %s connect/hold failed: %s",
                server_id,
                exc,
            )
        finally:
            if session is not None:
                await session.close()

        # Reached only on a (non-cancel) drop or connect failure → decide whether to
        # reconnect. Bounded: after MAX consecutive failures, give up and tear down.
        state = _subscriptions.get(server_id)
        attempts = (state.reconnect_attempts if state is not None else 0) + 1
        if state is not None:
            state.reconnect_attempts = attempts

        if attempts > config.MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS:
            logger.warning(
                "mcp-proxy list_changed: server %s exceeded %d reconnect attempts — "
                "tearing subscription down",
                server_id,
                config.MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS,
            )
            break

        logger.info(
            "mcp-proxy list_changed: reconnecting server %s in %.1fs (attempt %d/%d)",
            server_id,
            config.MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS,
            attempts,
            config.MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS,
        )
        await asyncio.sleep(config.MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS)

    # Teardown: cancel any pending debounce timer and drop ourselves from the registry
    # (only if we're still the registered task — ensure_subscription may have replaced us).
    state = _subscriptions.get(server_id)
    if state is not None and state.task is asyncio.current_task():
        if state.debounce_task is not None and not state.debounce_task.done():
            state.debounce_task.cancel()
        _subscriptions.pop(server_id, None)


async def _hold_session_open(session: "mcp_client.McpSession", dropped: asyncio.Event) -> None:
    """Block while the session is alive; return once it has dropped.

    Two drop signals, whichever comes first:
      - `dropped` is set by the message handler when the SDK delivers a terminal
        Exception (fast path for an observed transport error), or
      - a periodic `list_tools()` keepalive raises (catches a clean EOF the handler
        never sees; also keeps the pooled connection warm). `list_tools` is bounded by
        MCP_CONNECT_TIMEOUT_SECONDS inside McpSession, so this can't hang.
    Notifications are dispatched to the handler by the SDK's own background receive task,
    independent of this coroutine — so a 30s heartbeat does not delay a re-sync.
    """
    while not dropped.is_set():
        try:
            await asyncio.wait_for(dropped.wait(), timeout=_SESSION_HEARTBEAT_SECONDS)
            return  # handler observed a terminal exception
        except asyncio.TimeoutError:
            # Heartbeat: confirm the session still answers; a raise → drop → caller reconnects.
            await session.list_tools()


# ---------------------------------------------------------------------------
# Notification handling + debounced callback
# ---------------------------------------------------------------------------

def _make_message_handler(server_id: str, dropped: asyncio.Event):
    """Build the per-session mcp message handler (closure over server_id + drop event)."""

    async def _handler(message) -> None:  # noqa: ANN001 — SDK union type
        # The SDK delivers a terminal Exception here when the session fails — use it as
        # the fast drop signal so the subscriber reconnects without waiting a heartbeat.
        if isinstance(message, Exception):
            dropped.set()
            return
        # A ServerNotification wraps the concrete notification in `.root`; read `.method`
        # defensively (works whether `message` is the wrapper or the inner model).
        root = getattr(message, "root", message)
        method = getattr(root, "method", None)
        if method == _LIST_CHANGED_METHOD:
            logger.info(
                "mcp-proxy list_changed: notification received for server %s — debouncing",
                server_id,
            )
            _schedule_debounced_resync(server_id)

    return _handler


def _schedule_debounced_resync(server_id: str) -> None:
    """Trailing-edge debounce: (re)arm the per-server timer; the last one in a burst wins."""
    state = _subscriptions.get(server_id)
    if state is None:
        return
    if state.debounce_task is not None and not state.debounce_task.done():
        state.debounce_task.cancel()
    state.debounce_task = asyncio.create_task(_debounced_fire(server_id))


async def _debounced_fire(server_id: str) -> None:
    """Wait out the debounce window (cancelled + replaced if another notification lands),
    then POST the single coalesced re-sync callback."""
    try:
        await asyncio.sleep(config.MCP_LIST_CHANGED_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return  # superseded by a newer notification in the same burst
    state = _subscriptions.get(server_id)
    if state is not None:
        state.last_fire_time = time.time()
    await _post_list_changed(server_id)


async def _post_list_changed(server_id: str) -> None:
    """POST the re-sync trigger to registry-api. Best-effort: a failure is logged, not
    raised — the next notification will retry, and registry-api coalesces duplicates.

    NetworkPolicy-trusted, no bearer token — identical trust to authz.py's existing
    `/internal/mcp/authorize-tool-call` callback (the caller is the in-cluster proxy;
    registry-api does not re-verify)."""
    url = f"{config.REGISTRY_API_URL}/api/v1/internal/mcp/list-changed"
    try:
        async with httpx.AsyncClient(
            timeout=config.REGISTRY_API_TIMEOUT_SECONDS
        ) as client:
            resp = await client.post(url, json={"server_id": str(server_id)})
            resp.raise_for_status()
        logger.info(
            "mcp-proxy list_changed: re-sync POSTed for server %s → %s",
            server_id,
            resp.status_code,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort callback
        logger.warning(
            "mcp-proxy list_changed: re-sync POST failed for server %s: %s "
            "(will retry on next notification)",
            server_id,
            exc,
        )

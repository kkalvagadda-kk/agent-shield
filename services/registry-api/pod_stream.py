"""The ONE member-pod SSE reader (No-Bandaid: single reader for both surfaces).

``stream_pod_chat_frames`` POSTs a member pod's ``/chat/stream`` and yields
NORMALIZED frame dicts — each tagged with ``author`` — that two callers consume:

  * ``routers/chat.py::_proxy_agent_stream``  (single-agent / consumer chat)
  * ``workflow_orchestrator._dispatch_stream`` (a reactive workflow member)

It replaces the per-caller SSE parsing that used to live in ``_proxy_agent_stream``
and, critically, STOPS dropping the pod's ``tool_call_start``/``tool_call_end``
events (the L473 drop) — they now surface as a single ``tool_call`` frame per
invocation (the 2b-i fix).

Frame vocabulary yielded (see contracts/sse-frames.md §C) — NO run-level ``done``;
the caller owns the terminal frame:

    {"type": "agent_start", "author": author}
    {"type": "token",       "author": author, "content": str}
    {"type": "tool_call",   "author": author, "tool": str, "status": "ok" | "error"}
    {"type": "rationale",   "author": author, "content": str}
    {"type": "approval_requested", "author": author, **payload}
    {"type": "error",       "author": author, "message": str}
"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, Optional

import httpx

logger = logging.getLogger(__name__)


async def stream_pod_chat_frames(
    service_url: str,
    *,
    message: str,
    thread_id: str,
    conversation_id: str,
    scope: str,                 # "agent" | "workflow_run"
    author: str,                # tags every yielded frame
    trace_id: str | None = None,
    user_id: str = "",
    user_team: str = "",
    deployment_id: str = "",
    auto_approve: bool = False,
) -> AsyncGenerator[dict, None]:
    """POST ``{service_url}/chat/stream`` and yield normalized frame dicts.

    Reads the pod stream to natural EOF (a trailing ``rationale`` event, which the
    runner emits AFTER its internal ``done``, is captured). ``httpx.ConnectError`` /
    non-200 / any other error → yields a single ``error`` frame. Never yields a
    run-level ``done`` — the caller owns that.
    """
    target = f"{service_url}/chat/stream"
    timeout = httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0)

    req_headers: dict[str, str] = {"Content-Type": "application/json"}
    if trace_id:
        req_headers["X-AgentShield-Trace-ID"] = trace_id
    # Identity + deployment propagation so the runner can load/save memory scoped
    # to this user and deployment (thread-ownership.md, memory-api.md).
    if user_id:
        req_headers["x-user-sub"] = user_id
    if user_team:
        req_headers["x-agent-team"] = user_team
    if deployment_id:
        req_headers["x-deployment-id"] = deployment_id
    if auto_approve:
        req_headers["x-agentshield-auto-approve"] = "true"

    body: dict[str, str] = {
        "message": message,
        "thread_id": thread_id,
        "conversation_id": conversation_id,
        "scope": scope,
    }

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", target, json=body, headers=req_headers, timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    logger.error("Agent pod %s returned HTTP %d", target, response.status_code)
                    yield {
                        "type": "error", "author": author,
                        "message": f"Agent returned HTTP {response.status_code}",
                    }
                    return

                # Open the author's bubble before the first token (single-agent = the
                # degenerate one-speaker case). The workflow path suppresses this in
                # _dispatch_stream because _run_step_stream owns the member lifecycle.
                yield {"type": "agent_start", "author": author}

                current_event: Optional[str] = None
                current_data: Optional[str] = None

                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        current_event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        current_data = line[len("data:"):].strip()
                    elif line == "":
                        if current_data is not None:
                            try:
                                payload = json.loads(current_data)
                            except json.JSONDecodeError:
                                payload = {}
                            frame = _translate(current_event, payload, author)
                            if frame is not None:
                                yield frame
                        current_event = None
                        current_data = None

    except httpx.ConnectError:
        logger.error("Cannot reach agent pod at %s", target)
        yield {
            "type": "error", "author": author,
            "message": "Agent pod is unreachable. It may still be starting.",
        }
    except Exception as exc:  # noqa: BLE001 — surface as a single error frame, never raise
        logger.exception("Unexpected error reading agent pod stream %s", target)
        yield {"type": "error", "author": author, "message": str(exc)}


def _translate(event: Optional[str], payload: dict, author: str) -> Optional[dict]:
    """Translate one named pod SSE event → a normalized frame dict (or None to skip).

    ``tool_call_start`` → one ``tool_call`` chip (status ok). ``tool_call_end`` only
    emits a second frame when it signals an error (keeps one chip per call). ``done``
    is skipped so the reader continues to EOF and captures a trailing ``rationale``.
    """
    if event == "text_delta":
        return {"type": "token", "author": author, "content": payload.get("content", "")}
    if event == "tool_call_start":
        return {
            "type": "tool_call", "author": author,
            "tool": payload.get("tool") or payload.get("tool_name", ""),
            "status": "ok",
        }
    if event == "tool_call_end":
        # One chip per call: the start already emitted it. Emit a follow-up error
        # frame ONLY when the end signals failure.
        if payload.get("error") or payload.get("status") == "error":
            return {
                "type": "tool_call", "author": author,
                "tool": payload.get("tool") or payload.get("tool_name", ""),
                "status": "error",
            }
        return None
    if event == "rationale":
        return {"type": "rationale", "author": author, "content": payload.get("content", "")}
    if event == "error":
        return {
            "type": "error", "author": author,
            "message": payload.get("message") or payload.get("reason", "Agent error"),
        }
    if event == "approval_requested":
        return {"type": "approval_requested", "author": author, **payload}
    # done → not yielded (read continues to EOF for a trailing rationale).
    return None

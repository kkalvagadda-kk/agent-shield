"""Regression — the SDK safety_client must speak the orchestrator's wire contract.

The Safety Orchestrator (services/safety-orchestrator/schemas.py) reads ``message``
+ ``thread_id`` on the request and returns ``anonymized_message`` (input) /
``deanonymized_message`` (output). The SDK previously sent ``text``/``trace_id``
and read ``sanitized_text``/``clean_text``, so on a REAL deploy every scan 422'd
(missing required ``message``) and fail-closed — silently. These tests drive
scan_input/scan_output/deanonymize_args against a MockTransport asserting the
exact wire fields so the two-sided mismatch can never quietly return.

Written regression-first (fails against the buggy code) per DoD rule 7. Cross-ref:
docs/bugs/safety-client-scan-field-mismatch.md.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from agentshield_sdk import config, safety_client

# Capture the genuine class ONCE, before any test monkeypatches
# safety_client.httpx.AsyncClient (which is the same module object as httpx here,
# so a patch is global). Building the mock client off this original avoids a
# later factory wrapping an earlier one.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_client(handler):
    """Return a drop-in for httpx.AsyncClient backed by a MockTransport."""

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

    return factory


def test_scan_output_sends_message_thread_id_reads_deanonymized(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "allowed": True,
                "blocked": False,
                "deanonymized_message": "Jane Doe",
                "scores": {},
            },
        )

    monkeypatch.setattr(config, "AGENTSHIELD_SAFETY_URL", "http://safety.test")
    monkeypatch.setattr(safety_client.httpx, "AsyncClient", _mock_client(handler))

    result = asyncio.run(
        safety_client.scan_output(
            "REDACTED_OUTPUT", agent_name="a", session_id="s1", trace_id="t1"
        )
    )
    body = captured["body"]
    assert body.get("message") == "REDACTED_OUTPUT"  # was 'text' (bug)
    assert body.get("thread_id") == "t1"  # was 'trace_id' (bug)
    assert "text" not in body
    assert "trace_id" not in body
    assert result.clean_text == "Jane Doe"  # read deanonymized_message, not clean_text


def test_scan_input_sends_message_thread_id_reads_anonymized(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "allowed": True,
                "blocked": False,
                "anonymized_message": "<PERSON>",
                "scores": {},
            },
        )

    monkeypatch.setattr(config, "AGENTSHIELD_SAFETY_URL", "http://safety.test")
    monkeypatch.setattr(safety_client.httpx, "AsyncClient", _mock_client(handler))

    result = asyncio.run(
        safety_client.scan_input(
            "my name is Jane", agent_name="a", session_id="s1", trace_id="t1"
        )
    )
    body = captured["body"]
    assert body.get("message") == "my name is Jane"
    assert body.get("thread_id") == "t1"
    assert "text" not in body
    assert "trace_id" not in body
    assert result.sanitized_text == "<PERSON>"  # read anonymized_message


def test_deanonymize_args_substitutes_and_fails_open(monkeypatch):
    # Happy path: the orchestrator returns de-anonymized args.
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"args": {"to": "jane@example.com"}})

    monkeypatch.setattr(config, "AGENTSHIELD_SAFETY_URL", "http://safety.test")
    monkeypatch.setattr(safety_client.httpx, "AsyncClient", _mock_client(ok_handler))
    out = asyncio.run(
        safety_client.deanonymize_args({"to": "<EMAIL>"}, agent_name="a", session_id="s1")
    )
    assert out == {"to": "jane@example.com"}

    # Fail-open: a scanner error returns the args UNCHANGED and never raises.
    def boom_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(safety_client.httpx, "AsyncClient", _mock_client(boom_handler))
    same = asyncio.run(
        safety_client.deanonymize_args({"to": "<EMAIL>"}, agent_name="a", session_id="s1")
    )
    assert same == {"to": "<EMAIL>"}

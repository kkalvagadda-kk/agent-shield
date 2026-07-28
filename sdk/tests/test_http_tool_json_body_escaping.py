"""Regression — an HTTP tool body_template must not break when a value contains
JSON-special characters (quote / newline / backslash).

Root cause (2026-07-27): ``HttpToolExecutor`` (and the declarative-runner's
``HttpToolNodeExecutor``) substituted ``{{var}}`` into the JSON body_template as a
RAW string, THEN called ``json.loads``. So an email body like ``He said "hi"`` or
one with a newline produced invalid JSON — the call fell back to sending the
malformed string as ``content`` and the upstream (Resend) returned 400. The fix
parses the template ONCE, then substitutes ``{{var}}`` inside the string leaves
with real values, so value content can never break the JSON structure.

This locks it in: the same value that used to break substitute-then-parse must now
land intact in a structured body.
"""
from __future__ import annotations

import json
import re

import pytest

from agentshield_sdk.tool_executor import _render_http_body


def test_quotes_and_newlines_do_not_break_json():
    # The email_notifier template shape (from/to/subject/html).
    tpl = '{"from":"kkalyan@agentsheild.com","to":"{{to}}","subject":"{{subject}}","html":"{{body}}"}'
    body, is_json = _render_http_body(
        tpl,
        {
            "to": "recipient@example.com",
            "subject": 'Re: "urgent" update',
            "body": 'Hello,\nHe said "hi" and used a \\ backslash.\nBye',
        },
    )
    assert is_json is True
    assert body["from"] == "kkalyan@agentsheild.com"
    assert body["to"] == "recipient@example.com"
    assert body["subject"] == 'Re: "urgent" update'
    # the {{body}} placeholder maps into the "html" field of the Resend payload
    assert body["html"] == 'Hello,\nHe said "hi" and used a \\ backslash.\nBye'
    # Round-trips to valid JSON (what httpx json= will serialize).
    json.loads(json.dumps(body))


def test_old_substitute_then_parse_would_have_failed():
    # Demonstrates the exact former failure mode the fix removes.
    tpl = '{"html":"{{body}}"}'
    naive = re.sub(r"\{\{(\w+)\}\}", lambda m: 'He said "hi"', tpl)  # old path
    with pytest.raises(json.JSONDecodeError):
        json.loads(naive)  # '{"html":"He said "hi""}' → invalid
    # New path handles the same value.
    body, is_json = _render_http_body('{"html":"{{body}}"}', {"body": 'He said "hi"'})
    assert is_json and body["html"] == 'He said "hi"'


def test_simple_string_body_unchanged():
    body, is_json = _render_http_body('{"text":"{{message}}"}', {"message": "hello world"})
    assert is_json and body == {"text": "hello world"}


def test_numeric_placeholder_template_falls_back_to_legacy():
    # Placeholder OUTSIDE quotes → template isn't valid JSON → legacy
    # substitute-then-parse still yields a proper numeric body.
    body, is_json = _render_http_body('{"count": {{n}}}', {"n": "5"})
    assert is_json is True and body == {"count": 5}


def test_non_json_template_returns_content_string():
    body, is_json = _render_http_body("plain {{x}} body", {"x": "Z"})
    assert is_json is False and body == "plain Z body"


def test_missing_var_left_as_placeholder():
    # An unresolved var is left as-is (not crashed) — same tolerance as before.
    body, is_json = _render_http_body('{"to":"{{to}}"}', {})
    assert is_json and body == {"to": "{{to}}"}

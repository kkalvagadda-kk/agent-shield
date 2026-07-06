"""
Filter evaluation engine for webhook triggers (vendored from registry-api).

Evaluates `filter_conditions` JSONB (array of {field, op, value} rules) against
a payload dict. All rules must match (AND semantics).

Supported operators: eq, neq, contains, not_contains, gt, gte, lt, lte,
exists, not_exists, in, regex

⚠ ReDoS mitigation (threat model T-7): the `regex` operator runs against
attacker-controlled payload values. CPython's `re` does not release the GIL
during matching, so a watchdog thread cannot preempt catastrophic backtracking.
We instead **bound the input length** fed to the matcher (backtracking cost is a
function of input size), and validate the pattern compiles. On any regex error
or over-length input we fail SAFE (not matched) — a filter never over-matches
due to a bad/hostile pattern. `google-re2` (linear-time) is the production
hardening upgrade; kept out of the image to avoid a C++ build dependency.
"""
from __future__ import annotations

import os
import re
from typing import Any

# Max characters of payload value passed to a regex match (bounds backtracking).
REGEX_INPUT_MAX = int(os.getenv("REGEX_INPUT_MAX", "4096"))


def _resolve_field(payload: dict[str, Any], field: str) -> tuple[bool, Any]:
    """Resolve a dot-separated field path against a payload dict."""
    parts = field.split(".")
    current: Any = payload
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def _evaluate_rule(payload: dict[str, Any], rule: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate a single filter rule. Returns (matched, reason)."""
    field = rule.get("field", "")
    op = rule.get("op", "eq")
    expected = rule.get("value")

    found, actual = _resolve_field(payload, field)

    if op == "exists":
        if found:
            return True, ""
        return False, f"field '{field}' does not exist"

    if op == "not_exists":
        if not found:
            return True, ""
        return False, f"field '{field}' exists"

    if not found:
        return False, f"field '{field}' not found in payload"

    if op == "eq":
        if actual == expected:
            return True, ""
        return False, f"{field}={actual!r}, expected {expected!r}"

    if op == "neq":
        if actual != expected:
            return True, ""
        return False, f"{field}={actual!r}, expected != {expected!r}"

    if op == "contains":
        if isinstance(actual, str) and isinstance(expected, str) and expected in actual:
            return True, ""
        if isinstance(actual, (list, tuple)) and expected in actual:
            return True, ""
        return False, f"{field} does not contain {expected!r}"

    if op == "not_contains":
        if isinstance(actual, str) and isinstance(expected, str) and expected not in actual:
            return True, ""
        if isinstance(actual, (list, tuple)) and expected not in actual:
            return True, ""
        return False, f"{field} contains {expected!r}"

    if op == "gt":
        try:
            if float(actual) > float(expected):
                return True, ""
        except (TypeError, ValueError):
            pass
        return False, f"{field}={actual!r} not > {expected!r}"

    if op == "gte":
        try:
            if float(actual) >= float(expected):
                return True, ""
        except (TypeError, ValueError):
            pass
        return False, f"{field}={actual!r} not >= {expected!r}"

    if op == "lt":
        try:
            if float(actual) < float(expected):
                return True, ""
        except (TypeError, ValueError):
            pass
        return False, f"{field}={actual!r} not < {expected!r}"

    if op == "lte":
        try:
            if float(actual) <= float(expected):
                return True, ""
        except (TypeError, ValueError):
            pass
        return False, f"{field}={actual!r} not <= {expected!r}"

    if op == "in":
        if isinstance(expected, list) and actual in expected:
            return True, ""
        return False, f"{field}={actual!r} not in {expected!r}"

    if op == "regex":
        if isinstance(actual, str) and isinstance(expected, str):
            # ReDoS bound (T-7): cap the input length before matching, and
            # fail safe on over-length input or an invalid pattern.
            if len(actual) > REGEX_INPUT_MAX:
                return False, f"{field} exceeds regex input cap ({REGEX_INPUT_MAX})"
            try:
                if re.search(expected, actual):
                    return True, ""
            except re.error:
                return False, f"invalid regex: {expected!r}"
        return False, f"{field}={actual!r} does not match {expected!r}"

    return False, f"unknown operator: {op}"


def evaluate_filters(
    filter_conditions: list[dict[str, Any]] | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate all filter rules against a payload.

    Returns: {"matched": bool, "reason": str}
    """
    if not filter_conditions:
        return {"matched": True, "reason": "no filters configured"}

    for rule in filter_conditions:
        matched, reason = _evaluate_rule(payload, rule)
        if not matched:
            return {"matched": False, "reason": reason}

    return {"matched": True, "reason": "all rules matched"}

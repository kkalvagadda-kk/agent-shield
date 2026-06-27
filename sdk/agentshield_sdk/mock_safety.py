"""
Mock safety layer — used when AGENTSHIELD_SAFETY_URL is not set.

Every call is a pass-through: input is returned unchanged, output is returned
unchanged, and ``blocked`` is always False.  This makes local development work
without a running Safety Orchestrator.
"""
from __future__ import annotations


async def scan_input(text: str, **kwargs) -> dict:
    """Mock input scan — always passes.

    Returns:
        dict with ``blocked=False`` and ``sanitized_text`` equal to *text*.
    """
    return {"blocked": False, "sanitized_text": text, "scores": {}}


async def scan_output(text: str, **kwargs) -> dict:
    """Mock output scan — always passes.

    Returns:
        dict with ``blocked=False`` and ``clean_text`` equal to *text*.
    """
    return {"blocked": False, "clean_text": text, "scores": {}}

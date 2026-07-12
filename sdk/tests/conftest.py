"""Pytest config for the AgentShield SDK test suite.

Ensures the ``sdk/`` package root is importable regardless of where pytest is
invoked from, so ``import agentshield_sdk`` resolves to this repo's SDK.
"""
from __future__ import annotations

import os
import sys

_SDK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SDK_ROOT not in sys.path:
    sys.path.insert(0, _SDK_ROOT)

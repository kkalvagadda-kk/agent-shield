"""
AgentShield NeMo Guardrails service.

Implements pattern-based scanning that mirrors the YARA rules mounted at /app/rules/.
Uses Python regex for portability — no native YARA binary required.
High-severity patterns (prompt injection) → blocked=true.
Medium patterns (data exfil probes) → risk_score=0.5, not blocked.
"""

import logging
import os
import re
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Compiled rule patterns ──────────────────────────────────────────────────

_HIGH_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore previous instructions",
        r"disregard all prior instructions",
        r"forget everything above",
        r"you are now\b",
        r"act as if you have no restrictions",
        r"\bDAN mode\b",
        r"\bdeveloper mode\b",
        r"jailbreak",
        r"bypass\s+(all\s+)?restrictions",
        r"pretend\s+you\s+(have\s+no|are\s+not)",
    ]
]

_MEDIUM_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"send all data to",
        r"dump the database",
        r"export all records",
        r"list all users",
        r"show me all (passwords|credentials|secrets)",
        r"reveal\s+(system\s+)?prompt",
    ]
]


class CheckRequest(BaseModel):
    text: str


class CheckResponse(BaseModel):
    blocked: bool
    risk_score: float
    matched_rules: list[str] = []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    rules_dir = os.getenv("RULES_DIR", "/app/rules")
    if os.path.isdir(rules_dir):
        yar_files = [f for f in os.listdir(rules_dir) if f.endswith(".yar")]
        logger.info("NeMo Guardrails: loaded %d YARA rule files from %s (using regex mirror)", len(yar_files), rules_dir)
    else:
        logger.info("NeMo Guardrails: no rules directory found at %s — using built-in patterns only", rules_dir)
    yield


app = FastAPI(title="AgentShield NeMo Guardrails", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/check", response_model=CheckResponse)
def check(req: CheckRequest) -> CheckResponse:
    matched: list[str] = []

    for pat in _HIGH_PATTERNS:
        if pat.search(req.text):
            matched.append(f"PromptInjection:{pat.pattern[:40]}")

    if matched:
        return CheckResponse(blocked=True, risk_score=0.95, matched_rules=matched)

    for pat in _MEDIUM_PATTERNS:
        if pat.search(req.text):
            matched.append(f"DataExfil:{pat.pattern[:40]}")

    if matched:
        return CheckResponse(blocked=False, risk_score=0.5, matched_rules=matched)

    return CheckResponse(blocked=False, risk_score=0.0)

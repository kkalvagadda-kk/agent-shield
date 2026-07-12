"""
Observability backend abstraction — the single seam between the platform and
whatever traces/cost live behind it (Langfuse today, any OTEL/OTLP backend
tomorrow).

Architectural rule (see docs/design/todo/observability-provider-abstraction.md):
**no router or service module calls the observability backend's REST API
directly.** They all go through `get_observability_backend()` → an
`ObservabilityBackend` implementation. Studio never sees a backend-specific
shape — reads return the provider-neutral `NormalizedTrace` / `CostByModel` /
`ToolCallStat` / `RunCost` types defined here. Adding a backend = one new adapter
class; swapping backends = one env var (`OBSERVABILITY_BACKEND`).

This module owns the READ seam (get_trace, cost/observation aggregation, deep-link
URL construction, score push/read). The EMIT seam (trace creation/completion)
still lives in `tracing.py` on the langfuse client and moves separately.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request as urlreq
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider-neutral read types (what Studio and the routers consume)
# ---------------------------------------------------------------------------

class NormalizedSpan(BaseModel):
    id: str
    name: str
    type: str
    parent_id: str | None = None       # for nesting spans into a tree/waterfall
    start_time: str | None = None
    end_time: str | None = None
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] | None = None
    status_message: str | None = None
    level: str | None = None
    # Per-generation economics (populated for GENERATION spans; None otherwise)
    model: str | None = None
    cost_usd: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class NormalizedScore(BaseModel):
    name: str
    value: float | None = None
    comment: str | None = None


class NormalizedTrace(BaseModel):
    trace_id: str
    name: str | None = None
    user: str | None = None
    started_at: str | None = None
    tags: list[str] = []
    total_cost: float | None = None
    warning: str | None = None  # e.g. "trace not yet ingested"
    spans: list[NormalizedSpan] = []
    scores: list[NormalizedScore] = []


class RunCost(BaseModel):
    cost_usd: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str | None = None


class CostByModel(BaseModel):
    model: str
    cost_usd: float
    calls: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ToolCallStat(BaseModel):
    tool_name: str
    count: int
    avg_latency_ms: float | None = None


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class ObservabilityBackend(Protocol):
    def get_trace(self, trace_id: str) -> NormalizedTrace | None: ...
    def get_run_cost(self, trace_id: str) -> RunCost | None: ...
    def spend_by_model(self, trace_ids: set[str], from_date: Optional[datetime]) -> list[CostByModel]: ...
    def tool_call_stats(self, trace_ids: set[str], from_date: Optional[datetime]) -> list[ToolCallStat]: ...
    def build_trace_url(self, trace_id: str) -> str | None: ...
    def push_score(self, trace_id: str, name: str, value: float, comment: str | None = None) -> bool: ...


# ---------------------------------------------------------------------------
# Langfuse adapter (backend #1)
# ---------------------------------------------------------------------------

class LangfuseBackend:
    """Reads traces/cost from Langfuse's public REST API + builds deep-links.

    All the previously-scattered `/api/public/*` calls live here now. Every
    method is best-effort: a Langfuse blip returns None/[] rather than raising,
    so a trace-backend hiccup never breaks a real request.
    """

    def __init__(self) -> None:
        self._host = os.getenv("LANGFUSE_HOST", "http://agentshield-langfuse-web:3000")
        self._public_url = os.getenv("LANGFUSE_PUBLIC_URL", "")
        self._project_id = os.getenv("LANGFUSE_PROJECT_ID", "")
        self._pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self._sk = os.getenv("LANGFUSE_SECRET_KEY", "")

    # -- helpers ------------------------------------------------------------
    @property
    def _configured(self) -> bool:
        return bool(self._pk and self._sk)

    def _creds(self) -> str:
        return base64.b64encode(f"{self._pk}:{self._sk}".encode()).decode()

    def _get(self, path: str, timeout: int = 6) -> Any:
        req = urlreq.Request(
            f"{self._host}{path}",
            headers={"Authorization": f"Basic {self._creds()}"},
        )
        with urlreq.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    # -- reads --------------------------------------------------------------
    def build_trace_url(self, trace_id: str) -> str | None:
        # Full path avoids Langfuse's /trace short-link redirect (loses the path
        # prefix behind the Gateway).
        if self._public_url and self._project_id and trace_id:
            return f"{self._public_url}/project/{self._project_id}/traces/{trace_id}"
        return None

    def get_trace(self, trace_id: str) -> NormalizedTrace | None:
        if not self._configured:
            return None
        raw: dict[str, Any] = {}
        try:
            raw = self._get(f"/api/public/traces/{trace_id}", timeout=5)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return NormalizedTrace(trace_id=trace_id, warning="trace not yet ingested by Langfuse")
            logger.debug("Langfuse trace fetch error %s: %s", exc.code, exc)
            return None
        except Exception as exc:
            logger.debug("Langfuse trace fetch failed: %s", exc)
            return None
        return self._normalize_trace(trace_id, raw)

    @staticmethod
    def _normalize_trace(trace_id: str, raw: dict[str, Any]) -> NormalizedTrace:
        spans = [
            NormalizedSpan(
                id=str(o.get("id") or ""),
                name=o.get("name") or "",
                type=o.get("type") or "SPAN",
                parent_id=o.get("parentObservationId"),
                start_time=o.get("startTime"),
                end_time=o.get("endTime"),
                input=o.get("input"),
                output=o.get("output"),
                metadata=o.get("metadata") if isinstance(o.get("metadata"), dict) else None,
                status_message=o.get("statusMessage"),
                level=o.get("level"),
                model=o.get("model"),
                cost_usd=(
                    round(float(c), 6)
                    if isinstance((c := o.get("calculatedTotalCost") or o.get("totalCost")), (int, float)) and c
                    else None
                ),
                prompt_tokens=o.get("promptTokens") if isinstance(o.get("promptTokens"), int) else None,
                completion_tokens=o.get("completionTokens") if isinstance(o.get("completionTokens"), int) else None,
            )
            for o in (raw.get("observations") or [])
        ]
        scores = [
            NormalizedScore(
                name=s.get("name") or "",
                value=s.get("value") if isinstance(s.get("value"), (int, float)) else None,
                comment=s.get("comment"),
            )
            for s in (raw.get("scores") or [])
        ]
        return NormalizedTrace(
            trace_id=trace_id,
            name=raw.get("name"),
            user=raw.get("userId"),
            started_at=raw.get("timestamp"),
            tags=list(raw.get("tags") or []),
            total_cost=raw.get("totalCost") if isinstance(raw.get("totalCost"), (int, float)) else None,
            warning=raw.get("warning"),
            spans=spans,
            scores=scores,
        )

    def get_run_cost(self, trace_id: str) -> RunCost | None:
        """Sum cost + tokens across a trace's GENERATION observations."""
        if not self._configured:
            return None
        cost = 0.0
        ptok = 0
        ctok = 0
        model_counts: dict[str, int] = {}
        found = False
        try:
            for page in range(1, 4):  # cap 3 pages (300 GENERATIONs) per trace
                data = self._get(
                    f"/api/public/observations?traceId={trace_id}&type=GENERATION&limit=100&page={page}"
                ).get("data", [])
                for o in data:
                    found = True
                    c = o.get("calculatedTotalCost") or o.get("totalCost")
                    if isinstance(c, (int, float)):
                        cost += c
                    if isinstance(o.get("promptTokens"), int):
                        ptok += o["promptTokens"]
                    if isinstance(o.get("completionTokens"), int):
                        ctok += o["completionTokens"]
                    m = o.get("model")
                    if m:
                        model_counts[m] = model_counts.get(m, 0) + 1
                if len(data) < 100:
                    break
        except Exception as exc:
            logger.debug("get_run_cost error for %s: %s", trace_id, exc)
            return None
        if not found:
            return None  # nothing ingested yet — caller retries later
        return RunCost(
            cost_usd=round(cost, 6) if cost > 0 else None,
            prompt_tokens=ptok or None,
            completion_tokens=ctok or None,
            model=max(model_counts, key=model_counts.get) if model_counts else None,
        )

    def _paged_observations(self, obs_type: str, from_date: Optional[datetime], pages: int = 5):
        """Yield observations of a type across pages (capped). Time-filtered."""
        from_param = ""
        if from_date:
            iso = from_date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            from_param = f"&fromStartTime={iso}"
        for page in range(1, pages + 1):
            data = self._get(
                f"/api/public/observations?type={obs_type}&limit=100&page={page}{from_param}"
            ).get("data", [])
            for o in data:
                yield o
            if len(data) < 100:
                break

    def spend_by_model(self, trace_ids: set[str], from_date: Optional[datetime]) -> list[CostByModel]:
        if not (trace_ids and self._configured):
            return []
        agg: dict[str, list] = {}  # model -> [cost, calls, ptok, ctok]
        try:
            for o in self._paged_observations("GENERATION", from_date):
                if o.get("traceId") not in trace_ids:
                    continue
                model = o.get("model") or "unknown"
                c = o.get("calculatedTotalCost") or o.get("totalCost") or 0
                slot = agg.setdefault(model, [0.0, 0, 0, 0])
                if isinstance(c, (int, float)):
                    slot[0] += c
                slot[1] += 1
                if isinstance(o.get("promptTokens"), int):
                    slot[2] += o["promptTokens"]
                if isinstance(o.get("completionTokens"), int):
                    slot[3] += o["completionTokens"]
        except Exception as exc:
            logger.debug("spend_by_model: Langfuse fetch failed: %s", exc)
            return []
        out = [
            CostByModel(model=m, cost_usd=round(cost, 6), calls=calls, prompt_tokens=pt, completion_tokens=ct)
            for m, (cost, calls, pt, ct) in agg.items()
        ]
        out.sort(key=lambda x: x.cost_usd, reverse=True)
        return out

    def tool_call_stats(self, trace_ids: set[str], from_date: Optional[datetime]) -> list[ToolCallStat]:
        if not (trace_ids and self._configured):
            return []
        agg: dict[str, list] = {}  # name -> [count, latency_sum_seconds, latency_n]
        try:
            for o in self._paged_observations("TOOL", from_date):
                if o.get("traceId") not in trace_ids:
                    continue
                name = o.get("name") or "tool"
                lat = o.get("latency")  # Langfuse reports latency in SECONDS
                slot = agg.setdefault(name, [0, 0.0, 0])
                slot[0] += 1
                if isinstance(lat, (int, float)):
                    slot[1] += lat
                    slot[2] += 1
        except Exception as exc:
            logger.debug("tool_call_stats: Langfuse fetch failed: %s", exc)
            return []
        stats = [
            ToolCallStat(
                tool_name=name,
                count=cnt,
                avg_latency_ms=round((lsum / ln) * 1000, 1) if ln else None,
            )
            for name, (cnt, lsum, ln) in agg.items()
        ]
        stats.sort(key=lambda s: s.count, reverse=True)
        return stats[:15]

    # -- writes (score push; kept here as it shares the same coupling) ------
    def push_score(self, trace_id: str, name: str, value: float, comment: str | None = None) -> bool:
        if not self._configured:
            return False
        try:
            body = json.dumps({
                "traceId": trace_id,
                "name": name,
                "value": value,
                **({"comment": comment} if comment else {}),
            }).encode()
            req = urlreq.Request(
                f"{self._host}/api/public/scores",
                data=body,
                headers={
                    "Authorization": f"Basic {self._creds()}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            urlreq.urlopen(req, timeout=5).read()
            return True
        except Exception as exc:
            logger.debug("push_score failed for %s: %s", trace_id, exc)
            return False


class NoneBackend:
    """OBSERVABILITY_BACKEND=none — tracing disabled. All reads empty, no URLs."""

    def get_trace(self, trace_id: str) -> NormalizedTrace | None:
        return None

    def get_run_cost(self, trace_id: str) -> RunCost | None:
        return None

    def spend_by_model(self, trace_ids: set[str], from_date: Optional[datetime]) -> list[CostByModel]:
        return []

    def tool_call_stats(self, trace_ids: set[str], from_date: Optional[datetime]) -> list[ToolCallStat]:
        return []

    def build_trace_url(self, trace_id: str) -> str | None:
        return None

    def push_score(self, trace_id: str, name: str, value: float, comment: str | None = None) -> bool:
        return False


_backend: ObservabilityBackend | None = None


def get_observability_backend() -> ObservabilityBackend:
    """Return the configured backend (cached). `OBSERVABILITY_BACKEND` selects it;
    defaults to langfuse. `none` disables reads + hides trace UI cleanly."""
    global _backend
    if _backend is None:
        choice = os.getenv("OBSERVABILITY_BACKEND", "langfuse").strip().lower()
        _backend = NoneBackend() if choice == "none" else LangfuseBackend()
        logger.info("observability backend: %s", choice)
    return _backend

"""
OpenTelemetry emit — provider-agnostic LLM/tool span capture.

Instruments LangChain / LangGraph via OpenInference (vendor-neutral OTEL GenAI
instrumentation) and exports OTEL spans over OTLP/HTTP to the configured backend.

The backend is Langfuse today (its ``/api/public/otel`` OTLP endpoint), but ANY
OTLP-compatible backend (Datadog, Honeycomb, Grafana Tempo, Arize Phoenix, a
collector, …) works by pointing the exporter elsewhere — nothing here imports a
Langfuse client. See docs/design/todo/observability-provider-abstraction.md.

Config precedence:
  1. OTEL_EXPORTER_OTLP_ENDPOINT (+ OTEL_EXPORTER_OTLP_HEADERS) — true
     backend-agnostic path; set these to point at any OTLP backend.
  2. Fall back to Langfuse: derive the endpoint + Basic-auth header from the
     LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY env the pod
     already carries.

No-ops gracefully (returns False) when neither is configured, so importing and
calling ``setup_otel()`` is always safe.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import uuid as _uuid
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_initialized = False


@contextmanager
def otel_run_context(run_id: str | None):
    """Bind OTEL spans created within this block to a trace whose id is derived
    deterministically from ``run_id``.

    Without this, OpenInference auto-generates a fresh trace id per langgraph run,
    so the agent's LLM/tool spans land in a SEPARATE Langfuse trace from the
    platform's envelope trace (which is keyed by run_id). Deriving the OTEL trace
    id from run_id (a UUID is 128 bits — a valid W3C trace id) makes the agent's
    spans land on a trace the platform can reference by run_id. No-ops if tracing
    is disabled or run_id is absent.
    """
    if not run_id or not _initialized:
        yield
        return
    try:
        from opentelemetry import context as _otel_context
        from opentelemetry import trace as _otel_trace
        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

        try:
            trace_id_int = _uuid.UUID(str(run_id)).int  # 128-bit → valid OTEL trace id
        except Exception:
            trace_id_int = int.from_bytes(
                hashlib.sha256(str(run_id).encode()).digest()[:16], "big"
            )
        span_id_int = (_uuid.uuid4().int & ((1 << 64) - 1)) or 1  # random non-zero 64-bit
        parent = NonRecordingSpan(
            SpanContext(
                trace_id=trace_id_int,
                span_id=span_id_int,
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
        )
        token = _otel_context.attach(_otel_trace.set_span_in_context(parent))
        try:
            yield
        finally:
            _otel_context.detach(token)
    except Exception as exc:  # never let trace-context binding break execution
        logger.debug("otel_run_context failed: %s", exc)
        yield


def _resolve_otlp() -> tuple[str, dict[str, str]] | None:
    """Return (endpoint, headers) for the OTLP span exporter, or None if unconfigured."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    raw_headers = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "").strip()

    if not endpoint:
        # Langfuse fallback — build the OTLP endpoint + auth from the langfuse env.
        host = os.getenv("LANGFUSE_HOST", "").rstrip("/")
        pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        sk = os.getenv("LANGFUSE_SECRET_KEY", "")
        if not (host and pk and sk):
            return None
        endpoint = f"{host}/api/public/otel/v1/traces"
        auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
        raw_headers = f"Authorization=Basic {auth}"

    headers: dict[str, str] = {}
    for pair in raw_headers.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            headers[k.strip()] = v.strip()
    return endpoint, headers


def setup_otel() -> bool:
    """Configure OTEL + OpenInference LangChain instrumentation. Idempotent.

    Returns True if tracing was enabled, False if unconfigured or on error.
    Call once at process startup — OpenInference auto-instruments every
    subsequent LangChain/LangGraph invocation globally, so no per-call wiring.
    """
    global _initialized
    if _initialized:
        return True

    resolved = _resolve_otlp()
    if resolved is None:
        logger.info("OTEL tracing disabled (no OTLP endpoint / Langfuse creds)")
        return False
    endpoint, headers = resolved

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from openinference.instrumentation.langchain import LangChainInstrumentor

        resource = Resource.create(
            {
                "service.name": os.getenv("AGENT_NAME", "agentshield-agent"),
                "service.namespace": os.getenv("AGENTSHIELD_AGENT_TEAM", "platform"),
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
        )
        trace.set_tracer_provider(provider)
        LangChainInstrumentor().instrument(tracer_provider=provider)

        _initialized = True
        logger.info("OTEL tracing enabled → %s", endpoint)
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("OTEL setup failed — LLM/tool span capture disabled: %s", exc)
        return False

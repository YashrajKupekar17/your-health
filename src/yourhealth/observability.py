"""Metrics, traces and build info for running the service (Prometheus + OpenTelemetry).

Metrics: RED for HTTP (rate, errors, duration) by route template, never by raw path, so session ids
don't explode label cardinality; plus the agent's own signals (LLM calls, turn outcomes) because a
200 response can still be a failed conversation (a handoff after an LLM error).

Traces: exported over OTLP only when OTEL_EXPORTER_OTLP_ENDPOINT is set, so tests and local runs
need no collector. Same PHI policy as logs: spans carry ids, codes and timings, never patient text.
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI, Request
from opentelemetry import trace
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

BUILD = {
    "version": os.getenv("APP_VERSION", "dev"),
    "commit": os.getenv("GIT_SHA", "unknown"),
    "build_date": os.getenv("BUILD_DATE", "unknown"),
}

HTTP_REQUESTS = Counter("yourhealth_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "yourhealth_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    # Chat turns call an LLM several times: the useful range runs to tens of seconds.
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15, 30, 60),
)
LLM_CALLS = Counter("yourhealth_llm_calls_total", "LLM API calls", ["kind", "result"])
LLM_LATENCY = Histogram(
    "yourhealth_llm_call_duration_seconds", "LLM API call latency", ["kind"], buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30)
)
TURNS = Counter("yourhealth_turns_total", "Patient turns by outcome", ["outcome"])
ACTIVE_SESSIONS = Gauge("yourhealth_active_sessions", "Conversations held in memory by this pod")
BUILD_INFO = Gauge("yourhealth_build_info", "Running build (value is always 1)", ["version", "commit"])
BUILD_INFO.labels(BUILD["version"], BUILD["commit"]).set(1)

tracer = trace.get_tracer("yourhealth")


def setup_tracing(app: FastAPI) -> bool:
    """Export spans when an OTLP endpoint is configured. Returns whether tracing is on."""
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "yourhealth"),
                "service.version": BUILD["version"],
                "vcs.commit": BUILD["commit"],
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
    # Probes and scrapes would drown the real traffic.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    return True


def instrument(app: FastAPI, active_sessions) -> None:
    """Add the RED middleware and the /metrics endpoint. `active_sessions()` returns the store size."""

    @app.middleware("http")
    async def red_metrics(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", "unmatched")
            if template != "/metrics":
                HTTP_REQUESTS.labels(request.method, template, str(status)).inc()
                HTTP_LATENCY.labels(request.method, template).observe(time.perf_counter() - started)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        ACTIVE_SESSIONS.set(active_sessions())
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class timed_llm_call:
    """Context manager: count + time one LLM call and wrap it in a span."""

    def __init__(self, kind: str, model: str):
        self.kind, self.model = kind, model

    def __enter__(self):
        self.started = time.perf_counter()
        self.span_cm = tracer.start_as_current_span(f"llm.{self.kind}", attributes={"llm.model": self.model})
        self.span = self.span_cm.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        LLM_LATENCY.labels(self.kind).observe(time.perf_counter() - self.started)
        result = "ok" if exc_type is None else exc_type.__name__
        LLM_CALLS.labels(self.kind, result).inc()
        self.span.set_attribute("llm.result", result)
        return self.span_cm.__exit__(exc_type, exc, tb)

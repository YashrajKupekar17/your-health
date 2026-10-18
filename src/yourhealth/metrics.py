"""Prometheus metrics for the web server and the agent."""

import time

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter("yourhealth_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "yourhealth_http_request_duration_seconds",
    "HTTP request latency",
    ["route"],
    buckets=(0.05, 0.1, 0.5, 1, 2.5, 5, 10, 20, 40),  # chat turns call the LLM, so go up to tens of seconds
)
LLM_CALLS = Counter("yourhealth_llm_calls_total", "LLM API calls", ["result"])
TURNS = Counter("yourhealth_turns_total", "Patient turns by outcome", ["outcome"])


def setup_metrics(app: FastAPI) -> None:
    @app.middleware("http")
    async def track(request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            # use the route template, not the raw path, otherwise every session id becomes a new series
            route = getattr(request.scope.get("route"), "path", "unmatched")
            if route != "/metrics":
                REQUESTS.labels(request.method, route, str(status)).inc()
                LATENCY.labels(route).observe(time.perf_counter() - start)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# Web UI + API. Build: docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t yourhealth .   Run: docker run -p 8000:8000 --env-file .env yourhealth
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY config ./config
COPY data ./data
RUN uv sync --frozen --no-dev

# fixed uid so k8s can check runAsNonRoot
RUN useradd --uid 10001 --no-create-home app
USER 10001

ARG GIT_SHA=unknown
ENV HOST=0.0.0.0 PORT=8000 DEMO_MODE=1 LOG_LEVEL=INFO GIT_SHA=${GIT_SHA} PATH=/app/.venv/bin:$PATH
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "-m", "yourhealth.server"]

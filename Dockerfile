# Web UI + API. Build: docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t yourhealth .
#              Run:   docker run -p 8000:8000 --env-file .env yourhealth

# --- build stage: resolve dependencies with uv into /app/.venv ---------------------------------
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
# Dependencies first so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

# --- runtime stage: no uv, no build tools, fixed non-root UID ----------------------------------
FROM python:3.12-slim
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.source="https://github.com/YashrajKupekar17/your-health" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.version="${APP_VERSION}"

# Numeric UID so Kubernetes can verify runAsNonRoot.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY config ./config
COPY data ./data
# Editable install: the package resolves config/ and data/ relative to /app.
COPY --from=build /app/src ./src

ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 PORT=8000 DEMO_MODE=1 LOG_LEVEL=INFO \
    GIT_SHA=${GIT_SHA} BUILD_DATE=${BUILD_DATE} APP_VERSION=${APP_VERSION}
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "-m", "yourhealth.server"]

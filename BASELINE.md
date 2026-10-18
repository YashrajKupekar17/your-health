# Baseline: the project before the DevOps work

| | |
|---|---|
| Name | Yashraj Kupekar |
| Student ID | yashraj.23bcs10102@sst.scaler.com |
| Project | YourHealth - patient appointment scheduling agent |
| Repository | https://github.com/YashrajKupekar17/your-health |
| Baseline commit | [`bc50eb827e1ae76066e93e2b816572f62dcd8e56`](https://github.com/YashrajKupekar17/your-health/commit/bc50eb827e1ae76066e93e2b816572f62dcd8e56) (also tagged `baseline`) |

## The application

A clinic's scheduling assistant. A patient chats (text or voice) with an LLM agent that verifies
identity and books, reschedules or cancels appointments through tools; code-level guards stop it
from claiming a booking that didn't happen or leaking another patient's data, and an emergency gate
hands urgent messages to staff before the LLM runs. FastAPI serves a web UI and a JSON/SSE API; the
LLM is OpenAI. Offline, an eval harness and an improvement loop tune the agent's config.

Request path: browser -> FastAPI (uvicorn, one process) -> agent loop -> OpenAI API, with tools
reading/writing an in-memory clinic loaded from `data/clinic.json`. Sessions are held in memory.

## DevOps features that already existed at the baseline

- `Dockerfile` (single stage, python:3.12-slim + uv, non-root user, `HEALTHCHECK` on `/healthz`)
- GitHub Actions `ci.yml`: ruff lint/format, config validation, pytest (on every push/PR). No image
  build, no scan, no deploy.
- `/healthz` endpoint (liveness only)
- Structured JSON logs with a PHI policy (ids/timings only)
- Settings from environment variables; `.env` gitignored, `.env.example` committed
- Makefile for local tasks

Not present: Kubernetes manifests, Helm, image registry/versioning, metrics, tracing, readiness
probe, security scanning, release/rollback process, deployment docs.

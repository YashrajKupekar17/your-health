# Baseline

- Name: Yashraj Kupekar
- Student ID: yashraj.23bcs10102@sst.scaler.com
- Project: YourHealth (clinic appointment scheduling agent)
- Repo: https://github.com/YashrajKupekar17/your-health
- Baseline commit: [bc50eb827e1ae76066e93e2b816572f62dcd8e56](https://github.com/YashrajKupekar17/your-health/commit/bc50eb827e1ae76066e93e2b816572f62dcd8e56) (tagged `baseline`)

## What the app does

Patients chat with an LLM agent (OpenAI) that verifies who they are and books, moves or cancels
appointments. FastAPI serves the web UI and the API. Sessions and bookings are kept in memory,
seeded from `data/clinic.json`.

## DevOps stuff that already existed

- Dockerfile (python slim + uv, non-root user, HEALTHCHECK on /healthz)
- GitHub Actions: lint + tests on every push/PR. No image build, no deploy, no scanning.
- /healthz endpoint
- JSON logs (no patient data in them)
- config through env vars, .env is gitignored

No Kubernetes, no metrics/tracing, no readiness check, no image registry or versioning.

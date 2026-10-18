# Running YourHealth on Kubernetes

What I added on top of the [baseline](../BASELINE.md), why, and how to run it.

## Setup

```
git push / PR
   └─> GitHub Actions: lint, tests, helm lint ─> docker build (tag = commit sha) ─> trivy ─> ghcr.io
                                                                                          │
                                                       helm upgrade --set image.tag=<sha> (manual)
                                                                                          v
minikube (calico)
  ingress-nginx ─> Service ─> yourhealth pod (FastAPI + agent) ─> OpenAI API (443)
                                   │  └─ OTLP traces ─> jaeger
                                   └─ /metrics <─ prometheus ─> grafana
```

Request path: browser -> ingress-nginx (`yourhealth.local`) -> Service -> pod :8000 -> FastAPI ->
agent -> OpenAI (1-4 calls per turn) -> reply.

What depends on what:
- The app needs its ConfigMap, the `yourhealth-llm` Secret and OpenAI.
- Prometheus, Grafana and Jaeger are only watching. If they go down, the app keeps working
  (traces are sent in the background, metrics are pulled).
- If OpenAI is down the app still answers, but the agent hands the patient to the front desk.

## Decisions

**1 replica, Deployment, no volume.** Sessions and bookings live in process memory. Two replicas
would be two clinics with different bookings, and a request could land on a pod that doesn't
know the session. So it stays at 1 replica and there's no HPA. A StatefulSet/PVC wouldn't help
because nothing is written to disk. To scale I'd first move sessions to Redis and bookings to
Postgres, then go to 2+ replicas with an HPA.

**Probes.** Liveness hits `/healthz` (process is up). Readiness hits `/readyz` (agent config loads).
OpenAI is not part of readiness on purpose: if it's down, all pods would go unready and users
would get 503s instead of the "let me get the front desk" handoff. Restarting doesn't fix OpenAI
anyway. That case is covered by an alert instead.

**Rolling update with maxUnavailable 0.** The new pod has to be ready before the old one goes
away, so a broken release never takes the app down. Deploys use `--atomic`, which rolls back
by itself if the new pod never gets ready.

**Resources.** Requests 50m CPU / 128Mi, limit 384Mi memory. Measured ~90Mi in use. No CPU limit
because the app mostly waits on OpenAI, and a CPU limit would only throttle the streamed replies.

**Helm.** One chart, `values.yaml` has the defaults and `values-dev.yaml` / `values-prod.yaml`
only override what's different (image source, demo mode, log level, host). Dev uses a locally
built image, prod uses the image CI pushed to ghcr. `image.tag` is required so nobody deploys
`latest` by accident.

**CI/CD.** Every PR/push to main: lint, tests, helm lint, then build the image tagged with the
commit sha, scan it with trivy (fails on HIGH/CRITICAL), scan the repo for secrets and k8s
misconfig, push to ghcr. Deploying is a manual `helm upgrade` because GitHub can't reach my
laptop's cluster, and deciding what goes to "prod" should be a person anyway. With a cloud
cluster I'd add a deploy job behind a GitHub environment with a required reviewer, or use Argo CD.

**Release / rollback.** Rolling updates. The running version is traceable: the image tag is the
commit sha, the pod has an `app.kubernetes.io/version=<sha>` label, `GET /version` returns the sha,
and Prometheus/Grafana show it. To go back: `helm rollback yourhealth <revision>`. I didn't do
canary or blue-green because both need two versions running at once, which the in-memory state
doesn't allow.

**Monitoring.** Plain Prometheus + Grafana + Jaeger manifests instead of kube-prometheus-stack,
which needs ~2GB and my laptop doesn't have that to spare.
- Metrics: requests by route and status, latency, LLM calls by result (ok / error type), turn
  outcomes (reply, handoff, llm_error, ...). Routes use the template (`/api/session/{session_id}/message`)
  so session ids don't create new series.
- Logs: the app's JSON logs, now with `trace_id`, read with `kubectl logs`.
- Traces: one per request, with a child span for each LLM call, so you can see a slow turn is
  slow because of OpenAI.

**SLOs / alerts.**
- 99% of API requests don't return 5xx (`YourHealthHigh5xx`)
- 99% of patient turns don't fail because of us: `llm_error`, `deadline`, `step_limit`
  (`YourHealthConversationFailures`). This one matters because when OpenAI fails the API still
  returns 200, so the 5xx alert never fires.
- `YourHealthDown` if the pod isn't scraped for 2 minutes.

Healthy looks like: pod up, `yourhealth_llm_calls_total{result="ok"}` going up and nothing else,
turn outcomes mostly `reply`/`handoff`, no alerts.

**Security.**
- OpenAI key and Grafana password are created with `kubectl create secret`, never committed.
  CI never sees a real key (tests fake the LLM).
- Pod runs as uid 10001, read-only root filesystem, all capabilities dropped, no privilege
  escalation, seccomp default.
- Own ServiceAccount with no permissions and no token mounted. Prometheus can only list pods.
- NetworkPolicy: only ingress-nginx and the observability namespace can reach the app, and the app
  can only reach DNS, port 443 on the internet and Jaeger. Needs Calico, the default minikube CNI
  doesn't enforce NetworkPolicy.
- Only ClusterIP + ingress, nothing else exposed. Prod runs with `DEMO_MODE=0`, which hides the
  patient list and tool internals.
- Trivy in CI for image CVEs, leaked secrets and k8s misconfig.

## How to run

Needs Docker (5GB+ memory), minikube, kubectl, helm, and a `.env` with `OPENAI_API_KEY`.

```bash
# cluster
minikube start --cpus=3 --memory=4000 --cni=calico
minikube addons enable ingress
minikube addons enable metrics-server

# secrets (never in git)
kubectl create namespace yourhealth
kubectl -n yourhealth create secret generic yourhealth-llm --from-env-file=.env
kubectl create namespace observability
kubectl -n observability create secret generic grafana-admin --from-literal=password=<pick one>

# monitoring
kubectl apply -k deploy/monitoring

# app: use a commit whose CI run is green (image is on ghcr)
helm upgrade --install yourhealth deploy/helm/yourhealth -n yourhealth \
  -f deploy/helm/yourhealth/values-prod.yaml --set image.tag=<commit sha> --atomic --wait

# or for dev, build locally:
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t yourhealth:$(git rev-parse HEAD) .
minikube image load yourhealth:$(git rev-parse HEAD)
helm upgrade --install yourhealth deploy/helm/yourhealth -n yourhealth \
  -f deploy/helm/yourhealth/values-dev.yaml --set image.tag=$(git rev-parse HEAD) --atomic --wait
```

Access (on macOS with the docker driver the minikube IP isn't reachable, so port-forward):

| | |
|---|---|
| app | `kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8080:80`, add `127.0.0.1 yourhealth.local` to /etc/hosts, open http://yourhealth.local:8080 |
| grafana | `kubectl -n observability port-forward svc/grafana 3000:3000`, user `admin` |
| prometheus | `kubectl -n observability port-forward svc/prometheus 9090:9090` |
| jaeger | `kubectl -n observability port-forward svc/jaeger 16686:16686` |

Useful:
```bash
curl -H 'Host: yourhealth.local' localhost:8080/version   # which commit is running
helm history yourhealth -n yourhealth
helm rollback yourhealth <revision> -n yourhealth
kubectl -n yourhealth logs deploy/yourhealth
```

## Known limitations

- 1 replica, so not highly available. A restart or deploy ends open chats and resets bookings.
- One local cluster. "dev" and "prod" are different values files, not different clusters.
- Prometheus and Jaeger keep data in memory/emptyDir, it's gone if the pod restarts.
- No Alertmanager, alerts are only visible in Prometheus/Grafana.
- No log aggregation (Loki), logs are read with kubectl.
- Images aren't signed.

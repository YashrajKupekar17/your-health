# Operational evidence

All output below is from real runs on 18 Oct 2026 (local minikube v1.37, Calico CNI, Docker Desktop
5GB/4 CPU on macOS). Secrets are never shown; the OpenAI key exists only in the `yourhealth-llm`
Secret, created from the gitignored `.env`.

**Tested version:** `d4e58da5a2b6c1bebdc34c02c483e0372e804728`
(image `ghcr.io/yashrajkupekar17/your-health:d4e58da…`, digest
`sha256:5c15daddace5a7694ab7f55a4e71ad1c8b070935d6ba10e9d2a0150f93f0fb7e`).

## 1. Source -> delivery: the pipeline run for that commit

CI run [39127135788](https://github.com/YashrajKupekar17/your-health/actions/runs/39127135788) (PR #1, head `d4e58da`):

```
checks: success          # ruff, config validation, 98 pytest tests, helm lint/render x3 envs, kustomize render
security-scan: success   # Trivy fs: secrets, vulnerable locked deps, Dockerfile/K8s misconfig (HIGH/CRITICAL gate)
build: success           # image tagged with the SHA, Trivy image gate, pushed to GHCR
deploy-e2e: success      # kind cluster: helm --atomic install, helm test, /version == SHA, session created
```

The gate catches real problems: the first run of the security job failed and was fixed, and Trivy
flagged Grafana/Jaeger running with a writable root FS (KSV-0014, HIGH), fixed in `1b45c79`.

## 2. Delivery -> runtime: gated promotion, traceable version

Promotion refuses a commit whose CI did not pass (`2071c32` failed CI):
```
$ scripts/deploy.sh 2071c321c3c508a6e77c13357e8b44416efc8792
CI for 2071c321c3c508a6e77c13357e8b44416efc8792 is 'completed/failure', not completed/success: refusing
exit=1
```

Promoting the green commit (helm test runs as part of the script):
```
$ scripts/deploy.sh d4e58da5a2b6c1bebdc34c02c483e0372e804728
Deployed ghcr.io/yashrajkupekar17/your-health:d4e58da5a2b6c1bebdc34c02c483e0372e804728 as revision 4.
TEST SUITE: yourhealth-test   Phase: Succeeded

$ kubectl -n yourhealth rollout history deploy/yourhealth
REVISION  CHANGE-CAUSE
1         deploy 1b45c797c81794f581f07b1b59936d2f4e16fb2a (helm rev 1)
2         deploy d4e58da5a2b6c1bebdc34c02c483e0372e804728 (helm rev 4)
```

The running version is the same everywhere:
```
pod image:      ghcr.io/yashrajkupekar17/your-health:d4e58da5a2b6c1bebdc34c02c483e0372e804728
pod imageID:    ghcr.io/yashrajkupekar17/your-health@sha256:5c15dadd…f0fb7e
label:          app.kubernetes.io/version=d4e58da5a2b6c1bebdc34c02c483e0372e804728
GET /version:   {"version":"0.1.0","commit":"d4e58da5…","build_date":"…"}
Prometheus:     yourhealth_build_info{commit="d4e58da5a2b6c1bebdc34c02c483e0372e804728"} 1   (also via Grafana's datasource)
```

**Incident while doing this (real, not staged):** the first promotion of `d4e58da` was interrupted
mid-upgrade, leaving the release locked:
```
2  pending-upgrade  Preparing upgrade
Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress
```
Recovery: `helm rollback yourhealth 1` (clears the lock, revision 3), then re-promote (revision 4).

## 3. A working user journey through the real entry point

`scripts/smoke.sh` goes through ingress-nginx (Host `yourhealth.local`), not a port-forward to the pod:
```
version:  {"version":"0.1.0","commit":"1b45c797c81794f581f07b1b59936d2f4e16fb2a",…}
ready:    {"ready":true,"providers":4}
session:  e7e82c6c6afb4dee82ad2eac96a3c2b3
reply:    {"reply":"Could you please confirm if by \"next week\" you mean the week starting Monday 19 October 2026? Also, may I have your full name and date of birth to proceed with booking?","ended":false}
OK: user journey succeeded through ingress
```
(Run against revision 1; repeated against `d4e58da` during recovery in section 6.)

## 4. Observability: metrics, logs and traces tie together

Prometheus discovers and scrapes the pod; SLO recording rules and alerts load:
```
target yourhealth-646d98c7d6-7x57b  up
YourHealthDown inactive ok             yourhealth:api_requests:rate5m ok
YourHealthApiErrorBudgetBurn inactive  yourhealth:api_errors:ratio_rate5m ok
YourHealthConversationFailures inactive yourhealth:turn_failures:ratio_rate5m ok
YourHealthSlowTurns inactive           yourhealth:turn_latency:p95_5m ok
```
Grafana: dashboard "YourHealth - service health & SLOs" (`/d/yourhealth`) provisioned from git with
Prometheus + Jaeger datasources.

One turn, three signals joined by `trace_id`:
```
log:   {"event":"turn","outcome":"reply","llm_calls":1,"prompt_tokens":1171,"ms":3055.7,"trace_id":"c4d06f7a42f2c20bd951a7b7ae6b8f4b",…}
trace: c4d06f7a… POST /api/session/{session_id}/message 3071ms
         └─ llm.chat 3054ms        <- the turn's time is the LLM call, not our code
metric: yourhealth_http_request_duration_seconds{route="/api/session/{session_id}/message"}  (route template, no session ids)
```

## 5. Security controls, verified at runtime

```
$ kubectl exec <app pod> -- id                ->  uid=10001(app) gid=10001(app)
$ kubectl exec <app pod> -- touch /app/x      ->  Read-only file system
$ ls /var/run/secrets/kubernetes.io           ->  No such file or directory   (no SA token mounted)
container securityContext: {"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"readOnlyRootFilesystem":true}
git ls-files | grep -c '^.env$'               ->  0
```
NetworkPolicy is enforced (Calico):
```
from namespace default -> yourhealth svc:      curl: (28) Connection timed out after 5002 ms   (blocked)
from namespace observability -> yourhealth:    {"ok":true,"sessions":2}                       (allowed: Prometheus)
from app pod -> grafana.observability:3000:    URLError timed out                              (egress blocked)
```

## 6. Fault and recovery

### A. Bad configuration release -> automatic rollback, no user impact
- **Change:** release with `env.LLM_TIMEOUT_S=0` (invalid: must be > 0).
- **Symptom:** new pod `CrashLoopBackOff`; old pod still `1/1 Running` (`maxUnavailable: 0`).
- **Evidence:** pod log `ValidationError ... Input should be greater than 0`.
- **Recovery (automatic):** `--atomic` / rollback-on-failure:
  ```
  Error: UPGRADE FAILED: release yourhealth failed, and has been rolled back due to rollback-on-failure being set
  5  failed    Upgrade "yourhealth" failed: resource Deployment/yourhealth/yourhealth not ready
  6  deployed  Rollback to 4
  ```
- **Verification:** a request every second for the whole 2 minutes through the ingress: **120/120 HTTP 200**.

### B. Release that is "healthy" but broken for patients -> alert -> rollback
- **Change:** release pointing at a Secret with an invalid OpenAI key (simulates a bad credential rotation).
- **Symptom:** nothing in the usual places: pod `1/1 Running`, probes green, **45/45 HTTP 200**, API 5xx ratio 0.
  But every patient got `"Sorry, I'm having trouble with that. Let me connect you with our front desk."`
- **Evidence** (this is why the conversation-success SLI exists; HTTP status alone said all was well):
  ```
  sum by (result) (yourhealth_llm_calls_total)   {result="AuthenticationError"} 46
  sum by (outcome) (yourhealth_turns_total)      {outcome="llm_error"} 46
  yourhealth:turn_failures:ratio_rate5m          1
  ALERT YourHealthConversationFailures           firing (after its 5m 'for')
  log: {"event":"turn","outcome":"llm_error","error_type":"AuthenticationError","handoff":true,…}
  ```
- **Hypothesis:** the dependency rejects our credential (AuthenticationError, not timeouts), and it
  started with revision 7, the only change being the Secret reference.
- **Change:** `helm rollback yourhealth 6` (revision 8) and delete the bad Secret.
- **Verification:** three journeys through the ingress get real agent replies, e.g.
  `"To help you book a general checkup next week, may I have your full name and date of birth, please?"`;
  5 minutes later (18:28) Prometheus shows `{result="ok"} 13`, no failed turns, and
  `active alerts: []`: the alert resolved by itself once the 5m window was clean.

## 7. Resource sizing
`kubectl top` after the recovery traffic: **app 19m CPU / 89Mi**; grafana 231Mi, prometheus 94Mi,
jaeger 42Mi. App requests 50m/128Mi (measured 89Mi fits), limit 384Mi memory, no CPU limit (I/O-bound, see DEVOPS.md 3.3).

## 8. Limitations of this evidence
- Single local cluster; "prod" is the `values-prod.yaml` release on minikube, not separate infrastructure.
- Counters reset when the pod is replaced (expected for Prometheus counters; `rate()` handles it).
- Jaeger's in-memory UI showed some spans listed twice with identical span IDs; a display quirk of
  the all-in-one memory store, not double instrumentation (each span ID is unique per operation).
- Screenshots are not included; every claim above is reproducible with `scripts/` and the queries shown.

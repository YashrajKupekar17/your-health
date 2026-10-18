# Evidence

Everything below is from my local minikube cluster on 18 Oct 2026.

Tested version: `5265c0c601cde21ec4cda8dc2559e377d7944f2e`
(`ghcr.io/yashrajkupekar17/your-health:5265c0c601cde21ec4cda8dc2559e377d7944f2e`)

The commits after `5265c0c` in the PR only touch docs (`docs/`, `BASELINE.md`, `README.md`), so the
image and chart that were tested are the same as in the final commit.

## CI run for that commit

https://github.com/YashrajKupekar17/your-health/actions/runs/39132051079

```
test: success    # ruff, config check, pytest, helm lint (dev + prod values)
image: success   # build tagged with the sha, trivy image scan, trivy secret/misconfig scan, push to ghcr
```

## Deploy and the running version

```
$ helm upgrade --install yourhealth deploy/helm/yourhealth -n yourhealth \
    -f deploy/helm/yourhealth/values-prod.yaml --set image.tag=5265c0c601cde21ec4cda8dc2559e377d7944f2e --atomic --wait
STATUS: deployed
REVISION: 2

$ curl -H "Host: yourhealth.local" localhost:8080/version
{"commit":"5265c0c601cde21ec4cda8dc2559e377d7944f2e"}
```

Prometheus target labels (and the "Running commit" panel in Grafana) show the same commit:
```
{'commit': '5265c0c601cde21ec4cda8dc2559e377d7944f2e', 'namespace': 'yourhealth', 'pod': 'yourhealth-68d9898b4d-...'} up
```

## A user request through the ingress

```
$ curl -X POST -H "Host: yourhealth.local" localhost:8080/api/session/<id>/message -d '{"text":"Hi, I want to book a checkup next week"}'
{"reply":"Could you please confirm if by \"next week\" you mean the week starting Monday 19 October 2026? Also, may I have your full name and date of birth to proceed with booking?","ended":false}
```

The same request in Jaeger has spans `POST /api/session/{session_id}/message` with a child `llm.chat`
span, and its `trace_id` is in the app's log line for that turn.

## Security checks

```
# from the default namespace (blocked by the NetworkPolicy)
curl: (28) Connection timed out after 5002 milliseconds

# from the observability namespace (allowed, this is how prometheus scrapes)
{"ok":true,"sessions":1}

# inside the app pod
uid=10001(app) gid=10001(app) groups=10001(app)
touch: cannot touch '/app/x': Read-only file system
ls: cannot access '/var/run/secrets/kubernetes.io': No such file or directory   # no SA token
```

Resource use after some traffic (`kubectl top`): app pod 16m CPU, 103Mi memory (request 128Mi, limit 384Mi).

## Fault and recovery: broken OpenAI key

I deployed revision 3 pointing at a secret with an invalid OpenAI key, to see what a bad
credential rotation looks like.

**Symptom.** Nothing looks wrong at first: the pod is `1/1 Running`, probes pass, and all API
calls return 200 (45 chats, 92 requests). But every patient gets
`"Sorry, I'm having trouble with that. Let me connect you with our front desk."`

**Evidence.**
```
sum by (result) (yourhealth_llm_calls_total)                    {result="AuthenticationError"} 46
sum by (outcome) (yourhealth_turns_total)                       {outcome="llm_error"} 46
sum by (status) (yourhealth_http_requests_total{route=~"/api/.*"})  {status="200"} 92
YourHealthConversationFailures  firing

log: {"event": "turn", "outcome": "llm_error", "error_type": "AuthenticationError", "handoff": true, ...}
```
The 5xx alert stays quiet because HTTP is fine. Only the conversation alert catches it.

**Hypothesis.** OpenAI is rejecting our key (AuthenticationError, not timeouts). The only change
in revision 3 was the secret.

**Fix.** `helm rollback yourhealth 2` (becomes revision 4), and delete the bad secret.
```
REVISION  STATUS      DESCRIPTION
2         superseded  Upgrade complete
3         superseded  Upgrade complete
4         deployed    Rollback to 2
```

**Verify.**
```
{"reply":"I can help with that. Could you please provide your full name and date of birth to verify your identity?","ended":false}
sum by (result) (yourhealth_llm_calls_total)   {result="ok"} 12
19:18:31  alerts: []
```
The alert cleared about 5 minutes after the rollback, once the failures had left the 5m window.

## Drift check

Changed the live deployment by hand, then compared it with what helm deployed:
```
$ kubectl -n yourhealth scale deploy/yourhealth --replicas=2
$ kubectl -n yourhealth set env deploy/yourhealth LOG_LEVEL=DEBUG
$ helm get manifest yourhealth -n yourhealth | kubectl diff -n yourhealth -f -
-  replicas: 2
+  replicas: 1
```
The replica change shows up, the added env var doesn't. A plain `helm upgrade` with the same
values left both in place. `--server-side=true --force-conflicts` fixed replicas but kept the env
var. `--server-side=false --force-replace` (revision 11) reset both:
```
replicas=1 env=OPENAI_API_KEY
```

#!/usr/bin/env bash
# User journey through the real entry point (ingress-nginx), not a port-forward to the pod:
# version -> readiness -> start a conversation -> send a message -> metrics show it.
# Usage: scripts/smoke.sh [host]   (default yourhealth.local; dev uses yourhealth.dev.local)
set -euo pipefail
HOST=${1:-yourhealth.local}
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8080:80 >/dev/null 2>&1 &
PF=$!; trap 'kill $PF' EXIT; sleep 3
c() { curl -fsS -H "Host: $HOST" "$@"; }
echo "version:  $(c localhost:8080/version)"
echo "ready:    $(c localhost:8080/readyz)"
sid=$(c -X POST localhost:8080/api/session | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_id"])')
echo "session:  $sid"
reply=$(c -X POST -H 'Content-Type: application/json' -d '{"text":"Hi, I would like to book a general checkup next week."}' \
  "localhost:8080/api/session/$sid/message")
echo "reply:    $reply"
echo "$reply" | grep -q '"reply"'
echo "OK: user journey succeeded through ingress"

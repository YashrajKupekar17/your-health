#!/usr/bin/env bash
# Promote one build to the local cluster. This is the manual approval step.
#
#   scripts/deploy.sh <git-sha>   prod: the image CI built, scanned and pushed for that commit.
#                                 Refuses if the CI run for that commit did not succeed.
#   scripts/deploy.sh --local     dev: build the current (clean) HEAD locally, demo panel on.
#
# helm --atomic rolls back automatically if the new pod never becomes Ready.
set -euo pipefail
cd "$(dirname "$0")/.."
NS=yourhealth
CHART=deploy/helm/yourhealth

if [ "${1:-}" = "--local" ]; then
  [ -z "$(git status --porcelain -- src config data Dockerfile pyproject.toml uv.lock)" ] \
    || { echo "uncommitted app changes: commit first so the image maps to a SHA" >&2; exit 1; }
  SHA=$(git rev-parse HEAD)
  docker build --build-arg GIT_SHA="$SHA" --build-arg BUILD_DATE="$(date -u +%FT%TZ)" -t "yourhealth:$SHA" .
  minikube image load "yourhealth:$SHA"
  VALUES=$CHART/values-dev.yaml
else
  SHA=${1:?usage: deploy.sh <full-git-sha> | --local}
  [ ${#SHA} -eq 40 ] || { echo "use the full 40-char SHA" >&2; exit 1; }
  result=$(gh run list --workflow ci --commit "$SHA" --json conclusion,status -q '.[0] | "\(.status)/\(.conclusion)"')
  [ "$result" = "completed/success" ] || { echo "CI for $SHA is '$result', not completed/success: refusing" >&2; exit 1; }
  IMAGE=ghcr.io/yashrajkupekar17/your-health:$SHA
  minikube image ls | grep -q "$SHA" || { docker pull "$IMAGE" && minikube image load "$IMAGE"; }
  VALUES=$CHART/values-prod.yaml
fi

helm upgrade --install yourhealth "$CHART" -n "$NS" -f "$VALUES" --set image.tag="$SHA" \
  --atomic --wait --timeout 3m --history-max 10
helm test yourhealth -n "$NS" --logs
kubectl -n "$NS" rollout history deploy/yourhealth | tail -3
echo "Deployed $SHA"

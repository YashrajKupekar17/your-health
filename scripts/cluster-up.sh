#!/usr/bin/env bash
# One-time local platform: minikube (Calico so NetworkPolicy is enforced), ingress-nginx,
# metrics-server, the observability stack, and the app's secrets (from .env, never from git).
set -euo pipefail
cd "$(dirname "$0")/.."

minikube status --format "{{.Host}}" 2>/dev/null | grep -q Running || minikube start --driver=docker --cpus=3 --memory=4000 --cni=calico
# The addon command gives up waiting on slow image pulls; the controller keeps starting anyway.
minikube addons enable ingress || echo "ingress still starting, continuing"
minikube addons enable metrics-server || true

kubectl apply -f deploy/observability/namespace.yaml
kubectl create namespace yourhealth --dry-run=client -o yaml | kubectl apply -f -

# Secrets are created out of band and never printed. Re-running keeps existing values.
if ! kubectl -n observability get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n observability create secret generic grafana-admin --from-literal=password="$(openssl rand -hex 16)"
fi
if ! kubectl -n yourhealth get secret yourhealth-llm >/dev/null 2>&1; then
  key=$(grep -E '^OPENAI_API_KEY=' .env | cut -d= -f2-)
  [ -n "$key" ] || { echo "OPENAI_API_KEY missing from .env" >&2; exit 1; }
  kubectl -n yourhealth create secret generic yourhealth-llm --from-literal=OPENAI_API_KEY="$key"
fi

kubectl apply -k deploy/observability
kubectl -n observability rollout status deploy/prometheus deploy/grafana deploy/jaeger --timeout=5m
echo "Platform ready. Grafana password: kubectl -n observability get secret grafana-admin -o jsonpath='{.data.password}' | base64 -d"

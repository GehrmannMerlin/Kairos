#!/usr/bin/env bash
# Bring up the full local stack. Run from the repository root.
set -euo pipefail
cd "$(dirname "$0")/../.."
COMPOSE="docker compose -f infra/compose/compose.yaml"

if [[ ! -f .env ]]; then
  echo "No .env found — copying from .env.example (dev defaults are safe)."
  cp .env.example .env
fi

echo "==> building and starting services"
"$COMPOSE" up -d --build

echo "==> waiting for api /health/live ..."
for i in $(seq 1 60); do
  if curl -sf http://localhost:8000/api/health/live >/dev/null 2>&1; then
    echo "api is up"
    break
  fi
  sleep 2
done

echo "==> stack status"
"$COMPOSE" ps
echo
echo "API          http://localhost:8000/api/health/live"
echo "Web          http://localhost:5173"
echo "Temporal UI  http://localhost:8088"
echo "MinIO        http://localhost:9001"

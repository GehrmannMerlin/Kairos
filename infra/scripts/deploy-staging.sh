#!/usr/bin/env bash
# Build immutable kairos-staging images locally, transfer via docker save/load,
# sync compose + vhost to the server, and bring the stack up.
#
# Usage (from repository root):
#   DEPLOY_HOST=47.238.145.24 ./infra/scripts/deploy-staging.sh
#
# DEPLOY-GATE-1 bootstrap transport: local build -> docker save -> SSH -> docker load.
# This is a Staging-first-bootstrap mechanism, NOT the Production release path.
# A future REGISTRY_IMAGE_PREFIX can switch CI -> Registry -> server pull with no
# business-code refactor (see agent-project-implementation-plan.md I-005).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required (e.g. 47.238.145.24)}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")
SCP=(scp -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
SERVER_COMPOSE_DIR="/srv/kairos/compose"
SERVER_OTEL_DIR="/srv/kairos/otel"
SERVER_VHOST_DIR="/srv/kairos/deploy/nginx/conf.d"
PLATFORM="${PLATFORM:-linux/amd64}"   # server arch confirmed x86_64

[[ -f "$SSH_KEY" ]] || { echo "missing SSH key: $SSH_KEY"; exit 1; }

SHA="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
WEB_IMAGE="kairos-web:staging-$SHA"
API_IMAGE="kairos-api:staging-$SHA"
WORKER_IMAGE="kairos-worker:staging-$SHA"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

echo "==> building immutable images (platform=$PLATFORM, sha=$SHA)"
docker buildx build --platform "$PLATFORM" --load -t "$WEB_IMAGE" "$ROOT/frontend/" \
  || fail "web image build failed"
docker buildx build --platform "$PLATFORM" --load -t "$API_IMAGE" "$ROOT/backend/" \
  || fail "api image build failed"
docker buildx build --platform "$PLATFORM" --load -t "$WORKER_IMAGE" "$ROOT/backend/" \
  || fail "worker image build failed"

for img in "$WEB_IMAGE" "$API_IMAGE" "$WORKER_IMAGE"; do
  docker image inspect "$img" >/dev/null 2>&1 || fail "image not found: $img"
done
echo "    images: $WEB_IMAGE / $API_IMAGE / $WORKER_IMAGE"

echo "==> transferring images (docker save | ssh docker load)"
docker save "$WEB_IMAGE" "$API_IMAGE" "$WORKER_IMAGE" \
  | "${SSH[@]}" "docker load" || fail "image transfer failed"

echo "==> syncing compose, vhost and otel config"
"${SSH[@]}" "mkdir -p ${SERVER_OTEL_DIR}"
"${SCP[@]}" "$ROOT"/infra/compose/compose.base.yml \
            "$ROOT"/infra/compose/compose.staging.yml \
            "$ROOT"/infra/compose/compose.staging.override.yml \
            "${DEPLOY_USER}@${DEPLOY_HOST}:${SERVER_COMPOSE_DIR}/"
"${SCP[@]}" "$ROOT"/infra/reverse-proxy/zz-kairos-staging-tls.conf \
            "${DEPLOY_USER}@${DEPLOY_HOST}:${SERVER_VHOST_DIR}/"
# compose.base.yml mounts ../otel/otel-collector.yaml relative to the compose dir,
# so on the server it resolves to /srv/kairos/otel/otel-collector.yaml.
"${SCP[@]}" "$ROOT"/infra/otel/otel-collector.yaml \
            "${DEPLOY_USER}@${DEPLOY_HOST}:${SERVER_OTEL_DIR}/otel-collector.yaml"

echo "==> validating compose config on server"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml config -q" \
  || fail "compose config validation failed on server"

echo "==> bringing up kairos-staging"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml \
    --env-file /srv/kairos/env/staging.env up -d --wait 2>&1 | tail -25" \
  || fail "compose up failed"

echo "==> stack status"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml ps"

echo "DONE: $SHA (images: $WEB_IMAGE / $API_IMAGE / $WORKER_IMAGE)"

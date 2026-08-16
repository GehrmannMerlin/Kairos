#!/usr/bin/env bash
# kairos-staging deploy via the OCI container registry (standard path).
#
# Server pulls immutable images by tag (registry login must already exist on the
# server via `docker login`; credentials stay in ~/.docker/config.json), syncs
# compose/vhost/otel, validates config and brings the stack up.
#
# Usage (from repository root):
#   REGISTRY=ghcr.io NAMESPACE=gehrmannmerlin \
#   RELEASE_TAG=v0.1.2-dbbd7b5e2fb9 DEPLOY_HOST=47.238.145.24 \
#   ./infra/scripts/deploy-staging.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

REGISTRY="${REGISTRY:?REGISTRY required}"
NAMESPACE="${NAMESPACE:?NAMESPACE required}"
RELEASE_TAG="${RELEASE_TAG:?RELEASE_TAG required (from registry-push.sh)}"
DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required (e.g. 47.238.145.24)}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")
SCP=(scp -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
SERVER_COMPOSE_DIR="/srv/kairos/compose"
SERVER_OTEL_DIR="/srv/kairos/otel"
SERVER_VHOST_DIR="/srv/kairos/deploy/nginx/conf.d"

WEB_IMAGE="${REGISTRY}/${NAMESPACE}/kairos-web:${RELEASE_TAG}"
API_IMAGE="${REGISTRY}/${NAMESPACE}/kairos-api:${RELEASE_TAG}"
WORKER_IMAGE="${REGISTRY}/${NAMESPACE}/kairos-worker:${RELEASE_TAG}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
ok() { printf '  ok: %s\n' "$*"; }

echo "==> server: docker pull immutable images (tag=$RELEASE_TAG)"
"${SSH[@]}" "docker pull ${WEB_IMAGE} && docker pull ${API_IMAGE} && docker pull ${WORKER_IMAGE}" \
  || fail "docker pull (is the server logged into ${REGISTRY}?)"
ok "docker pull"

echo "==> syncing compose, vhost and otel config"
"${SSH[@]}" "mkdir -p ${SERVER_OTEL_DIR}"
"${SCP[@]}" "$ROOT"/infra/compose/compose.base.yml \
            "$ROOT"/infra/compose/compose.staging.yml \
            "$ROOT"/infra/compose/compose.staging.override.yml \
            "${DEPLOY_USER}@${DEPLOY_HOST}:${SERVER_COMPOSE_DIR}/"
"${SCP[@]}" "$ROOT"/infra/reverse-proxy/zz-kairos-staging-tls.conf \
            "${DEPLOY_USER}@${DEPLOY_HOST}:${SERVER_VHOST_DIR}/"
"${SCP[@]}" "$ROOT"/infra/otel/otel-collector.yaml \
            "${DEPLOY_USER}@${DEPLOY_HOST}:${SERVER_OTEL_DIR}/otel-collector.yaml"

echo "==> validating compose config on server"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml config -q" \
  || fail "compose config validation failed on server"

echo "==> staging up: infra first (storage/db/temporal/init)"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml \
    --env-file /srv/kairos/env/staging.env up -d postgres minio temporal minio-init" \
  || fail "infra up failed"

echo "==> staging up: app stack with readiness wait"
"${SSH[@]}" "set -o pipefail; cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml \
    --env-file /srv/kairos/env/staging.env up -d --wait \
    otel-collector migrate api worker web 2>&1 | tail -25" \
  || fail "compose up failed"

echo "==> stack status"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml ps"

echo "DONE: $RELEASE_TAG (images: $WEB_IMAGE / $API_IMAGE / $WORKER_IMAGE)"

#!/usr/bin/env bash
# kairos-production immutable deploy via the OCI container registry (standard path).
#
# Server pulls immutable images by tag (registry login must already exist on the
# server), syncs compose.production.yml + release-manifest.sh, validates compose,
# writes the release manifest, and brings the stack up in two stages (infra first,
# then migrate -> api -> worker -> web). The infra `up` deliberately omits `--wait`
# because one-shot init containers (minio-init/temporal-init, restart:no) make
# docker compose misjudge readiness for the whole batch.
#
# Usage (from repository root):
#   REGISTRY=ghcr.io NAMESPACE=gehrmannmerlin \
#   RELEASE_TAG=v0.1.2-dbbd7b5e2fb9 BACKUP_ID=production-... \
#   PREVIOUS_RELEASE=v0.1.1-3538e8841fd6 ROLLBACK_TARGET=kairos-*:v0.1.1-3538e8841fd6 \
#   ./infra/scripts/deploy-production.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

REGISTRY="${REGISTRY:?REGISTRY required}"
NAMESPACE="${NAMESPACE:?NAMESPACE required}"
RELEASE_TAG="${RELEASE_TAG:?RELEASE_TAG required (from registry-push.sh)}"
DEPLOY_HOST="${DEPLOY_HOST:-47.238.145.24}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")
SCP=(scp -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
SERVER_COMPOSE_DIR="/srv/kairos/compose"
SERVER_RELEASES="/srv/kairos/releases"

WEB_IMAGE="${REGISTRY}/${NAMESPACE}/kairos-web:${RELEASE_TAG}"
API_IMAGE="${REGISTRY}/${NAMESPACE}/kairos-api:${RELEASE_TAG}"
WORKER_IMAGE="${REGISTRY}/${NAMESPACE}/kairos-worker:${RELEASE_TAG}"

# Release manifest inputs (recorded on the server; never secrets).
BACKUP_ID="${BACKUP_ID:-not-yet}"
PREVIOUS_RELEASE="${PREVIOUS_RELEASE:-none-first-release}"
ROLLBACK_TARGET="${ROLLBACK_TARGET:-none-first-release}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

echo "==> server: docker pull immutable images (tag=$RELEASE_TAG)"
"${SSH[@]}" "docker pull ${WEB_IMAGE} && docker pull ${API_IMAGE} && docker pull ${WORKER_IMAGE}" \
  || fail "docker pull (is the server logged into ${REGISTRY}?)"

echo "==> syncing compose.production.yml + release-manifest.sh"
"${SCP[@]}" "$ROOT"/infra/compose/compose.production.yml \
            "${DEPLOY_USER}@${DEPLOY_HOST}:${SERVER_COMPOSE_DIR}/"
"${SCP[@]}" "$ROOT"/infra/scripts/release-manifest.sh \
            "${DEPLOY_USER}@${DEPLOY_HOST}:/srv/kairos/scripts/"

echo "==> validating compose config on server"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env config -q" || fail "compose config validation failed"

echo "==> writing release manifest"
# RELEASE_TAG=vX.Y.Z-<sha12> -> RELEASE_VERSION=vX.Y.Z, RELEASE_SHA=<sha12>
RELEASE_VERSION="${RELEASE_TAG%-*}"
RELEASE_SHA="${RELEASE_TAG##*-}"
"${SSH[@]}" "cd /srv/kairos && RELEASE_VERSION=${RELEASE_VERSION} RELEASE_SHA=${RELEASE_SHA} \
    BACKUP_ID=${BACKUP_ID} PREVIOUS_RELEASE=${PREVIOUS_RELEASE} \
    ROLLBACK_TARGET=${ROLLBACK_TARGET} \
    bash /srv/kairos/scripts/release-manifest.sh" || fail "release manifest failed"

echo "==> production up: infra first (storage/db/temporal/init) — no --wait (init containers)"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env up -d postgres minio temporal minio-init temporal-init" \
  || fail "infra up failed"

echo "==> production up: app stack (migrate → api → worker → web) with readiness wait"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env up -d --wait 2>&1 | tail -20" \
  || fail "app up failed"

echo "DEPLOY_PRODUCTION_OK images=$WEB_IMAGE/$API_IMAGE/$WORKER_IMAGE"

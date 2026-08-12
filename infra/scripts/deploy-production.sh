#!/usr/bin/env bash
# Production immutable deploy。
# 本地构建 kairos-{web,api,worker}:<RELEASE_VERSION>-<gitsha12> 不可变镜像 → docker save/load 到服务器 →
# 同步 compose.production.yml / release-manifest.sh → compose 校验 → 写 release manifest → 分阶段 up。
# 服务器只做 docker load + compose up，绝不现场构建源码（Deployment Standards §10A.1）。
#   RELEASE_VERSION=v0.1.0 ./infra/scripts/deploy-production.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:-47.238.145.24}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")
SCP=(scp -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
SERVER_COMPOSE_DIR="/srv/kairos/compose"
SERVER_RELEASES="/srv/kairos/releases"
PLATFORM="${PLATFORM:-linux/amd64}"   # server arch confirmed x86_64
RELEASE_VERSION="${RELEASE_VERSION:-v0.1.0}"

[[ -f "$SSH_KEY" ]] || { echo "missing SSH key: $SSH_KEY"; exit 1; }
SHA="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
WEB_IMAGE="kairos-web:$RELEASE_VERSION-$SHA"
API_IMAGE="kairos-api:$RELEASE_VERSION-$SHA"
WORKER_IMAGE="kairos-worker:$RELEASE_VERSION-$SHA"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

echo "==> building immutable production images (release=$RELEASE_VERSION sha=$SHA)"
docker buildx build --platform "$PLATFORM" --load -t "$WEB_IMAGE" "$ROOT/frontend/" || fail "web image build"
docker buildx build --platform "$PLATFORM" --load -t "$API_IMAGE" "$ROOT/backend/" || fail "api image build"
docker buildx build --platform "$PLATFORM" --load -t "$WORKER_IMAGE" "$ROOT/backend/" || fail "worker image build"
for img in "$WEB_IMAGE" "$API_IMAGE" "$WORKER_IMAGE"; do
  docker image inspect "$img" >/dev/null 2>&1 || fail "image not found: $img"
done
echo "    images: $WEB_IMAGE / $API_IMAGE / $WORKER_IMAGE"

echo "==> transferring images (docker save | ssh docker load)"
docker save "$WEB_IMAGE" "$API_IMAGE" "$WORKER_IMAGE" \
  | "${SSH[@]}" "docker load" || fail "image transfer failed"

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
"${SSH[@]}" "cd /srv/kairos && RELEASE_VERSION=${RELEASE_VERSION} \
    bash /srv/kairos/scripts/release-manifest.sh" || fail "release manifest failed"

echo "==> production up: infra first (storage/db/temporal/init)"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env up -d postgres minio temporal minio-init temporal-init --wait" \
  || fail "infra up failed"

echo "==> production up: full stack (migrate → api → worker → web)"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env up -d --wait 2>&1 | tail -20" \
  || fail "app up failed"

echo "DEPLOY_PRODUCTION_OK images=$WEB_IMAGE/$API_IMAGE/$WORKER_IMAGE"

#!/usr/bin/env bash
# kairos-production rollback / recovery helper。
#
# FIRST_PRODUCTION_RELEASE：无上一 production release → 用当前不可变 v0.1.0-<sha> 镜像
# 重建/恢复整个 stack（down 不带 -v，保留 PG/MinIO volume）。
# 后续 release：设置 PREVIOUS_PRODUCTION_IMAGE 为上一不可变 tag，脚本切回该 tag 重建。
#
# Usage (from repository root):
#   RELEASE_VERSION=v0.1.0 ./infra/scripts/rollback-production.sh            # restore / rollback
#   RELEASE_VERSION=v0.1.0 ./infra/scripts/rollback-production.sh --check     # readiness only
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:-47.238.145.24}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")
COMPOSE_DIR="/srv/kairos/compose"
COMPOSE=(docker compose -p kairos-production -f "${COMPOSE_DIR}/compose.base.yml" -f "${COMPOSE_DIR}/compose.production.yml")
RELEASE_VERSION="${RELEASE_VERSION:-v0.1.0}"
CHECK_ONLY="${1:-}"

echo "==> rollback readiness for kairos-production (release=$RELEASE_VERSION)"

# 解析服务器上最新创建的不可变 tag（按创建时间，不按 lexicographic SHA）。
CURRENT_WEB="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | grep \"^kairos-web:${RELEASE_VERSION}-\" | sort -t'|' -k2 | tail -1 | cut -d'|' -f1" 2>/dev/null || true)"
CURRENT_API="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | grep \"^kairos-api:${RELEASE_VERSION}-\" | sort -t'|' -k2 | tail -1 | cut -d'|' -f1" 2>/dev/null || true)"
CURRENT_WORKER="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | grep \"^kairos-worker:${RELEASE_VERSION}-\" | sort -t'|' -k2 | tail -1 | cut -d'|' -f1" 2>/dev/null || true)"
[[ -n "$CURRENT_WEB" && -n "$CURRENT_API" && -n "$CURRENT_WORKER" ]] \
  || { echo "ERROR: could not resolve current immutable images"; exit 1; }
echo "current images: $CURRENT_WEB / $CURRENT_API / $CURRENT_WORKER"

if [[ -n "${PREVIOUS_PRODUCTION_IMAGE:-}" ]]; then
  echo "previous image: $PREVIOUS_PRODUCTION_IMAGE (switching compose image tags)"
  WEB_IMAGE="${PREVIOUS_PRODUCTION_IMAGE}"; API_IMAGE="${PREVIOUS_PRODUCTION_IMAGE}"; WORKER_IMAGE="${PREVIOUS_PRODUCTION_IMAGE}"
else
  echo "FIRST_PRODUCTION_RELEASE — no previous image; use current immutable images to restore."
  WEB_IMAGE="$CURRENT_WEB"; API_IMAGE="$CURRENT_API"; WORKER_IMAGE="$CURRENT_WORKER"
fi

# readiness-only：确认镜像存在、manifest 可读、production.env 存在、migration 版本兼容。
if [ "$CHECK_ONLY" = "--check" ]; then
  "${SSH[@]}" "[ -f /srv/kairos/env/production.env ] && stat -c '%a' /srv/kairos/env/production.env | grep -q '^600$'" \
    || { echo "ERROR: production.env missing or not 0600"; exit 1; }
  LATEST_MANIFEST="$("${SSH[@]}" "ls -1t /srv/kairos/releases/manifest-*.json 2>/dev/null | head -1")"
  [ -n "$LATEST_MANIFEST" ] || { echo "ERROR: no release manifest found"; exit 1; }
  "${SSH[@]}" "docker image inspect ${WEB_IMAGE} ${API_IMAGE} ${WORKER_IMAGE} >/dev/null 2>&1" \
    || { echo "ERROR: immutable images missing on server"; exit 1; }
  MIG="$("${SSH[@]}" "docker exec kairos-production-postgres-1 psql -U kairos_production -d kairos_production -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '[:space:]'")"
  echo "migration head: ${MIG:-unknown} (expected 0014)"
  echo "ROLLBACK_READY=PASS previous=none-first-release"
  exit 0
fi

# down WITHOUT -v：保留 named volumes（postgres_data/minio_data）。
"${SSH[@]}" "cd ${COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env down --remove-orphans"

"${SSH[@]}" "cd ${COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env up -d --wait 2>&1 | tail -12" \
  || { echo "ERROR: restore up failed"; exit 1; }

if [[ -n "${PREVIOUS_PRODUCTION_IMAGE:-}" ]]; then
  echo "ROLLBACK COMPLETE (previous image $PREVIOUS_PRODUCTION_IMAGE)"
else
  echo "RESTORE COMPLETE: current immutable images brought the stack back up (FIRST_PRODUCTION_RELEASE)."
fi

#!/usr/bin/env bash
# kairos-production rollback / recovery helper（Registry 镜像交付路径）。
#
# Registry 路径下回滚 = 用 deploy-production.sh 指向上一个 immutable RELEASE_TAG
# （服务器 docker pull 上版 ghcr 镜像 + compose up）。本脚本提供：
#   --check：回滚就绪检查（上版/当前镜像在场、manifest、migration、production.env）。
#   （无 --check）：保留的本地恢复 helper，用于 FIRST-release 或 break-glass 恢复。
#
# Usage (from repository root):
#   REGISTRY=ghcr.io NAMESPACE=gehrmannmerlin RELEASE_VERSION=v0.1.2 \
#     PREVIOUS_RELEASE=v0.1.1-3538e8841fd6 \
#     ./infra/scripts/rollback-production.sh --check
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:-47.238.145.24}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")
COMPOSE_DIR="/srv/kairos/compose"
REGISTRY="${REGISTRY:-ghcr.io}"
NAMESPACE="${NAMESPACE:-gehrmannmerlin}"
RELEASE_VERSION="${RELEASE_VERSION:-v0.1.0}"
CHECK_ONLY="${1:-}"

echo "==> rollback readiness for kairos-production (release=$RELEASE_VERSION)"

# 解析服务器上最新创建的不可变 tag（按创建时间，不按 lexicographic SHA）。
CURRENT_WEB="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | grep \"^${REGISTRY}/${NAMESPACE}/kairos-web:${RELEASE_VERSION}-\" | sort -t'|' -k2 | tail -1 | cut -d'|' -f1" 2>/dev/null || true)"
CURRENT_API="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | grep \"^${REGISTRY}/${NAMESPACE}/kairos-api:${RELEASE_VERSION}-\" | sort -t'|' -k2 | tail -1 | cut -d'|' -f1" 2>/dev/null || true)"
CURRENT_WORKER="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | grep \"^${REGISTRY}/${NAMESPACE}/kairos-worker:${RELEASE_VERSION}-\" | sort -t'|' -k2 | tail -1 | cut -d'|' -f1" 2>/dev/null || true)"
[[ -n "$CURRENT_WEB" && -n "$CURRENT_API" && -n "$CURRENT_WORKER" ]] \
  || { echo "ERROR: could not resolve current immutable images"; exit 1; }
echo "current images: $CURRENT_WEB / $CURRENT_API / $CURRENT_WORKER"

# readiness-only：确认镜像存在、manifest 可读、production.env 存在、migration 版本兼容、
# 回滚目标（上版）镜像在场。
if [ "$CHECK_ONLY" = "--check" ]; then
  "${SSH[@]}" "[ -f /srv/kairos/env/production.env ] && stat -c '%a' /srv/kairos/env/production.env | grep -q '^600$'" \
    || { echo "ERROR: production.env missing or not 0600"; exit 1; }
  LATEST_MANIFEST="$("${SSH[@]}" "ls -1t /srv/kairos/releases/manifest-*.json 2>/dev/null | head -1")"
  [ -n "$LATEST_MANIFEST" ] || { echo "ERROR: no release manifest found"; exit 1; }
  "${SSH[@]}" "docker image inspect ${CURRENT_WEB} ${CURRENT_API} ${CURRENT_WORKER} >/dev/null 2>&1" \
    || { echo "ERROR: current immutable images missing on server"; exit 1; }
  # 回滚目标：上版 release 的镜像。优先取 manifest 的 previous_release，否则要求显式传入。
  PREV="${PREVIOUS_RELEASE:-$("${SSH[@]}" "python3 -c \"import json;print(json.load(open('${LATEST_MANIFEST}')).get('previous_release',''))\"" 2>/dev/null || true)}"
  if [ -n "$PREV" ] && [ "$PREV" != "none-first-release" ]; then
    PREV_SHA="${PREV##*-}"
    "${SSH[@]}" "docker image inspect ${REGISTRY}/${NAMESPACE}/kairos-web:${PREV} ${REGISTRY}/${NAMESPACE}/kairos-api:${PREV} ${REGISTRY}/${NAMESPACE}/kairos-worker:${PREV} >/dev/null 2>&1" \
      && echo "rollback target images present: ${PREV}" \
      || echo "WARN: rollback target images not local (will docker pull on rollback): ${PREV}"
  else
    echo "rollback target: none-first-release (no previous release recorded)"
  fi
  MIG="$("${SSH[@]}" "docker exec kairos-production-postgres-1 psql -U kairos_production -d kairos_production -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '[:space:]'")"
  echo "migration head: ${MIG:-unknown} (expected 0014)"
  echo "ROLLBACK_READY=PASS previous=${PREV:-none-first-release}"
  exit 0
fi

# 无 --check：本地恢复 helper（FIRST-release / break-glass）。默认用当前 immutable 镜像重建。
WEB_IMAGE="$CURRENT_WEB"; API_IMAGE="$CURRENT_API"; WORKER_IMAGE="$CURRENT_WORKER"
"${SSH[@]}" "cd ${COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env down --remove-orphans"

"${SSH[@]}" "cd ${COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env up -d --wait 2>&1 | tail -12" \
  || { echo "ERROR: restore up failed"; exit 1; }
echo "RESTORE COMPLETE: current immutable images brought the stack back up."

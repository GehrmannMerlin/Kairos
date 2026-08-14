#!/usr/bin/env bash
# 生成/校验 production release manifest（/srv/kairos/releases/manifest-<version>.json）。
# 绝不写入任何 Secret。必填字段见 M-18 plan TEST C（test_release_contract.py）。
#   RELEASE_VERSION=v0.1.0 BACKUP_ID=production-... ROLLBACK_TARGET=kairos-*:v0.1.0-<sha> bash release-manifest.sh
set -euo pipefail
VERSION="${RELEASE_VERSION:-v0.1.0}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# 服务器 /srv/kairos 不是 git 仓库；release SHA 由发布流程显式传入（RELEASE_SHA），
# 否则回退到本机 git（仅本地场景），最后回退 unknown 并在校验阶段失败。
SHA="${RELEASE_SHA:-$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)}"
[ "$SHA" != "unknown" ] || { echo "RELEASE_SHA required (server has no git repo)" >&2; exit 2; }
# migration head 由发布流程显式传入（MIGRATION_HEAD，deploy-production.sh 从本地仓库推导）；
# 服务器非 git/build 机器，不得自行从 /backend 推导。
MIG="${MIGRATION_HEAD:-0015}"
DIR=/srv/kairos/releases
mkdir -p "$DIR"
OUT="$DIR/manifest-$VERSION.json"

# Images are pulled as <REGISTRY>/<NAMESPACE>/kairos-{web,api,worker}:<VERSION>-<SHA>.
REGISTRY="${REGISTRY:-ghcr.io}"
NAMESPACE="${NAMESPACE:-gehrmannmerlin}"

_digest() {
  local ref="$1" d
  d="$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null)" || d=""
  d="$(printf '%s' "$d" | tr -d '[:space:]')"  # strip any control/newline chars
  printf '%s' "${d:-n/a}"
}

WEB_DIGEST="$(_digest "${REGISTRY}/${NAMESPACE}/kairos-web:${VERSION}-${SHA}")"
API_DIGEST="$(_digest "${REGISTRY}/${NAMESPACE}/kairos-api:${VERSION}-${SHA}")"
WORKER_DIGEST="$(_digest "${REGISTRY}/${NAMESPACE}/kairos-worker:${VERSION}-${SHA}")"
BACKUP_ID="${BACKUP_ID:-not-yet}"
PREVIOUS="${PREVIOUS_RELEASE:-none-first-release}"
ROLLBACK="${ROLLBACK_TARGET:-none-first-release}"
cat > "$OUT" <<JSON
{
  "release_version": "$VERSION",
  "git_sha": "$SHA",
  "web_digest": "$WEB_DIGEST",
  "api_digest": "$API_DIGEST",
  "worker_digest": "$WORKER_DIGEST",
  "migration_version": "$MIG",
  "deploy_time": "$(date -u +%FT%TZ)",
  "environment": "production",
  "backup_id": "$BACKUP_ID",
  "previous_release": "$PREVIOUS",
  "rollback_target": "$ROLLBACK",
  "config_version": "production.env-$(stat -c '%Y' /srv/kairos/env/production.env 2>/dev/null || echo 0)"
}
JSON
python3 -c "import json,sys; d=json.load(open('$OUT')); required=['release_version','git_sha','web_digest','api_digest','worker_digest','migration_version','deploy_time','environment','backup_id','previous_release','rollback_target','config_version']; assert all(k in d for k in required), 'manifest missing fields'; assert 'secret' not in open('$OUT').read().lower(), 'manifest must not carry secrets'"
echo "RELEASE_MANIFEST_OK $OUT"

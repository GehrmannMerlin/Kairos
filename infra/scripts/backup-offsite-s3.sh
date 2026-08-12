#!/usr/bin/env bash
# 把 backup bundle push 到服务器外部 S3-compatible/OSS target 并校验 checksum。
#   BACKUP_ID=<id> bash backup-offsite-s3.sh
# 读取 /srv/kairos/env/backup-target.env（0600，值绝不回显）。
# 使用 minio/mc 一次性容器上传 + 重新下载校验 sha256。
# 输出 OFF_SERVER_S3_COPY=PASS（src/dst checksum 一致）或 FAIL。
set -euo pipefail
BACKUP_ID="${BACKUP_ID:?BACKUP_ID required}"
BACKUP_DIR="${BACKUP_DIR:-/srv/kairos/backups}"
SRC="$BACKUP_DIR/$BACKUP_ID"
[ -d "$SRC" ] || { echo "backup dir not found: $SRC" >&2; exit 1; }
ENV_FILE="/srv/kairos/env/backup-target.env"
[ -f "$ENV_FILE" ] || { echo "backup-target.env missing (must be 0600)" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
: "${BACKUP_S3_ENDPOINT:?BACKUP_S3_ENDPOINT required}"
: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET required}"
: "${BACKUP_S3_ACCESS_KEY:?BACKUP_S3_ACCESS_KEY required}"
: "${BACKUP_S3_SECRET_KEY:?BACKUP_S3_SECRET_KEY required}"

MC_IMG="${MC_IMAGE:-minio/mc:latest}"
PREFIX="${BACKUP_S3_PREFIX:-kairos-prod-backups}"
SCHEME="https"
# 兼容带 https:// 前缀的 endpoint
case "$BACKUP_S3_ENDPOINT" in
  http://*) SCHEME="http"; EP="${BACKUP_S3_ENDPOINT#http://}" ;;
  https://*) SCHEME="https"; EP="${BACKUP_S3_ENDPOINT#https://}" ;;
  *) EP="$BACKUP_S3_ENDPOINT" ;;
esac

# 打包源 bundle 并计算源 checksum
TARBALL="/tmp/backup-bundle-$BACKUP_ID.tar.gz"
tar czf "$TARBALL" -C "$SRC" .
SRC_SHA="$(sha256sum "$TARBALL" | cut -d' ' -f1)"

# 上传（minio/mc 一次性容器；credential 经环境变量传入，不写 shell history / 不回显）
docker run --rm \
  -e MC_HOST_target="$SCHEME://$EP" \
  -e MC_HOST_target_ACCESS_KEY="$BACKUP_S3_ACCESS_KEY" \
  -e MC_HOST_target_SECRET_KEY="$BACKUP_S3_SECRET_KEY" \
  -v "$TARBALL:/bundle.tar.gz:ro" \
  "$MC_IMG" cp /bundle.tar.gz "target/$PREFIX/$BACKUP_ID.tar.gz" \
  || { echo "S3 push failed" >&2; exit 1; }

# 重新下载并校验 checksum
DST_SHA="$(docker run --rm \
  -e MC_HOST_target="$SCHEME://$EP" \
  -e MC_HOST_target_ACCESS_KEY="$BACKUP_S3_ACCESS_KEY" \
  -e MC_HOST_target_SECRET_KEY="$BACKUP_S3_SECRET_KEY" \
  "$MC_IMG" cat "target/$PREFIX/$BACKUP_ID.tar.gz" | sha256sum | cut -d' ' -f1)"
rm -f "$TARBALL"

if [ -n "$DST_SHA" ] && [ "$SRC_SHA" = "$DST_SHA" ]; then
  echo "OFF_SERVER_S3_COPY=PASS backup_id=$BACKUP_ID src=$SRC_SHA dst=$DST_SHA"
  echo "OFFSITE_S3_KEY=$PREFIX/$BACKUP_ID.tar.gz"
else
  echo "OFF_SERVER_S3_COPY=FAIL src=$SRC_SHA dst=${DST_SHA:-none}" >&2
  exit 1
fi

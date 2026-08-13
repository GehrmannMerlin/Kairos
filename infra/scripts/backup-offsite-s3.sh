#!/usr/bin/env bash
# 把 backup bundle push 到服务器外部 S3-compatible/OSS target 并校验 checksum。
#   BACKUP_ID=<id> bash backup-offsite-s3.sh
# 读取 /srv/kairos/env/backup-target.env（0600，值绝不回显）。
# 使用 minio/mc 一次性容器上传 + 重新下载校验 sha256。
# 认证用 mc 标准 MC_HOST_<alias>=scheme://AK:SK@endpoint 格式，经 --env-file（600）传入，
# 绝不回显 credential、不写 shell history。
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
PREFIX="${BACKUP_S3_PREFIX:-}"
SCHEME="https"
case "$BACKUP_S3_ENDPOINT" in
  http://*) SCHEME="http"; EP="${BACKUP_S3_ENDPOINT#http://}" ;;
  https://*) EP="${BACKUP_S3_ENDPOINT#https://}" ;;
  *) EP="$BACKUP_S3_ENDPOINT" ;;
esac

# mc 认证：MC_HOST_<alias> 用 URL 内嵌 AK:SK（mc 官方格式）。写入 0600 env 文件经 --env-file 传入。
MC_ENV="/tmp/kairos-mc-${BACKUP_ID}.env"
umask 077
printf 'MC_HOST_target=%s://%s:%s@%s\n' "$SCHEME" "$BACKUP_S3_ACCESS_KEY" "$BACKUP_S3_SECRET_KEY" "$EP" > "$MC_ENV"
chmod 600 "$MC_ENV"
trap 'rm -f "$MC_ENV" "$TARBALL"' EXIT

# 打包源 bundle 并计算源 checksum
TARBALL="/tmp/backup-bundle-$BACKUP_ID.tar.gz"
tar czf "$TARBALL" -C "$SRC" .
SRC_SHA="$(sha256sum "$TARBALL" | cut -d' ' -f1)"

DEST="target/$BACKUP_S3_BUCKET/${PREFIX:+$PREFIX/}$BACKUP_ID.tar.gz"

# 上传
docker run --rm --env-file "$MC_ENV" -v "$TARBALL:/bundle.tar.gz:ro" \
  "$MC_IMG" cp /bundle.tar.gz "$DEST" \
  || { echo "S3 push failed" >&2; exit 1; }

# 重新下载并校验 checksum
DST_SHA="$(docker run --rm --env-file "$MC_ENV" \
  "$MC_IMG" cat "$DEST" | sha256sum | cut -d' ' -f1)"

if [ -n "$DST_SHA" ] && [ "$SRC_SHA" = "$DST_SHA" ]; then
  echo "OFF_SERVER_S3_COPY=PASS backup_id=$BACKUP_ID src=$SRC_SHA dst=$DST_SHA"
  echo "OFFSITE_S3_KEY=$BACKUP_S3_BUCKET/${PREFIX:+$PREFIX/}$BACKUP_ID.tar.gz"
else
  echo "OFF_SERVER_S3_COPY=FAIL src=$SRC_SHA dst=${DST_SHA:-none}" >&2
  exit 1
fi

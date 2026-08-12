#!/usr/bin/env bash
# 把 backup bundle 复制到服务器之外并校验 checksum。
#
# Staging Restore Drill off-server copy（OFF_SERVER_STAGING_DRILL_COPY）：
# 在本机（运行 Claude Code 的工作站）执行，从服务器拉取 backup bundle 到受控目录，
# 并比对源/目标聚合 sha256。这只是 Staging Restore Drill 的 off-server 证据，
# 不是长期 Production backup service（长期外部目标绑定 M-18 preflight）。
#
# 用法（本机）：
#   BACKUP_ID=staging-20260812-000000-abcd \
#   OFFSITE_LOCAL_DIR=~/kairos-offsite-backups \
#   DEPLOY_HOST=47.238.145.24 ./infra/scripts/backup-offsite.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_ID="${BACKUP_ID:?BACKUP_ID required (e.g. staging-20260812-000000-abcd)}"
OFFSITE_LOCAL_DIR="${OFFSITE_LOCAL_DIR:-$HOME/kairos-offsite-backups/staging}"
DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SERVER_BACKUP_DIR="${SERVER_BACKUP_DIR:-/srv/kairos/backups}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")

echo "==> verifying server bundle exists"
"${SSH[@]}" "test -d '$SERVER_BACKUP_DIR/$BACKUP_ID/manifest.json' && echo exists" 2>/dev/null || true
"${SSH[@]}" "test -f '$SERVER_BACKUP_DIR/$BACKUP_ID/manifest.json' || { echo 'bundle not found'; exit 1; }"

echo "==> pulling bundle to $OFFSITE_LOCAL_DIR"
mkdir -p "$OFFSITE_LOCAL_DIR"
rm -rf "$OFFSITE_LOCAL_DIR/$BACKUP_ID"
scp -q -r -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
  "${DEPLOY_USER}@${DEPLOY_HOST}:${SERVER_BACKUP_DIR}/${BACKUP_ID}" "$OFFSITE_LOCAL_DIR/"

echo "==> verifying checksums (src vs dst)"
SRC_SHA="$("${SSH[@]}" "cd '$SERVER_BACKUP_DIR/$BACKUP_ID' && find . -name '*.sha256' -type f | sort | xargs cat | sha256sum | cut -d' ' -f1")"
DST_SHA="$(cd "$OFFSITE_LOCAL_DIR/$BACKUP_ID" && find . -name '*.sha256' -type f | sort | xargs cat | sha256sum | cut -d' ' -f1)"

if [ "$SRC_SHA" = "$DST_SHA" ] && [ -f "$OFFSITE_LOCAL_DIR/$BACKUP_ID/manifest.json" ]; then
  echo "OFF_SERVER_COPY=PASS backup_id=$BACKUP_ID src=$SRC_SHA dst=$DST_SHA"
  echo "OFFSITE_DEST=$OFFSITE_LOCAL_DIR/$BACKUP_ID"
  exit 0
fi
echo "OFF_SERVER_COPY=FAIL src=$SRC_SHA dst=$DST_SHA" >&2
exit 1

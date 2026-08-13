#!/usr/bin/env bash
# 隔离 Restore Drill。在服务器（deploy 用户）执行。
#   RESTORE_BACKUP_DIR=/srv/kairos/backups/<backup_id> bash /srv/kairos/scripts/restore-drill.sh
#
# 绝不触碰 staging volume/网络/域名：独立 kairos-restore-drill Compose 项目，
# 独立 volume（drill_postgres_data / drill_minio_data）+ 独立 network，
# 不绑定公网域名、不发布 host 端口。
#
# 流程：PG 恢复 → MinIO 恢复 → migration 版本一致 → 5 项只读验证 → 清理。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC="${RESTORE_BACKUP_DIR:?RESTORE_BACKUP_DIR required (backup dir, e.g. /srv/kairos/backups/staging-...)}"
[ -f "$SRC/manifest.json" ] || { echo "no manifest.json in $SRC" >&2; exit 1; }

if [ -f "$SCRIPT_DIR/../compose/compose.restore-drill.yml" ]; then
  COMPOSE_FILE="$SCRIPT_DIR/../compose/compose.restore-drill.yml"
else
  COMPOSE_FILE="$ROOT/infra/compose/compose.restore-drill.yml"
fi
COMPOSE=(docker compose -f "$COMPOSE_FILE" --project-name kairos-restore-drill)
PROJECT="kairos-restore-drill"
RESTORE_API_IMAGE="${RESTORE_API_IMAGE:-kairos-api:staging-6a423a2a5e18}"
DRILL_MINIO_VOL="kairos-restore-drill_drill_minio_data"

PY="python3"
command -v python3 >/dev/null 2>&1 || PY="python"
MANIFEST_MIG="$("$PY" -c "import json;print(json.load(open('$SRC/manifest.json'))['migration_head'])")"
REC_COUNT="$("$PY" -c "import json;print(json.load(open('$SRC/manifest.json'))['postgres'].get('record_count', 0))")"
echo "manifest: migration_head=$MANIFEST_MIG record_count=$REC_COUNT"

fail() { echo "ERROR: $*" >&2; exit 1; }

cleanup() {
  echo "==> cleanup drill env"
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  docker volume rm "$DRILL_MINIO_VOL" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> fresh drill volumes"
"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
docker volume rm "$DRILL_MINIO_VOL" >/dev/null 2>&1 || true
docker volume create "$DRILL_MINIO_VOL" >/dev/null || fail "create drill minio volume"

echo "==> restore object storage into fresh drill minio volume (before minio starts)"
docker run --rm -v "$DRILL_MINIO_VOL:/data" -v "$SRC/objects:/backups:ro" alpine \
  tar xzf /backups/objects.tar.gz -C /data || fail "object restore failed"

echo "==> start drill postgres + minio"
"${COMPOSE[@]}" up -d postgres minio >/dev/null
for i in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T postgres pg_isready -U kairos_restore -d kairos_restore >/dev/null 2>&1; then break; fi
  sleep 2
done
"${COMPOSE[@]}" exec -T postgres pg_isready -U kairos_restore -d kairos_restore >/dev/null 2>&1 || fail "drill postgres not ready"

echo "==> restore postgres"
"${COMPOSE[@]}" exec -T postgres \
  sh -c 'pg_restore -U kairos_restore -d kairos_restore --no-owner --no-privileges --exit-on-error' \
  < "$SRC/postgres/postgres.dump" || fail "PG restore failed"

echo "==> migration / version compatibility check"
DB_MIG="$("${COMPOSE[@]}" exec -T postgres psql -U kairos_restore -d kairos_restore -tAc 'SELECT version_num FROM alembic_version' | tr -d '[:space:]')"
if [ "$MANIFEST_MIG" != "$DB_MIG" ]; then
  fail "MIGRATION MISMATCH manifest=$MANIFEST_MIG db=$DB_MIG"
fi
echo "MIGRATION_COMPATIBLE $DB_MIG"

echo "==> read validation (5 items, one-shot api-image container)"
docker run --rm --network kairos-restore-internal \
  -e KAIROS_ENV=staging \
  -e KAIROS_DATABASE_URL=postgresql+psycopg://kairos_restore:drill_dev_pw@postgres:5432/kairos_restore \
  -e KAIROS_S3_ENDPOINT=minio:9000 \
  -e KAIROS_S3_ACCESS_KEY=drill_minio \
  -e KAIROS_S3_SECRET_KEY=drill_minio_secret \
  -e KAIROS_S3_BUCKET=kairos-staging \
  -e KAIROS_S3_SECURE=false \
  -e KAIROS_OTEL_ENABLED=false \
  -e EXPECTED_RECORDS="$REC_COUNT" \
  -v "$SCRIPT_DIR/_restore_verify.py:/app/_restore_verify.py:ro" \
  "$RESTORE_API_IMAGE" python /app/_restore_verify.py || fail "restore verify failed"

echo "RESTORE_DRILL=PASS backup_id=$(basename "$SRC")"

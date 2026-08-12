#!/usr/bin/env bash
# kairos 完整 backup bundle。在服务器（deploy 用户）执行。
#   ENV=staging BACKUP_DIR=/srv/kairos/backups ./backup.sh
# 产出 <backup_id>/{postgres/postgres.dump, objects/objects.tar.gz, config/*.tar.gz,
#        secrets/secrets.env.enc, manifest.json} + 各文件 .sha256。
# 退出码：0=成功 2=INSUFFICIENT_BACKUP_SPACE 3=lock/其他错误。
#
# 规则（agent-production-deployment-standards.md §16 / M-17）：
#   - PG 使用 pg_dump 逻辑备份，绝不 cp data dir。
#   - MinIO/S3 对象备份：MinIO data volume 只读 tar + sha256（可验证复制 + 文件清单）。
#   - Secret 用 openssl AES-256-CBC 加密，manifest 只记录引用，绝无明文。
#   - flock 互斥锁 + 磁盘 preflight fail-fast（INSUFFICIENT_BACKUP_SPACE）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_NAME="${ENV:-staging}"
BACKUP_DIR="${BACKUP_DIR:-/srv/kairos/backups}"
MIN_FREE_MB="${MIN_FREE_MB:-2048}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
BACKUP_KEY="${BACKUP_KEY:-/srv/kairos/env/backup.key}"

# 服务器布局：脚本在 /srv/kairos/scripts，compose 在 /srv/kairos/compose
SERVICE_ROOT="/srv/kairos"
COMPOSE_DIR=""
if [ -d "$SERVICE_ROOT/compose" ]; then
  COMPOSE_DIR="$SERVICE_ROOT/compose"
elif [ -d "$ROOT/infra/compose" ]; then
  COMPOSE_DIR="$ROOT/infra/compose"
else
  echo "ERROR: compose dir not found" >&2
  exit 3
fi

compose() {  # compose <args...>
  if [ "$COMPOSE_DIR" = "$SERVICE_ROOT/compose" ]; then
    (cd "$COMPOSE_DIR" && docker compose -f compose.base.yml -f "compose.${ENV_NAME}.yml" "$@")
  else
    (cd "$COMPOSE_DIR" && docker compose -f compose.base.yml -f "compose.${ENV_NAME}.yml" "$@")
  fi
}

fail() { echo "ERROR: $*" >&2; exit "${2:-1}"; }

PY="python3"
command -v python3 >/dev/null 2>&1 || PY="python"

echo "==> disk preflight (min free ${MIN_FREE_MB}MB)"
mkdir -p "$BACKUP_DIR"
PROBLEMS="$("$PY" -c "
import sys
sys.path.insert(0, '$ROOT/infra/scripts')
from _backup_common import disk_preflight
print('\n'.join(disk_preflight(['$BACKUP_DIR', '/var/lib/docker'], $MIN_FREE_MB)))
")"
if [ -n "$PROBLEMS" ]; then
  echo "INSUFFICIENT_BACKUP_SPACE: $PROBLEMS" >&2
  exit 2
fi

echo "==> backup lock"
"$PY" -c "
import sys
sys.path.insert(0, '$ROOT/infra/scripts')
from _backup_common import acquire_lock
with acquire_lock('$BACKUP_DIR/.backup.lock'):
    print('lock acquired')
" || fail "backup lock held — another backup is running" 3

SHA="$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
BACKUP_ID="${ENV_NAME}-$(date -u +%Y%m%d-%H%M%S)-${SHA}"
DEST="$BACKUP_DIR/$BACKUP_ID"
mkdir -p "$DEST/postgres" "$DEST/objects" "$DEST/config" "$DEST/secrets"

echo "==> postgres dump (backup_id=$BACKUP_ID)"
compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' \
  > "$DEST/postgres/postgres.dump" || fail "pg_dump failed"
MIG_HEAD="$(compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"' | tr -d '[:space:]')"
sha256sum "$DEST/postgres/postgres.dump" | cut -d' ' -f1 > "$DEST/postgres/postgres.dump.sha256"

echo "==> object storage backup (MinIO data volume, read-only tar)"
MINIO_VOL="$(compose config --volumes 2>/dev/null | grep -i minio | head -1 || echo kairos-staging_minio_data)"
docker run --rm -v "$MINIO_VOL:/data:ro" -v "$DEST/objects:/backups" alpine \
  tar czf /backups/objects.tar.gz -C /data . || fail "minio volume tar failed"
sha256sum "$DEST/objects/objects.tar.gz" | cut -d' ' -f1 > "$DEST/objects/objects.tar.gz.sha256"

echo "==> config backup"
if [ "$COMPOSE_DIR" = "$SERVICE_ROOT/compose" ]; then
  (cd "$SERVICE_ROOT" && tar czf "$DEST/config/config.tar.gz" compose 2>/dev/null || true)
  if [ -f "$SERVICE_ROOT/deploy/nginx/conf.d/zz-kairos-staging-tls.conf" ]; then
    (cd "$SERVICE_ROOT/deploy/nginx/conf.d" && tar czf "$DEST/config/vhost.tar.gz" zz-kairos-staging-tls.conf)
  fi
else
  (cd "$ROOT" && tar czf "$DEST/config/config.tar.gz" infra/compose infra/reverse-proxy infra/otel)
fi
for f in "$DEST"/config/*.tar.gz; do
  [ -f "$f" ] && sha256sum "$f" | cut -d' ' -f1 > "$f.sha256"
done

echo "==> secret-safe backup (encrypted)"
if [ -f "/srv/kairos/env/${ENV_NAME}.env" ]; then
  if [ ! -f "$BACKUP_KEY" ]; then
    umask 077
    "$PY" -c "import secrets;open('$BACKUP_KEY','w').write(secrets.token_hex(32))"
    chmod 600 "$BACKUP_KEY"
  fi
  openssl enc -aes-256-cbc -pbkdf2 -salt \
    -in "/srv/kairos/env/${ENV_NAME}.env" \
    -out "$DEST/secrets/secrets.env.enc" \
    -pass file:"$BACKUP_KEY" || fail "secret encryption failed"
  sha256sum "$DEST/secrets/secrets.env.enc" | cut -d' ' -f1 > "$DEST/secrets/secrets.env.enc.sha256"
else
  echo "no /srv/kairos/env/${ENV_NAME}.env to encrypt — secret backup omitted"
fi

echo "==> manifest"
"$PY" - <<PYEOF
import json, os, sys
sys.path.insert(0, "$ROOT/infra/scripts")
from _backup_common import BackupManifest, sha256_file, write_manifest

d = "$DEST"
def ref(p: str) -> dict | None:
    path = os.path.join(d, p)
    if not os.path.exists(path):
        return None
    return {"ref": p, "sha256": sha256_file(path), "size": os.path.getsize(path)}

secrets_path = os.path.join(d, "secrets/secrets.env.enc")
m = BackupManifest(
    backup_id="$BACKUP_ID",
    environment="$ENV_NAME",
    timestamp="$(date -u +%FT%TZ)",
    git_sha="$SHA",
    migration_head="$MIG_HEAD",
    postgres=ref("postgres/postgres.dump"),
    objects=ref("objects/objects.tar.gz"),
    config=ref("config/config.tar.gz"),
    secrets={
        "encrypted": True,
        "cipher": "aes-256-cbc-pbkdf2",
        "ref": "secrets/secrets.env.enc" if os.path.exists(secrets_path) else None,
        "sha256": sha256_file(secrets_path) if os.path.exists(secrets_path) else None,
        "key_location": "$BACKUP_KEY (0600, on server only)",
    },
)
write_manifest(os.path.join(d, "manifest.json"), m)
print("MANIFEST_OK", json.dumps(m.to_dict(), sort_keys=True)[:160])
PYEOF

echo "==> retention (keep ${RETENTION_DAYS}d)"
"$PY" -c "
import sys
sys.path.insert(0, '$ROOT/infra/scripts')
from _backup_common import apply_retention
print('removed', apply_retention('$BACKUP_DIR', $RETENTION_DAYS))
"

echo "BACKUP_DONE backup_id=$BACKUP_ID"
echo "BACKUP_DEST=$DEST"

#!/usr/bin/env bash
# 在目标 production 服务器以 deploy 用户运行：生成 /srv/kairos/env/production.env（0600）。
# 全部随机值用 openssl rand，绝不回显任何 secret 值。
# 用法：PROD_ENV_PATH=/srv/kairos/env/production.env bash infra/scripts/gen-production-env.sh
set -euo pipefail

DEST="${PROD_ENV_PATH:-/srv/kairos/env/production.env}"
POSTGRES_DB="${POSTGRES_DB:-kairos_production}"
POSTGRES_USER="${POSTGRES_USER:-kairos_production}"
umask 077

cat > "$DEST" <<EOF
POSTGRES_DB=$POSTGRES_DB
POSTGRES_USER=$POSTGRES_USER
POSTGRES_PASSWORD=$(openssl rand -hex 24)
MINIO_ACCESS_KEY=$(openssl rand -hex 16)
MINIO_SECRET_KEY=$(openssl rand -hex 32)
KAIROS_TEMPORAL_NAMESPACE=kairos-production
KAIROS_S3_BUCKET=kairos-production
KAIROS_SESSION_SECRET=$(openssl rand -hex 32)
KAIROS_CREDENTIAL_MASTER_KEY=$(openssl rand -hex 32)
KAIROS_CREDENTIAL_KEY_VERSION=k1
KAIROS_WORKER_ROLES=all
EOF

chmod 600 "$DEST"
echo "generated $DEST (0600, owner $(stat -c '%U' "$DEST")) — values not echoed"

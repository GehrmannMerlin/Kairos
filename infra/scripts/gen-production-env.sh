#!/usr/bin/env bash
# 生成 production env 模板（M-18 发布时在目标服务器以 deploy 用户运行）。
# 绝不回显任何 secret 值；只生成 /srv/kairos/env/production.env（0600）。
# 本机不创建 production 环境。
set -euo pipefail

echo "usage: 在目标 production 服务器以 deploy 用户运行本脚本，生成 /srv/kairos/env/production.env（600）。"
echo "已生成变量名（值不回显）："
cat <<'EOF'
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
MINIO_ACCESS_KEY
MINIO_SECRET_KEY
KAIROS_TEMPORAL_NAMESPACE
KAIROS_S3_BUCKET
KAIROS_SESSION_SECRET
KAIROS_CREDENTIAL_MASTER_KEY
KAIROS_CREDENTIAL_KEY_VERSION
EOF

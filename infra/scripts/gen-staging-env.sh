#!/usr/bin/env bash
# Generate (idempotently) the kairos-staging secret env on the server.
#
#   DEPLOY_HOST=47.238.145.24 ./infra/scripts/gen-staging-env.sh
#
# Rules:
#   - If /srv/kairos/env/staging.env already exists, it is PRESERVED (never
#     regenerated) so the M-03 credential master key stays stable across
#     redeploys and existing encrypted credentials remain decryptable.
#   - KAIROS_CREDENTIAL_MASTER_KEY is exactly 64 hex chars (32 bytes) as
#     required by app.credentials.crypto.master_key_from_env_value.
#   - Secrets never leave the server; this script prints key names only.
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes "${DEPLOY_USER}@${DEPLOY_HOST}")
ENV_PATH="/srv/kairos/env/staging.env"

"${SSH[@]}" 'bash -s' <<'EOF'
set -euo pipefail
umask 077
ENV=/srv/kairos/env/staging.env
if [ -f "$ENV" ]; then
  echo "ENV_EXISTS_PRESERVE (not regenerating — master key stability)"
  grep '^KAIROS_CREDENTIAL_MASTER_KEY=' "$ENV" | awk -F= '{print "master_key_len=" length($2)}'
  exit 0
fi
gen() { python3 -c "import secrets,sys;print(secrets.token_hex(int(sys.argv[1])//4))" "$1"; }
{
  echo "# kairos-staging env — generated $(date -u +%FT%TZ), owner deploy, DO NOT COMMIT"
  echo "POSTGRES_DB=kairos_staging"
  echo "POSTGRES_USER=kairos_staging"
  echo "POSTGRES_PASSWORD=$(gen 24)"
  echo "MINIO_ACCESS_KEY=kairos_staging"
  echo "MINIO_SECRET_KEY=$(gen 24)"
  echo "KAIROS_SESSION_SECRET=$(gen 32)"
  echo "KAIROS_CREDENTIAL_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
  echo "KAIROS_CREDENTIAL_KEY_VERSION=k1"
  echo "KAIROS_OTEL_ENABLED=true"
  echo "KAIROS_STAGING_PROJECT=kairos-staging"
} > "$ENV"
chmod 600 "$ENV"
echo "SECRETS_GENERATED"
grep '^KAIROS_CREDENTIAL_MASTER_KEY=' "$ENV" | awk -F= '{print "master_key_len=" length($2)}'
EOF

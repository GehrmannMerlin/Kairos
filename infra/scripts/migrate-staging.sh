#!/usr/bin/env bash
# Apply pending Alembic migrations against the kairos-staging database and report
# the resulting alembic_version.
#
# Usage (from repository root):
#   DEPLOY_HOST=47.238.145.24 ./infra/scripts/migrate-staging.sh [revision]
#
# Migration runs through the `migrate` compose service (same image as api) against
# the staging DB. No manual table changes are ever allowed on the server.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")
COMPOSE_DIR="/srv/kairos/compose"
REVISION="${1:-head}"

echo "==> running alembic upgrade $REVISION on kairos-staging"
"${SSH[@]}" "cd ${COMPOSE_DIR} && docker compose -p kairos-staging \
    -f compose.base.yml -f compose.staging.yml \
    --env-file /srv/kairos/env/staging.env run --rm migrate upgrade $REVISION" \
  || { echo "ERROR: migration failed"; exit 1; }

echo "==> current alembic version"
"${SSH[@]}" "docker compose -p kairos-staging exec -T postgres \
    psql -U \${POSTGRES_USER:-kairos_staging} -d \${POSTGRES_DB:-kairos_staging} \
    -tAc 'select version_num from alembic_version;'" \
  || { echo "ERROR: could not read alembic_version"; exit 1; }

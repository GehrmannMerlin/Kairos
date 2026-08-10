#!/usr/bin/env bash
# kairos-staging rollback / recovery helper.
#
# For the FIRST Staging release there is no previous image: record FIRST_STAGING_RELEASE
# and verify the current immutable digest can be re-deployed / brought up to restore.
# For later releases, set PREVIOUS_STAGING_IMAGE to the previous immutable tag and this
# script will switch compose back to it and re-run the stack.
#
# Usage (from repository root):
#   DEPLOY_HOST=47.238.145.24 ./infra/scripts/rollback-staging.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes "${DEPLOY_USER}@${DEPLOY_HOST}")
COMPOSE_DIR="/srv/kairos/compose"
COMPOSE=(docker compose -p kairos-staging -f "${COMPOSE_DIR}/compose.base.yml" -f "${COMPOSE_DIR}/compose.staging.yml")

echo "==> rollback readiness for kairos-staging"

# Resolve the most recently created immutable tag from the server (staging-<sha>).
# Sort by creation time, not tag lexicographically (SHA sort is meaningless).
CURRENT_WEB="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | grep '^kairos-web:staging-' | sort -t'|' -k2 | tail -1 | cut -d'|' -f1" 2>/dev/null || true)"
CURRENT_API="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | grep '^kairos-api:staging-' | sort -t'|' -k2 | tail -1 | cut -d'|' -f1" 2>/dev/null || true)"
CURRENT_WORKER="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' | grep '^kairos-worker:staging-' | sort -t'|' -k2 | tail -1 | cut -d'|' -f1" 2>/dev/null || true)"
[[ -n "$CURRENT_WEB" && -n "$CURRENT_API" && -n "$CURRENT_WORKER" ]] \
  || { echo "ERROR: could not resolve current immutable images"; exit 1; }
echo "current images: $CURRENT_WEB / $CURRENT_API / $CURRENT_WORKER"

if [[ -n "${PREVIOUS_STAGING_IMAGE:-}" ]]; then
  echo "previous image: $PREVIOUS_STAGING_IMAGE (switching compose image tags)"
  WEB_IMAGE="${PREVIOUS_STAGING_IMAGE}"; API_IMAGE="${PREVIOUS_STAGING_IMAGE}"; WORKER_IMAGE="${PREVIOUS_STAGING_IMAGE}"
else
  echo "FIRST_STAGING_RELEASE — no previous image; verify current immutable images can restore the stack."
  WEB_IMAGE="$CURRENT_WEB"; API_IMAGE="$CURRENT_API"; WORKER_IMAGE="$CURRENT_WORKER"
fi

# down WITHOUT -v: named volumes (postgres_data/minio_data) are preserved.
"${SSH[@]}" "cd ${COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml \
    --env-file /srv/kairos/env/staging.env down --remove-orphans"

"${SSH[@]}" "cd ${COMPOSE_DIR} && KAIROS_WEB_IMAGE=${WEB_IMAGE} \
    KAIROS_API_IMAGE=${API_IMAGE} KAIROS_WORKER_IMAGE=${WORKER_IMAGE} \
    docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml \
    --env-file /srv/kairos/env/staging.env up -d --wait 2>&1 | tail -12" \
  || { echo "ERROR: restore up failed"; exit 1; }

if [[ -n "${PREVIOUS_STAGING_IMAGE:-}" ]]; then
  echo "ROLLBACK COMPLETE (previous image $PREVIOUS_STAGING_IMAGE)"
else
  echo "RESTORE COMPLETE: current immutable images brought the stack back up (FIRST_STAGING_RELEASE)."
fi

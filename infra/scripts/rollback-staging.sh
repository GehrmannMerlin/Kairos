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
if [[ -n "${PREVIOUS_STAGING_IMAGE:-}" ]]; then
  echo "previous image: $PREVIOUS_STAGING_IMAGE (switching compose image tags)"
  "${SSH[@]}" "cd ${COMPOSE_DIR} && sed -i 's|KAIROS_*_IMAGE.*|&|' /dev/null" >/dev/null 2>&1 || true
  # Compose image tags come from the deploy script env; for rollback we override
  # them by exporting the previous tags for this invocation.
  "${SSH[@]}" "cd ${COMPOSE_DIR} && KAIROS_WEB_IMAGE=$PREVIOUS_STAGING_IMAGE \
      KAIROS_API_IMAGE=$PREVIOUS_STAGING_IMAGE \
      KAIROS_WORKER_IMAGE=$PREVIOUS_STAGING_IMAGE \
      docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml \
      --env-file /srv/kairos/env/staging.env up -d --wait"
  echo "ROLLBACK COMPLETE (previous image $PREVIOUS_STAGING_IMAGE)"
else
  echo "FIRST_STAGING_RELEASE — no previous image to roll back to by definition."
  echo "Verifying the current immutable images can restore the stack from down/up."
  CURRENT_WEB="$("${SSH[@]}" "docker images --format '{{.Repository}}:{{.Tag}}' | grep '^kairos-web:staging-' | sort | tail -1")"
  echo "current web image: ${CURRENT_WEB:-none}"
  "${SSH[@]}" "cd ${COMPOSE_DIR} && docker compose -p kairos-staging \
      -f compose.base.yml -f compose.staging.yml \
      --env-file /srv/kairos/env/staging.env down --remove-orphans"
  "${SSH[@]}" "cd ${COMPOSE_DIR} && docker compose -p kairos-staging \
      -f compose.base.yml -f compose.staging.yml \
      --env-file /srv/kairos/env/staging.env up -d --wait 2>&1 | tail -15"
  echo "RESTORE COMPLETE: current immutable images brought the stack back up."
fi
